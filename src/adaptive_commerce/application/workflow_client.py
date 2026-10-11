"""Small HTTP client for the separately deployed local workflow orchestrator."""

import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class WorkflowApiError(RuntimeError):
    """Raised when the workflow service rejects a request or is unavailable."""


def workflow_api_url() -> str:
    """Return the configured orchestrator base URL."""
    return os.getenv("ATLAS_ORCHESTRATOR_URL", "http://127.0.0.1:8502").rstrip("/")


def workflow_request(method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
    """Call a bounded local workflow endpoint and decode its JSON response."""
    body = None if payload is None else json.dumps(payload).encode()
    request = Request(
        f"{workflow_api_url()}{path}", data=body,
        headers={"Content-Type": "application/json"}, method=method,
    )
    try:
        with urlopen(request, timeout=4) as response:
            return json.loads(response.read().decode())
    except HTTPError as error:
        detail = error.read().decode(errors="replace")[:500]
        raise WorkflowApiError(f"Workflow service returned {error.code}: {detail}") from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise WorkflowApiError(f"Workflow service is unavailable: {type(error).__name__}") from error
