"""Ranking boundary contracts until shared domain models are available.

Domain objects can be supplied through ``model_validate(..., from_attributes=True)``.
These are ranking projections, not replacements for the persistence models.
"""

from datetime import datetime
from decimal import Context, Decimal, localcontext
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Number = Annotated[Decimal, Field(allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]
Identifier = Annotated[str, Field(min_length=1)]


class RankingModel(BaseModel):
    """Validate ranking payloads without silently accepting misspelled fields."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)


class RankingProduct(RankingModel):
    """Catalog fields used by the scorer; eligibility is explicitly required."""

    id: Identifier
    name: Identifier
    tags: frozenset[Identifier]
    price: Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
    cost: Nonnegative
    in_stock: bool
    eligible: bool
    base_popularity: Nonnegative = Decimal("0")


class RankingProfile(RankingModel):
    """A regional weight snapshot with explicit recommendation version context."""

    region_id: Identifier
    active_version_id: Identifier
    weights: dict[Identifier, Number]


class RankingPolicy(RankingModel):
    """Explicit product/tag exclusions and optional soft score penalties."""

    excluded_product_ids: frozenset[Identifier] = frozenset()
    excluded_tags: frozenset[Identifier] = frozenset()
    product_penalties: dict[Identifier, Nonnegative] = Field(default_factory=dict)
    margin_weight: Nonnegative = Decimal("1")


class RankingEvent(RankingModel):
    """Regional event active in the half-open interval [starts_at, expires_at)."""

    id: Identifier
    region_id: Identifier
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    boosts: dict[Identifier, Number]

    @model_validator(mode="after")
    def validate_window(self) -> "RankingEvent":
        """Reject empty or reversed event windows."""
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        return self

    def is_active(self, now: datetime, region_id: str) -> bool:
        """Return whether the event applies to this region at the given time."""
        return self.region_id == region_id and self.starts_at <= now < self.expires_at


class ScoreBreakdown(RankingModel):
    """Additive score components and per-tag evidence for a recommendation."""

    base_popularity: Number
    regional_interest_match: Number
    temporary_event_boost: Number
    margin_bonus: Number
    policy_penalty: Nonnegative
    interest_by_tag: dict[str, Number]
    event_by_id: dict[str, Number]

    @property
    def total(self) -> Decimal:
        """Reconstruct the score from its five components."""
        with localcontext(Context(prec=28)):
            return (
                self.base_popularity + self.regional_interest_match
                + self.temporary_event_boost + self.margin_bonus - self.policy_penalty
            )


class RankedProduct(RankingModel):
    """Serializable result that presentation and persistence can consume."""

    product_id: Identifier
    rank: int = Field(ge=1)
    score: Number
    breakdown: ScoreBreakdown
