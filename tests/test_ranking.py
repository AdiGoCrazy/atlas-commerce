"""Synthetic Sprint 1 acceptance fixtures and ranker failure cases."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from itertools import permutations

import pytest
from pydantic import ValidationError

from adaptive_commerce.recommender import rank_products
from adaptive_commerce.recommender.contracts import (
    RankingEvent, RankingPolicy, RankingProduct, RankingProfile,
)

NOW = datetime(2026, 10, 10, 9, tzinfo=timezone.utc)


def product(product_id="boots", tags=("outdoor",), **overrides):
    values = dict(id=product_id, name=product_id, tags=tags, price="20", cost="10",
                  in_stock=True, eligible=True, base_popularity="1")
    return RankingProduct(**(values | overrides))


def profile(**overrides):
    return RankingProfile(**(dict(region_id="cedar", active_version_id="v1",
                                 weights={"outdoor": "3"}) | overrides))


def event(**overrides):
    return RankingEvent(**(dict(id="festival", region_id="cedar",
                               starts_at=NOW, expires_at=NOW + timedelta(hours=1),
                               boosts={"festival": "2", "electronics": "2"}) | overrides))


@pytest.fixture
def festival_catalog():
    return [product(name) for name in ("boots", "tent", "pack", "bottle", "poles")] + [
        product("charger", ("electronics",)),
        product("pouch", ("festival",)),
        product("lights", ("festival", "electronics")),
    ]


def test_recommendation_ranking_changes_after_signal(festival_catalog):
    # A synthetic festival signal's proposed profile is supplied directly.
    # Signal extraction and bounded proposals belong to Sprint 2.
    before_profile = profile()
    after_profile = profile(active_version_id="v2", weights={
        "outdoor": "3", "festival": "4", "electronics": "4",
    })
    before = rank_products(festival_catalog, before_profile, now=NOW)
    after = rank_products(festival_catalog, after_profile, now=NOW)
    assert [item.product_id for item in before] == ["boots", "bottle", "pack", "poles", "tent"]
    assert [item.product_id for item in after] == ["lights", "charger", "pouch", "boots", "bottle"]
    assert before_profile.weights == {"outdoor": Decimal("3")}
    assert rank_products(festival_catalog, before_profile, now=NOW) == before


def test_ties_and_input_order_are_deterministic():
    catalog = [product(name) for name in ("c", "a", "b")]
    for ordering in permutations(catalog):
        assert [item.product_id for item in rank_products(ordering, profile(), now=NOW)] == ["a", "b", "c"]


def test_policy_and_stock_exclusions_precede_scoring():
    catalog = [
        product("allowed"), product("sold_out", in_stock=False, base_popularity="999"),
        product("ineligible", eligible=False, base_popularity="999"),
        product("regional", base_popularity="999"),
        product("campaign", ("restricted",), base_popularity="999"),
    ]
    policy = RankingPolicy(excluded_product_ids={"regional"}, excluded_tags={"restricted"})
    assert [item.product_id for item in rank_products(catalog, profile(), now=NOW, policy=policy)] == ["allowed"]


def test_score_breakdown_explains_all_five_components():
    result = rank_products(
        [product(tags=("outdoor", "festival", "festival"))], profile(), now=NOW,
        events=[event()], policy=RankingPolicy(product_penalties={"boots": "0.25"}),
    )[0]
    assert result.score == Decimal("6.25")
    assert result.breakdown.total == result.score
    assert result.breakdown.interest_by_tag == {"festival": 0, "outdoor": 3}
    assert result.breakdown.event_by_id == {"festival": 2}
    assert result.breakdown.margin_bonus == Decimal("0.5")
    assert type(result).model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("offset, region, boost", [(-1, "cedar", 0), (0, "cedar", 2),
                                                          (3599, "cedar", 2), (3600, "cedar", 0),
                                                          (1, "other", 0)])
def test_event_window_and_region_isolation(offset, region, boost):
    item = rank_products([product(tags=("festival",))], profile(),
                         now=NOW + timedelta(seconds=offset), events=[event(region_id=region)])[0]
    assert item.breakdown.temporary_event_boost == boost


def test_loss_making_product_gets_negative_margin_bonus():
    item = rank_products([product(cost="30")], profile(), now=NOW)[0]
    assert item.breakdown.margin_bonus == Decimal("-0.5")


def test_empty_catalog_and_zero_limit():
    assert rank_products([], profile(), now=NOW) == []
    assert rank_products([product()], profile(), now=NOW, limit=0) == []


@pytest.mark.parametrize("limit", [-1, True, 1.5])
def test_invalid_limit_rejected(limit):
    with pytest.raises(ValueError, match="limit"):
        rank_products([product()], profile(), now=NOW, limit=limit)


def test_ambiguous_inputs_rejected():
    with pytest.raises(ValueError, match="timezone"):
        rank_products([product()], profile(), now=NOW.replace(tzinfo=None))
    with pytest.raises(ValueError, match="duplicate product"):
        rank_products([product(), product()], profile(), now=NOW)
    with pytest.raises(ValueError, match="duplicate event"):
        rank_products([product()], profile(), now=NOW, events=[event(), event()])


@pytest.mark.parametrize("overrides", [{"price": "0"}, {"price": "NaN"}, {"cost": "-1"},
                                        {"base_popularity": "Infinity"}, {"unknown": True}])
def test_invalid_product_boundary(overrides):
    with pytest.raises(ValidationError):
        product(**overrides)


def test_invalid_weights_and_event_windows():
    with pytest.raises(ValidationError):
        profile(weights={"outdoor": "NaN"})
    with pytest.raises(ValidationError):
        event(expires_at=NOW)
    with pytest.raises(ValidationError):
        event(starts_at=NOW.replace(tzinfo=None))


def test_decimal_precision_and_structured_context(caplog):
    with caplog.at_level("INFO"):
        expected = rank_products([product(price="3", cost="1")], profile(), now=NOW, cycle_id="cycle-1")
    with localcontext() as context:
        context.prec = 6
        assert rank_products([product(price="3", cost="1")], profile(), now=NOW) == expected
    record = next(record for record in caplog.records if record.message == "ranking_completed")
    assert (record.region_id, record.version_id, record.cycle_id) == ("cedar", "v1", "cycle-1")
