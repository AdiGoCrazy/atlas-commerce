"""Repository layer for Atlas Commerce — data access for domain aggregates."""

import sqlite3
from datetime import datetime, timezone
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
from adaptive_commerce.domain.enums import (
    FinanceDecision,
    RegionState,
    UpdateCycleStatus,
    VersionStatus,
)

from .database import _json_dumps, _json_loads, connect, initialize


class Repository:
    """Data access layer returning domain models for `model_validate(from_attributes=True)`."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # =========================== REGION ===========================

    def get_region(self, region_id: str) -> Region | None:
        """Get a region by ID."""
        row = self.conn.execute(
            "SELECT * FROM regions WHERE id = ?", (region_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_region(row)

    def get_all_regions(self) -> list[Region]:
        """Get all regions."""
        rows = self.conn.execute("SELECT * FROM regions ORDER BY created_at").fetchall()
        return [self._row_to_region(row) for row in rows]

    def get_regions_by_state(self, state: RegionState) -> list[Region]:
        """Get regions by lifecycle state."""
        rows = self.conn.execute(
            "SELECT * FROM regions WHERE state = ? ORDER BY created_at", (state.value,)
        ).fetchall()
        return [self._row_to_region(row) for row in rows]

    def create_region(self, region: Region) -> None:
        """Create a new region."""
        self.conn.execute(
            """
            INSERT INTO regions (id, name, state, locale, available_source_types, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                region.id,
                region.name,
                region.state.value,
                region.locale,
                _json_dumps([s.value for s in region.available_source_types]),
                region.created_at.isoformat(),
                region.updated_at.isoformat() if region.updated_at else None,
            ),
        )

    def update_region_state(self, region_id: str, state: RegionState) -> bool:
        """Update region lifecycle state."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "UPDATE regions SET state = ?, updated_at = ? WHERE id = ?",
            (state.value, now, region_id),
        )
        return cur.rowcount > 0

    def update_region_locale(self, region_id: str, locale: str) -> bool:
        """Update region locale."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "UPDATE regions SET locale = ?, updated_at = ? WHERE id = ?",
            (locale, now, region_id),
        )
        return cur.rowcount > 0

    def _row_to_region(self, row: sqlite3.Row) -> Region:
        data = dict(row)
        data["available_source_types"] = [
            SignalSourceType(s) for s in _json_loads(data.get("available_source_types", "[]"))
        ]
        data["state"] = RegionState(data["state"])
        return Region.model_validate(data)

    # =========================== PRODUCT ===========================

    def get_product(self, product_id: str) -> Product | None:
        """Get a product by ID."""
        row = self.conn.execute(
            "SELECT * FROM products WHERE id = ?", (product_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_product(row)

    def get_all_products(self) -> list[Product]:
        """Get all products."""
        rows = self.conn.execute("SELECT * FROM products ORDER BY id").fetchall()
        return [self._row_to_product(row) for row in rows]

    def get_products_for_ranking(self, region_id: str) -> list[Product]:
        """Get eligible, in-stock products for a region's ranking."""
        rows = self.conn.execute(
            """
            SELECT DISTINCT p.* FROM products p
            JOIN recommendation_versions rv ON rv.region_id = ?
            JOIN region_profiles rp ON rp.region_id = ?
            WHERE p.in_stock = 1 AND p.eligible = 1
            ORDER BY p.id
            """,
            (region_id, region_id),
        ).fetchall()
        return [self._row_to_product(row) for row in rows]

    def create_product(self, product: Product) -> None:
        """Create a new product."""
        self.conn.execute(
            """
            INSERT INTO products (id, name, tags, price, cost, in_stock, eligible, base_popularity)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product.id,
                product.name,
                _json_dumps(product.tags),
                str(product.price),
                str(product.cost),
                1 if product.in_stock else 0,
                1 if product.eligible else 0,
                str(product.base_popularity),
            ),
        )

    def update_product_stock(self, product_id: str, in_stock: bool) -> bool:
        """Update product stock status."""
        cur = self.conn.execute(
            "UPDATE products SET in_stock = ? WHERE id = ?",
            (1 if in_stock else 0, product_id),
        )
        return cur.rowcount > 0

    def _row_to_product(self, row: sqlite3.Row) -> Product:
        data = dict(row)
        data["tags"] = _json_loads(data.get("tags", "[]"))
        # price, cost, base_popularity are stored as strings
        for field in ("price", "cost", "base_popularity"):
            if data.get(field) is not None:
                data[field] = Decimal(str(data[field]))
        return Product.model_validate(data)

    # =========================== SIGNAL ===========================

    def get_signal(self, signal_id: str) -> Signal | None:
        """Get a signal by ID."""
        row = self.conn.execute(
            "SELECT * FROM signals WHERE id = ?", (signal_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_signal(row)

    def get_signals_by_region(
        self, region_id: str, limit: int = 100, since_id: str | None = None
    ) -> list[Signal]:
        """Get signals for a region in ID order, optionally after an ID."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if since_id:
            rows = self.conn.execute(
                """
                SELECT * FROM signals
                WHERE region_id = ? AND id > ?
                ORDER BY id
                LIMIT ?
                """,
                (region_id, since_id, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT * FROM signals
                WHERE region_id = ?
                ORDER BY id
                LIMIT ?
                """,
                (region_id, limit),
            ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def get_signals_by_source_id(self, source_id: str) -> list[Signal]:
        """Get signals by source_id (for idempotency checks)."""
        rows = self.conn.execute(
            "SELECT * FROM signals WHERE source_id = ?", (source_id,)
        ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def get_unprocessed_signals(self, region_id: str, limit: int = 100) -> list[Signal]:
        """Return signals not yet consumed by a completed region cycle."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        rows = self.conn.execute(
            "SELECT s.* FROM signals s LEFT JOIN consumed_signals c ON c.signal_id = s.id "
            "WHERE s.region_id = ? AND c.signal_id IS NULL ORDER BY s.observed_at, s.id LIMIT ?",
            (region_id, limit),
        ).fetchall()
        return [self._row_to_signal(row) for row in rows]

    def count_unprocessed_signals(self, region_id: str) -> int:
        """Count unconsumed signals waiting for a regional cycle."""
        row = self.conn.execute(
            "SELECT COUNT(*) FROM signals s LEFT JOIN consumed_signals c "
            "ON c.signal_id = s.id WHERE s.region_id = ? AND c.signal_id IS NULL",
            (region_id,),
        ).fetchone()
        return int(row[0])

    def mark_signals_processed(self, signal_ids: list[str], cycle_id: str) -> None:
        """Mark source evidence consumed in the same transaction as its cycle."""
        for signal_id in signal_ids:
            self.conn.execute(
                "INSERT OR IGNORE INTO consumed_signals (signal_id, cycle_id) VALUES (?, ?)",
                (signal_id, cycle_id),
            )

    def create_signal(self, signal: Signal) -> bool:
        """Create a new signal. Returns False if source_id already exists (idempotent)."""
        try:
            self.conn.execute(
                """
                INSERT INTO signals (id, region_id, source_id, source_type, topics, confidence, observed_at, evidence_excerpt, language, normalized_text)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    signal.id,
                    signal.region_id,
                    signal.source_id,
                    signal.source_type.value,
                    _json_dumps(signal.topics),
                    str(signal.confidence),
                    signal.observed_at.isoformat(),
                    signal.evidence_excerpt,
                    signal.language,
                    signal.normalized_text,
                ),
            )
            return True
        except sqlite3.IntegrityError as e:
            if "UNIQUE constraint failed: signals.source_id" in str(e):
                return False  # Idempotent: signal with this source_id already exists
            raise

    def _row_to_signal(self, row: sqlite3.Row) -> Signal:
        data = dict(row)
        data["source_type"] = SignalSourceType(data["source_type"])
        data["topics"] = _json_loads(data.get("topics", "[]"))
        data["confidence"] = Decimal(str(data["confidence"]))
        return Signal.model_validate(data)

    # =========================== FINANCE ASSESSMENT ===========================

    def get_finance_assessment(self, assessment_id: str) -> FinanceAssessment | None:
        """Get a finance assessment by ID."""
        row = self.conn.execute(
            "SELECT * FROM finance_assessments WHERE id = ?", (assessment_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_finance_assessment(row)

    def get_finance_assessments_by_region(self, region_id: str) -> list[FinanceAssessment]:
        """Get all finance assessments for a region."""
        rows = self.conn.execute(
            "SELECT * FROM finance_assessments WHERE region_id = ? ORDER BY created_at",
            (region_id,),
        ).fetchall()
        return [self._row_to_finance_assessment(row) for row in rows]

    def create_finance_assessment(self, assessment: FinanceAssessment) -> None:
        """Create a new finance assessment."""
        self.conn.execute(
            """
            INSERT INTO finance_assessments (id, region_id, viability_score, projected_profit, decision, assumptions, cfo_reference, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                assessment.id,
                assessment.region_id,
                str(assessment.viability_score),
                str(assessment.projected_profit),
                assessment.decision.value,
                _json_dumps(assessment.assumptions),
                assessment.cfo_reference,
                assessment.created_at.isoformat(),
            ),
        )

    def _row_to_finance_assessment(self, row: sqlite3.Row) -> FinanceAssessment:
        data = dict(row)
        data["viability_score"] = Decimal(str(data["viability_score"]))
        data["projected_profit"] = Decimal(str(data["projected_profit"]))
        data["decision"] = FinanceDecision(data["decision"])
        data["assumptions"] = _json_loads(data.get("assumptions", "{}"))
        return FinanceAssessment.model_validate(data)

    # =========================== RECOMMENDATION VERSION ===========================

    def get_recommendation_version(self, version_id: str) -> RecommendationVersion | None:
        """Get a recommendation version by ID."""
        row = self.conn.execute(
            "SELECT * FROM recommendation_versions WHERE id = ?", (version_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_recommendation_version(row)

    def get_active_version(self, region_id: str) -> RecommendationVersion | None:
        """Get the active recommendation version for a region."""
        row = self.conn.execute(
            """
            SELECT rv.* FROM recommendation_versions rv
            JOIN region_profiles rp ON rp.active_version_id = rv.id
            WHERE rv.region_id = ? AND rv.status = 'active'
            """,
            (region_id,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_recommendation_version(row)

    def get_version_lineage(self, region_id: str) -> list[RecommendationVersion]:
        """Get the full version lineage for a region, newest first."""
        rows = self.conn.execute(
            """
            SELECT * FROM recommendation_versions
            WHERE region_id = ?
            ORDER BY created_at DESC
            """,
            (region_id,),
        ).fetchall()
        return [self._row_to_recommendation_version(row) for row in rows]

    def create_update_cycle(self, cycle: UpdateCycle) -> None:
        """Persist a cycle record with its validated input and output summary."""
        self.conn.execute(
            """INSERT INTO update_cycles
            (id, region_id, signal_ids, proposed_changes, status, rejection_reason,
             evaluation, created_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cycle.id, cycle.region_id, _json_dumps(cycle.signal_ids),
                _json_dumps(cycle.proposed_changes) if cycle.proposed_changes is not None else None,
                cycle.status.value, cycle.rejection_reason,
                _json_dumps(cycle.evaluation) if cycle.evaluation is not None else None,
                cycle.created_at.isoformat(),
                cycle.finished_at.isoformat() if cycle.finished_at else None,
            ),
        )

    def update_update_cycle(self, cycle: UpdateCycle) -> None:
        """Update the terminal details of a previously persisted cycle."""
        self.conn.execute(
            """UPDATE update_cycles SET signal_ids = ?, proposed_changes = ?, status = ?,
            rejection_reason = ?, evaluation = ?, finished_at = ? WHERE id = ?""",
            (
                _json_dumps(cycle.signal_ids),
                _json_dumps(cycle.proposed_changes) if cycle.proposed_changes is not None else None,
                cycle.status.value, cycle.rejection_reason,
                _json_dumps(cycle.evaluation) if cycle.evaluation is not None else None,
                cycle.finished_at.isoformat() if cycle.finished_at else None,
                cycle.id,
            ),
        )

    def get_update_cycle(self, cycle_id: str) -> UpdateCycle | None:
        """Load one cycle by stable identifier."""
        row = self.conn.execute(
            "SELECT * FROM update_cycles WHERE id = ?", (cycle_id,)
        ).fetchone()
        return self._row_to_update_cycle(row) if row else None

    def list_update_cycles(self, region_id: str, limit: int = 50) -> list[UpdateCycle]:
        """Return recent cycles for the region, newest first."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        rows = self.conn.execute(
            "SELECT * FROM update_cycles WHERE region_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (region_id, limit),
        ).fetchall()
        return [self._row_to_update_cycle(row) for row in rows]

    def _row_to_update_cycle(self, row: sqlite3.Row) -> UpdateCycle:
        data = dict(row)
        data["signal_ids"] = _json_loads(data["signal_ids"])
        data["proposed_changes"] = {
            key: Decimal(str(value))
            for key, value in _json_loads(data.get("proposed_changes")).items()
        } if data.get("proposed_changes") else None
        data["status"] = UpdateCycleStatus(data["status"])
        data["evaluation"] = _json_loads(data["evaluation"]) if data.get("evaluation") else None
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["finished_at"] = datetime.fromisoformat(data["finished_at"]) if data.get("finished_at") else None
        return UpdateCycle.model_validate(data)

    def create_operator_action(
        self, action_id: str, region_id: str, action: str,
        detail: dict[str, Any], created_at: datetime,
    ) -> None:
        """Record a human control action for review and recovery history."""
        self.conn.execute(
            "INSERT INTO operator_actions (id, region_id, action, detail, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (action_id, region_id, action, _json_dumps(detail), created_at.isoformat()),
        )

    def list_operator_actions(self, region_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """Return recent human actions for the selected region."""
        rows = self.conn.execute(
            "SELECT * FROM operator_actions WHERE region_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (region_id, limit),
        ).fetchall()
        return [
            {**dict(row), "detail": _json_loads(row["detail"])} for row in rows
        ]

    def create_recommendation_version(self, version: RecommendationVersion) -> None:
        """Create a new recommendation version (immutable — no updates)."""
        self.conn.execute(
            """
            INSERT INTO recommendation_versions (id, region_id, parent_id, weights, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version.id,
                version.region_id,
                version.parent_id,
                _json_dumps(version.weights),
                version.status.value,
                version.created_at.isoformat(),
            ),
        )

    def _row_to_recommendation_version(self, row: sqlite3.Row) -> RecommendationVersion:
        data = dict(row)
        data["weights"] = {
            k: Decimal(str(v)) for k, v in _json_loads(data.get("weights", "{}")).items()
        }
        data["status"] = VersionStatus(data["status"])
        return RecommendationVersion.model_validate(data)

    # =========================== REGION PROFILE ===========================

    def get_region_profile(self, region_id: str) -> RegionProfile | None:
        """Get the region profile (weights + active version)."""
        row = self.conn.execute(
            "SELECT * FROM region_profiles WHERE region_id = ?", (region_id,)
        ).fetchone()
        if not row:
            return None
        return self._row_to_region_profile(row)

    def get_active_profile(self, region_id: str) -> RegionProfile | None:
        """Get the active region profile with weights and active_version_id."""
        return self.get_region_profile(region_id)

    def set_active_version(self, region_id: str, version_id: str) -> bool:
        """Activate a same-region candidate version atomically; caller commits."""
        self.conn.execute("SAVEPOINT activate_version")
        try:
            target = self.conn.execute(
                "SELECT status FROM recommendation_versions WHERE id = ? AND region_id = ?",
                (version_id, region_id),
            ).fetchone()
            profile = self.conn.execute(
                "SELECT active_version_id FROM region_profiles WHERE region_id = ?",
                (region_id,),
            ).fetchone()
            if target is None or profile is None or target["status"] not in {
                VersionStatus.candidate.value, VersionStatus.active.value,
                VersionStatus.rolled_back.value,
            }:
                self.conn.execute("ROLLBACK TO SAVEPOINT activate_version")
                self.conn.execute("RELEASE SAVEPOINT activate_version")
                return False

            previous_id = profile["active_version_id"]
            previous = self.conn.execute(
                "SELECT status FROM recommendation_versions WHERE id = ? AND region_id = ?",
                (previous_id, region_id),
            ).fetchone()
            if previous is None:
                self.conn.execute("ROLLBACK TO SAVEPOINT activate_version")
                self.conn.execute("RELEASE SAVEPOINT activate_version")
                return False
            if previous_id != version_id:
                self.conn.execute(
                    "UPDATE recommendation_versions SET status = ? WHERE id = ? AND status = ?",
                    (VersionStatus.rolled_back.value, previous_id, VersionStatus.active.value),
                )
            self.conn.execute(
                "UPDATE recommendation_versions SET status = ? WHERE id = ?",
                (VersionStatus.active.value, version_id),
            )
            self.conn.execute(
                "UPDATE region_profiles SET active_version_id = ?, weights = "
                "(SELECT weights FROM recommendation_versions WHERE id = ?) WHERE region_id = ?",
                (version_id, version_id, region_id),
            )
        except Exception:
            self.conn.execute("ROLLBACK TO SAVEPOINT activate_version")
            self.conn.execute("RELEASE SAVEPOINT activate_version")
            raise
        self.conn.execute("RELEASE SAVEPOINT activate_version")
        return True

    def reject_recommendation_version(self, version_id: str) -> bool:
        """Mark an unused candidate version rejected without changing active state."""
        cur = self.conn.execute(
            "UPDATE recommendation_versions SET status = ? "
            "WHERE id = ? AND status = ?",
            (VersionStatus.rejected.value, version_id, VersionStatus.candidate.value),
        )
        return cur.rowcount == 1

    def _row_to_region_profile(self, row: sqlite3.Row) -> RegionProfile:
        data = dict(row)
        data["weights"] = {
            k: Decimal(str(v)) for k, v in _json_loads(data.get("weights", "{}")).items()
        }
        data["max_weight_delta"] = Decimal(str(data["max_weight_delta"]))
        return RegionProfile.model_validate(data)

    # =========================== EVENT WINDOWS ===========================

    def get_active_events(self, region_id: str, now: str) -> list[EventWindow]:
        """Get active event windows for a region at a given time (ISO string)."""
        rows = self.conn.execute(
            """
            SELECT * FROM event_windows
            WHERE region_id = ? AND starts_at <= ? AND expires_at > ?
            ORDER BY starts_at
            """,
            (region_id, now, now),
        ).fetchall()
        return [self._row_to_event_window(row) for row in rows]

    def get_all_events(self, region_id: str) -> list[EventWindow]:
        """Get all event windows for a region."""
        rows = self.conn.execute(
            "SELECT * FROM event_windows WHERE region_id = ? ORDER BY starts_at",
            (region_id,),
        ).fetchall()
        return [self._row_to_event_window(row) for row in rows]

    def create_event_window(self, event: EventWindow) -> None:
        """Create a new event window."""
        self.conn.execute(
            """
            INSERT OR IGNORE INTO event_windows (id, region_id, starts_at, expires_at, boosts)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.region_id,
                event.starts_at.isoformat(),
                event.expires_at.isoformat(),
                _json_dumps(event.boosts),
            ),
        )

    def _row_to_event_window(self, row: sqlite3.Row) -> EventWindow:
        data = dict(row)
        data["boosts"] = {
            k: Decimal(str(v)) for k, v in _json_loads(data.get("boosts", "{}")).items()
        }
        return EventWindow.model_validate(data)

    # =========================== RANKING POLICY (from domain config) ===========================

    def get_ranking_policy(self, region_id: str) -> dict[str, Any]:
        """Get ranking policy for a region (exclusions, penalties, margin_weight).
        
        This is a placeholder — in production this would come from a policy table.
        For the prototype, we return defaults with region-specific exclusions if any.
        """
        # For now, return defaults — Sprint 2 doesn't require a policy table
        return {
            "excluded_product_ids": set(),
            "excluded_tags": set(),
            "product_penalties": {},
            "margin_weight": Decimal("1"),
        }


def get_repository(db_path: str | Path = "data/atlas_commerce.db") -> Repository:
    """Factory to get a repository with an initialized connection."""
    conn = connect(db_path)
    return Repository(conn)
