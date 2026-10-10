"""Tests for repository CRUD operations and version history."""

import sqlite3
import tempfile
from decimal import Decimal
from datetime import datetime, timezone
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
from adaptive_commerce.persistence import Repository, connect, initialize, load_fixtures


class TestRepository:
    """Tests for the Repository class."""

    @pytest.fixture
    def repo(self):
        """Create a fresh database and repository for each test."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        conn = connect(db_path)
        initialize(conn)
        load_fixtures(conn, Path("data/fixtures"))
        yield Repository(conn)
        conn.close()
        db_path.unlink(missing_ok=True)

    @pytest.fixture
    def empty_repo(self):
        """Create an empty database and repository for CRUD tests."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)
        conn = connect(db_path)
        initialize(conn)
        yield Repository(conn)
        conn.close()
        db_path.unlink(missing_ok=True)

    # =========================== REGION CRUD ===========================

    def test_region_crud_round_trip(self, empty_repo):
        """Create, read, update, delete region."""
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.candidate,
            locale="en_US",
            available_source_types=[SignalSourceType.news, SignalSourceType.event],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)
        retrieved = empty_repo.get_region("test-region")
        assert retrieved == region

        # Update state
        assert empty_repo.update_region_state("test-region", RegionState.active)
        retrieved = empty_repo.get_region("test-region")
        assert retrieved.state == RegionState.active
        assert retrieved.updated_at is not None

    def test_get_all_regions(self, repo):
        """Get all regions from seeded database."""
        regions = repo.get_all_regions()
        assert len(regions) == 3
        ids = {r.id for r in regions}
        assert ids == {"cedar", "aspen", "harbor"}

    def test_get_regions_by_state(self, repo):
        """Get regions filtered by state."""
        active = repo.get_regions_by_state(RegionState.active)
        assert len(active) == 2
        assert {r.id for r in active} == {"cedar", "aspen"}

        awaiting_approval = repo.get_regions_by_state(RegionState.awaiting_approval)
        assert len(awaiting_approval) == 1
        assert awaiting_approval[0].id == "harbor"

    # =========================== PRODUCT CRUD ===========================

    def test_product_crud_round_trip(self, empty_repo):
        """Create, read, update stock for product."""
        product = Product(
            id="test-prod",
            name="Test Product",
            tags=["outdoor", "electronics"],
            price=Decimal("99.99"),
            cost=Decimal("45.00"),
            in_stock=True,
            eligible=True,
            base_popularity=Decimal("2"),
        )
        empty_repo.create_product(product)
        retrieved = empty_repo.get_product("test-prod")
        assert retrieved == product

        # Update stock
        assert empty_repo.update_product_stock("test-prod", False)
        retrieved = empty_repo.get_product("test-prod")
        assert retrieved.in_stock is False

    def test_get_all_products(self, repo):
        """Get all products from seeded database."""
        products = repo.get_all_products()
        assert len(products) == 8
        ids = {p.id for p in products}
        expected = {"boots", "tent", "pack", "bottle", "poles", "charger", "pouch", "lights"}
        assert ids == expected

    def test_get_products_for_ranking(self, repo):
        """Get eligible in-stock products for ranking."""
        products = repo.get_products_for_ranking("cedar")
        assert len(products) == 8  # all fixtures are eligible and in_stock
        for p in products:
            assert p.in_stock is True
            assert p.eligible is True

    # =========================== SIGNAL CRUD & IDEMPOTENCY ===========================

    def test_signal_crud_round_trip(self, empty_repo):
        """Create and read signal."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        signal = Signal(
            id="sig-1",
            region_id="test-region",
            source_id="src-test-1",
            source_type=SignalSourceType.news,
            topics=["festival", "outdoor"],
            confidence=Decimal("0.85"),
            observed_at=datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
            evidence_excerpt="Festival announced",
            language="en",
        )
        assert empty_repo.create_signal(signal) is True
        retrieved = empty_repo.get_signal("sig-1")
        assert retrieved == signal

    def test_signal_idempotent_on_duplicate_source_id(self, empty_repo):
        """Duplicate source_id insert is no-op (returns False)."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        signal1 = Signal(
            id="sig-1",
            region_id="test-region",
            source_id="src-duplicate",
            source_type=SignalSourceType.news,
            topics=["festival"],
            confidence=Decimal("0.8"),
            observed_at=datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
            evidence_excerpt="Test",
        )
        signal2 = Signal(
            id="sig-2",  # Different ID, same source_id
            region_id="test-region",
            source_id="src-duplicate",
            source_type=SignalSourceType.news,
            topics=["outdoor"],
            confidence=Decimal("0.9"),
            observed_at=datetime(2026, 10, 10, 13, tzinfo=timezone.utc),
            evidence_excerpt="Test 2",
        )
        assert empty_repo.create_signal(signal1) is True
        assert empty_repo.create_signal(signal2) is False  # Idempotent

        # Only first signal should exist
        signals = empty_repo.get_signals_by_source_id("src-duplicate")
        assert len(signals) == 1
        assert signals[0].id == "sig-1"

    def test_get_signals_by_region_and_cursor(self, repo):
        """Get signals for a region, optionally after an ID."""
        signals = repo.get_signals_by_region("cedar")
        assert len(signals) == 1
        assert signals[0].region_id == "cedar"
        assert signals[0].source_id == "src-event-festival-2026-001"
        assert repo.get_signals_by_region("cedar", since_id="zzz") == []
        with pytest.raises(ValueError, match="positive integer"):
            repo.get_signals_by_region("cedar", limit=0)

    def test_get_signals_by_source_id(self, repo):
        """Get signals by source_id."""
        signals = repo.get_signals_by_source_id("src-event-festival-2026-001")
        assert len(signals) == 1
        assert signals[0].region_id == "cedar"

    # =========================== FINANCE ASSESSMENT CRUD ===========================

    def test_finance_assessment_crud_round_trip(self, empty_repo):
        """Create and read finance assessment."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.candidate,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        assessment = FinanceAssessment(
            id="fa-test",
            region_id="test-region",
            viability_score=Decimal("0.75"),
            projected_profit=Decimal("100000"),
            decision=FinanceDecision.approve,
            assumptions={"traffic": "1000"},
            cfo_reference="cfo://model/scenario/1",
            created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        empty_repo.create_finance_assessment(assessment)
        retrieved = empty_repo.get_finance_assessment("fa-test")
        assert retrieved == assessment

    def test_get_finance_assessments_by_region(self, repo):
        """Get all finance assessments for a region."""
        assessments = repo.get_finance_assessments_by_region("harbor")
        assert len(assessments) == 1
        assert assessments[0].decision == FinanceDecision.approve
        assert assessments[0].viability_score == Decimal("0.72")

    # =========================== RECOMMENDATION VERSION CRUD & LINEAGE ===========================

    def test_recommendation_version_crud_round_trip(self, empty_repo):
        """Create and read recommendation version."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        version = RecommendationVersion(
            id="v-test-1",
            region_id="test-region",
            parent_id=None,
            weights={"outdoor": Decimal("3")},
            status=VersionStatus.active,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_recommendation_version(version)
        retrieved = empty_repo.get_recommendation_version("v-test-1")
        assert retrieved == version

    def test_version_immutability_parent_readable_after_child(self, empty_repo):
        """Parent version remains readable after child version is saved."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        v1 = RecommendationVersion(
            id="v-parent",
            region_id="test-region",
            parent_id=None,
            weights={"outdoor": Decimal("3")},
            status=VersionStatus.active,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        v2 = RecommendationVersion(
            id="v-child",
            region_id="test-region",
            parent_id="v-parent",
            weights={"outdoor": Decimal("3"), "festival": Decimal("4")},
            status=VersionStatus.candidate,
            created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        empty_repo.create_recommendation_version(v1)
        empty_repo.create_recommendation_version(v2)

        # Parent still readable
        parent = empty_repo.get_recommendation_version("v-parent")
        assert parent is not None
        assert parent.weights["outdoor"] == Decimal("3")
        assert parent.parent_id is None

        # Child has correct parent
        child = empty_repo.get_recommendation_version("v-child")
        assert child.parent_id == "v-parent"
        assert child.weights["festival"] == Decimal("4")

    def test_version_lineage(self, empty_repo):
        """Get version lineage for a region (newest first)."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        v1 = RecommendationVersion(
            id="v1", region_id="test-region", parent_id=None,
            weights={"outdoor": Decimal("1")}, status=VersionStatus.active,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        v2 = RecommendationVersion(
            id="v2", region_id="test-region", parent_id="v1",
            weights={"outdoor": Decimal("2")}, status=VersionStatus.rejected,
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        v3 = RecommendationVersion(
            id="v3", region_id="test-region", parent_id="v2",
            weights={"outdoor": Decimal("3")}, status=VersionStatus.active,
            created_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_recommendation_version(v1)
        empty_repo.create_recommendation_version(v2)
        empty_repo.create_recommendation_version(v3)

        lineage = empty_repo.get_version_lineage("test-region")
        assert len(lineage) == 3
        assert [v.id for v in lineage] == ["v3", "v2", "v1"]  # newest first

    def test_get_active_version(self, empty_repo):
        """Get the active version for a region."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        # Also create region profile
        empty_repo.conn.execute(
            "INSERT INTO region_profiles (region_id, weights, active_version_id, max_weight_delta) VALUES (?, ?, ?, ?)",
            ("test-region", "{}", "v-active", "5"),
        )
        empty_repo.conn.commit()

        v1 = RecommendationVersion(
            id="v-inactive", region_id="test-region", parent_id=None,
            weights={"outdoor": Decimal("1")}, status=VersionStatus.candidate,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        v2 = RecommendationVersion(
            id="v-active", region_id="test-region", parent_id="v-inactive",
            weights={"outdoor": Decimal("2")}, status=VersionStatus.active,
            created_at=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_recommendation_version(v1)
        empty_repo.create_recommendation_version(v2)

        active = empty_repo.get_active_version("test-region")
        assert active is not None
        assert active.id == "v-active"
        assert active.status == VersionStatus.active

    # =========================== REGION PROFILE CRUD ===========================

    def test_region_profile_crud(self, empty_repo):
        """Create, read, update region profile."""
        # First create region
        region = Region(
            id="test-region",
            name="Test",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        # Create profile and its active/candidate version snapshots.
        profile = RegionProfile(
            region_id="test-region",
            weights={"outdoor": Decimal("3")},
            active_version_id="v1",
            max_weight_delta=Decimal("5"),
        )
        empty_repo.conn.execute(
            "INSERT INTO region_profiles (region_id, weights, active_version_id, max_weight_delta) VALUES (?, ?, ?, ?)",
            ("test-region", _json_dumps(profile.weights), profile.active_version_id, str(profile.max_weight_delta)),
        )
        for version_id, weights, status in (
            ("v1", {"outdoor": Decimal("3")}, VersionStatus.active),
            ("v2", {"outdoor": Decimal("3"), "festival": Decimal("4")}, VersionStatus.candidate),
        ):
            empty_repo.create_recommendation_version(RecommendationVersion(
                id=version_id, region_id="test-region", parent_id=None,
                weights=weights, status=status, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ))

        retrieved = empty_repo.get_region_profile("test-region")
        assert retrieved is not None
        assert retrieved.region_id == "test-region"
        assert retrieved.weights["outdoor"] == Decimal("3")
        assert retrieved.max_weight_delta == Decimal("5")

        # Activate a candidate and update the profile from its immutable snapshot.
        assert empty_repo.set_active_version("test-region", "v2")
        retrieved = empty_repo.get_region_profile("test-region")
        assert retrieved.active_version_id == "v2"
        assert retrieved.weights == {"outdoor": Decimal("3"), "festival": Decimal("4")}
        assert empty_repo.get_recommendation_version("v1").status == VersionStatus.rolled_back
        assert empty_repo.get_recommendation_version("v2").status == VersionStatus.active

    def test_active_version_rejects_missing_cross_region_and_rejected_versions(self, empty_repo):
        region = Region(
            id="r1", name="One", state=RegionState.active, locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        other = Region(
            id="r2", name="Two", state=RegionState.active, locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)
        empty_repo.create_region(other)
        empty_repo.conn.execute(
            "INSERT INTO region_profiles (region_id, weights, active_version_id) VALUES (?, ?, ?)",
            ("r1", '{"old":"1"}', "old"),
        )
        for version_id, region_id, status in (
            ("old", "r1", VersionStatus.active),
            ("foreign", "r2", VersionStatus.candidate),
            ("rejected", "r1", VersionStatus.rejected),
        ):
            empty_repo.create_recommendation_version(RecommendationVersion(
                id=version_id, region_id=region_id, parent_id=None, weights={"new": Decimal("2")},
                status=status, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ))

        assert not empty_repo.set_active_version("r1", "missing")
        assert not empty_repo.set_active_version("r1", "foreign")
        assert not empty_repo.set_active_version("r1", "rejected")
        assert empty_repo.get_region_profile("r1").active_version_id == "old"
        assert empty_repo.get_recommendation_version("old").status == VersionStatus.active

    def test_repository_writes_follow_caller_transaction(self, empty_repo):
        region = Region(
            id="rollback-region", name="Rollback", state=RegionState.candidate,
            locale="en_US", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.conn.execute("SAVEPOINT caller_work")
        empty_repo.create_region(region)
        empty_repo.conn.execute("ROLLBACK TO SAVEPOINT caller_work")
        empty_repo.conn.execute("RELEASE SAVEPOINT caller_work")
        assert empty_repo.get_region("rollback-region") is None

    def test_active_version_change_rolls_back_all_rows_on_write_failure(self, empty_repo):
        region = Region(
            id="r-atomic", name="Atomic", state=RegionState.active,
            locale="en_US", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)
        empty_repo.conn.execute(
            "INSERT INTO region_profiles (region_id, weights, active_version_id) VALUES (?, ?, ?)",
            ("r-atomic", '{"old":"1"}', "old"),
        )
        for version_id, status in (("old", VersionStatus.active), ("new", VersionStatus.candidate)):
            empty_repo.create_recommendation_version(RecommendationVersion(
                id=version_id, region_id="r-atomic", parent_id=None,
                weights={version_id: Decimal("2")}, status=status,
                created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ))
        empty_repo.conn.execute(
            "CREATE TRIGGER fail_pointer BEFORE UPDATE OF active_version_id ON region_profiles "
            "BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )

        with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
            empty_repo.set_active_version("r-atomic", "new")
        assert empty_repo.get_region_profile("r-atomic").active_version_id == "old"
        assert empty_repo.get_recommendation_version("old").status == VersionStatus.active
        assert empty_repo.get_recommendation_version("new").status == VersionStatus.candidate

    # =========================== EVENT WINDOWS ===========================

    def test_event_window_crud(self, empty_repo):
        """Create and read event windows."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        event = EventWindow(
            id="evt-test",
            region_id="test-region",
            starts_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
            expires_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
            boosts={"festival": Decimal("4")},
        )
        empty_repo.create_event_window(event)

        retrieved = empty_repo.get_all_events("test-region")
        assert len(retrieved) == 1
        assert retrieved[0].id == "evt-test"
        assert retrieved[0].boosts["festival"] == Decimal("4")

    def test_get_active_events(self, empty_repo):
        """Get active events at a given time."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        event = EventWindow(
            id="evt-active",
            region_id="test-region",
            starts_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
            expires_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
            boosts={"festival": Decimal("4")},
        )
        empty_repo.create_event_window(event)

        # During event
        active = empty_repo.get_active_events("test-region", "2026-10-12T12:00:00+00:00")
        assert len(active) == 1
        assert active[0].id == "evt-active"

        # Before event
        active = empty_repo.get_active_events("test-region", "2026-10-09T12:00:00+00:00")
        assert len(active) == 0

        # After event
        active = empty_repo.get_active_events("test-region", "2026-10-18T12:00:00+00:00")
        assert len(active) == 0

    # =========================== DECIMAL PRECISION ===========================

    def test_decimal_precision_survives_storage(self, empty_repo):
        """High-precision Decimal values round-trip exactly."""
        # Use a value with many decimal places
        precise_value = Decimal("0.1234567890123456789012345678")

        product = Product(
            id="precision-test",
            name="Precision Test",
            tags=["test"],
            price=precise_value,
            cost=Decimal("10"),
            in_stock=True,
            eligible=True,
            base_popularity=Decimal("0"),
        )
        empty_repo.create_product(product)

        retrieved = empty_repo.get_product("precision-test")
        assert retrieved.price == precise_value
        assert str(retrieved.price) == "0.1234567890123456789012345678"

    # =========================== SEED ROLLBACK ===========================

    def test_seed_rollback_on_validation_failure(self):
        """Invalid fixture row causes full rollback, no partial data."""
        import sqlite3
        from decimal import Decimal

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        conn = connect(db_path)
        initialize(conn)

        # Create a temp fixtures dir with an invalid confidence
        import json
        temp_fixtures = Path(tempfile.mkdtemp())
        (temp_fixtures / "products.json").write_text("[]")
        (temp_fixtures / "regions.json").write_text("[]")
        (temp_fixtures / "region_profiles.json").write_text("[]")
        (temp_fixtures / "recommendation_versions.json").write_text("[]")
        (temp_fixtures / "event_windows.json").write_text("[]")
        (temp_fixtures / "finance_assessments.json").write_text("[]")
        # Invalid confidence > 1
        invalid_signals = [{
            "id": "sig-bad",
            "region_id": "test",
            "source_id": "src-bad",
            "source_type": "news",
            "topics": ["test"],
            "confidence": "1.5",  # Invalid: > 1
            "observed_at": "2026-10-10T12:00:00+00:00",
            "evidence_excerpt": "bad",
            "language": "en",
        }]
        (temp_fixtures / "signals.json").write_text(json.dumps(invalid_signals))
        (temp_fixtures / "update_cycles.json").write_text("[]")

        try:
            load_fixtures(conn, temp_fixtures)
            pytest.fail("Should have raised ValidationError")
        except Exception:
            pass  # Expected

        # Verify no partial data was committed
        count = conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
        assert count == 0, "Rollback should have prevented partial insert"

        conn.close()
        db_path.unlink(missing_ok=True)

    # =========================== UPDATE CYCLE ===========================

    def test_update_cycle_crud(self, empty_repo):
        """Create and read update cycle."""
        # First create the region
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        empty_repo.create_region(region)

        cycle = UpdateCycle(
            id="cycle-1",
            region_id="test-region",
            signal_ids=["sig-1", "sig-2"],
            proposed_changes={"festival": Decimal("2")},
            status=UpdateCycleStatus.evaluating,
            created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        # Direct insert since no create method in repo yet
        empty_repo.conn.execute(
            """
            INSERT INTO update_cycles (id, region_id, signal_ids, proposed_changes, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                cycle.id,
                cycle.region_id,
                _json_dumps(cycle.signal_ids),
                _json_dumps(cycle.proposed_changes),
                cycle.status.value,
                cycle.created_at.isoformat(),
            ),
        )
        empty_repo.conn.commit()

        row = empty_repo.conn.execute(
            "SELECT * FROM update_cycles WHERE id = ?", ("cycle-1",)
        ).fetchone()
        assert row is not None
        data = dict(row)
        data["signal_ids"] = _json_loads(data["signal_ids"])
        data["proposed_changes"] = {k: Decimal(str(v)) for k, v in _json_loads(data["proposed_changes"]).items()}
        data["status"] = UpdateCycleStatus(data["status"])
        assert data["region_id"] == "test-region"
        assert data["proposed_changes"]["festival"] == Decimal("2")


# Import _json_dumps for the test
from adaptive_commerce.persistence.database import _json_dumps, _json_loads
