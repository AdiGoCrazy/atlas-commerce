"""Tests for domain model validation and round-trip."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

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


class TestRegionModel:
    def test_valid_region_round_trip(self):
        region = Region(
            id="test-region",
            name="Test Region",
            state=RegionState.active,
            locale="en_US",
            available_source_types=[SignalSourceType.news, SignalSourceType.event],
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            updated_at=datetime(2026, 1, 15, tzinfo=timezone.utc),
        )
        data = region.model_dump()
        reloaded = Region.model_validate(data)
        assert reloaded == region

    def test_invalid_state_rejected(self):
        with pytest.raises(ValidationError) as exc:
            Region(
                id="r1",
                name="Test",
                state="invalid_state",
                locale="en_US",
                created_at=datetime.now(timezone.utc),
            )
        assert "state" in str(exc.value)

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            Region(
                id="r1",
                name="Test",
                state=RegionState.active,
                locale="en_US",
                created_at=datetime.now(timezone.utc),
                unknown_field="oops",
            )
        assert "extra" in str(exc.value).lower() or "unknown" in str(exc.value).lower()


class TestProductModel:
    def test_valid_product_round_trip(self):
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
        data = product.model_dump()
        reloaded = Product.model_validate(data)
        assert reloaded == product

    def test_price_must_be_positive(self):
        with pytest.raises(ValidationError) as exc:
            Product(
                id="p1",
                name="Test",
                price=Decimal("0"),
                cost=Decimal("10"),
            )
        assert "price" in str(exc.value)

    def test_negative_price_rejected(self):
        with pytest.raises(ValidationError) as exc:
            Product(
                id="p1",
                name="Test",
                price=Decimal("-10"),
                cost=Decimal("5"),
            )
        assert "price" in str(exc.value)

    def test_cost_must_be_nonnegative(self):
        with pytest.raises(ValidationError) as exc:
            Product(
                id="p1",
                name="Test",
                price=Decimal("50"),
                cost=Decimal("-1"),
            )
        assert "cost" in str(exc.value)

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            Product(
                id="p1",
                name="Test",
                price=Decimal("10"),
                cost=Decimal("5"),
                unknown_field="oops",
            )
        assert "extra" in str(exc.value).lower() or "unknown" in str(exc.value).lower()


class TestSignalModel:
    def test_valid_signal_round_trip(self):
        signal = Signal(
            id="sig-1",
            region_id="r1",
            source_id="src-1",
            source_type=SignalSourceType.news,
            topics=["festival", "outdoor"],
            confidence=Decimal("0.85"),
            observed_at=datetime(2026, 10, 10, 12, tzinfo=timezone.utc),
            evidence_excerpt="Festival announced",
            language="en",
        )
        data = signal.model_dump()
        reloaded = Signal.model_validate(data)
        assert reloaded == signal

    def test_confidence_bounds(self):
        # Valid boundaries
        Signal(
            id="s1",
            region_id="r1",
            source_id="src1",
            source_type=SignalSourceType.news,
            confidence=Decimal("0"),
            observed_at=datetime.now(timezone.utc),
            evidence_excerpt="test",
        )
        Signal(
            id="s2",
            region_id="r1",
            source_id="src2",
            source_type=SignalSourceType.news,
            confidence=Decimal("1"),
            observed_at=datetime.now(timezone.utc),
            evidence_excerpt="test",
        )

        # Invalid: below 0
        with pytest.raises(ValidationError) as exc:
            Signal(
                id="s3",
                region_id="r1",
                source_id="src3",
                source_type=SignalSourceType.news,
                confidence=Decimal("-0.1"),
                observed_at=datetime.now(timezone.utc),
                evidence_excerpt="test",
            )
        assert "confidence" in str(exc.value)

        # Invalid: above 1
        with pytest.raises(ValidationError) as exc:
            Signal(
                id="s4",
                region_id="r1",
                source_id="src4",
                source_type=SignalSourceType.news,
                confidence=Decimal("1.1"),
                observed_at=datetime.now(timezone.utc),
                evidence_excerpt="test",
            )
        assert "confidence" in str(exc.value)


class TestRegionProfileModel:
    def test_valid_profile_round_trip(self):
        profile = RegionProfile(
            region_id="r1",
            weights={"outdoor": Decimal("3"), "festival": Decimal("2")},
            active_version_id="v1",
            max_weight_delta=Decimal("5"),
        )
        data = profile.model_dump()
        reloaded = RegionProfile.model_validate(data)
        assert reloaded == profile


class TestRecommendationVersionModel:
    def test_valid_version_round_trip(self):
        version = RecommendationVersion(
            id="v1",
            region_id="r1",
            parent_id=None,
            weights={"outdoor": Decimal("3")},
            status=VersionStatus.active,
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        data = version.model_dump()
        reloaded = RecommendationVersion.model_validate(data)
        assert reloaded == version


class TestFinanceAssessmentModel:
    def test_valid_assessment_round_trip(self):
        assessment = FinanceAssessment(
            id="fa1",
            region_id="r1",
            viability_score=Decimal("0.75"),
            projected_profit=Decimal("100000"),
            decision=FinanceDecision.approve,
            assumptions={"traffic": "1000"},
            cfo_reference="cfo://model/scenario/1",
            created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        data = assessment.model_dump()
        reloaded = FinanceAssessment.model_validate(data)
        assert reloaded == assessment

    def test_viability_score_bounds(self):
        FinanceAssessment(
            id="fa1",
            region_id="r1",
            viability_score=Decimal("0"),
            projected_profit=Decimal("0"),
            decision=FinanceDecision.reject,
            created_at=datetime.now(timezone.utc),
        )
        FinanceAssessment(
            id="fa2",
            region_id="r1",
            viability_score=Decimal("1"),
            projected_profit=Decimal("100"),
            decision=FinanceDecision.approve,
            created_at=datetime.now(timezone.utc),
        )

        with pytest.raises(ValidationError) as exc:
            FinanceAssessment(
                id="fa3",
                region_id="r1",
                viability_score=Decimal("-0.1"),
                projected_profit=Decimal("0"),
                decision=FinanceDecision.reject,
                created_at=datetime.now(timezone.utc),
            )
        assert "viability_score" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            FinanceAssessment(
                id="fa4",
                region_id="r1",
                viability_score=Decimal("1.1"),
                projected_profit=Decimal("0"),
                decision=FinanceDecision.approve,
                created_at=datetime.now(timezone.utc),
            )
        assert "viability_score" in str(exc.value)


class TestUpdateCycleModel:
    def test_valid_cycle_round_trip(self):
        cycle = UpdateCycle(
            id="cycle-1",
            region_id="r1",
            signal_ids=["sig-1", "sig-2"],
            proposed_changes={"festival": Decimal("2")},
            status=UpdateCycleStatus.evaluating,
            created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        )
        data = cycle.model_dump()
        reloaded = UpdateCycle.model_validate(data)
        assert reloaded == cycle


class TestEventWindowModel:
    def test_valid_event_round_trip(self):
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        event = EventWindow(
            id="evt-1",
            region_id="r1",
            starts_at=now,
            expires_at=datetime(2026, 10, 17, 12, tzinfo=timezone.utc),
            boosts={"festival": Decimal("4")},
        )
        data = event.model_dump()
        reloaded = EventWindow.model_validate(data)
        assert reloaded == event

    def test_reversed_window_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EventWindow(
                id="evt-bad",
                region_id="r1",
                starts_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
                expires_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
                boosts={},
            )
        assert "expires_at" in str(exc.value) or "window" in str(exc.value).lower()

    def test_equal_start_expires_rejected(self):
        now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
        with pytest.raises(ValidationError) as exc:
            EventWindow(
                id="evt-bad2",
                region_id="r1",
                starts_at=now,
                expires_at=now,
                boosts={},
            )
        assert "expires_at" in str(exc.value) or "window" in str(exc.value).lower()

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError) as exc:
            EventWindow(
                id="evt-1",
                region_id="r1",
                starts_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
                expires_at=datetime(2026, 10, 17, tzinfo=timezone.utc),
                boosts={},
                unknown_field="oops",
            )
        assert "extra" in str(exc.value).lower() or "unknown" in str(exc.value).lower()


class TestAllModelsRejectExtraFields:
    """Ensure all models have extra='forbid'."""

    @pytest.mark.parametrize("model_cls,valid_kwargs", [
        (Region, dict(id="r1", name="Test", state=RegionState.active, locale="en_US", created_at=datetime.now(timezone.utc))),
        (Product, dict(id="p1", name="Test", price=Decimal("10"), cost=Decimal("5"))),
        (Signal, dict(id="s1", region_id="r1", source_id="src1", source_type=SignalSourceType.news, confidence=Decimal("0.5"), observed_at=datetime.now(timezone.utc), evidence_excerpt="test")),
        (RegionProfile, dict(region_id="r1", active_version_id="v1")),
        (RecommendationVersion, dict(id="v1", region_id="r1", created_at=datetime.now(timezone.utc))),
        (FinanceAssessment, dict(id="fa1", region_id="r1", viability_score=Decimal("0.5"), projected_profit=Decimal("0"), decision=FinanceDecision.approve, created_at=datetime.now(timezone.utc))),
        (UpdateCycle, dict(id="c1", region_id="r1", created_at=datetime.now(timezone.utc))),
        (EventWindow, dict(id="e1", region_id="r1", starts_at=datetime.now(timezone.utc), expires_at=datetime(2026, 10, 17, tzinfo=timezone.utc))),
    ])
    def test_extra_fields_rejected(self, model_cls, valid_kwargs):
        with pytest.raises(ValidationError) as exc:
            model_cls(**valid_kwargs, unknown_field="oops")
        assert "extra" in str(exc.value).lower() or "unknown" in str(exc.value).lower()