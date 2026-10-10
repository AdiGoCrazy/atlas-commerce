"""Deterministic gates and bounded regional profile proposals."""

import logging
from datetime import datetime, timedelta
from decimal import Context, Decimal, localcontext
from enum import Enum
from typing import Annotated, Sequence

import neatlogs
from pydantic import BaseModel, ConfigDict, Field

from adaptive_commerce.agents.signals import NormalizedSignal, event_window_for_signal
from adaptive_commerce.domain import EventWindow, RegionProfile

logger = logging.getLogger(__name__)

Identifier = Annotated[str, Field(min_length=1)]
FiniteDecimal = Annotated[Decimal, Field(allow_inf_nan=False)]
Nonnegative = Annotated[FiniteDecimal, Field(ge=0)]
Positive = Annotated[FiniteDecimal, Field(gt=0)]


class ProposalModel(BaseModel):
    """Strict contract for update policy and proposal results."""

    model_config = ConfigDict(extra="forbid")


class UpdatePolicy(ProposalModel):
    """Explicit gates and limits for one regional update cycle."""

    min_confidence: Annotated[FiniteDecimal, Field(ge=0, le=1)] = Decimal("0.70")
    max_signal_age: timedelta = Field(default=timedelta(hours=72), gt=timedelta(0))
    future_tolerance: timedelta = Field(default=timedelta(minutes=5), ge=timedelta(0))
    min_evidence_characters: int = Field(default=20, ge=1, le=280)
    max_signal_delta: Positive = Decimal("5")
    max_topics_per_cycle: int = Field(default=3, ge=1, le=20)
    blocked_topics: frozenset[Identifier] = frozenset({
        "disability", "ethnicity", "health", "race", "religion", "sexual_orientation",
    })


class RejectionReason(str, Enum):
    """Machine-readable reason an input did not affect a candidate."""

    low_confidence = "low_confidence"
    stale = "stale"
    future_dated = "future_dated"
    insufficient_evidence = "insufficient_evidence"
    region_mismatch = "region_mismatch"
    blocked_topic = "blocked_topic"
    duplicate_source = "duplicate_source"
    no_effect = "no_effect"


class SignalRejection(ProposalModel):
    """Rejected signal and its deterministic gate result."""

    signal_id: Identifier
    source_id: Identifier
    reason: RejectionReason


class ProfileUpdateProposal(ProposalModel):
    """Candidate-only update; activation and persistence remain downstream."""

    region_id: Identifier
    base_version_id: Identifier
    candidate_version_id: Identifier
    candidate_profile: RegionProfile | None = None
    changes: dict[Identifier, FiniteDecimal] = Field(default_factory=dict)
    accepted_signal_ids: list[Identifier] = Field(default_factory=list)
    rejections: list[SignalRejection] = Field(default_factory=list)
    event_windows: list[EventWindow] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Return whether a candidate profile contains at least one change."""
        return self.candidate_profile is not None


def _gate_signal(
    normalized: NormalizedSignal,
    profile: RegionProfile,
    now: datetime,
    policy: UpdatePolicy,
) -> RejectionReason | None:
    signal = normalized.signal
    if signal.region_id != profile.region_id:
        return RejectionReason.region_mismatch
    if signal.confidence < policy.min_confidence:
        return RejectionReason.low_confidence
    if signal.observed_at < now - policy.max_signal_age:
        return RejectionReason.stale
    if normalized.event is not None and normalized.event.expires_at <= now:
        return RejectionReason.stale
    if signal.observed_at > now + policy.future_tolerance:
        return RejectionReason.future_dated
    if len(signal.evidence_excerpt.strip()) < policy.min_evidence_characters:
        return RejectionReason.insufficient_evidence
    if set(signal.topics).intersection(policy.blocked_topics):
        return RejectionReason.blocked_topic
    if not any(normalized.topic_impacts.values()):
        return RejectionReason.no_effect
    return None


def _bounded_changes(
    profile: RegionProfile,
    accepted: Sequence[NormalizedSignal],
    policy: UpdatePolicy,
) -> dict[str, Decimal]:
    requested: dict[str, Decimal] = {}
    support: dict[str, Decimal] = {}
    with localcontext(Context(prec=28)):
        for normalized in accepted:
            signal = normalized.signal
            for topic in signal.topics:
                impact = normalized.topic_impacts[topic]
                requested[topic] = requested.get(topic, Decimal("0")) + (
                    impact * signal.confidence * policy.max_signal_delta
                )
                support[topic] = support.get(topic, Decimal("0")) + abs(
                    impact * signal.confidence
                )
        selected = sorted(requested, key=lambda topic: (-support[topic], topic))[
            : policy.max_topics_per_cycle
        ]
        bound = profile.max_weight_delta
        return {
            topic: max(-bound, min(bound, requested[topic]))
            for topic in selected
            if requested[topic] != 0
        }


@neatlogs.span(kind="CHAIN", name="propose_profile_update")
def propose_profile_update(
    profile: RegionProfile,
    signals: Sequence[NormalizedSignal],
    *,
    now: datetime,
    candidate_version_id: str,
    policy: UpdatePolicy | None = None,
    cycle_id: str | None = None,
) -> ProfileUpdateProposal:
    """Gate evidence and return a bounded candidate without mutating inputs.

    Each topic's total change is clamped to ``profile.max_weight_delta`` relative
    to the active snapshot. Repeated source IDs contribute once, event boosts use
    the same bound, and an unchanged result has no candidate profile.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not candidate_version_id.strip():
        raise ValueError("candidate_version_id must not be empty")
    resolved_policy = policy if policy is not None else UpdatePolicy()
    accepted: list[NormalizedSignal] = []
    rejections: list[SignalRejection] = []
    seen_sources: set[str] = set()

    for normalized in sorted(signals, key=lambda item: item.signal.id):
        signal = normalized.signal
        reason: RejectionReason | None
        if signal.source_id in seen_sources:
            reason = RejectionReason.duplicate_source
        else:
            seen_sources.add(signal.source_id)
            reason = _gate_signal(normalized, profile, now, resolved_policy)
        if reason is None:
            accepted.append(normalized)
        else:
            rejections.append(SignalRejection(
                signal_id=signal.id, source_id=signal.source_id, reason=reason,
            ))

    changes = _bounded_changes(profile, accepted, resolved_policy)
    candidate_profile = None
    if changes:
        weights = dict(profile.weights)
        for topic, delta in changes.items():
            weights[topic] = weights.get(topic, Decimal("0")) + delta
        candidate_profile = RegionProfile(
            region_id=profile.region_id,
            weights=weights,
            active_version_id=candidate_version_id,
            max_weight_delta=profile.max_weight_delta,
        )

    event_windows_by_id: dict[str, EventWindow] = {}
    for normalized in accepted:
        if normalized.event is None:
            continue
        boosts = {
            topic: max(
                -profile.max_weight_delta,
                min(
                    profile.max_weight_delta,
                    normalized.topic_impacts[topic]
                    * normalized.signal.confidence
                    * resolved_policy.max_signal_delta,
                ),
            )
            for topic in normalized.signal.topics
            if normalized.topic_impacts[topic] != 0
        }
        window = event_window_for_signal(normalized, boosts)
        if window is not None:
            previous = event_windows_by_id.get(window.id)
            if previous is not None and previous != window:
                raise ValueError(f"conflicting event window ID: {window.id}")
            event_windows_by_id[window.id] = window

    result = ProfileUpdateProposal(
        region_id=profile.region_id,
        base_version_id=profile.active_version_id,
        candidate_version_id=candidate_version_id,
        candidate_profile=candidate_profile,
        changes=changes,
        accepted_signal_ids=[item.signal.id for item in accepted],
        rejections=rejections,
        event_windows=sorted(event_windows_by_id.values(), key=lambda item: item.id),
    )
    logger.info(
        "profile_update_proposed",
        extra={
            "region_id": profile.region_id,
            "version_id": candidate_version_id,
            "base_version_id": profile.active_version_id,
            "cycle_id": cycle_id,
            "accepted_count": len(accepted),
            "rejected_count": len(rejections),
            "changed_topic_count": len(changes),
            "result_status": "candidate" if result.changed else "unchanged",
        },
    )
    return result
