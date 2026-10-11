"""Read-only comparison of two saved recommendation versions."""

from datetime import datetime, timezone
from pathlib import Path

from pydantic import AwareDatetime, BaseModel

from adaptive_commerce.domain.models import RecommendationVersion
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.recommender import (
    RankedProduct,
    RankingEvent,
    RankingPolicy,
    RankingProduct,
    RankingProfile,
    rank_products,
)


class RecommendationComparison(BaseModel):
    """Ranked results for two versions evaluated at the same instant."""

    region_id: str
    before_version_id: str
    after_version_id: str
    evaluated_at: AwareDatetime
    before: list[RankedProduct]
    after: list[RankedProduct]


def compare_recommendations(
    path: Path,
    region_id: str,
    before_version_id: str,
    after_version_id: str,
    *,
    evaluated_at: datetime | None = None,
) -> RecommendationComparison:
    """Rank two saved same-region versions without changing persistent state."""
    when = evaluated_at or datetime.now(timezone.utc)
    if when.tzinfo is None or when.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")

    with connect(path) as conn:
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
        events = [
            RankingEvent.model_validate(event)
            for event in repository.get_active_events(region_id, when.isoformat())
        ]

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
        before=rank_products(products, before_profile, now=when, policy=policy, events=events),
        after=rank_products(products, after_profile, now=when, policy=policy, events=events),
    )


def list_recommendation_versions(
    path: Path,
    region_id: str,
) -> list[RecommendationVersion]:
    """Return saved recommendation versions for one region."""
    with connect(path) as conn:
        repository = Repository(conn)
        return repository.get_version_lineage(region_id)
