"""Deterministic regional update cycles and bounded orchestration helpers."""

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from adaptive_commerce.agents import PersistedSignalSource, SignalSource
from adaptive_commerce.agents.signals import NormalizedSignal
from adaptive_commerce.application.profile_updates import propose_profile_update
from adaptive_commerce.domain import (
    RegionState, RecommendationVersion, UpdateCycle, UpdateCycleStatus, VersionStatus,
)
from adaptive_commerce.integrations.observability import trace_step
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.recommender import (
    RankingEvent, RankingPolicy, RankingProduct, RankingProfile, rank_products,
)

logger = logging.getLogger(__name__)


class CycleResult(BaseModel):
    """Operator-facing result for one durable regional cycle."""

    model_config = ConfigDict(extra="forbid")

    cycle: UpdateCycle
    before_version_id: str | None = None
    after_version_id: str | None = None
    promoted: list[str] = Field(default_factory=list)
    demoted: list[str] = Field(default_factory=list)
    before: list[dict[str, Any]] = Field(default_factory=list)
    after: list[dict[str, Any]] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    replay_of: str | None = None


class EvaluationPolicy(BaseModel):
    """Minimum deterministic quality and profit retention for activation."""

    min_profit_retention: Decimal = Field(default=Decimal("0.85"), ge=0, le=1)
    min_relevance_delta: Decimal = Field(default=Decimal("0"), ge=0)


def run_region_cycle(
    path: Path,
    region_id: str,
    *,
    now: datetime | None = None,
    cycle_id: str | None = None,
    replay_signal_ids: list[str] | None = None,
    replay_of: str | None = None,
    signal_source: SignalSource | None = None,
    evaluation_policy: EvaluationPolicy | None = None,
) -> CycleResult:
    """Run one idempotent update cycle for an active region.

    Signal consumption, cycle outcome, candidate version, event windows, and
    activation are committed together. Failures are recorded separately while
    leaving the active version and unconsumed signals intact.
    """
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None or at.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    stable_cycle_id = cycle_id or str(uuid4())
    try:
        with trace_step("WORKFLOW", "regional_update_cycle", region_id=region_id,
                        cycle_id=stable_cycle_id) as span:
            trace_id = f"{span.get_span_context().trace_id:032x}"
            result = _run_cycle_transaction(
                path, region_id, at, stable_cycle_id,
                replay_signal_ids=replay_signal_ids, replay_of=replay_of,
                signal_source=signal_source,
                evaluation_policy=evaluation_policy or EvaluationPolicy(),
                neatlogs_trace_id=trace_id,
            )
            span.set_attribute("atlas.result_status", result.cycle.status.value)
            span.set_attribute("atlas.activation_status", "activated" if result.after_version_id != result.before_version_id else "unchanged")
            logger.info("regional_cycle_finished", extra={
                "region_id": region_id, "cycle_id": stable_cycle_id,
                "result_status": result.cycle.status.value,
            })
            return result
    except Exception as error:
        _record_failure(path, region_id, stable_cycle_id, at, error,
                        neatlogs_trace_id=locals().get("trace_id"))
        logger.exception("regional_cycle_failed", extra={
            "region_id": region_id, "cycle_id": stable_cycle_id,
            "result_status": "failed",
        })
        raise


def _run_cycle_transaction(
    path: Path,
    region_id: str,
    now: datetime,
    cycle_id: str,
    *,
    replay_signal_ids: list[str] | None,
    replay_of: str | None,
    signal_source: SignalSource | None,
    evaluation_policy: EvaluationPolicy,
    neatlogs_trace_id: str | None,
) -> CycleResult:
    with connect(path) as conn:
        repository = Repository(conn)
        previous = repository.get_update_cycle(cycle_id)
        if previous is not None:
            return _result_from_cycle(previous)
        region = repository.get_region(region_id)
        if region is None or region.state is not RegionState.active:
            raise ValueError("regional cycles require an active region")
        active = repository.get_active_profile(region_id)
        active_version = repository.get_active_version(region_id)
        if active is None or active_version is None:
            raise ValueError("active region has no recommendation profile")
        with trace_step("TOOL", "signal_fetch", region_id=region_id,
                        cycle_id=cycle_id) as span:
            signals = (
                [repository.get_signal(item) for item in replay_signal_ids]
                if replay_signal_ids is not None
                else (signal_source or PersistedSignalSource()).fetch_unprocessed(repository, region_id)
            )
            span.set_attribute("atlas.signal_count", len(signals))
        if any(signal is None or signal.region_id != region_id for signal in signals):
            raise ValueError("cycle signal is missing or belongs to another region")
        concrete_signals = [signal for signal in signals if signal is not None]
        with trace_step("CHAIN", "signal_normalization", region_id=region_id,
                        cycle_id=cycle_id) as span:
            normalized = [
                NormalizedSignal(
                    signal=signal,
                    topic_impacts={topic: Decimal("1") for topic in signal.topics},
                )
                for signal in concrete_signals
            ]
            span.set_attribute("atlas.normalized_count", len(normalized))

        candidate_version_id = f"v-{region_id}-{uuid4().hex[:10]}"
        with trace_step("GUARDRAIL", "signal_policy_result", region_id=region_id,
                        cycle_id=cycle_id, version_id=candidate_version_id) as span:
            proposal = propose_profile_update(
                active, normalized, now=now, candidate_version_id=candidate_version_id,
                cycle_id=cycle_id,
            )
            span.set_attribute("atlas.accepted_count", len(proposal.accepted_signal_ids))
            span.set_attribute("atlas.rejected_count", len(proposal.rejections))
            span.set_attribute("atlas.changed_topic_count", len(proposal.changes))
            span.set_attribute("atlas.result_status", "candidate" if proposal.changed else "unchanged")
        cycle = UpdateCycle(
            id=cycle_id,
            region_id=region_id,
            signal_ids=[signal.id for signal in concrete_signals],
            proposed_changes=proposal.changes or None,
            status=UpdateCycleStatus.evaluating,
            created_at=now,
        )
        repository.create_update_cycle(cycle)

        if not proposal.changed:
            evaluation = {
                "result": "no_actionable_change",
                "rejections": [item.model_dump(mode="json") for item in proposal.rejections],
                "before_version_id": active.active_version_id,
                "after_version_id": active.active_version_id,
                "replay_of": replay_of,
                "simulated": True,
                "neatlogs_trace_id": neatlogs_trace_id,
            }
            final = cycle.model_copy(update={
                "status": UpdateCycleStatus.rejected,
                "rejection_reason": "no_actionable_signals",
                "evaluation": evaluation,
                "finished_at": now,
            })
            repository.update_update_cycle(final)
            if not replay_signal_ids:
                repository.mark_signals_processed(final.signal_ids, cycle_id)
            return CycleResult(cycle=final, before_version_id=active.active_version_id,
                               after_version_id=active.active_version_id,
                               metrics=evaluation, replay_of=replay_of)

        candidate = RecommendationVersion(
            id=candidate_version_id,
            region_id=region_id,
            parent_id=active.active_version_id,
            weights=proposal.candidate_profile.weights,
            status=VersionStatus.candidate,
            created_at=now,
        )
        repository.create_recommendation_version(candidate)
        for event in proposal.event_windows:
            repository.create_event_window(event)

        products = [RankingProduct.model_validate(item)
                    for item in repository.get_products_for_ranking(region_id)]
        policy = RankingPolicy.model_validate(repository.get_ranking_policy(region_id))
        event_windows = [RankingEvent.model_validate(item)
                         for item in repository.get_active_events(region_id, now.isoformat())]
        active_profile = RankingProfile.model_validate(active)
        candidate_profile = RankingProfile.model_validate(proposal.candidate_profile)
        with trace_step("CHAIN", "candidate_comparison_ranking", region_id=region_id,
                        cycle_id=cycle_id, version_id=candidate_version_id,
                        attributes={"product_count": len(products)}):
            before = rank_products(products, active_profile, now=now, policy=policy,
                                   events=event_windows, cycle_id=cycle_id)
            after = rank_products(products, candidate_profile, now=now, policy=policy,
                                  events=event_windows, cycle_id=cycle_id)
        with trace_step("EVALUATOR", "same_cohort_comparison", region_id=region_id,
                        cycle_id=cycle_id, version_id=candidate_version_id) as span:
            metrics = _evaluate_same_cohort(before, after, products, active.weights,
                                            proposal.changes, normalized)
            accepted = metrics["candidate_relevance"] >= (
                metrics["active_relevance"] + float(evaluation_policy.min_relevance_delta)
            ) and metrics["candidate_profit"] >= (
                metrics["active_profit"] * float(evaluation_policy.min_profit_retention)
            )
            span.set_attribute("atlas.policy_passed", accepted)
            span.set_attribute("atlas.result_status", "approved" if accepted else "rejected")
        metrics["policy_passed"] = accepted
        metrics["min_profit_retention"] = str(evaluation_policy.min_profit_retention)
        metrics["min_relevance_delta"] = str(evaluation_policy.min_relevance_delta)
        metrics["simulated"] = True
        metrics["cohort"] = "fixed regional demand cohort, same for active and candidate"
        metrics["rejections"] = [item.model_dump(mode="json") for item in proposal.rejections]
        metrics["neatlogs_trace_id"] = neatlogs_trace_id
        old_ranks = {item.product_id: item.rank for item in before}
        new_ranks = {item.product_id: item.rank for item in after}
        promoted = [item.product_id for item in after
                    if item.product_id not in old_ranks or item.rank < old_ranks[item.product_id]]
        demoted = [item.product_id for item in before
                   if item.product_id not in new_ranks or item.rank > new_ranks[item.product_id]]
        if accepted:
            if not repository.set_active_version(region_id, candidate_version_id):
                raise RuntimeError("candidate activation failed")
            status = UpdateCycleStatus.activated
            reason = None
            after_version_id = candidate_version_id
        else:
            repository.reject_recommendation_version(candidate_version_id)
            status = UpdateCycleStatus.rejected
            reason = "evaluation_gate_failed"
            after_version_id = active.active_version_id
        with trace_step("CHAIN", "recommendation_version_outcome", region_id=region_id,
                        cycle_id=cycle_id, version_id=candidate_version_id,
                        attributes={"result_status": "activated" if accepted else "rejected"}):
            pass
        metrics.update({
            "before_version_id": active.active_version_id,
            "after_version_id": after_version_id,
            "before": [item.model_dump(mode="json") for item in before],
            "after": [item.model_dump(mode="json") for item in after],
            "promoted": promoted if accepted else [],
            "demoted": demoted if accepted else [],
            "replay_of": replay_of,
        })
        final = cycle.model_copy(update={
            "status": status,
            "rejection_reason": reason,
            "evaluation": metrics,
            "finished_at": now,
        })
        repository.update_update_cycle(final)
        if not replay_signal_ids:
            repository.mark_signals_processed(final.signal_ids, cycle_id)
        return CycleResult(
            cycle=final,
            before_version_id=active.active_version_id,
            after_version_id=after_version_id,
            promoted=promoted if accepted else [],
            demoted=demoted if accepted else [],
            before=[item.model_dump(mode="json") for item in before],
            after=[item.model_dump(mode="json") for item in after],
            metrics=metrics,
            replay_of=replay_of,
        )


def _evaluate_same_cohort(
    before: list[Any],
    after: list[Any],
    products: list[RankingProduct],
    active_weights: dict[str, Decimal],
    changes: dict[str, Decimal],
    signals: list[NormalizedSignal],
) -> dict[str, Any]:
    """Compare ranked results on identical topic demand and simulated traffic."""
    by_id = {product.id: product for product in products}
    demand = {topic: max(Decimal("0"), value) for topic, value in active_weights.items()}
    for signal in signals:
        for topic in signal.signal.topics:
            impact = signal.topic_impacts.get(topic, Decimal("0"))
            demand[topic] = max(
                Decimal("0"),
                demand.get(topic, Decimal("0"))
                + impact * signal.signal.confidence * Decimal("5"),
            )

    def metrics(ranked: list[Any]) -> tuple[Decimal, Decimal]:
        relevance = Decimal("0")
        profit = Decimal("0")
        for item in ranked:
            product = by_id[item.product_id]
            relevance += sum((demand.get(tag, Decimal("0")) for tag in product.tags), Decimal("0")) / item.rank
            impressions = Decimal("100")
            click_rate = Decimal("0.08") / item.rank
            orders = impressions * click_rate * Decimal("0.025")
            profit += orders * (product.price - product.cost - Decimal("12"))
        return relevance, profit

    before_relevance, before_profit = metrics(before)
    after_relevance, after_profit = metrics(after)
    return {
        "active_relevance": float(before_relevance),
        "candidate_relevance": float(after_relevance),
        "active_profit": float(before_profit),
        "candidate_profit": float(after_profit),
        "cohort_size": 100,
        "active_top_five": [item.product_id for item in before],
        "candidate_top_five": [item.product_id for item in after],
        "weight_changes": {key: float(value) for key, value in changes.items()},
    }


def _record_failure(path: Path, region_id: str, cycle_id: str,
                    now: datetime, error: Exception,
                    *, neatlogs_trace_id: str | None = None) -> None:
    """Persist failure state separately after the work transaction rolls back."""
    with connect(path) as conn:
        repository = Repository(conn)
        if repository.get_region(region_id) is None:
            return
        if repository.get_update_cycle(cycle_id) is None:
            repository.create_update_cycle(UpdateCycle(
                id=cycle_id,
                region_id=region_id,
                status=UpdateCycleStatus.failed,
                rejection_reason=type(error).__name__,
                evaluation={"error": str(error)[:500], "active_version_preserved": True,
                            "neatlogs_trace_id": neatlogs_trace_id},
                created_at=now,
                finished_at=datetime.now(timezone.utc),
            ))


def _result_from_cycle(cycle: UpdateCycle) -> CycleResult:
    """Return the durable outcome for an idempotently repeated cycle ID."""
    evaluation = cycle.evaluation or {}
    return CycleResult(
        cycle=cycle,
        before_version_id=evaluation.get("before_version_id"),
        after_version_id=evaluation.get("after_version_id"),
        before=evaluation.get("before", []),
        after=evaluation.get("after", []),
        metrics=evaluation,
        replay_of=evaluation.get("replay_of"),
    )


def replay_cycle(path: Path, cycle_id: str, *, now: datetime | None = None) -> CycleResult:
    """Re-run the original cycle's exact signals as a new auditable cycle."""
    with connect(path) as conn:
        cycle = Repository(conn).get_update_cycle(cycle_id)
    if cycle is None:
        raise ValueError("cycle to replay was not found")
    return run_region_cycle(
        path, cycle.region_id, now=now, replay_signal_ids=cycle.signal_ids,
        replay_of=cycle.id,
    )


def run_all_active_regions(
    path: Path,
    *,
    now: datetime | None = None,
    max_attempts: int = 2,
    retry_delay_seconds: float = 0.05,
) -> list[CycleResult]:
    """Run each active region independently with a small bounded retry policy."""
    if max_attempts < 1 or max_attempts > 5:
        raise ValueError("max_attempts must be between 1 and 5")
    with connect(path) as conn:
        active_regions = Repository(conn).get_regions_by_state(RegionState.active)
    results: list[CycleResult] = []
    for region in active_regions:
        last_error: Exception | None = None
        for attempt in range(max_attempts):
            try:
                results.append(run_region_cycle(path, region.id, now=now))
                last_error = None
                break
            except Exception as error:
                last_error = error
                if attempt + 1 < max_attempts:
                    with trace_step("TOOL", "regional_cycle_retry", region_id=region.id,
                                    attributes={"attempt": attempt + 1,
                                                "result_status": "retrying"}):
                        logger.warning("regional_cycle_retry", extra={
                            "region_id": region.id, "attempt": attempt + 1,
                            "result_status": "retrying",
                        })
                    time.sleep(retry_delay_seconds * (2 ** attempt))
        if last_error is not None:
            failed = UpdateCycle(
                id=str(uuid4()), region_id=region.id, status=UpdateCycleStatus.failed,
                rejection_reason=f"retries_exhausted:{type(last_error).__name__}",
                evaluation={"error": str(last_error)[:500], "attempts": max_attempts},
                created_at=now or datetime.now(timezone.utc),
                finished_at=datetime.now(timezone.utc),
            )
            with connect(path) as conn:
                Repository(conn).create_update_cycle(failed)
            results.append(CycleResult(cycle=failed))
    return results


def scheduler_loop(path: Path, interval_seconds: int = 300) -> None:
    """Run active regions on a simple local polling schedule until interrupted."""
    if interval_seconds < 10:
        raise ValueError("interval_seconds must be at least 10")
    logger.info("atlas_scheduler_started", extra={"interval_seconds": interval_seconds})
    while True:
        run_all_active_regions(path)
        time.sleep(interval_seconds)
