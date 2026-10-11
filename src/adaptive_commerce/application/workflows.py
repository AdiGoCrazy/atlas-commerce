"""Persistence and approval boundaries for coordinated Atlas agent workflows."""

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from adaptive_commerce.persistence import connect
from adaptive_commerce.persistence.database import _json_dumps

AgentRole = Literal["planner", "research", "finance", "recommendation", "review"]


class ProposedOperation(BaseModel):
    """One allowlisted application operation proposed for operator approval."""

    model_config = ConfigDict(extra="forbid")
    agent_role: AgentRole
    tool_name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]
    rationale: str = Field(min_length=1, max_length=1000)
    impact: str = Field(min_length=1, max_length=1000)
    impact_details: dict[str, Any] = Field(default_factory=dict)


class WorkflowPlan(BaseModel):
    """Immutable action plan shown before any agent is permitted to write."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=2000)
    actions: list[ProposedOperation] = Field(min_length=1, max_length=20)


class SpecialistReport(BaseModel):
    """Short, user-visible report from one specialist worker."""

    model_config = ConfigDict(extra="forbid")
    role: AgentRole
    status: Literal["complete", "concerns", "degraded"]
    label: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=2000)
    findings: list[str] = Field(default_factory=list, max_length=12)


class WorkflowProposal(BaseModel):
    """Plan and specialist assessments produced before operator approval."""

    model_config = ConfigDict(extra="forbid")
    plan: WorkflowPlan
    reports: list[SpecialistReport]
    review: SpecialistReport


AGENT_DUTIES: dict[str, str] = {
    "planner": "Turn the operator request into a scoped, reviewable action plan.",
    "research": "Inspect Atlas evidence, signal history, and region context.",
    "finance": "Assess financial assumptions, viability, and finance integration status.",
    "recommendation": "Inspect ranking state, candidate changes, and deterministic outcomes.",
    "review": "Check evidence, action scope, risks, and missing approval context.",
}


def create_workflow(path: Path, request: str) -> str:
    """Create a durable workflow shell and queued specialist activity rows."""
    workflow_id = str(uuid4())
    now = _now()
    with connect(path) as conn, conn:
        conn.execute(
            "INSERT INTO agent_workflows (id, request, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (workflow_id, request, "planning", now, now),
        )
        for role, duty in AGENT_DUTIES.items():
            conn.execute(
                """INSERT INTO agent_workflow_steps
                (id, workflow_id, role, duty, status, label, updated_at)
                VALUES (?, ?, ?, ?, 'queued', 'Queued', ?)""",
                (str(uuid4()), workflow_id, role, duty, now),
            )
        _event(conn, workflow_id, "workflow_started", "operator", {"request": request}, now)
    return workflow_id


def update_step(
    path: Path,
    workflow_id: str,
    role: str,
    *,
    status: str,
    label: str,
    summary: str = "",
    result: dict[str, Any] | None = None,
) -> None:
    """Persist a concise live status for a specialist duty."""
    now = _now()
    started_at = now if status in {"working", "reviewing", "executing"} else None
    finished_at = now if status in {"complete", "failed", "awaiting_approval"} else None
    with connect(path) as conn, conn:
        conn.execute(
            """UPDATE agent_workflow_steps SET status = ?, label = ?, summary = ?, result_json = ?,
            started_at = COALESCE(started_at, ?), finished_at = COALESCE(?, finished_at), updated_at = ?
            WHERE workflow_id = ? AND role = ?""",
            (status, label, summary, _json_dumps(result or {}), started_at, finished_at,
             now, workflow_id, role),
        )


def save_proposal(path: Path, workflow_id: str, proposal: WorkflowProposal, trace_id: str | None) -> str:
    """Persist an immutable, validated plan and its specialist findings before execution."""
    plan_payload = proposal.plan.model_dump(mode="json")
    plan_hash = hashlib.sha256(_canonical_json(plan_payload).encode()).hexdigest()
    now = _now()
    with connect(path) as conn, conn:
        row = conn.execute("SELECT status FROM agent_workflows WHERE id = ?", (workflow_id,)).fetchone()
        if row is None:
            raise ValueError("workflow was not found")
        if row["status"] != "planning":
            raise ValueError("workflow is no longer planning")
        conn.execute(
            """UPDATE agent_workflows SET title = ?, summary = ?, status = 'awaiting_approval',
            plan_hash = ?, plan_json = ?, trace_id = ?, updated_at = ? WHERE id = ?""",
            (proposal.plan.title, proposal.plan.summary, plan_hash, _json_dumps(plan_payload), trace_id,
             now, workflow_id),
        )
        for ordinal, action in enumerate(proposal.plan.actions):
            conn.execute(
                """INSERT INTO agent_workflow_actions
                (id, workflow_id, ordinal, agent_role, tool_name, arguments_json, rationale, impact, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(uuid4()), workflow_id, ordinal, action.agent_role,
                 action.tool_name, _json_dumps(action.arguments), action.rationale, action.impact, now),
            )
        for report in [*proposal.reports, proposal.review]:
            conn.execute(
                """UPDATE agent_workflow_steps SET status = ?, label = ?, summary = ?, result_json = ?,
                finished_at = ?, updated_at = ? WHERE workflow_id = ? AND role = ?""",
                (report.status, report.label, report.summary,
                 _json_dumps(report.model_dump(mode="json")), now, now, workflow_id, report.role),
            )
        _event(conn, workflow_id, "plan_validated", "agent_orchestrator",
               {"plan_hash": plan_hash, "action_count": len(proposal.plan.actions)}, now)
    return plan_hash


def approve_workflow(path: Path, workflow_id: str, plan_hash: str, actor: str) -> dict[str, Any]:
    """Approve exactly the persisted plan currently awaiting operator review."""
    now = _now()
    with connect(path) as conn, conn:
        row = conn.execute(
            "SELECT status, plan_hash FROM agent_workflows WHERE id = ?", (workflow_id,),
        ).fetchone()
        if row is None:
            raise ValueError("workflow was not found")
        if row["status"] != "awaiting_approval" or row["plan_hash"] != plan_hash:
            raise ValueError("workflow plan changed or is no longer awaiting approval")
        conn.execute(
            "UPDATE agent_workflows SET status = 'approved', approved_by = ?, approved_at = ?, updated_at = ? WHERE id = ?",
            (actor, now, now, workflow_id),
        )
        event_type = "request_authorized_plan" if actor == "operator_prompt" else "plan_approved"
        _event(conn, workflow_id, event_type, actor, {"plan_hash": plan_hash}, now)
    return get_workflow(path, workflow_id)  # type: ignore[return-value]


def set_workflow_status(path: Path, workflow_id: str, status: str, *, error: str | None = None) -> None:
    """Persist workflow lifecycle transitions and operator-visible failures."""
    with connect(path) as conn, conn:
        conn.execute(
            "UPDATE agent_workflows SET status = ?, error = ?, updated_at = ? WHERE id = ?",
            (status, error, _now(), workflow_id),
        )


def claim_action(path: Path, workflow_id: str, action_id: str, plan_hash: str) -> dict[str, Any]:
    """Atomically claim one action only when covered by the approved plan."""
    now = _now()
    with connect(path) as conn, conn:
        workflow = conn.execute(
            "SELECT status, plan_hash FROM agent_workflows WHERE id = ?", (workflow_id,),
        ).fetchone()
        if workflow is None or workflow["status"] not in {"approved", "running"}:
            raise ValueError("workflow does not have an approved write scope")
        if workflow["plan_hash"] != plan_hash:
            raise ValueError("operation is not covered by the approved plan")
        action = conn.execute(
            "SELECT * FROM agent_workflow_actions WHERE id = ? AND workflow_id = ?",
            (action_id, workflow_id),
        ).fetchone()
        if action is None:
            raise ValueError("operation was not found in this workflow")
        if action["status"] == "complete":
            return {**dict(action), "already_complete": True}
        if action["status"] != "pending":
            raise ValueError("operation is already running or has failed")
        conn.execute(
            "UPDATE agent_workflow_actions SET status = 'executing' WHERE id = ? AND status = 'pending'",
            (action_id,),
        )
        conn.execute(
            "UPDATE agent_workflows SET status = 'running', updated_at = ? WHERE id = ?",
            (now, workflow_id),
        )
        return {**dict(action), "already_complete": False}


def finish_action(
    path: Path,
    workflow_id: str,
    action_id: str,
    *,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Record a terminal operation result without changing its approved payload."""
    status = "failed" if error else "complete"
    with connect(path) as conn, conn:
        conn.execute(
            "UPDATE agent_workflow_actions SET status = ?, result_json = ?, error = ?, finished_at = ? WHERE id = ? AND workflow_id = ?",
            (status, _json_dumps(result or {}), error, _now(), action_id, workflow_id),
        )


def skip_pending_actions(path: Path, workflow_id: str, reason: str) -> list[dict[str, str]]:
    """Mark unstarted actions skipped after a prerequisite fails, preserving the reason."""
    now = _now()
    with connect(path) as conn, conn:
        rows = conn.execute(
            "SELECT id, tool_name FROM agent_workflow_actions WHERE workflow_id = ? AND status = 'pending' ORDER BY ordinal",
            (workflow_id,),
        ).fetchall()
        conn.execute(
            "UPDATE agent_workflow_actions SET status = 'skipped', error = ?, finished_at = ? "
            "WHERE workflow_id = ? AND status = 'pending'",
            (reason[:500], now, workflow_id),
        )
    return [{"id": row["id"], "tool_name": row["tool_name"]} for row in rows]


def record_event(path: Path, workflow_id: str, event_type: str, actor: str, detail: dict[str, Any]) -> None:
    """Append a durable, content-bounded workflow timeline event."""
    with connect(path) as conn, conn:
        _event(conn, workflow_id, event_type, actor, detail, _now())


def get_workflow(path: Path, workflow_id: str) -> dict[str, Any] | None:
    """Load a workflow with its specialist, action, and approval timeline state."""
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM agent_workflows WHERE id = ?", (workflow_id,)).fetchone()
        if row is None:
            return None
        workflow = dict(row)
        workflow["plan"] = json.loads(workflow.pop("plan_json") or "{}")
        workflow["result"] = json.loads(workflow.pop("result_json") or "{}")
        workflow["steps"] = [dict(item) for item in conn.execute(
            "SELECT * FROM agent_workflow_steps WHERE workflow_id = ? ORDER BY rowid", (workflow_id,),
        )]
        for item in workflow["steps"]:
            item["result"] = json.loads(item.pop("result_json") or "{}")
        workflow["actions"] = [dict(item) for item in conn.execute(
            "SELECT * FROM agent_workflow_actions WHERE workflow_id = ? ORDER BY ordinal", (workflow_id,),
        )]
        for item in workflow["actions"]:
            item["arguments"] = json.loads(item.pop("arguments_json"))
            item["result"] = json.loads(item.pop("result_json") or "{}")
            plan_actions = workflow["plan"].get("actions", [])
            ordinal = int(item["ordinal"])
            item["impact_details"] = plan_actions[ordinal].get("impact_details", {}) if ordinal < len(plan_actions) else {}
        workflow["events"] = [dict(item) for item in conn.execute(
            "SELECT * FROM agent_workflow_events WHERE workflow_id = ? ORDER BY created_at, rowid", (workflow_id,),
        )]
        for item in workflow["events"]:
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
        workflow["deletions"] = [dict(item) for item in conn.execute(
            "SELECT * FROM workflow_deletion_audit WHERE workflow_id = ? ORDER BY created_at", (workflow_id,),
        )]
        for item in workflow["deletions"]:
            item["snapshot"] = json.loads(item.pop("snapshot_json"))
            item["dependencies"] = json.loads(item.pop("dependency_summary_json") or "{}")
        return workflow


def list_workflows(path: Path, limit: int = 20) -> list[dict[str, Any]]:
    """List recent workflow summaries for dashboard selection."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT id, request, title, summary, status, plan_hash, trace_id, error, created_at, updated_at "
            "FROM agent_workflows ORDER BY created_at DESC LIMIT ?", (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def _event(conn: sqlite3.Connection, workflow_id: str, event_type: str, actor: str,
            detail: dict[str, Any], created_at: str) -> None:
    conn.execute(
        "INSERT INTO agent_workflow_events (id, workflow_id, event_type, actor, detail_json, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid4()), workflow_id, event_type, actor, _json_dumps(detail), created_at),
    )


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
