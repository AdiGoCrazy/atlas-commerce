"""Read sanitized Neatlogs trace metadata through the supported CLI."""

import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

TRACE_ID_PATTERN = re.compile(r"^[0-9a-fA-F]{32}$")


class TraceSpanSummary(BaseModel):
    """Safe span metadata with user content omitted."""

    model_config = ConfigDict(extra="forbid")
    name: str
    kind: str | None = None
    status: str | None = None
    error_type: str | None = None


class TraceReadback(BaseModel):
    """Explicitly status-bearing response from Neatlogs readback."""

    model_config = ConfigDict(extra="forbid")
    status: str
    detail: str
    trace_id: str
    spans: list[TraceSpanSummary] = Field(default_factory=list)


def read_trace_summary(trace_id: str, *, timeout_seconds: float = 12) -> TraceReadback:
    """Fetch an exact trace and retain only span names, types, status, and error class."""
    from adaptive_commerce.integrations.llm import load_app_environment

    load_app_environment()
    if not TRACE_ID_PATTERN.fullmatch(trace_id):
        return TraceReadback(status="failed", detail="Invalid trace identifier.", trace_id=trace_id)
    if not (os.getenv("NEATLOGS_PROJECT_ID") and os.getenv("NEATLOGS_TOKEN")):
        return TraceReadback(
            status="degraded", detail="NEATLOGS_PROJECT_ID and NEATLOGS_TOKEN are required for trace readback.",
            trace_id=trace_id,
        )
    executable = shutil.which("neatlogs")
    command = [executable, "trace", "get", trace_id, "--json"] if executable else [
        sys.executable, "-m", "neatlogs", "trace", "get", trace_id, "--json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return TraceReadback(status="failed", detail="Neatlogs trace readback could not complete.", trace_id=trace_id)
    if result.returncode != 0:
        return TraceReadback(status="failed", detail="Neatlogs did not return the requested trace.", trace_id=trace_id)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return TraceReadback(status="failed", detail="Neatlogs returned an unreadable trace response.", trace_id=trace_id)
    spans = _sanitize_spans(payload)
    if not spans:
        return TraceReadback(status="failed", detail="The requested trace contained no readable spans.", trace_id=trace_id)
    return TraceReadback(status="verified", detail="Exact trace retrieved; prompt and evidence text were omitted.",
                         trace_id=trace_id, spans=spans)


def _sanitize_spans(payload: Any) -> list[TraceSpanSummary]:
    """Extract span metadata recursively without copying attributes or content."""
    collected: list[TraceSpanSummary] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            name = value.get("name") or value.get("span_name")
            attributes = value.get("attributes") or value.get("attributes_map") or {}
            if isinstance(name, str) and isinstance(attributes, dict):
                kind = attributes.get("neatlogs.span.kind") or attributes.get("openinference.span.kind")
                status_value = value.get("status")
                if isinstance(status_value, dict):
                    status_value = status_value.get("status_code")
                error = attributes.get("exception.type") or attributes.get("error.type")
                collected.append(TraceSpanSummary(
                    name=name[:120], kind=str(kind)[:40] if kind is not None else None,
                    status=str(status_value)[:40] if status_value is not None else None,
                    error_type=str(error)[:120] if error is not None else None,
                ))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    unique = {(item.name, item.kind, item.status, item.error_type): item for item in collected}
    return list(unique.values())[:100]
