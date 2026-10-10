"""Candidate lifecycle and read-only recommendation comparison tests."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from adaptive_commerce.application.dashboard import bootstrap_database, list_regions, submit_candidate, CandidateRequest
from adaptive_commerce.application.regions import approve_candidate, compare_recommendations, reject_candidate
from adaptive_commerce.domain import RecommendationVersion, RegionState, VersionStatus
from adaptive_commerce.integrations.finance import FinanceInputs
from adaptive_commerce.persistence import Repository, connect


def test_candidate_approval_and_rejection_are_persisted(tmp_path):
    path = tmp_path / "lifecycle.db"
    bootstrap_database(path)
    approved = submit_candidate(path, CandidateRequest(name="Approved Bay", assumptions=FinanceInputs()))
    rejected = submit_candidate(path, CandidateRequest(name="Rejected Bay", assumptions=FinanceInputs()))

    assert approve_candidate(path, approved.region.id)
    assert not approve_candidate(path, approved.region.id)
    assert reject_candidate(path, rejected.region.id)
    assert not reject_candidate(path, rejected.region.id)

    states = {region.id: region.state for region in list_regions(path)}
    assert states[approved.region.id] is RegionState.active
    assert states[rejected.region.id] is RegionState.rejected
    with connect(path) as conn:
        profile = conn.execute(
            "SELECT active_version_id FROM region_profiles WHERE region_id = ?",
            (approved.region.id,),
        ).fetchone()
        assert profile is not None
        assert conn.execute(
            "SELECT COUNT(*) FROM recommendation_versions WHERE region_id = ?",
            (rejected.region.id,),
        ).fetchone()[0] == 0


def test_comparison_checks_region_uses_one_time_and_does_not_write(tmp_path):
    path = tmp_path / "comparison.db"
    bootstrap_database(path)
    candidate = RecommendationVersion(
        id="cedar-candidate", region_id="cedar", parent_id="v-cedar-1",
        weights={"festival": Decimal("8")}, status=VersionStatus.candidate,
        created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    with connect(path) as conn:
        Repository(conn).create_recommendation_version(candidate)
        conn.commit()
        before_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("regions", "recommendation_versions", "region_profiles", "recommendation_items")
        }

    at = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    result = compare_recommendations(path, "cedar", "v-cedar-1", candidate.id, evaluated_at=at)
    assert result.evaluated_at == at
    assert len(result.before) == len(result.after) == 5
    assert [item.product_id for item in result.before] != [item.product_id for item in result.after]
    with connect(path) as conn:
        after_counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in before_counts
        }
    assert after_counts == before_counts

    with pytest.raises(ValueError, match="belong to the selected region"):
        compare_recommendations(path, "aspen", "v-cedar-1", candidate.id, evaluated_at=at)
    with pytest.raises(ValueError, match="timezone-aware"):
        compare_recommendations(
            path, "cedar", "v-cedar-1", candidate.id,
            evaluated_at=datetime(2026, 10, 10),
        )
