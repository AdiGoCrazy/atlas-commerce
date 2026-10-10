"""Pure deterministic ranking with explicit time, policy, and trace context."""

import logging
from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Sequence

import neatlogs

from .contracts import (
    RankedProduct, RankingEvent, RankingPolicy, RankingProduct, RankingProfile,
    ScoreBreakdown,
)

logger = logging.getLogger(__name__)


def is_eligible(product: RankingProduct, policy: RankingPolicy) -> bool:
    """Apply hard exclusions before scoring, even for high-scoring products."""
    return (
        product.in_stock and product.eligible
        and product.id not in policy.excluded_product_ids
        and not product.tags.intersection(policy.excluded_tags)
    )


@neatlogs.span(kind="CHAIN", name="rank_products")
def rank_products(
    products: Sequence[RankingProduct],
    profile: RankingProfile,
    *,
    now: datetime,
    policy: RankingPolicy | None = None,
    events: Sequence[RankingEvent] = (),
    limit: int = 5,
    cycle_id: str | None = None,
) -> list[RankedProduct]:
    """Return eligible products ordered by descending score, then product ID.

    Interest and event terms sum unique product-tag weights. Margin bonus is
    ``margin_weight * (price - cost) / price``; losses produce a negative bonus.
    No input is mutated, and this function never activates or persists a version.
    The application initializes neatlogs once; uninitialized SDK usage is local.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    if len({p.id for p in products}) != len(products):
        raise ValueError("duplicate product IDs")
    if len({e.id for e in events}) != len(events):
        raise ValueError("duplicate event IDs")
    policy = policy if policy is not None else RankingPolicy()
    active_events = sorted(
        (event for event in events if event.is_active(now, profile.region_id)),
        key=lambda event: event.id,
    )
    scored: list[tuple[str, ScoreBreakdown]] = []
    # Fix precision locally so a caller's Decimal settings cannot change ordering.
    with localcontext(Context(prec=28)):
        for product in products:
            if not is_eligible(product, policy):
                continue
            interests = {
                tag: profile.weights.get(tag, Decimal("0"))
                for tag in sorted(product.tags)
            }
            event_scores = {
                event.id: sum(
                    (event.boosts.get(tag, Decimal("0")) for tag in sorted(product.tags)),
                    Decimal("0"),
                )
                for event in active_events
            }
            breakdown = ScoreBreakdown(
                base_popularity=product.base_popularity,
                regional_interest_match=sum(interests.values(), Decimal("0")),
                temporary_event_boost=sum(event_scores.values(), Decimal("0")),
                margin_bonus=policy.margin_weight * (product.price - product.cost) / product.price,
                policy_penalty=policy.product_penalties.get(product.id, Decimal("0")),
                interest_by_tag=interests,
                event_by_id=event_scores,
            )
            scored.append((product.id, breakdown))
        scored.sort(key=lambda item: (-item[1].total, item[0]))
        result = [
            RankedProduct(product_id=product_id, rank=index, score=breakdown.total, breakdown=breakdown)
            for index, (product_id, breakdown) in enumerate(scored[:limit], start=1)
        ]
    logger.info("ranking_completed", extra={
        "region_id": profile.region_id, "version_id": profile.active_version_id,
        "cycle_id": cycle_id, "result_status": "success",
        "catalog_count": len(products), "eligible_count": len(scored),
        "returned_count": len(result),
    })
    return result
