"""Candidate lifecycle actions and read-only recommendation comparison."""

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import AwareDatetime, BaseModel

from adaptive_commerce.domain import RecommendationVersion, VersionStatus
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.recommender import (
    RankedProduct, RankingPolicy, RankingProduct, RankingProfile, rank_products,
)


class RecommendationComparison(BaseModel):
    """Ranked results for two versions evaluated against identical inputs."""

    region_id: str
    before_version_id: str
    after_version_id: str
    evaluated_at: AwareDatetime
    before: list[RankedProduct]
    after: list[RankedProduct]


def approve_candidate(path: Path, region_id: str) -> bool:
    """Approve a candidate and create its initial neutral recommendation version."""
    now = datetime.now(timezone.utc)
    version_id = str(uuid4())
    with closing(connect(path)) as conn, conn:
        region = conn.execute(
            "SELECT state FROM regions WHERE id = ?", (region_id,),
        ).fetchone()
        if region is None or region["state"] != "candidate":
            return False
        version = RecommendationVersion(
            # shortcut: start new regions with neutral weights; seed richer priors when onboarding data exists.
            id=version_id, region_id=region_id, parent_id=None, weights={},
            status=VersionStatus.active, created_at=now,
        )
        Repository(conn).create_recommendation_version(version)
        conn.execute(
            "INSERT INTO region_profiles (region_id, weights, active_version_id, max_weight_delta) "
            "VALUES (?, ?, ?, ?)",
            (region_id, "{}", version_id, "5"),
        )
        updated = conn.execute(
            "UPDATE regions SET state = 'active', updated_at = ? WHERE id = ? AND state = 'candidate'",
            (now.isoformat(), region_id),
        )
        if updated.rowcount != 1:
            raise RuntimeError("candidate state changed during approval")
    return True


def reject_candidate(path: Path, region_id: str) -> bool:
    """Reject a candidate without creating a profile or recommendation version."""
    with closing(connect(path)) as conn, conn:
        cur = conn.execute(
            "UPDATE regions SET state = 'rejected', updated_at = ? "
            "WHERE id = ? AND state = 'candidate'",
            (datetime.now(timezone.utc).isoformat(), region_id),
        )
    return cur.rowcount > 0


def recommendation_versions(path: Path, region_id: str) -> list[RecommendationVersion]:
    """List a region's saved versions for comparison controls."""
    with closing(connect(path)) as conn:
        return Repository(conn).get_version_lineage(region_id)


def compare_recommendations(
    path: Path,
    region_id: str,
    before_version_id: str,
    after_version_id: str,
    *,
    evaluated_at: datetime | None = None,
) -> RecommendationComparison:
    """Rank two same-region versions over the same eligible products and time."""
    when = evaluated_at or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    with closing(connect(path)) as conn:
        repository = Repository(conn)
        before_version = repository.get_recommendation_version(before_version_id)
        after_version = repository.get_recommendation_version(after_version_id)
        if (
            before_version is None or after_version is None
            or before_version.region_id != region_id or after_version.region_id != region_id
        ):
            raise ValueError("both recommendation versions must belong to the selected region")
        products = [
            RankingProduct.model_validate(product)
            for product in repository.get_products_for_ranking(region_id)
        ]
        policy = RankingPolicy.model_validate(repository.get_ranking_policy(region_id))

    before_profile = RankingProfile(
        region_id=region_id, active_version_id=before_version.id, weights=before_version.weights,
    )
    after_profile = RankingProfile(
        region_id=region_id, active_version_id=after_version.id, weights=after_version.weights,
    )
    return RecommendationComparison(
        region_id=region_id,
        before_version_id=before_version.id,
        after_version_id=after_version.id,
        evaluated_at=when,
        before=rank_products(products, before_profile, now=when, policy=policy),
        after=rank_products(products, after_profile, now=when, policy=policy),
    )
