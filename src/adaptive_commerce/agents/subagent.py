"""Same-model HTTP specialist workers for Atlas's approved workflow runtime."""

import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from adaptive_commerce.application.assistant import (
    TOOL_SCHEMAS,
    _WRITE_TOOL_PATTERNS,
    _action_request_allowed,
    _positive_intent_matches,
)
from adaptive_commerce.application.workflows import AGENT_DUTIES, SpecialistReport, WorkflowPlan
from adaptive_commerce.integrations.llm import configured_model, get_llm_client, llm_status
from adaptive_commerce.integrations.observability import trace_step

logger = logging.getLogger(__name__)
_ROLES = tuple(AGENT_DUTIES)

_ROLE_PROMPTS: dict[str, str] = {
    "planner": "Create a scoped plan that answers the operator's request. Include only necessary, allowlisted Atlas operations and obey their supplied argument schemas. Never invent unsupported fields or claim to have performed an operation. Region records support name and locale, not descriptions. Catalog changes are global, not region-specific; never claim to filter the shared catalog for only one region. If the operator explicitly asks to create a candidate and then run its regional cycle, include the ordered steps create candidate → approve/activate candidate → add requested signal(s) → run cycle. The direct create-and-cycle request authorizes this prerequisite activation, which still passes Atlas's deterministic finance gate. Use the candidate's exact requested name as the region_id reference in later steps; Atlas resolves it after creation. Do not activate a candidate without a requested downstream cycle or signal operation. Do not add lifecycle steps when user intent is negated, hypothetical, or informational. Never invent finance assumptions: include a finance value only when the operator states that value; omit all other fields so Atlas applies its documented simulation defaults. If required values such as signal confidence are unspecified, choose conservative demo values and include them in the validated proposed action. Do not invent region descriptions because the domain has no description field. For confidence comparisons on an active region, alternate each signal addition with its own cycle so each cycle consumes only that signal. Consider critical actions and hard deletion only when explicitly requested, name the exact target, and explain exact impact. Atlas automatically executes only operations validated against the operator's direct request; no second approval click is required.",
    "research": "Review the supplied Atlas records and the proposed plan. Report factual findings with record IDs, identify missing evidence, and distinguish pending from processed signals. Do not invent live external data or claim an operation is unavailable merely because you cannot call tools; Atlas validates the supplied plan separately.",
    "finance": "Review supplied finance records and planned actions. Clearly distinguish verified cfo.ai data from simulated fallback. Atlas's deterministic finance gate is authoritative; do not fabricate scenario outputs or claim a live CFO call.",
    "recommendation": "Review the supplied profile, products, events, and planned actions. Ranking remains deterministic in Atlas; do not invent scores. Identify expected recommendation effects only when supported by supplied evidence. Do not claim an operation is unavailable merely because you cannot call tools; Atlas validates the supplied plan separately.",
    "review": "Independently inspect the operator request, proposed plan, specialist reports, evidence, and impacts. Flag out-of-scope, unsupported, destructive, or ambiguous operations. Your report is shown in the workflow audit; never execute a write.",
}


def handle_task(payload: dict[str, Any]) -> dict[str, Any]:
    """Run one role-specific same-model task and validate its structured output."""
    role = str(payload.get("role", ""))
    if role not in AGENT_DUTIES:
        raise ValueError("unknown specialist role")
    client = get_llm_client()
    if client is None:
        raise RuntimeError(f"{llm_status()['provider']} credentials are unavailable")
    if role == "planner":
        schema = _plan_tool_schema()
        function_name = "submit_atlas_plan"
    else:
        schema = _report_tool_schema(role)
        function_name = "submit_specialist_report"
    context = json.dumps(payload.get("context", {}), ensure_ascii=False, default=str)[:24000]
    task = json.dumps({
        "operator_request": payload.get("request", ""),
        "context": json.loads(context),
        "plan": payload.get("plan"),
        "reports": payload.get("reports", []),
        "assigned_duty": AGENT_DUTIES[role],
        "operation_argument_schemas": {
            name: TOOL_SCHEMAS[name]
            for name, pattern in _WRITE_TOOL_PATTERNS.items()
            if name in TOOL_SCHEMAS and _positive_intent_matches(pattern, str(payload.get("request", "")))
        } if role == "planner" else {},
    }, ensure_ascii=False, default=str)[:30000]
    if role == "planner":
        request_text = str(payload.get("request", ""))
        if (
            _action_request_allowed("create_candidate_region", request_text)
            and _action_request_allowed("run_region_cycle", request_text)
        ):
            task_payload = json.loads(task)
            task_payload["operation_argument_schemas"]["set_region_lifecycle"] = TOOL_SCHEMAS[
                "set_region_lifecycle"
            ]
            task = json.dumps(task_payload, ensure_ascii=False, default=str)[:30000]
    with trace_step("AGENT", f"atlas_{role}_specialist",
                    attributes={"workflow_id": str(payload.get("workflow_id", "")), "specialist_role": role}) as span:
        response = client.chat.completions.create(
            model=configured_model(),
            messages=[
                {"role": "system", "content": f"You are Atlas's {role} specialist. {_ROLE_PROMPTS[role]} Return only the required structured tool result. Keep summaries concise and findings to at most three short items; do not include hidden reasoning."},
                {"role": "user", "content": task},
            ],
            tools=[{"type": "function", "function": schema}],
            tool_choice={"type": "function", "function": {"name": function_name}},
            temperature=0.1,
            max_tokens=4096,
        )
        span.set_attribute("atlas.result_status", "model_response_received")
    choice = response.choices[0]
    message = choice.message
    calls = list(message.tool_calls or [])
    if len(calls) == 1 and calls[0].function.name == function_name:
        result = json.loads(calls[0].function.arguments)
    else:
        # Some OpenAI-compatible model routes return the requested object as
        # JSON text instead of a tool call. Accept that only when it is a JSON
        # object; the same Pydantic and operation authorization checks below
        # still apply before anything can be approved or executed.
        result = _json_object_from_content(message.content)
        if result is None:
            content = str(message.content or "").strip().replace("\n", " ")[:240]
            model = getattr(response, "model", configured_model())
            raise ValueError(
                f"{role} returned neither the required tool call nor a JSON object "
                f"(model={model}, finish_reason={choice.finish_reason}, response={content!r})"
            )
    if not isinstance(result, dict):
        raise ValueError(f"{role} response must be a JSON object")
    if role == "planner":
        plan = WorkflowPlan.model_validate(result)
        for action in plan.actions:
            if action.tool_name not in _WRITE_TOOL_PATTERNS and action.tool_name != "hard_delete_record":
                raise ValueError(f"planner proposed unsupported operation: {action.tool_name}")
        return {"plan": plan.model_dump(mode="json")}
    report = _normalize_specialist_report(role, result)
    return {"report": report.model_dump(mode="json")}


def _json_object_from_content(content: Any) -> dict[str, Any] | None:
    """Return a JSON object from plain model text, if the whole response is JSON."""
    if not isinstance(content, str):
        return None
    candidate = content.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[3:-3].strip()
        if candidate.startswith("json"):
            candidate = candidate[4:].strip()
    try:
        result = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def _normalize_specialist_report(role: str, result: dict[str, Any]) -> SpecialistReport:
    """Normalize common provider shape drift, then validate a specialist report."""
    allowed_fields = {"role", "status", "label", "summary", "findings"}
    normalized = {key: value for key, value in result.items() if key in allowed_fields}
    discarded_fields = sorted(set(result) - allowed_fields)
    if discarded_fields:
        logger.warning(
            "specialist_report_fields_discarded",
            extra={"role": role, "fields": discarded_fields[:12]},
        )
    reported_role = normalized.get("role")
    if reported_role != role:
        logger.warning(
            "specialist_report_role_corrected",
            extra={"role": role, "reported_role": str(reported_role or "")[:40]},
        )
    normalized["role"] = role

    if normalized.get("status") not in {"complete", "concerns", "degraded"}:
        normalized["status"] = "degraded"

    label = normalized.get("label")
    if not isinstance(label, str) or not label.strip():
        normalized["label"] = f"{role.title()} specialist report"
        normalized["status"] = "degraded"
    else:
        normalized["label"] = label.strip()[:120]

    summary = normalized.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        normalized["summary"] = "The specialist returned an incomplete report; review the Atlas evidence before approval."
        normalized["status"] = "degraded"
    else:
        normalized["summary"] = summary.strip()[:2000]

    findings = normalized.get("findings", [])
    if isinstance(findings, str):
        candidate = findings.strip()
        if candidate.startswith("["):
            try:
                parsed_findings = json.loads(candidate)
            except json.JSONDecodeError:
                parsed_findings = None
            if isinstance(parsed_findings, list):
                findings = parsed_findings
            else:
                findings = [candidate] if candidate else []
        else:
            findings = [candidate] if candidate else []
    elif not isinstance(findings, list):
        findings = []
        normalized["status"] = "degraded"
    normalized["findings"] = [
        (item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str))[:500]
        for item in findings[:12]
    ]
    if normalized["findings"] != result.get("findings", []):
        logger.warning(
            "specialist_report_shape_normalized",
            extra={"role": role, "findings_type": type(result.get("findings", [])).__name__},
        )
    return SpecialistReport.model_validate(normalized)


def _plan_tool_schema() -> dict[str, Any]:
    return {
        "name": "submit_atlas_plan",
        "description": "Submit the full action plan for human review. This does not execute or persist actions.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                "actions": {"type": "array", "minItems": 1, "maxItems": 20, "items": {
                    "type": "object",
                    "properties": {
                        "agent_role": {"type": "string", "enum": list(_ROLES)},
                        "tool_name": {"type": "string", "enum": [*list(_WRITE_TOOL_PATTERNS), "hard_delete_record"]},
                        "arguments": {"type": "object", "additionalProperties": True},
                        "rationale": {"type": "string", "minLength": 1, "maxLength": 1000},
                        "impact": {"type": "string", "minLength": 1, "maxLength": 1000},
                    },
                    "required": ["agent_role", "tool_name", "arguments", "rationale", "impact"],
                    "additionalProperties": False,
                }},
            },
            "required": ["title", "summary", "actions"],
            "additionalProperties": False,
        },
    }


def _report_tool_schema(role: str) -> dict[str, Any]:
    return {
        "name": "submit_specialist_report",
        "description": "Return concise status and evidence-based findings for the assigned specialist duty.",
        "parameters": {
            "type": "object",
            "properties": {
                "role": {"type": "string", "enum": [role]},
                "status": {"type": "string", "enum": ["complete", "concerns", "degraded"]},
                "label": {"type": "string", "minLength": 1, "maxLength": 120},
                "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                "findings": {"type": "array", "maxItems": 12, "items": {"type": "string", "maxLength": 500}},
            },
            "required": ["role", "status", "label", "summary", "findings"],
            "additionalProperties": False,
        },
    }


def execute_approved_operation(payload: dict[str, Any]) -> dict[str, Any]:
    """Ask the orchestrator to execute only a reviewed operation in an approved plan."""
    destination = os.getenv(
        "ATLAS_ORCHESTRATOR_INTERNAL_URL",
        "http://orchestrator:8502/v1/internal/execute",
    )
    token = os.getenv("ATLAS_INTERNAL_TOKEN", "")
    if not token:
        raise RuntimeError("internal workflow authorization is not configured")
    request = Request(
        destination,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise RuntimeError(f"approved operation was rejected by orchestrator ({error.code}): {detail}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"orchestrator execution endpoint unavailable: {error}") from error


def _handler_class(role: str):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path != "/healthz":
                self._write(404, {"status": "not_found"})
                return
            model_state = llm_status()
            self._write(200, {
                "status": "configured" if model_state["status"] == "configured" else "degraded",
                "role": role,
                "duty": AGENT_DUTIES[role],
                "provider": model_state["provider"],
                "model": model_state["model"],
                "detail": model_state["detail"],
            })

        def do_POST(self) -> None:
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 1 or size > 100_000:
                    self._write(413, {"status": "invalid", "detail": "request body must be 1 to 100000 bytes"})
                    return
                payload = json.loads(self.rfile.read(size))
                if self.path == "/v1/task":
                    if payload.get("role") != role:
                        self._write(403, {"status": "denied", "detail": "task role does not match this worker"})
                        return
                    self._write(200, handle_task(payload))
                elif self.path == "/v1/execute":
                    if payload.get("agent_role") != role:
                        self._write(403, {"status": "denied", "detail": "operation role does not match this worker"})
                        return
                    self._write(200, execute_approved_operation(payload))
                else:
                    self._write(404, {"status": "not_found"})
            except (json.JSONDecodeError, ValidationError, ValueError) as error:
                self._write(400, {"status": "invalid", "detail": str(error)[:500]})
            except Exception as error:
                logger.exception("subagent_request_failed", extra={"role": role})
                self._write(502, {"status": "failed", "detail": f"{type(error).__name__}: {str(error)[:300]}"})

        def _write(self, status_code: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: Any) -> None:
            # ``message`` is reserved by logging.LogRecord; use an application
            # field so health checks and worker responses are logged safely.
            logger.info("subagent_http", extra={"role": role, "http_request": format % args})

    return Handler


def serve_agent(role: str, host: str = "0.0.0.0", port: int = 8503) -> None:
    """Start an isolated specialist worker HTTP service."""
    if role not in AGENT_DUTIES:
        raise ValueError(f"unknown specialist role: {role}")
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    server = ThreadingHTTPServer((host, port), _handler_class(role))
    logger.info("subagent_started", extra={"role": role, "port": port, "model": configured_model()})
    server.serve_forever()
