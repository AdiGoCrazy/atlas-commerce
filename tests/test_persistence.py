"""Tests for SQLite persistence and fixture loading."""

import json
import tempfile
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_commerce.domain import (
    EventWindow,
    FinanceAssessment,
    FinanceDecision,
    Product,
    Region,
    RegionProfile,
    RegionState,
    RecommendationVersion,
    Signal,
    SignalSourceType,
    UpdateCycle,
    UpdateCycleStatus,
    VersionStatus,
)
from adaptive_commerce.persistence import connect, initialize, load_fixtures


def _count_rows(conn, table: str) -> int:
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _parse_json_fields(row: dict, json_fields: list[str]) -> dict:
    """Parse JSON string fields from SQLite row into Python objects."""
    result = dict(row)
    for field in json_fields:
        if field in result and result[field] is not None:
            result[field] = json.loads(result[field])
    return result


class TestPersistence:
    @pytest.fixture
    def empty_db(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        conn = connect(db_path)
        initialize(conn)
        yield conn
        conn.close()
        db_path.unlink(missing_ok=True)

    @pytest.fixture
    def fixtures_dir(self):
        return Path("data/fixtures")

    def test_initialize_creates_all_tables(self, empty_db):
        """All 12 ARCHITECTURE tables should exist after initialization."""
        tables = [
            "regions",
            "region_profiles",
            "products",
            "source_records",
            "signals",
            "event_windows",
            "finance_assessments",
            "recommendation_versions",
            "recommendation_items",
            "update_cycles",
            "evidence_references",
            "simulated_events",
        ]
        for table in tables:
            count = _count_rows(empty_db, table)
            assert count == 0, f"Table {table} should be empty initially"

    def test_load_fixtures_populates_expected_counts(self, empty_db, fixtures_dir):
        """Seeding should load all fixture records."""
        counts = load_fixtures(empty_db, fixtures_dir)

        assert counts.products == 8
        assert counts.regions == 3
        assert counts.region_profiles == 2  # only active regions have profiles
        assert counts.recommendation_versions == 2
        assert counts.event_windows == 1
        assert counts.finance_assessments == 1
        assert counts.signals == 1
        assert counts.update_cycles == 0

        # Verify actual row counts
        assert _count_rows(empty_db, "products") == 8
        assert _count_rows(empty_db, "regions") == 3
        assert _count_rows(empty_db, "region_profiles") == 2
        assert _count_rows(empty_db, "recommendation_versions") == 2
        assert _count_rows(empty_db, "event_windows") == 1
        assert _count_rows(empty_db, "finance_assessments") == 1
        assert _count_rows(empty_db, "signals") == 1

    def test_seed_is_idempotent(self, empty_db, fixtures_dir):
        """Second load should not duplicate rows."""
        load_fixtures(empty_db, fixtures_dir)
        counts1 = load_fixtures(empty_db, fixtures_dir)

        # All counts should be zero on second load (INSERT OR IGNORE)
        assert counts1.products == 0
        assert counts1.regions == 0
        assert counts1.region_profiles == 0
        assert counts1.recommendation_versions == 0
        assert counts1.event_windows == 0
        assert counts1.finance_assessments == 0
        assert counts1.signals == 0

        # Row counts unchanged
        assert _count_rows(empty_db, "products") == 8
        assert _count_rows(empty_db, "regions") == 3

    def test_seeded_regions_round_trip_into_domain_models(self, empty_db, fixtures_dir):
        """Seeded rows should validate back into domain models."""
        load_fixtures(empty_db, fixtures_dir)

        # Regions - parse JSON fields and convert enum strings
        rows = empty_db.execute("SELECT * FROM regions").fetchall()
        regions = []
        for row in rows:
            data = _parse_json_fields(dict(row), ["available_source_types"])
            data["available_source_types"] = [SignalSourceType(s) for s in data["available_source_types"]]
            data["state"] = RegionState(data["state"])
            regions.append(Region.model_validate(data))
        assert len(regions) == 3
        region_ids = {r.id for r in regions}
        assert region_ids == {"cedar", "aspen", "harbor"}
        assert regions[0].state in (RegionState.active, RegionState.candidate)

        # Products - parse JSON fields
        rows = empty_db.execute("SELECT * FROM products").fetchall()
        products = [Product.model_validate(_parse_json_fields(dict(row), ["tags"])) for row in rows]
        assert len(products) == 8
        product_ids = {p.id for p in products}
        expected = {"boots", "tent", "pack", "bottle", "poles", "charger", "pouch", "lights"}
        assert product_ids == expected

        # Region profiles - parse JSON fields
        rows = empty_db.execute("SELECT * FROM region_profiles").fetchall()
        profiles = [RegionProfile.model_validate(_parse_json_fields(dict(row), ["weights"])) for row in rows]
        assert len(profiles) == 2
        for p in profiles:
            assert p.region_id in ("cedar", "aspen")
            assert p.weights.get("outdoor") == Decimal("3")
            assert p.max_weight_delta == Decimal("5")

        # Recommendation versions - parse JSON fields
        rows = empty_db.execute("SELECT * FROM recommendation_versions").fetchall()
        versions = [RecommendationVersion.model_validate(_parse_json_fields(dict(row), ["weights"])) for row in rows]
        assert len(versions) == 2
        for v in versions:
            assert v.status == VersionStatus.active
            assert v.parent_id is None
            assert v.weights.get("outdoor") == Decimal("3")

        # Event windows - parse JSON fields
        rows = empty_db.execute("SELECT * FROM event_windows").fetchall()
        events = [EventWindow.model_validate(_parse_json_fields(dict(row), ["boosts"])) for row in rows]
        assert len(events) == 1
        assert events[0].region_id == "cedar"
        assert events[0].boosts.get("festival") == Decimal("4")
        assert events[0].boosts.get("electronics") == Decimal("4")

        # Finance assessments - parse JSON fields
        rows = empty_db.execute("SELECT * FROM finance_assessments").fetchall()
        assessments = [FinanceAssessment.model_validate(_parse_json_fields(dict(row), ["assumptions"])) for row in rows]
        assert len(assessments) == 1
        assert assessments[0].region_id == "harbor"
        assert assessments[0].decision == FinanceDecision.approve
        assert assessments[0].viability_score == Decimal("0.72")

        # Signals - parse JSON fields
        rows = empty_db.execute("SELECT * FROM signals").fetchall()
        signals = [Signal.model_validate(_parse_json_fields(dict(row), ["topics"])) for row in rows]
        assert len(signals) == 1
        assert signals[0].region_id == "cedar"
        assert signals[0].confidence == Decimal("0.92")

    def test_decimal_weights_survive_storage_exactly(self, empty_db, fixtures_dir):
        """Decimal values should round-trip exactly through TEXT storage."""
        load_fixtures(empty_db, fixtures_dir)

        # Check weights are stored and retrieved as exact values (JSON number or string)
        row = empty_db.execute(
            "SELECT weights FROM region_profiles WHERE region_id = 'cedar'"
        ).fetchone()
        stored = json.loads(row["weights"])
        # JSON serializes Decimal as float; accept either string "3" or number 3.0
        assert stored["outdoor"] in ("3", 3, 3.0)

        # When loaded into model, Decimal should match exactly
        row = empty_db.execute("SELECT * FROM region_profiles WHERE region_id = 'cedar'").fetchone()
        profile = RegionProfile.model_validate(_parse_json_fields(dict(row), ["weights"]))
        assert profile.weights["outdoor"] == Decimal("3")

        # Product prices/costs exact
        row = empty_db.execute("SELECT price, cost FROM products WHERE id = 'boots'").fetchone()
        # Stored as JSON numbers (float)
        assert float(row["price"]) == 120.00
        assert float(row["cost"]) == 65.00

        row = empty_db.execute("SELECT * FROM products WHERE id = 'boots'").fetchone()
        product = Product.model_validate(_parse_json_fields(dict(row), ["tags"]))
        assert product.price == Decimal("120.00")
        assert product.cost == Decimal("65.00")

    def test_foreign_keys_enforced(self, empty_db, fixtures_dir):
        """Foreign key constraints should be active."""
        load_fixtures(empty_db, fixtures_dir)

        # This should fail - region_id doesn't exist
        with pytest.raises(Exception) as exc:
            empty_db.execute(
                "INSERT INTO region_profiles (region_id, weights, active_version_id) VALUES (?, ?, ?)",
                ("nonexistent", "{}", "v1"),
            )
            empty_db.commit()
        # SQLite raises IntegrityError for FK violation
        assert "FOREIGN KEY" in str(exc.value).upper() or "foreign key" in str(exc.value).lower()

    def test_unique_signal_source_id(self, empty_db, fixtures_dir):
        """signals.source_id unique constraint should exist."""
        load_fixtures(empty_db, fixtures_dir)

        # Duplicate source_id should fail
        with pytest.raises(Exception) as exc:
            empty_db.execute(
                """
                INSERT INTO signals (id, region_id, source_id, source_type, topics, confidence, observed_at, evidence_excerpt)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("sig-dup", "cedar", "src-event-festival-2026-001", "event", "[]", "0.5", "2026-10-10T12:00:00+00:00", "test"),
            )
            empty_db.commit()
        assert "UNIQUE" in str(exc.value).upper() or "unique" in str(exc.value).lower()