"""Sprint 2 acceptance and failure tests for Ad-owned signal updates."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from adaptive_commerce.agents import (
    FixtureEvent,
    FixtureSourceRecord,
    normalize_fixture_record,
    normalize_fixture_records,
)
from adaptive_commerce.application import RejectionReason, UpdatePolicy, propose_profile_update
from adaptive_commerce.domain import RegionProfile
from adaptive_commerce.recommender import (
    RankingEvent,
    RankingProduct,
    RankingProfile,
    rank_products,
)

NOW = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)


def source_record(**overrides) -> FixtureSourceRecord:
    values = {
        "id": "src-festival-1",
        "region_id": "cedar",
        "source_type": "event",
        "observed_at": NOW - timedelta(hours=1),
        "content": " Cedar Fall Festival expects strong portable electronics demand. ",
        "topics": ["Festival", "Electronics"],
        "confidence": "0.92",
        "event": {
            "id": "event-festival-1",
            "starts_at": NOW - timedelta(hours=2),
            "expires_at": NOW + timedelta(hours=4),
        },
    }
    return FixtureSourceRecord.model_validate(values | overrides)


def profile(**overrides) -> RegionProfile:
    values = {
        "region_id": "cedar",
        "active_version_id": "v1",
        "weights": {"outdoor": "3"},
        "max_weight_delta": "5",
    }
    return RegionProfile.model_validate(values | overrides)


def proposal(record: FixtureSourceRecord, **overrides):
    return propose_profile_update(
        profile(), [normalize_fixture_record(record)], now=NOW,
        candidate_version_id="v2", **overrides,
    )


def test_fixture_record_becomes_validated_shared_signal():
    normalized = normalize_fixture_record(source_record())
    assert normalized.signal.id == "signal:src-festival-1"
    assert normalized.signal.source_id == "src-festival-1"
    assert normalized.signal.topics == ["festival", "electronics"]
    assert normalized.signal.normalized_text == (
        "Cedar Fall Festival expects strong portable electronics demand."
    )
    assert normalized.topic_impacts == {"festival": 1, "electronics": 1}


def test_normalization_order_and_source_idempotency_are_explicit():
    second = source_record(id="src-a", event=None, source_type="news")
    first = source_record(id="src-z", event=None, source_type="forum")
    result = normalize_fixture_records([first, second])
    assert [item.signal.source_id for item in result] == ["src-a", "src-z"]
    with pytest.raises(ValueError, match="duplicate source"):
        normalize_fixture_records([first, first])


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"confidence": "1.1"}, "less than or equal"),
        ({"topics": ["Festival", "festival"]}, "unique"),
        ({"source_type": "news"}, "event metadata"),
        ({"event": {"id": "bad", "starts_at": NOW, "expires_at": NOW}}, "after"),
    ],
)
def test_invalid_source_boundaries_are_rejected(overrides, message):
    with pytest.raises(ValidationError, match=message):
        source_record(**overrides)


def test_low_confidence_signal_does_not_change_weights():
    result = proposal(source_record(confidence="0.69"))
    assert not result.changed
    assert result.changes == {}
    assert result.event_windows == []
    assert result.rejections[0].reason is RejectionReason.low_confidence


def test_stale_signal_does_not_change_weights():
    result = proposal(source_record(observed_at=NOW - timedelta(hours=73)))
    assert not result.changed
    assert result.rejections[0].reason is RejectionReason.stale


def test_update_is_bounded_and_does_not_mutate_active_profile():
    active = profile(max_weight_delta="2")
    normalized = normalize_fixture_record(source_record())
    result = propose_profile_update(
        active, [normalized], now=NOW, candidate_version_id="v2",
    )
    assert result.changes == {"festival": Decimal("2"), "electronics": Decimal("2")}
    assert result.candidate_profile is not None
    assert result.candidate_profile.weights == {
        "outdoor": Decimal("3"), "festival": Decimal("2"),
        "electronics": Decimal("2"),
    }
    assert active.weights == {"outdoor": Decimal("3")}
    assert all(abs(delta) <= active.max_weight_delta for delta in result.changes.values())
    assert all(
        abs(boost) <= active.max_weight_delta
        for boost in result.event_windows[0].boosts.values()
    )


def test_topic_limit_and_negative_direction_are_deterministic():
    record = source_record(
        topics=["zeta", "alpha", "beta"],
        topic_impacts={"zeta": "-1", "alpha": "1", "beta": "0.5"},
        event=None,
        source_type="social",
    )
    result = proposal(record, policy=UpdatePolicy(max_topics_per_cycle=2))
    assert result.changes == {"alpha": Decimal("4.60"), "zeta": Decimal("-4.60")}


def test_duplicate_source_contributes_once():
    normalized = normalize_fixture_record(source_record(event=None, source_type="news"))
    result = propose_profile_update(
        profile(), [normalized, normalized], now=NOW, candidate_version_id="v2",
    )
    assert result.changes["festival"] == Decimal("4.60")
    assert result.accepted_signal_ids == [normalized.signal.id]
    assert result.rejections[0].reason is RejectionReason.duplicate_source


@pytest.mark.parametrize(
    "record, reason",
    [
        (source_record(region_id="other"), RejectionReason.region_mismatch),
        (source_record(observed_at=NOW + timedelta(minutes=6)), RejectionReason.future_dated),
        (source_record(content="too short"), RejectionReason.insufficient_evidence),
        (source_record(topics=["religion"]), RejectionReason.blocked_topic),
    ],
)
def test_policy_failure_paths_do_not_create_candidates(record, reason):
    result = proposal(record)
    assert result.candidate_profile is None
    assert result.rejections[0].reason is reason


def test_event_boost_is_absent_at_expiry():
    result = proposal(source_record())
    window = result.event_windows[0]
    ranking_event = RankingEvent.model_validate(window)
    ranking_profile = RankingProfile.model_validate(result.candidate_profile)
    product = RankingProduct(
        id="charger", name="Charger", tags={"electronics"},
        price="20", cost="10", in_stock=True, eligible=True,
    )
    before_expiry = rank_products(
        [product], ranking_profile, now=window.expires_at - timedelta(microseconds=1),
        events=[ranking_event],
    )[0]
    at_expiry = rank_products(
        [product], ranking_profile, now=window.expires_at, events=[ranking_event],
    )[0]
    assert before_expiry.breakdown.temporary_event_boost == Decimal("4.60")
    assert at_expiry.breakdown.temporary_event_boost == 0


def test_fixture_signal_visibly_changes_the_expected_top_five():
    active = profile()
    result = propose_profile_update(
        active, [normalize_fixture_record(source_record())], now=NOW,
        candidate_version_id="v2",
    )
    assert result.candidate_profile is not None
    products = [
        RankingProduct(
            id=name, name=name, tags={"outdoor"}, price="20", cost="10",
            in_stock=True, eligible=True, base_popularity="1",
        )
        for name in ("boots", "tent", "pack", "bottle", "poles")
    ] + [
        RankingProduct(
            id="charger", name="charger", tags={"electronics"}, price="20", cost="10",
            in_stock=True, eligible=True, base_popularity="1",
        ),
        RankingProduct(
            id="pouch", name="pouch", tags={"festival"}, price="20", cost="10",
            in_stock=True, eligible=True, base_popularity="1",
        ),
        RankingProduct(
            id="lights", name="lights", tags={"festival", "electronics"},
            price="20", cost="10", in_stock=True, eligible=True, base_popularity="1",
        ),
    ]
    before = rank_products(products, RankingProfile.model_validate(active), now=NOW)
    after = rank_products(
        products, RankingProfile.model_validate(result.candidate_profile), now=NOW,
    )
    assert [item.product_id for item in before] == ["boots", "bottle", "pack", "poles", "tent"]
    assert [item.product_id for item in after] == ["lights", "charger", "pouch", "boots", "bottle"]


def test_already_expired_event_is_not_proposed():
    expired = FixtureEvent(
        id="expired", starts_at=NOW - timedelta(hours=2), expires_at=NOW,
    )
    result = proposal(source_record(event=expired))
    assert not result.changed
    assert result.event_windows == []
    assert result.rejections[0].reason is RejectionReason.stale


def test_timezone_aware_cycle_time_is_required():
    with pytest.raises(ValueError, match="timezone-aware"):
        propose_profile_update(
            profile(), [normalize_fixture_record(source_record())],
            now=NOW.replace(tzinfo=None), candidate_version_id="v2",
        )


def test_structured_proposal_context(caplog):
    with caplog.at_level("INFO"):
        result = proposal(source_record(), cycle_id="cycle-2")
    record = next(item for item in caplog.records if item.message == "profile_update_proposed")
    assert result.changed
    assert (record.region_id, record.version_id, record.cycle_id) == ("cedar", "v2", "cycle-2")
