"""Operator utilities for synthetic evidence and a repeatable demo reset."""

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from adaptive_commerce.domain import Signal, SignalSourceType
from adaptive_commerce.integrations.observability import traced_step
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


@traced_step(
    "WORKFLOW",
    "remove_pending_signal",
    lambda args: {"attributes": {"signal_id": args["signal_id"]}},
)
def remove_pending_signal(path: Path, signal_id: str) -> None:
    """Remove unconsumed evidence while retaining a durable copy for audit."""
    now = datetime.now(timezone.utc)
    with connect(path) as conn, conn:
        repository = Repository(conn)
        signal = repository.get_signal(signal_id)
        if signal is None:
            raise ValueError("signal was not found")
        if conn.execute("SELECT 1 FROM consumed_signals WHERE signal_id = ?", (signal_id,)).fetchone():
            raise ValueError("a signal already used by a cycle cannot be removed")
        correction = conn.execute(
            "SELECT 1 FROM signal_corrections WHERE source_signal_id = ? OR corrected_signal_id = ? LIMIT 1",
            (signal_id, signal_id),
        ).fetchone()
        if correction:
            raise ValueError("a signal in correction history cannot be removed")
        cycles = conn.execute("SELECT signal_ids FROM update_cycles WHERE region_id = ?", (signal.region_id,))
        if any(signal_id in json.loads(row["signal_ids"] or "[]") for row in cycles):
            raise ValueError("a signal referenced by cycle history cannot be removed")
        conn.execute(
            "INSERT INTO signal_deletions (id, region_id, signal_id, signal_json, removed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (str(uuid4()), signal.region_id, signal.id,
             json.dumps(signal.model_dump(mode="json"), sort_keys=True, allow_nan=False), now.isoformat()),
        )
        deleted = conn.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
        if deleted.rowcount != 1:
            raise ValueError("signal could not be removed")


def reset_demo(path: Path) -> None:
    """Clear local application state and restore the checked-in synthetic fixtures."""
    with connect(path) as conn:
        conn.execute("PRAGMA defer_foreign_keys = ON")
        for table in (
            "signal_deletions", "signal_corrections", "evidence_references", "recommendation_items", "consumed_signals", "signals", "source_records",
            "event_windows", "finance_assessments", "operator_actions", "update_cycles",
            "simulated_events", "region_profiles", "recommendation_versions", "regions",
            "catalog_actions", "products",
        ):
            conn.execute(f"DELETE FROM {table}")
        load_fixtures(conn, FIXTURES)
