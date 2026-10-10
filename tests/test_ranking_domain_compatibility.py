"""Verify ranking against the shared domain models and seeded SQLite catalog."""

import json
from datetime import timedelta
from pathlib import Path

import pytest

from adaptive_commerce.domain import EventWindow, Product, RegionProfile
from adaptive_commerce.persistence import connect, initialize, load_fixtures
from adaptive_commerce.recommender import (
    RankingEvent, RankingPolicy, RankingProduct, RankingProfile, rank_products,
)


@pytest.fixture
def seeded_ranking_inputs():
    """Read real seed rows through domain validation and ranking projections."""
    conn = connect(":memory:")
    try:
        initialize(conn)
        load_fixtures(conn, Path(__file__).resolve().parents[1] / "data/fixtures")

        def read_models(table, model, json_fields):
            result = []
            for row in conn.execute(f"SELECT * FROM {table}"):
                payload = dict(row)
                for field in json_fields:
                    payload[field] = json.loads(payload[field])
                result.append(model.model_validate(payload))
            return result

        products = [RankingProduct.model_validate(item) for item in
                    read_models("products", Product, ["tags"])]
        profiles = {item.region_id: RankingProfile.model_validate(item) for item in
                    read_models("region_profiles", RegionProfile, ["weights"])}
        events = [RankingEvent.model_validate(item) for item in
                  read_models("event_windows", EventWindow, ["boosts"])]
        yield products, profiles, events
    finally:
        conn.close()


def test_seeded_festival_changes_cedar_and_expires_without_affecting_aspen(
    seeded_ranking_inputs,
):
    products, profiles, events = seeded_ranking_inputs
    event = events[0]
    before_time = event.starts_at - timedelta(seconds=1)
    baseline = ["bottle", "boots", "tent", "pack", "poles"]

    def ids(region_id, now):
        return [item.product_id for item in rank_products(
            products, profiles[region_id], now=now, events=events,
        )]

    assert ids("cedar", before_time) == baseline
    assert ids("cedar", event.starts_at) == [
        "lights", "bottle", "charger", "boots", "pouch",
    ]
    assert ids("cedar", event.expires_at) == baseline
    assert ids("aspen", before_time) == baseline
    assert ids("aspen", event.starts_at) == baseline
    assert ids("aspen", event.expires_at) == baseline


def test_shared_catalog_exclusions_override_festival_boost(seeded_ranking_inputs):
    products, profiles, events = seeded_ranking_inputs
    unavailable = [RankingProduct.model_validate(
        item.model_dump() | {"in_stock": False}
    ) if item.id == "charger" else item for item in products]
    ranked = rank_products(
        unavailable, profiles["cedar"], now=events[0].starts_at, events=events,
        policy=RankingPolicy(excluded_product_ids={"lights"}), limit=20,
    )
    assert {item.product_id for item in ranked} == {
        "bottle", "boots", "tent", "pack", "poles", "pouch",
    }
    assert all(item.score == item.breakdown.total for item in ranked)
