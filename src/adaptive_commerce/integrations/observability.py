"""Initialize the official neatlogs SDK once; never claim unverified export."""

import logging
import os
from contextlib import contextmanager
from functools import lru_cache, wraps
from inspect import signature
from typing import Any, Callable, Iterator, TypeVar

import neatlogs
from pydantic import BaseModel

logger = logging.getLogger(__name__)
F = TypeVar("F", bound=Callable[..., Any])


class TraceStatus(BaseModel):
    """Runtime configuration status, separate from live trace readback evidence."""

    status: str
    detail: str


@lru_cache(maxsize=1)
def initialize_tracing() -> TraceStatus:
    """Configure one SDK generation with export disabled when no key exists."""
    key = os.environ.get("NEATLOGS_API_KEY")
    try:
        neatlogs.init(
            api_key=key, workflow_name="atlas-commerce",
            disable_export=not bool(key), capture_logs=False,
        )
    except Exception:
        logger.error("neatlogs_initialization_failed")
        return TraceStatus(status="failed", detail="neatlogs initialization failed; see local diagnostics.")
    if not key:
        return TraceStatus(status="degraded", detail="NEATLOGS_API_KEY missing; local spans only, no live export.")
    return TraceStatus(status="configured", detail="Export configured; live trace readback still requires verification.")


@contextmanager
def trace_step(
    kind: str,
    name: str,
    *,
    region_id: str | None = None,
    cycle_id: str | None = None,
    version_id: str | None = None,
) -> Iterator[None]:
    """Create a content-free Neatlogs span with Atlas workflow identifiers."""
    with neatlogs.trace(name, kind=kind) as span:
        for key, value in (
            ("region_id", region_id),
            ("cycle_id", cycle_id),
            ("version_id", version_id),
        ):
            if value is not None:
                span.set_attribute(f"atlas.{key}", value)
        yield


def traced_step(
    kind: str,
    name: str,
    attributes: Callable[[dict[str, Any]], dict[str, str | None]],
) -> Callable[[F], F]:
    """Decorate a service step and derive safe span IDs from its arguments."""
    def decorate(function: F) -> F:
        parameters = signature(function)

        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            bound = parameters.bind_partial(*args, **kwargs)
            with trace_step(kind, name, **attributes(bound.arguments)):
                return function(*args, **kwargs)

        return wrapped  # type: ignore[return-value]

    return decorate
