"""Tests for the read-only application-layer ranking comparison service."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from adaptive_commerce.application.dashboard import bootstrap_database
from adaptive_commerce.application.comparison import compare_recommendations
from adaptive_commerce.domain import RecommendationVersion, VersionStatus
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.recommender import ranking


def test_comparison_ranks_both_same_region_versions_once_at_same_time_without_writes(
    tmp_path, monkeypatch,
):
    path = tmp_path / "compare.db"
    bootstrap_database(path)
    candidate = RecommendationVersion(
        id="cedar-candidate", region_id="cedar", parent_id="v-cedar-1",
        weights={"festival": Decimal("8")}, status=VersionStatus.candidate,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    with connect(path) as conn:
        Repository(conn).create_recommendation_version(candidate)

    timestamp = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    calls = []
    original_rank = ranking.rank_products

    def capture_call(products, profile, *, now, policy=None, **kwargs):
        calls.append((profile.region_id, profile.active_version_id, now, policy, products))
        return original_rank(products, profile, now=now, policy=policy, **kwargs)

    monkeypatch.setattr("adaptive_commerce.application.comparison.rank_products", capture_call)
    with connect(path) as conn:
        before_count = conn.execute("SELECT COUNT(*) FROM recommendation_versions").fetchone()[0]

    result = compare_recommendations(
        path, "cedar", "v-cedar-1", candidate.id, evaluated_at=timestamp,
    )

    assert result.evaluated_at == timestamp
    assert len(calls) == 2
    assert [call[1] for call in calls] == ["v-cedar-1", "cedar-candidate"]
    assert all(call[0] == "cedar" and call[2] == timestamp for call in calls)
    assert calls[0][4] == calls[1][4]
    assert len(result.before) == len(result.after) == 5
    assert [item.product_id for item in result.before] != [item.product_id for item in result.after]
    with connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recommendation_versions").fetchone()[0] == before_count


def test_comparison_rejects_wrong_region_and_naive_time(tmp_path):
    path = tmp_path / "compare.db"
    bootstrap_database(path)
    with pytest.raises(ValueError, match="belong to the selected region"):
        compare_recommendations(path, "aspen", "v-cedar-1", "v-aspen-1")
    with pytest.raises(ValueError, match="timezone-aware"):
        compare_recommendations(
            path, "cedar", "v-cedar-1", "v-cedar-1",
            evaluated_at=datetime(2026, 10, 10),
        )
