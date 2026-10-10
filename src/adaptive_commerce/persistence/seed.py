"""Fixture seed loader for Atlas Commerce."""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from adaptive_commerce.domain import (
    EventWindow,
    FinanceAssessment,
    Product,
    Region,
    RegionProfile,
    RecommendationVersion,
    Signal,
    SignalSourceType,
    UpdateCycle,
)
from adaptive_commerce.domain.enums import FinanceDecision, RegionState, UpdateCycleStatus, VersionStatus

from .database import _json_dumps, connect, initialize


@dataclass
class SeedCounts:
    """Counts of seeded records per table."""

    regions: int = 0
    region_profiles: int = 0
    products: int = 0
    source_records: int = 0
    signals: int = 0
    event_windows: int = 0
    finance_assessments: int = 0
    recommendation_versions: int = 0
    recommendation_items: int = 0
    update_cycles: int = 0
    evidence_references: int = 0
    simulated_events: int = 0


def _load_json(fixtures_dir: Path, filename: str) -> list[dict[str, Any]]:
    path = fixtures_dir / filename
    if not path.exists():
        return []
    with path.open() as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _insert_region(conn: sqlite3.Connection, r: Region) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO regions (id, name, state, locale, available_source_types, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            r.id,
            r.name,
            r.state.value,
            r.locale,
            _json_dumps([s.value for s in r.available_source_types]),
            r.created_at.isoformat(),
            r.updated_at.isoformat() if r.updated_at else None,
        ),
    )
    return cur


def _insert_region_profile(conn: sqlite3.Connection, p: RegionProfile) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO region_profiles (region_id, weights, active_version_id, max_weight_delta)
        VALUES (?, ?, ?, ?)
        """,
        (
            p.region_id,
            _json_dumps(p.weights),
            p.active_version_id,
            _json_dumps(p.max_weight_delta),
        ),
    )
    return cur


def _insert_product(conn: sqlite3.Connection, p: Product) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO products (id, name, tags, price, cost, in_stock, eligible, base_popularity)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            p.id,
            p.name,
            _json_dumps(p.tags),
            _json_dumps(p.price),
            _json_dumps(p.cost),
            1 if p.in_stock else 0,
            1 if p.eligible else 0,
            _json_dumps(p.base_popularity),
        ),
    )
    return cur


def _insert_source_record(conn: sqlite3.Connection, rec: dict[str, Any]) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO source_records (id, region_id, source_type, raw_content, fetched_at, processed)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            rec["id"],
            rec["region_id"],
            rec["source_type"],
            rec["raw_content"],
            rec["fetched_at"],
            rec.get("processed", 0),
        ),
    )
    return cur


def _insert_signal(conn: sqlite3.Connection, s: Signal) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO signals (id, region_id, source_id, source_type, topics, confidence, observed_at, evidence_excerpt, language, normalized_text)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            s.id,
            s.region_id,
            s.source_id,
            s.source_type.value,
            _json_dumps(s.topics),
            _json_dumps(s.confidence),
            s.observed_at.isoformat(),
            s.evidence_excerpt,
            s.language,
            s.normalized_text,
        ),
    )
    return cur


def _insert_event_window(conn: sqlite3.Connection, e: EventWindow) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO event_windows (id, region_id, starts_at, expires_at, boosts)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            e.id,
            e.region_id,
            e.starts_at.isoformat(),
            e.expires_at.isoformat(),
            _json_dumps(e.boosts),
        ),
    )
    return cur


def _insert_finance_assessment(conn: sqlite3.Connection, a: FinanceAssessment) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO finance_assessments (id, region_id, viability_score, projected_profit, decision, assumptions, cfo_reference, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            a.id,
            a.region_id,
            _json_dumps(a.viability_score),
            _json_dumps(a.projected_profit),
            a.decision.value,
            _json_dumps(a.assumptions),
            a.cfo_reference,
            a.created_at.isoformat(),
        ),
    )
    return cur


def _insert_recommendation_version(conn: sqlite3.Connection, v: RecommendationVersion) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO recommendation_versions (id, region_id, parent_id, weights, status, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            v.id,
            v.region_id,
            v.parent_id,
            _json_dumps(v.weights),
            v.status.value,
            v.created_at.isoformat(),
        ),
    )
    return cur


def _insert_update_cycle(conn: sqlite3.Connection, c: UpdateCycle) -> sqlite3.Cursor:
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO update_cycles (id, region_id, signal_ids, proposed_changes, status, rejection_reason, evaluation, created_at, finished_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            c.id,
            c.region_id,
            _json_dumps(c.signal_ids),
            _json_dumps(c.proposed_changes) if c.proposed_changes else None,
            c.status.value,
            c.rejection_reason,
            _json_dumps(c.evaluation) if c.evaluation else None,
            c.created_at.isoformat(),
            c.finished_at.isoformat() if c.finished_at else None,
        ),
    )
    return cur


def load_fixtures(conn: sqlite3.Connection, fixtures_dir: Path) -> SeedCounts:
    """Load all fixture files into the database. Idempotent via INSERT OR IGNORE."""
    counts = SeedCounts()

    # Products
    for rec in _load_json(fixtures_dir, "products.json"):
        p = Product(**rec)
        cur = _insert_product(conn, p)
        if cur.rowcount > 0:
            counts.products += 1

    # Regions
    for rec in _load_json(fixtures_dir, "regions.json"):
        # Convert source types from strings to enums
        rec["available_source_types"] = [SignalSourceType(s) for s in rec.get("available_source_types", [])]
        rec["state"] = RegionState(rec["state"])
        r = Region(**rec)
        cur = _insert_region(conn, r)
        if cur.rowcount > 0:
            counts.regions += 1

    # Region profiles
    for rec in _load_json(fixtures_dir, "region_profiles.json"):
        rec["weights"] = {k: Decimal(str(v)) for k, v in rec.get("weights", {}).items()}
        rec["max_weight_delta"] = Decimal(str(rec.get("max_weight_delta", "5")))
        p = RegionProfile(**rec)
        cur = _insert_region_profile(conn, p)
        if cur.rowcount > 0:
            counts.region_profiles += 1

    # Recommendation versions
    for rec in _load_json(fixtures_dir, "recommendation_versions.json"):
        rec["weights"] = {k: Decimal(str(v)) for k, v in rec.get("weights", {}).items()}
        rec["status"] = VersionStatus(rec.get("status", "candidate"))
        v = RecommendationVersion(**rec)
        cur = _insert_recommendation_version(conn, v)
        if cur.rowcount > 0:
            counts.recommendation_versions += 1

    # Event windows
    for rec in _load_json(fixtures_dir, "event_windows.json"):
        rec["starts_at"] = datetime.fromisoformat(rec["starts_at"])
        rec["expires_at"] = datetime.fromisoformat(rec["expires_at"])
        rec["boosts"] = {k: Decimal(str(v)) for k, v in rec.get("boosts", {}).items()}
        e = EventWindow(**rec)
        cur = _insert_event_window(conn, e)
        if cur.rowcount > 0:
            counts.event_windows += 1

    # Finance assessments
    for rec in _load_json(fixtures_dir, "finance_assessments.json"):
        rec["viability_score"] = Decimal(str(rec["viability_score"]))
        rec["projected_profit"] = Decimal(str(rec["projected_profit"]))
        rec["decision"] = FinanceDecision(rec["decision"])
        rec["assumptions"] = rec.get("assumptions", {})
        a = FinanceAssessment(**rec)
        cur = _insert_finance_assessment(conn, a)
        if cur.rowcount > 0:
            counts.finance_assessments += 1

    # Signals
    for rec in _load_json(fixtures_dir, "signals.json"):
        rec["source_type"] = SignalSourceType(rec["source_type"])
        rec["confidence"] = Decimal(str(rec["confidence"]))
        rec["observed_at"] = datetime.fromisoformat(rec["observed_at"])
        s = Signal(**rec)
        cur = _insert_signal(conn, s)
        if cur.rowcount > 0:
            counts.signals += 1

    # Update cycles
    for rec in _load_json(fixtures_dir, "update_cycles.json"):
        rec["proposed_changes"] = {k: Decimal(str(v)) for k, v in rec.get("proposed_changes", {}).items()} if rec.get("proposed_changes") else None
        rec["status"] = UpdateCycleStatus(rec.get("status", "pending"))
        rec["created_at"] = datetime.fromisoformat(rec["created_at"])
        if rec.get("finished_at"):
            rec["finished_at"] = datetime.fromisoformat(rec["finished_at"])
        c = UpdateCycle(**rec)
        cur = _insert_update_cycle(conn, c)
        if cur.rowcount > 0:
            counts.update_cycles += 1

    conn.commit()
    return counts


if __name__ == "__main__":
    """CLI entry point for manual seeding."""
    import sys

    db_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/atlas_commerce.db")
    fixtures_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/fixtures")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    initialize(conn)
    counts = load_fixtures(conn, fixtures_dir)
    conn.close()

    print(f"Seeded database at {db_path}:")
    for field in counts.__dataclass_fields__:
        print(f"  {field}: {getattr(counts, field)}")