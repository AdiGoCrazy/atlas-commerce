"""HTTP orchestration for same-model specialist workflows and scoped writes."""

import hashlib
import hmac
import json
import logging
import math
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from pydantic import ValidationError

from adaptive_commerce.agents.subagent import AGENT_DUTIES
from adaptive_commerce.application.assistant import TOOL_SCHEMAS, _execute_tool
from adaptive_commerce.application.assistant import _action_request_allowed, _lifecycle_action_requested
from adaptive_commerce.application.workflows import (
    ProposedOperation, SpecialistReport, WorkflowPlan, WorkflowProposal,
    approve_workflow, claim_action, create_workflow, finish_action, get_workflow,
    list_workflows, record_event, save_proposal, set_workflow_status, skip_pending_actions, update_step,
)
from adaptive_commerce.integrations.observability import trace_step
from adaptive_commerce.integrations.trace_readback import read_trace_summary
from adaptive_commerce.integrations.finance import FinanceInputs
from adaptive_commerce.persistence import connect
from adaptive_commerce.persistence.database import _json_dumps

logger = logging.getLogger(__name__)
_COORDINATOR_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="atlas-workflow")
_SPECIALIST_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix="atlas-specialist")
_ROLES = ("planner", "research", "finance", "recommendation", "review")
_ACTION_ROLE = {
    "create_candidate_region": "finance", "update_candidate_region": "finance",
    "set_region_lifecycle": "finance", "remove_region": "finance",
    "add_synthetic_signal": "research", "remove_pending_signal": "research",
    "repair_signal": "research",
    "run_region_cycle": "recommendation", "replay_region_cycle": "recommendation",
    "manage_product_catalog": "recommendation", "hard_delete_record": "review",
}
_HARD_DELETE_TYPES = {"region", "signal", "product"}


class WorkflowService:
    """Coordinate specialist planning and execute operations directly requested by the operator."""

    def __init__(self, database_path: Path, worker_urls: dict[str, str] | None = None):
        self.path = database_path
        self.worker_urls = worker_urls or _worker_urls()

    def start(self, request_text: str) -> dict[str, Any]:
        if not request_text.strip() or len(request_text) > 4000:
            raise ValueError("workflow request must contain 1 to 4000 characters")
        workflow_id = create_workflow(self.path, request_text.strip())
        _COORDINATOR_POOL.submit(self._plan, workflow_id, request_text.strip())
        result = get_workflow(self.path, workflow_id)
        return result or {"id": workflow_id, "status": "planning"}

    def approve(self, workflow_id: str, plan_hash: str, actor: str) -> dict[str, Any]:
        workflow = approve_workflow(self.path, workflow_id, plan_hash, actor)
        _COORDINATOR_POOL.submit(self._execute_plan, workflow_id)
        return workflow

    def _plan(self, workflow_id: str, request_text: str) -> None:
        try:
            with trace_step("WORKFLOW", "atlas_multi_agent_plan",
                            attributes={"workflow_id": workflow_id, "agent_count": len(_ROLES)}) as span:
                context = _atlas_context(self.path)
                update_step(self.path, workflow_id, "planner", status="working", label="Planning scoped workflow…")
                planner_result = _post_json(self.worker_urls["planner"] + "/v1/task", {
                    "workflow_id": workflow_id, "role": "planner", "request": request_text, "context": context,
                })
                plan = WorkflowPlan.model_validate(planner_result["plan"])
                plan = self._validate_and_enrich_plan(workflow_id, plan)
                update_step(self.path, workflow_id, "planner", status="complete",
                            label="Plan drafted", summary=plan.summary,
                            result={"action_count": len(plan.actions)})
                reports = self._run_specialists(workflow_id, request_text, context, plan)
                update_step(self.path, workflow_id, "review", status="reviewing", label="Reviewing evidence and impact…")
                review_response = _post_json(self.worker_urls["review"] + "/v1/task", {
                    "workflow_id": workflow_id, "role": "review", "request": request_text,
                    "context": context, "plan": plan.model_dump(mode="json"),
                    "reports": [report.model_dump(mode="json") for report in reports],
                })
                review = SpecialistReport.model_validate(review_response["report"])
                proposal = WorkflowProposal(plan=plan, reports=reports, review=review)
                plan_hash = save_proposal(self.path, workflow_id, proposal,
                                          f"{span.get_span_context().trace_id:032x}")
                update_step(self.path, workflow_id, "review", status=review.status,
                            label="Review complete; executing requested work", summary=review.summary,
                            result={"findings": review.findings, "plan_hash": plan_hash})
                # The direct operator request authorizes its exact allowlisted operations.
                # Intent, schemas, dependencies, finance gates, and destructive targets
                # are validated before this transition; a second approval click is not needed.
                approve_workflow(self.path, workflow_id, plan_hash, "operator_prompt")
                record_event(self.path, workflow_id, "request_authorized_execution_started", "agent_orchestrator",
                             {"plan_hash": plan_hash, "action_count": len(plan.actions)})
                _COORDINATOR_POOL.submit(self._execute_plan, workflow_id)
        except Exception as error:
            logger.exception("workflow_planning_failed", extra={"workflow_id": workflow_id})
            current_workflow = get_workflow(self.path, workflow_id)
            if current_workflow and current_workflow["status"] != "cancelled":
                set_workflow_status(self.path, workflow_id, "failed", error=f"{type(error).__name__}: {str(error)[:500]}")
            current = get_workflow(self.path, workflow_id)
            if current and current["status"] != "cancelled":
                for step in current.get("steps", []):
                    if step["status"] == "working":
                        update_step(
                            self.path, workflow_id, step["role"], status="failed",
                            label="Could not complete",
                            summary=f"{type(error).__name__}: {str(error)[:300]}",
                        )
                    elif step["status"] in {"queued", "reviewing"}:
                        update_step(
                            self.path, workflow_id, step["role"], status="skipped",
                            label="Not run after an earlier workflow step failed",
                            summary="This specialist was not called because a required earlier step failed.",
                        )

    def _run_specialists(self, workflow_id: str, request_text: str,
                         context: dict[str, Any], plan: WorkflowPlan) -> list[SpecialistReport]:
        roles = ("research", "finance", "recommendation")
        futures = []
        for role in roles:
            label = {
                "research": "Researching Atlas records…",
                "finance": "Assessing finance and viability…",
                "recommendation": "Checking rankings and changes…",
            }[role]
            update_step(self.path, workflow_id, role, status="working", label=label)
            payload = {
                "workflow_id": workflow_id, "role": role, "request": request_text,
                "context": context, "plan": plan.model_dump(mode="json"),
            }
            futures.append((role, _SPECIALIST_POOL.submit(_post_json, self.worker_urls[role] + "/v1/task", payload)))
        reports: list[SpecialistReport] = []
        failures: list[str] = []
        for role, future in futures:
            try:
                response = future.result(timeout=100)
                report = SpecialistReport.model_validate(response["report"])
                if report.role != role:
                    raise ValueError(f"{role} worker returned another specialist's report")
                reports.append(report)
                update_step(self.path, workflow_id, role, status=report.status,
                            label=report.label, summary=report.summary,
                            result={"findings": report.findings})
            except Exception as error:
                failures.append(f"{role}: {type(error).__name__}: {str(error)[:240]}")
                update_step(
                    self.path, workflow_id, role, status="failed",
                    label=f"{role.title()} specialist failed",
                    summary=failures[-1],
                )
        if failures:
            raise RuntimeError("Specialist review failed: " + " | ".join(failures))
        return reports

    def _validate_and_enrich_plan(self, workflow_id: str, plan: WorkflowPlan) -> WorkflowPlan:
        enriched: list[ProposedOperation] = []
        ignored_fields: list[dict[str, Any]] = []
        request = get_workflow(self.path, workflow_id)["request"]
        actions = _ensure_candidate_activation_steps(plan.actions, request)
        for index, operation in enumerate(actions):
            if operation.tool_name not in _ACTION_ROLE:
                raise ValueError(f"unsupported workflow operation: {operation.tool_name}")
            # Providers sometimes attach planning prose or their own config fields to
            # an otherwise valid tool call. Drop only undeclared top-level metadata;
            # required fields, value types, intent, dependencies, and nested schemas
            # are still validated below before specialists can review the plan.
            schema = TOOL_SCHEMAS.get(operation.tool_name)
            allowed_fields = (
                set(schema.get("properties", {})) if schema is not None
                else {"target_type", "target_id"} if operation.tool_name == "hard_delete_record"
                else set()
            )
            extra_fields = sorted(set(operation.arguments) - allowed_fields)
            if extra_fields:
                operation = operation.model_copy(update={
                    "arguments": {
                        key: value for key, value in operation.arguments.items()
                        if key in allowed_fields
                    },
                })
                ignored_fields.append({
                    "tool_name": operation.tool_name,
                    "fields": extra_fields[:20],
                })
            if operation.tool_name == "create_candidate_region":
                finance_fields = set(TOOL_SCHEMAS[operation.tool_name]["properties"]) - {
                    "region_name", "locale",
                }
                explicit_finance_fields = _explicit_finance_fields(request)
                invented_finance_fields = sorted(
                    (set(operation.arguments) & finance_fields) - explicit_finance_fields,
                )
                if invented_finance_fields:
                    operation = operation.model_copy(update={
                        "arguments": {
                            key: value for key, value in operation.arguments.items()
                            if key not in invented_finance_fields
                        },
                    })
                    ignored_fields.append({
                        "tool_name": operation.tool_name,
                        "fields": invented_finance_fields[:20],
                    })
            if operation.tool_name == "add_synthetic_signal" and "source_type" not in operation.arguments:
                inferred_source_type = self._infer_explicit_signal_source_type(request)
                if inferred_source_type is None and _action_request_allowed("add_synthetic_signal", request):
                    inferred_source_type = self._default_synthetic_source_type(request)
                if inferred_source_type is not None:
                    operation = operation.model_copy(update={
                        "arguments": {**operation.arguments, "source_type": inferred_source_type},
                    })
            if operation.tool_name == "hard_delete_record":
                if not _hard_delete_was_explicitly_requested(request, operation.arguments):
                    raise ValueError("permanent deletion must be explicitly requested by the operator")
            elif not _action_request_allowed(
                operation.tool_name,
                request,
                operation.arguments,
            ) and not _activation_is_approved_workflow_prerequisite(
                operation, index, actions, request,
            ):
                raise ValueError(f"{operation.tool_name} is not explicitly authorized by the operator request")
            expected_role = _ACTION_ROLE[operation.tool_name]
            if operation.agent_role not in {expected_role, "planner"}:
                raise ValueError(f"{operation.tool_name} must be assigned to the {expected_role} specialist")
            operation = operation.model_copy(update={"agent_role": expected_role})
            if _activation_is_approved_workflow_prerequisite(operation, index, actions, request):
                operation = operation.model_copy(update={
                    "impact": (
                        f"{operation.impact} This activates the candidate created earlier in this plan, "
                        "only if Atlas's finance gate approves it. It is required for the explicitly "
                        "requested downstream signal/cycle. The finance gate remains authoritative."
                    )[:1000],
                })
            if operation.tool_name == "hard_delete_record":
                impact = _hard_delete_impact(self.path, operation.arguments)
                target_labels = [str(impact["target_id"])]
                for key in ("name", "id"):
                    label = impact["snapshot"].get(key)
                    if label:
                        target_labels.append(str(label))
                if not any(label.casefold() in request.casefold() for label in target_labels):
                    raise ValueError("permanent deletion target must be named exactly in the operator request")
                details = {"target_type": impact["target_type"], "target_id": impact["target_id"],
                           "dependencies": impact["dependencies"],
                           "target_snapshot": impact["snapshot"],
                           "snapshot_hash": _hash_json(impact["snapshot"])}
                operation = operation.model_copy(update={
                    "impact": f"{operation.impact} Permanent removal. Affected records: "
                              f"{json.dumps(impact['dependencies'], sort_keys=True)}",
                    "impact_details": details,
                })
            else:
                _validate_arguments(operation.tool_name, operation.arguments)
                _validate_candidate_dependency(operation, index, actions)
            enriched.append(operation)
        if ignored_fields:
            record_event(
                self.path,
                workflow_id,
                "planner_arguments_normalized",
                "agent_orchestrator",
                {"operations": ignored_fields},
            )
            logger.info(
                "planner_arguments_normalized",
                extra={"workflow_id": workflow_id, "operations": ignored_fields},
            )
        scope_notes = _unsupported_request_scope_notes(request)
        summary = plan.summary
        if scope_notes:
            summary = f"{summary}\n\nCurrent Atlas limits: {' '.join(scope_notes)}"[:2000]
        return plan.model_copy(update={"actions": enriched, "summary": summary})

    @staticmethod
    def _infer_explicit_signal_source_type(request: str) -> str | None:
        """Infer a signal source only when the operator names it beside signal/evidence."""
        match = re.search(
            r"\b(news|forum|social|event)(?:[- ]based)?\s+(?:synthetic\s+)?(?:signal|evidence)\b",
            request,
            re.I,
        )
        return match.group(1).lower() if match else None

    @staticmethod
    def _default_synthetic_source_type(request: str) -> str:
        """Choose a transparent demo source for an explicit synthetic signal request."""
        if re.search(r"\b(festivals?|holidays?|seasons?|events?)\b", request, re.I):
            return "event"
        return "news"

    def _execute_plan(self, workflow_id: str) -> None:
        workflow = get_workflow(self.path, workflow_id)
        if not workflow or workflow["status"] != "approved":
            return
        plan_hash = workflow["plan_hash"]
        actions = workflow["actions"]
        record_event(self.path, workflow_id, "authorized_execution_started", "agent_orchestrator",
                     {"plan_hash": plan_hash, "action_count": len(actions)})
        completed = 0
        try:
            with trace_step("WORKFLOW", "atlas_authorized_agent_execution",
                            attributes={"workflow_id": workflow_id, "approved_action_count": len(actions)}) as span:
                for action in actions:
                    role = action["agent_role"]
                    update_step(self.path, workflow_id, role, status="executing",
                                label=f"Working: {action['tool_name'].replace('_', ' ')}…")
                    result = _post_json(self.worker_urls[role] + "/v1/execute", {
                        "workflow_id": workflow_id,
                        "operation_id": action["id"],
                        "plan_hash": plan_hash,
                        "agent_role": role,
                    })
                    completed += 1
                    update_step(self.path, workflow_id, role, status="complete",
                                label=f"Completed: {action['tool_name'].replace('_', ' ')}",
                                summary=str(result.get("summary", "Requested operation completed."))[:2000],
                                result=result)
                set_workflow_status(self.path, workflow_id, "completed")
                record_event(self.path, workflow_id, "workflow_completed", "agent_orchestrator",
                             {"completed_actions": completed, "trace_id": f"{span.get_span_context().trace_id:032x}"})
        except Exception as error:
            logger.exception("approved_workflow_execution_failed", extra={"workflow_id": workflow_id})
            current = get_workflow(self.path, workflow_id)
            terminal = "partially_failed" if completed else "failed"
            set_workflow_status(self.path, workflow_id, terminal,
                                error=f"{type(error).__name__}: {str(error)[:500]}")
            record_event(self.path, workflow_id, "workflow_execution_failed", "agent_orchestrator",
                         {"completed_actions": completed, "error": f"{type(error).__name__}: {str(error)[:300]}"})
            if current:
                for step in current["steps"]:
                    if step["status"] == "executing":
                        update_step(self.path, workflow_id, step["role"], status="failed",
                                    label="Stopped after operation failure", summary=str(error)[:300])
            skipped = skip_pending_actions(
                self.path,
                workflow_id,
                f"Not run because an earlier operation failed: {type(error).__name__}: {str(error)[:240]}",
            )
            if skipped:
                record_event(self.path, workflow_id, "downstream_operations_skipped", "agent_orchestrator",
                             {"operation_ids": [action["id"] for action in skipped],
                              "reason": f"{type(error).__name__}: {str(error)[:240]}"})

    def execute_operation(self, payload: dict[str, Any]) -> dict[str, Any]:
        workflow_id = str(payload.get("workflow_id", ""))
        operation_id = str(payload.get("operation_id", ""))
        plan_hash = str(payload.get("plan_hash", ""))
        action = claim_action(self.path, workflow_id, operation_id, plan_hash)
        if action.get("already_complete"):
            return json.loads(action["result_json"] or "{}")
        try:
            arguments = json.loads(action["arguments_json"])
            if action["tool_name"] == "hard_delete_record":
                workflow = get_workflow(self.path, workflow_id)
                approved_operation = workflow["plan"]["actions"][int(action["ordinal"])]
                result = _execute_hard_delete(self.path, workflow_id, operation_id,
                                               action["impact"], arguments,
                                               approved_operation.get("impact_details", {}),
                                               get_workflow(self.path, workflow_id)["approved_by"])
            else:
                _validate_arguments(action["tool_name"], arguments)
                result = _execute_tool(self.path, action["tool_name"], arguments, trace_reader=read_trace_summary)
                if action["tool_name"] == "create_candidate_region":
                    result = _reassess_existing_candidate_for_workflow(
                        self.path, workflow_id, arguments, result,
                    )
            finish_action(self.path, workflow_id, operation_id, result=result)
            record_event(self.path, workflow_id, "operation_completed", action["agent_role"],
                         {"operation_id": operation_id, "tool_name": action["tool_name"]})
            return result
        except Exception as error:
            finish_action(self.path, workflow_id, operation_id,
                          error=f"{type(error).__name__}: {str(error)[:500]}")
            record_event(self.path, workflow_id, "operation_failed", action["agent_role"],
                         {"operation_id": operation_id, "tool_name": action["tool_name"],
                          "error": f"{type(error).__name__}: {str(error)[:300]}"})
            raise

    def agents(self) -> list[dict[str, Any]]:
        """Return current health and selected model for all deployed specialists."""
        result = []
        for role in _ROLES:
            try:
                health = _get_json(self.worker_urls[role] + "/healthz", timeout=2)
            except Exception as error:
                health = {"status": "offline", "role": role, "duty": AGENT_DUTIES[role],
                          "detail": f"Worker health check failed: {type(error).__name__}"}
            result.append(health)
        return result


def _atlas_context(path: Path) -> dict[str, Any]:
    """Read only bounded facts through Atlas's existing application tools."""
    context = {
        "portfolio": _execute_tool(path, "get_portfolio_snapshot", {}, trace_reader=lambda _: None),
        "catalog": _execute_tool(path, "get_product_catalog", {}, trace_reader=lambda _: None),
        "regions": [],
    }
    for region in context["portfolio"].get("regions", []):
        context["regions"].append(_execute_tool(
            path, "get_region_snapshot", {"region_id": region["id"]}, trace_reader=lambda _: None,
        ))
    return context


def _reassess_existing_candidate_for_workflow(
    path: Path,
    workflow_id: str,
    create_arguments: dict[str, Any],
    create_result: dict[str, Any],
) -> dict[str, Any]:
    """Refresh a rejected local candidate only when this plan depends on activating it."""
    if create_result.get("status") != "already_exists":
        return create_result
    region = create_result.get("region") or {}
    region_id = region.get("id")
    if region.get("state") != "candidate" or not region_id:
        return create_result

    workflow = get_workflow(path, workflow_id)
    plan_actions = (workflow or {}).get("plan", {}).get("actions", [])
    activation_requested = any(
        item.get("tool_name") == "set_region_lifecycle"
        and item.get("arguments", {}).get("action") == "approve"
        and str(item.get("arguments", {}).get("region_id", "")).strip().casefold()
        == str(create_arguments.get("region_name", "")).strip().casefold()
        for item in plan_actions
    )
    if not activation_requested:
        return create_result

    with connect(path) as conn:
        assessment = conn.execute(
            "SELECT decision, assumptions, cfo_reference FROM finance_assessments "
            "WHERE region_id = ? ORDER BY created_at DESC LIMIT 1",
            (region_id,),
        ).fetchone()
    if not assessment or assessment["decision"] != "reject" or assessment["cfo_reference"]:
        return create_result
    try:
        saved_assumptions = json.loads(assessment["assumptions"])
    except (TypeError, json.JSONDecodeError):
        return create_result
    if saved_assumptions.get("source") != "local_simulation":
        return create_result

    assumptions = FinanceInputs().model_dump(mode="json")
    finance_fields = set(assumptions)
    assumptions.update({
        key: value for key, value in create_arguments.items()
        if key in finance_fields
    })
    reassessment = _execute_tool(
        path,
        "update_candidate_region",
        {"region_id": region_id, "finance_updates": assumptions},
        trace_reader=read_trace_summary,
    )
    record_event(
        path,
        workflow_id,
        "existing_candidate_finance_reassessed",
        "agent_orchestrator",
        {"region_id": region_id, "previous_decision": "reject",
         "new_decision": reassessment.get("finance_decision"),
         "basis": "documented simulation defaults plus operator-stated assumptions"},
    )
    return {
        **create_result,
        "status": "reassessed_existing",
        "finance_decision": reassessment.get("finance_decision"),
        "simulated_monthly_profit": reassessment.get("simulated_monthly_profit"),
        "summary": (
            f"Reassessed the existing candidate {region.get('name')} using Atlas simulation defaults "
            "and only finance values stated in this request. The finance gate still determines activation."
        ),
    }


def _validate_arguments(tool_name: str, arguments: dict[str, Any]) -> None:
    """Validate proposed tool arguments against the existing allowlist schemas."""
    schema = TOOL_SCHEMAS.get(tool_name)
    if schema is None:
        raise ValueError(f"no application schema exists for {tool_name}")
    _validate_object(schema, arguments, tool_name)


_FINANCE_FIELD_TERMS = {
    "monthly_traffic": (r"monthly\s+traffic", r"traffic"),
    "conversion_rate": (r"conversion\s+rate", r"conversion"),
    "average_order_value": (r"average\s+order\s+value", r"order\s+value", r"aov"),
    "product_cost_per_order": (r"product\s+cost(?:\s+per\s+order)?",),
    "fulfillment_cost_per_order": (r"fulfillment\s+cost(?:\s+per\s+order)?", r"shipping\s+cost"),
    "campaign_cost": (r"campaign\s+cost", r"marketing\s+cost"),
    "inference_cost": (r"inference\s+cost",),
    "infrastructure_cost": (r"infrastructure\s+cost",),
    "cash_balance": (r"cash\s+balance", r"available\s+cash"),
    "opportunity_cost": (r"opportunity\s+cost",),
}
_EXPLICIT_FINANCE_NUMBER = re.compile(r"(?:[$€£]\s*)?\d[\d,]*(?:\.\d+)?\s*(?:%|k|m)?", re.I)


def _explicit_finance_fields(request: str) -> set[str]:
    """Return finance assumptions whose labels and numeric values came from the operator."""
    explicit: set[str] = set()
    for field, terms in _FINANCE_FIELD_TERMS.items():
        for term in terms:
            for match in re.finditer(rf"\b{term}\b", request, re.I):
                nearby = request[max(0, match.start() - 40):match.end() + 40]
                if _EXPLICIT_FINANCE_NUMBER.search(nearby):
                    explicit.add(field)
                    break
            if field in explicit:
                break
    return explicit


def _activation_is_approved_workflow_prerequisite(
    operation: ProposedOperation,
    index: int,
    actions: list[ProposedOperation],
    request: str,
) -> bool:
    """Allow gated activation only for a newly created candidate needed by a requested cycle.

    The direct request authorizes only the exact, visible action list. This exception does not
    authorize activation on its own: the same plan must create the named
    candidate first, the request must explicitly ask for that candidate and a regional cycle,
    and the cycle must appear after the activation step.
    """
    if operation.tool_name != "set_region_lifecycle":
        return False
    arguments = operation.arguments
    if arguments.get("action") != "approve":
        return False
    if re.search(r"\b(approve|activate)\w*\b", request, re.I) and not _lifecycle_action_requested(
        "approve", request,
    ):
        return False
    target = str(arguments.get("region_id", "")).strip().casefold()
    if not target:
        return False
    request_allows_create = _action_request_allowed("create_candidate_region", request)
    request_allows_cycle = _action_request_allowed("run_region_cycle", request)
    if not (request_allows_create and request_allows_cycle):
        return False

    created_before = any(
        action.tool_name == "create_candidate_region"
        and str(action.arguments.get("region_name", "")).strip().casefold() == target
        for action in actions[:index]
    )
    cycle_after = any(
        action.tool_name == "run_region_cycle"
        and str(action.arguments.get("region_id", "")).strip().casefold() == target
        for action in actions[index + 1:]
    )
    signal_after = any(
        action.tool_name == "add_synthetic_signal"
        and str(action.arguments.get("region_id", "")).strip().casefold() == target
        for action in actions[index + 1:]
    )
    return created_before and (cycle_after or signal_after)


def _ensure_candidate_activation_steps(
    actions: list[ProposedOperation],
    request: str,
) -> list[ProposedOperation]:
    """Insert a visible, finance-gated activation for an explicitly requested create-and-cycle chain.

    Provider models sometimes omit prerequisites even when prompted. Deriving this lifecycle
    step is safe only when the operator positively requested both candidate creation and a
    downstream cycle for the same named candidate. The resulting step remains inside the
    immutable, directly requested plan and still passes the deterministic finance gate.
    """
    if not (
        _action_request_allowed("create_candidate_region", request)
        and _action_request_allowed("run_region_cycle", request)
    ):
        return actions
    # Preserve an explicit refusal. The caller's normal validation will return a clear
    # dependency error instead of silently overriding the operator's constraint.
    if re.search(r"\b(approve|activate)\w*\b", request, re.I) and not _lifecycle_action_requested(
        "approve", request,
    ):
        return actions

    insert_after: dict[int, ProposedOperation] = {}
    remove_indices: set[int] = set()
    for index, action in enumerate(actions):
        if action.tool_name != "create_candidate_region":
            continue
        region_name = str(action.arguments.get("region_name", "")).strip()
        normalized_name = region_name.casefold()
        if not normalized_name:
            continue
        cycle_indices = [
            later_index for later_index, later in enumerate(actions[index + 1:], start=index + 1)
            if later.tool_name == "run_region_cycle"
            and str(later.arguments.get("region_id", "")).strip().casefold() == normalized_name
        ]
        if not cycle_indices:
            continue
        first_cycle = min(cycle_indices)
        approval_indices = [
            later_index for later_index, later in enumerate(actions[index + 1:], start=index + 1)
            if later.tool_name == "set_region_lifecycle"
            and later.arguments.get("action") == "approve"
            and str(later.arguments.get("region_id", "")).strip().casefold() == normalized_name
        ]
        if any(index < approval_index < first_cycle for approval_index in approval_indices):
            continue
        # A model may put the activation after the cycle. Replace that misplaced step
        # with the same explicit approval prerequisite in the correct order.
        remove_indices.update(approval_index for approval_index in approval_indices if approval_index > first_cycle)
        insert_after[index] = ProposedOperation(
            agent_role="finance",
            tool_name="set_region_lifecycle",
            arguments={"region_id": region_name, "action": "approve"},
            rationale="Activate the newly created candidate before the explicitly requested regional cycle.",
            impact=(
                "This required activation is authorized by the direct create-and-cycle request and "
                "executes only if Atlas's deterministic finance gate approves the candidate. Later "
                "signal and cycle actions stop if activation is rejected."
            ),
        )
    result: list[ProposedOperation] = []
    for index, action in enumerate(actions):
        if index in remove_indices:
            continue
        result.append(action)
        if index in insert_after:
            result.append(insert_after[index])
    return result


def _validate_candidate_dependency(
    operation: ProposedOperation,
    index: int,
    actions: list[ProposedOperation],
) -> None:
    """Catch candidate references that are missing or out of order before specialist review."""
    if operation.tool_name not in {"set_region_lifecycle", "add_synthetic_signal", "run_region_cycle"}:
        return
    target = str(operation.arguments.get("region_id", "")).strip().casefold()
    if not target:
        return
    earlier_creates = [
        action for action in actions[:index]
        if action.tool_name == "create_candidate_region"
        and str(action.arguments.get("region_name", "")).strip().casefold() == target
    ]
    any_create = any(
        action.tool_name == "create_candidate_region"
        and str(action.arguments.get("region_name", "")).strip().casefold() == target
        for action in actions
    )
    if not any_create:
        return
    if not earlier_creates:
        raise ValueError(f"{operation.tool_name} references a candidate before it is created")
    if operation.tool_name == "run_region_cycle":
        earlier_approval = any(
            action.tool_name == "set_region_lifecycle"
            and action.arguments.get("action") == "approve"
            and str(action.arguments.get("region_id", "")).strip().casefold() == target
            for action in actions[:index]
        )
        if not earlier_approval:
            raise ValueError(
                f"{operation.tool_name} must follow a finance-gated activation for the new candidate"
            )


def _unsupported_request_scope_notes(request: str) -> list[str]:
    """Surface requested behavior the current Atlas domain cannot persist or enforce."""
    notes: list[str] = []
    if re.search(r"\b(description|describe the region|region description)\b", request, re.I):
        notes.append("Region descriptions are not a persisted field, so no description will be saved.")
    if re.search(r"\b(products?|catalog)\b.{0,60}\b(only|exclusively|just for this region)\b", request, re.I):
        notes.append(
            "The product catalog is shared across regions and cannot currently enforce a region-only product restriction; no catalog change is included."
        )
    return notes


def _validate_object(schema: dict[str, Any], value: Any, path: str) -> None:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    missing = [key for key in required if key not in value]
    extras = set(value) - set(properties)
    if missing:
        raise ValueError(f"{path} is missing required fields: {', '.join(missing)}")
    if extras and schema.get("additionalProperties") is False:
        raise ValueError(f"{path} has unsupported fields: {', '.join(sorted(extras))}")
    for key, item in value.items():
        rule = properties.get(key, schema.get("additionalProperties"))
        if not isinstance(rule, dict):
            continue
        _validate_value(rule, item, f"{path}.{key}")


def _validate_value(rule: dict[str, Any], value: Any, path: str) -> None:
    expected = rule.get("type")
    valid = {
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
    }
    if expected in valid and not valid[expected](value):
        raise ValueError(f"{path} must be {('an ' if expected[0] in 'aeiou' else 'a ')}{expected}")
    if isinstance(value, str) and not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", 10**9):
        raise ValueError(f"{path} has an invalid string length")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{path} must be finite")
        if value < rule.get("minimum", float("-inf")) or value > rule.get("maximum", float("inf")):
            raise ValueError(f"{path} is outside the allowed range")
        if value <= rule.get("exclusiveMinimum", float("-inf")):
            raise ValueError(f"{path} must be greater than its minimum")
    if "enum" in rule and value not in rule["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    if isinstance(value, list):
        if not rule.get("minItems", 0) <= len(value) <= rule.get("maxItems", 10**9):
            raise ValueError(f"{path} has an invalid number of items")
        item_schema = rule.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_value(item_schema, item, f"{path}[{index}]")
    if isinstance(value, dict):
        _validate_object(rule, value, path)


def _hard_delete_impact(path: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    target_type = arguments.get("target_type")
    target_id = arguments.get("target_id")
    if target_type not in _HARD_DELETE_TYPES or not isinstance(target_id, str) or not target_id.strip():
        raise ValueError("hard deletion requires a supported target_type and exact target_id")
    with connect(path) as conn:
        table = {"region": "regions", "signal": "signals", "product": "products"}[target_type]
        return _hard_delete_impact_conn(conn, table, target_type, target_id)


def _hard_delete_impact_conn(conn: sqlite3.Connection, table: str,
                             target_type: str, target_id: str) -> dict[str, Any]:
    row = conn.execute(f"SELECT * FROM {table} WHERE id = ?", (target_id,)).fetchone()
    if row is None:
        raise ValueError("hard-delete target was not found by exact ID")
    dependencies: dict[str, int] = {}
    if target_type == "region":
        tables = ("region_profiles", "source_records", "signals", "event_windows",
                  "finance_assessments", "recommendation_versions", "update_cycles",
                  "operator_actions", "signal_corrections", "simulated_events")
        for child in tables:
            dependencies[child] = int(conn.execute(
                f"SELECT COUNT(*) FROM {child} WHERE region_id = ?", (target_id,),
            ).fetchone()[0])
        dependencies["recommendation_items"] = int(conn.execute(
            "SELECT COUNT(*) FROM recommendation_items i JOIN recommendation_versions v ON v.id = i.version_id WHERE v.region_id = ?",
            (target_id,),
        ).fetchone()[0])
        dependencies["consumed_signals"] = int(conn.execute(
            "SELECT COUNT(*) FROM consumed_signals c JOIN signals s ON s.id = c.signal_id WHERE s.region_id = ?",
            (target_id,),
        ).fetchone()[0])
        dependencies["evidence_references"] = int(conn.execute(
            "SELECT COUNT(*) FROM evidence_references e JOIN update_cycles c ON c.id = e.update_cycle_id WHERE c.region_id = ?",
            (target_id,),
        ).fetchone()[0])
        dependencies["retained_signal_deletion_audits"] = int(conn.execute(
            "SELECT COUNT(*) FROM signal_deletions WHERE region_id = ?", (target_id,),
        ).fetchone()[0])
    elif target_type == "signal":
        dependencies["source_records_retained"] = int(conn.execute(
            "SELECT COUNT(*) FROM source_records WHERE id = (SELECT source_id FROM signals WHERE id = ?)",
            (target_id,),
        ).fetchone()[0])
        dependencies["consumed_signals"] = int(conn.execute(
            "SELECT COUNT(*) FROM consumed_signals WHERE signal_id = ?", (target_id,),
        ).fetchone()[0])
        dependencies["signal_corrections"] = int(conn.execute(
            "SELECT COUNT(*) FROM signal_corrections WHERE source_signal_id = ? OR corrected_signal_id = ?",
            (target_id, target_id),
        ).fetchone()[0])
    else:
        dependencies["recommendation_items"] = int(conn.execute(
            "SELECT COUNT(*) FROM recommendation_items WHERE product_id = ?", (target_id,),
        ).fetchone()[0])
        dependencies["retained_catalog_actions"] = int(conn.execute(
            "SELECT COUNT(*) FROM catalog_actions WHERE product_id = ?", (target_id,),
        ).fetchone()[0])
    return {"target_type": target_type, "target_id": target_id,
            "snapshot": dict(row), "dependencies": dependencies}


def _hash_json(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _hard_delete_was_explicitly_requested(request: str, arguments: dict[str, Any]) -> bool:
    target_type = arguments.get("target_type")
    target_terms = {
        "region": r"region|candidate",
        "signal": r"signal|evidence",
        "product": r"product|catalog item",
    }
    terms = target_terms.get(target_type)
    permanent = re.search(r"\b(permanent(?:ly)?|hard delete|erase forever|irreversibl\w*)\b", request, re.I)
    deletion = re.search(r"\b(delete|remove|erase)\b", request, re.I)
    target = re.search(terms, request, re.I) if terms else None
    return bool(permanent and deletion and target)


def _execute_hard_delete(path: Path, workflow_id: str, action_id: str, impact_text: str,
                         arguments: dict[str, Any], approved_details: dict[str, Any],
                         approved_by: str) -> dict[str, Any]:
    target_type = arguments.get("target_type")
    target_id = arguments.get("target_id")
    if target_type not in _HARD_DELETE_TYPES or not isinstance(target_id, str):
        raise ValueError("hard deletion requires an exact supported target")
    table = {"region": "regions", "signal": "signals", "product": "products"}[target_type]
    audit_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        impact = _hard_delete_impact_conn(conn, table, target_type, target_id)
        if approved_details and (
            approved_details.get("dependencies") != impact["dependencies"]
            or approved_details.get("snapshot_hash") != _hash_json(impact["snapshot"])
        ):
            raise ValueError("delete target or impact changed after specialist review; request a fresh workflow plan")
        if target_type == "region":
            conn.execute("DELETE FROM signal_corrections WHERE region_id = ?", (target_id,))
        elif target_type == "signal":
            conn.execute(
                "DELETE FROM signal_corrections WHERE source_signal_id = ? OR corrected_signal_id = ?",
                (target_id, target_id),
            )
        conn.execute(f"DELETE FROM {table} WHERE id = ?", (target_id,))
        conn.execute(
            """INSERT INTO workflow_deletion_audit
            (id, workflow_id, target_type, target_id, snapshot_json, dependency_summary_json, approved_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (audit_id, workflow_id, target_type, target_id,
             _json_dumps(impact["snapshot"]), _json_dumps(impact["dependencies"]), approved_by, now),
        )
        conn.execute(
            "INSERT INTO agent_workflow_events (id, workflow_id, event_type, actor, detail_json, created_at) "
            "VALUES (?, ?, 'hard_delete_committed', ?, ?, ?)",
            (str(uuid4()), workflow_id, approved_by,
             _json_dumps({"target_type": target_type, "target_id": target_id,
                          "audit_id": audit_id, "action_id": action_id,
                          "approved_impact": impact_text}), now),
        )
    return {"status": "hard_deleted", "summary": f"Permanently deleted {target_type} {target_id} under the exact operator request.",
            "target_type": target_type, "target_id": target_id, "audit_id": audit_id,
            "dependencies": impact["dependencies"]}


def _worker_urls() -> dict[str, str]:
    return {role: os.getenv(f"ATLAS_AGENT_{role.upper()}_URL", f"http://{role}-agent:8503")
            for role in _ROLES}


def _post_json(url: str, payload: dict[str, Any], timeout: float = 100) -> dict[str, Any]:
    request = Request(url, data=json.dumps(payload, ensure_ascii=False, default=str).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise RuntimeError(f"worker request failed ({error.code}): {detail}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"worker unavailable: {error}") from error


def _get_json(url: str, timeout: float = 3) -> dict[str, Any]:
    with urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _handler_class(service: WorkflowService, database_path: Path):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._write(200, {"status": "healthy", "service": "atlas-orchestrator"})
                return
            if self.path == "/v1/agents":
                self._write(200, {"agents": service.agents()})
                return
            if self.path == "/v1/workflows":
                self._write(200, {"workflows": list_workflows(database_path)})
                return
            if self.path.startswith("/v1/workflows/"):
                workflow_id = self.path.removeprefix("/v1/workflows/").split("/", 1)[0]
                workflow = get_workflow(database_path, workflow_id)
                self._write(200 if workflow else 404, workflow or {"status": "not_found"})
                return
            self._write(404, {"status": "not_found"})

        def do_POST(self) -> None:
            try:
                payload = self._read_body()
                if self.path == "/v1/workflows":
                    workflow = service.start(str(payload.get("request", "")))
                    self._write(202, workflow)
                elif self.path.startswith("/v1/workflows/") and self.path.endswith("/approve"):
                    workflow_id = self.path.removeprefix("/v1/workflows/").removesuffix("/approve").strip("/")
                    workflow = service.approve(workflow_id, str(payload.get("plan_hash", "")),
                                               str(payload.get("approved_by", "dashboard_operator")))
                    self._write(202, workflow)
                elif self.path.startswith("/v1/workflows/") and self.path.endswith("/cancel"):
                    workflow_id = self.path.removeprefix("/v1/workflows/").removesuffix("/cancel").strip("/")
                    _cancel_workflow(database_path, workflow_id)
                    self._write(200, get_workflow(database_path, workflow_id) or {"status": "not_found"})
                elif self.path == "/v1/internal/execute":
                    self._authorize_internal()
                    result = service.execute_operation(payload)
                    self._write(200, result)
                else:
                    self._write(404, {"status": "not_found"})
            except (json.JSONDecodeError, ValueError, ValidationError, sqlite3.Error) as error:
                self._write(400, {"status": "rejected", "detail": str(error)[:500]})
            except Exception as error:
                logger.exception("orchestrator_request_failed")
                self._write(502, {"status": "failed", "detail": f"{type(error).__name__}: {str(error)[:300]}"})

        def _read_body(self) -> dict[str, Any]:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 1 or size > 100_000:
                raise ValueError("request body must be 1 to 100000 bytes")
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                raise ValueError("request body must be a JSON object")
            return payload

        def _authorize_internal(self) -> None:
            expected = os.getenv("ATLAS_INTERNAL_TOKEN", "")
            provided = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not expected or not hmac.compare_digest(expected, provided):
                raise ValueError("internal worker authorization failed")

        def _write(self, status_code: int, payload: Any) -> None:
            encoded = json.dumps(payload, ensure_ascii=False, default=str).encode()
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            # ``message`` is populated by logging.LogRecord itself and cannot
            # be supplied through ``extra``.
            logger.info("orchestrator_http", extra={"http_request": format % args})

    return Handler


def serve_orchestrator(database_path: Path, host: str = "0.0.0.0", port: int = 8502) -> None:
    """Start the workflow API used by the Streamlit UI and specialist workers."""
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    database_path.parent.mkdir(parents=True, exist_ok=True)
    from adaptive_commerce.application.dashboard import bootstrap_database

    bootstrap_database(database_path)
    server = ThreadingHTTPServer((host, port), _handler_class(WorkflowService(database_path), database_path))
    logger.info("orchestrator_started", extra={"database_path": str(database_path), "port": port})
    server.serve_forever()


def _cancel_workflow(path: Path, workflow_id: str) -> None:
    workflow = get_workflow(path, workflow_id)
    if workflow is None:
        raise ValueError("workflow was not found")
    if workflow["status"] not in {"planning", "awaiting_approval"}:
        raise ValueError("only a planning or approval-pending workflow can be cancelled")
    set_workflow_status(path, workflow_id, "cancelled")
    record_event(path, workflow_id, "workflow_cancelled", "dashboard_operator", {})
