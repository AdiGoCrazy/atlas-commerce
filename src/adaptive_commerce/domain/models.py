"""Domain models for Atlas Commerce."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .enums import (
    FinanceDecision,
    RegionState,
    SignalSourceType,
    UpdateCycleStatus,
    VersionStatus,
)

Number = Decimal
Nonnegative = Decimal
Positive = Decimal


class Region(BaseModel):
    """Region identity, lifecycle state, locale, source availability, and timestamps."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    state: RegionState
    locale: str = Field(min_length=1)
    available_source_types: list[SignalSourceType] = Field(default_factory=list)
    created_at: AwareDatetime
    updated_at: AwareDatetime | None = None


class Product(BaseModel):
    """Catalog product with tags, pricing, stock, eligibility, and base popularity."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    tags: list[str] = Field(default_factory=list)
    price: Positive = Field(gt=0)
    cost: Nonnegative = Field(ge=0)
    in_stock: bool = True
    eligible: bool = True
    base_popularity: Nonnegative = Field(default=Decimal("0"), ge=0)


class Signal(BaseModel):
    """Normalized evidence extracted from a source record."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_type: SignalSourceType
    topics: list[str] = Field(default_factory=list)
    confidence: Number = Field(ge=0, le=1)
    observed_at: AwareDatetime
    evidence_excerpt: str
    language: str = "en"
    normalized_text: str | None = None


class RegionProfile(BaseModel):
    """Regional weight snapshot with active recommendation version and update limits."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    region_id: str = Field(min_length=1)
    weights: dict[str, Number] = Field(default_factory=dict)
    active_version_id: str = Field(min_length=1)
    max_weight_delta: Positive = Field(default=Decimal("5"), gt=0)


class RecommendationVersion(BaseModel):
    """Immutable weight snapshot with parent version linkage."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    parent_id: str | None = None
    weights: dict[str, Number] = Field(default_factory=dict)
    status: VersionStatus = VersionStatus.candidate
    created_at: AwareDatetime


class FinanceAssessment(BaseModel):
    """Financial viability assessment with assumptions, scenarios, score, decision, and cfo.ai references."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    viability_score: Number = Field(ge=0, le=1)
    projected_profit: Number
    decision: FinanceDecision
    assumptions: dict[str, Any] = Field(default_factory=dict)
    cfo_reference: str | None = None
    created_at: AwareDatetime


class UpdateCycle(BaseModel):
    """Autonomous update cycle with input signals, proposed changes, evaluation, and result."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    signal_ids: list[str] = Field(default_factory=list)
    proposed_changes: dict[str, Number] | None = None
    status: UpdateCycleStatus = UpdateCycleStatus.pending
    rejection_reason: str | None = None
    evaluation: dict[str, Any] | None = None
    created_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class EventWindow(BaseModel):
    """Temporary topic boosts with start and expiry timestamps."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: str = Field(min_length=1)
    region_id: str = Field(min_length=1)
    starts_at: AwareDatetime
    expires_at: AwareDatetime
    boosts: dict[str, Number] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_window(self) -> "EventWindow":
        """Reject empty or reversed event windows."""
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        return self