"""Validated, deterministic extraction from synthetic source records."""

import logging
import re
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Mapping, Sequence

import neatlogs
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from adaptive_commerce.domain import EventWindow, Signal, SignalSourceType

logger = logging.getLogger(__name__)

Identifier = Annotated[str, Field(min_length=1, max_length=200)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Impact = Annotated[FiniteDecimal, Field(ge=-1, le=1)]


class SignalBoundaryModel(BaseModel):
    """Strict model used at the synthetic source and agent boundary."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class FixtureEvent(SignalBoundaryModel):
    """Optional event timing attached to a synthetic source record."""

    id: Identifier
    starts_at: AwareDatetime
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def validate_window(self) -> "FixtureEvent":
        """Require a non-empty half-open event interval."""
        if self.expires_at <= self.starts_at:
            raise ValueError("expires_at must be after starts_at")
        return self


class FixtureSourceRecord(SignalBoundaryModel):
    """Synthetic source input with explicit extraction labels.

    The fixture supplies topics, confidence, and direction so tests and demos do
    not depend on an LLM. A later extractor can construct this same boundary.
    """

    id: Identifier
    region_id: Identifier
    source_type: SignalSourceType
    observed_at: AwareDatetime
    content: str = Field(min_length=1, max_length=20_000)
    topics: list[Identifier] = Field(min_length=1, max_length=20)
    confidence: Annotated[FiniteDecimal, Field(ge=0, le=1)]
    topic_impacts: dict[Identifier, Impact] = Field(default_factory=dict)
    language: str = Field(default="en", min_length=2, max_length=35)
    event: FixtureEvent | None = None

    @model_validator(mode="after")
    def validate_labels(self) -> "FixtureSourceRecord":
        """Reject ambiguous labels and event metadata on non-event records."""
        normalized_topics = [_normalize_topic(topic) for topic in self.topics]
        if len(set(normalized_topics)) != len(normalized_topics):
            raise ValueError("topics must be unique after normalization")
        unknown_impacts = set(self.topic_impacts) - set(self.topics)
        if unknown_impacts:
            raise ValueError("topic_impacts keys must appear in topics")
        if self.event is not None and self.source_type is not SignalSourceType.event:
            raise ValueError("event metadata requires source_type='event'")
        return self


class NormalizedSignal(SignalBoundaryModel):
    """Shared signal plus Ad-owned directional and temporary-event metadata."""

    signal: Signal
    topic_impacts: dict[Identifier, Impact]
    event: FixtureEvent | None = None


def _normalize_topic(value: str) -> str:
    """Return a stable lowercase topic key without inventing semantics."""
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _normalize_text(value: str) -> str:
    """Collapse fixture whitespace while retaining the supplied wording."""
    return " ".join(value.split())


@neatlogs.span(kind="AGENT", name="normalize_fixture_signal")
def normalize_fixture_record(record: FixtureSourceRecord) -> NormalizedSignal:
    """Convert one validated fixture source record into a shared ``Signal``.

    No topic, confidence, or direction is inferred. That makes the fallback
    extraction reproducible and leaves an explicit seam for a future LLM-backed
    extractor that emits ``FixtureSourceRecord`` values.
    """
    normalized_text = _normalize_text(record.content)
    topics = [_normalize_topic(topic) for topic in record.topics]
    supplied_impacts = {
        _normalize_topic(topic): impact for topic, impact in record.topic_impacts.items()
    }
    impacts = {topic: supplied_impacts.get(topic, Decimal("1")) for topic in topics}
    signal = Signal(
        id=f"signal:{record.id}",
        region_id=record.region_id,
        source_id=record.id,
        source_type=record.source_type,
        topics=topics,
        confidence=record.confidence,
        observed_at=record.observed_at,
        evidence_excerpt=normalized_text[:280],
        language=record.language.lower(),
        normalized_text=normalized_text,
    )
    logger.info(
        "fixture_signal_normalized",
        extra={
            "region_id": signal.region_id,
            "signal_id": signal.id,
            "source_id": signal.source_id,
            "result_status": "success",
        },
    )
    return NormalizedSignal(signal=signal, topic_impacts=impacts, event=record.event)


def normalize_fixture_records(
    records: Sequence[FixtureSourceRecord | Mapping[str, Any]],
) -> list[NormalizedSignal]:
    """Validate and normalize records in stable source-ID order."""
    validated = [
        record
        if isinstance(record, FixtureSourceRecord)
        else FixtureSourceRecord.model_validate(record)
        for record in records
    ]
    source_ids = [record.id for record in validated]
    if len(set(source_ids)) != len(source_ids):
        raise ValueError("duplicate source IDs")
    ordered = sorted(validated, key=lambda item: item.id)
    return [normalize_fixture_record(record) for record in ordered]


def event_window_for_signal(
    normalized: NormalizedSignal,
    boosts: Mapping[str, Decimal],
) -> EventWindow | None:
    """Build a shared event window after proposal bounds have been applied."""
    if normalized.event is None:
        return None
    return EventWindow(
        id=normalized.event.id,
        region_id=normalized.signal.region_id,
        starts_at=normalized.event.starts_at,
        expires_at=normalized.event.expires_at,
        boosts=dict(boosts),
    )
