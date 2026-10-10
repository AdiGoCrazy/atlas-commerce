"""Operator utilities for synthetic evidence and a repeatable demo reset."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from adaptive_commerce.domain import Signal, SignalSourceType
from adaptive_commerce.persistence import Repository, connect, load_fixtures

FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures"


def inject_demo_signal(
    path: Path,
    region_id: str,
    topics: list[str],
    confidence: float,
    evidence: str,
    source_type: SignalSourceType = SignalSourceType.social,
    observed_at: datetime | None = None,
) -> Signal:
    """Persist one idempotent synthetic signal for an operator demonstration.

    Identical region, source type, topic, confidence, and evidence inputs reuse
    the same source record. Change the evidence text to model a distinct report.
    """
    with connect(path) as conn:
        repository = Repository(conn)
        region = repository.get_region(region_id)
        if region is None:
            raise ValueError("selected region does not exist")
        normalized_topics = sorted({topic.strip().lower() for topic in topics if topic.strip()})
        normalized_evidence = " ".join(evidence.split())
        identity = json.dumps({
            "region_id": region_id,
            "source_type": source_type.value,
            "topics": normalized_topics,
            "confidence": str(confidence),
            "evidence": normalized_evidence.casefold(),
        }, sort_keys=True, separators=(",", ":"))
        source_id = f"demo:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"
        existing = repository.get_signals_by_source_id(source_id)
        if existing:
            return existing[0]
        signal = Signal(
            id=f"signal:{uuid4().hex}",
            region_id=region_id,
            source_id=source_id,
            source_type=source_type,
            topics=normalized_topics,
            confidence=confidence,
            observed_at=observed_at or datetime.now(timezone.utc),
            evidence_excerpt=normalized_evidence,
            language="en",
            normalized_text=normalized_evidence,
        )
        if not repository.create_signal(signal):
            return repository.get_signals_by_source_id(source_id)[0]
        return signal


def reset_demo(path: Path) -> None:
    """Clear local application state and restore the checked-in synthetic fixtures."""
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for table in (
            "signal_corrections", "evidence_references", "recommendation_items", "consumed_signals", "signals", "source_records",
            "event_windows", "finance_assessments", "operator_actions", "update_cycles",
            "simulated_events", "region_profiles", "recommendation_versions", "regions",
            "products",
        ):
            conn.execute(f"DELETE FROM {table}")
        load_fixtures(conn, FIXTURES)
