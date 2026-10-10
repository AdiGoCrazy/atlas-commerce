"""Initialize the official neatlogs SDK once; never claim unverified export."""

import logging
import os
from functools import lru_cache

import neatlogs
from pydantic import BaseModel

logger = logging.getLogger(__name__)


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
