"""End-to-end coverage for operator lifecycle controls and durable cycles."""

from datetime import datetime, timezone

import pytest

from adaptive_commerce.application.dashboard import (
    CandidateRequest, bootstrap_database, count_pending_signals, list_regions,
    submit_candidate,
)
from adaptive_commerce.application.lifecycle import (
    approve_region, pause_region, reject_region, resume_region, rollback_region,
)
from adaptive_commerce.application.operator import inject_demo_signal, reset_demo
from adaptive_commerce.application.worker import (
    EvaluationPolicy, replay_cycle, run_all_active_regions, run_region_cycle,
)
from adaptive_commerce.domain import RegionState, UpdateCycleStatus
from adaptive_commerce.integrations.finance import FinanceInputs
from adaptive_commerce.persistence import Repository, connect

NOW = datetime(2026, 10, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def demo_db(tmp_path):
    path = tmp_path / "atlas.db"
    bootstrap_database(path)
    return path


def test_approval_activates_candidate_only_once(demo_db):
    version_id = approve_region(demo_db, "harbor")
    with connect(demo_db) as conn:
        repository = Repository(conn)
        assert repository.get_region("harbor").state is RegionState.active
        assert repository.get_active_version("harbor").id == version_id
        assert repository.list_operator_actions("harbor")[0]["action"] == "approved"
    with pytest.raises(ValueError, match="candidate"):
        approve_region(demo_db, "harbor")


def test_operator_can_reject_candidate(demo_db):
    candidate = submit_candidate(
        demo_db, CandidateRequest(name="Reject Coast", assumptions=FinanceInputs()),
    )
    reject_region(demo_db, candidate.region.id)
    assert next(item for item in list_regions(demo_db)
                if item.id == candidate.region.id).state is RegionState.rejected
    with pytest.raises(ValueError, match="candidate"):
        reject_region(demo_db, candidate.region.id)


def test_candidate_without_viable_finance_cannot_be_activated(demo_db):
    with connect(demo_db) as conn:
        conn.execute("UPDATE finance_assessments SET decision = 'reject' WHERE region_id = 'harbor'")
    with pytest.raises(ValueError, match="does not recommend"):
        approve_region(demo_db, "harbor")
    assert next(item for item in list_regions(demo_db) if item.id == "harbor").state is RegionState.candidate


def test_worker_activates_signal_update_idempotently_and_rollback_restores_parent(demo_db):
    first = run_region_cycle(demo_db, "cedar", now=NOW, cycle_id="cycle-demo-success")
    assert first.cycle.status is UpdateCycleStatus.activated
    assert first.before_version_id != first.after_version_id
    assert first.promoted or first.demoted
    assert first.metrics["cohort"] == "fixed regional demand cohort, same for active and candidate"
    repeated = run_region_cycle(demo_db, "cedar", now=NOW, cycle_id="cycle-demo-success")
    assert repeated.cycle.id == first.cycle.id
    assert repeated.after_version_id == first.after_version_id
    restored = rollback_region(demo_db, "cedar")
    assert restored == first.before_version_id
    with connect(demo_db) as conn:
        assert Repository(conn).get_active_version("cedar").id == restored


def test_poor_profit_candidate_is_rejected_and_active_version_is_preserved(demo_db):
    approve_region(demo_db, "harbor")
    inject_demo_signal(
        demo_db, "harbor", ["festival", "electronics"], 0.95,
        "Synthetic launch event predicts strong demand for portable electronics and festival accessories.",
        observed_at=NOW,
    )
    with connect(demo_db) as conn:
        active_before = Repository(conn).get_active_version("harbor").id
    result = run_region_cycle(demo_db, "harbor", now=NOW)
    assert result.cycle.status is UpdateCycleStatus.rejected
    assert result.after_version_id == active_before
    assert result.before_version_id == active_before
    assert result.metrics["candidate_relevance"] >= result.metrics["active_relevance"]
    assert result.metrics["candidate_profit"] < result.metrics["active_profit"] * 0.85
    assert result.cycle.evaluation["simulated"] is True


def test_operator_can_raise_activation_profit_threshold(demo_db):
    result = run_region_cycle(
        demo_db, "cedar", now=NOW,
        evaluation_policy=EvaluationPolicy(min_profit_retention="0.99"),
    )
    assert result.cycle.status is UpdateCycleStatus.rejected
    assert result.metrics["min_profit_retention"] == "0.99"
    assert result.after_version_id == result.before_version_id


def test_low_confidence_is_rejected_and_consumed_once(demo_db):
    inject_demo_signal(
        demo_db, "aspen", ["festival", "electronics"], 0.2,
        "Synthetic low-confidence signal for the recovery acceptance scenario.",
    )
    result = run_region_cycle(demo_db, "aspen", now=NOW)
    assert result.cycle.status is UpdateCycleStatus.rejected
    assert result.cycle.rejection_reason == "no_actionable_signals"
    assert result.metrics["rejections"][0]["reason"] == "low_confidence"
    next_cycle = run_region_cycle(demo_db, "aspen", now=NOW)
    assert next_cycle.cycle.status is UpdateCycleStatus.rejected
    assert next_cycle.cycle.signal_ids == []


def test_synthetic_signal_submission_is_idempotent(demo_db):
    first = inject_demo_signal(
        demo_db, "aspen", ["festival"], 0.8,
        "Synthetic community reports rising festival demand.", observed_at=NOW,
    )
    repeated = inject_demo_signal(
        demo_db, "aspen", ["festival"], 0.8,
        "Synthetic community reports rising festival demand.", observed_at=NOW,
    )
    distinct_confidence = inject_demo_signal(
        demo_db, "aspen", ["festival"], 0.75,
        "Synthetic community reports rising festival demand.", observed_at=NOW,
    )

    assert repeated.id == first.id
    assert distinct_confidence.id != first.id
    assert count_pending_signals(demo_db, "aspen") == 2


@pytest.mark.parametrize(
    ("confidence", "expected_status", "expected_reason", "expected_delta"),
    [
        (0.65, UpdateCycleStatus.rejected, "no_actionable_signals", None),
        (0.70, UpdateCycleStatus.activated, None, 3.5),
        (0.80, UpdateCycleStatus.rejected, "evaluation_gate_failed", 4.0),
    ],
)
def test_festival_confidence_calibration(
    tmp_path, confidence, expected_status, expected_reason, expected_delta,
):
    path = tmp_path / f"confidence-{confidence}.db"
    bootstrap_database(path)
    inject_demo_signal(
        path, "aspen", ["festival"], confidence,
        "Synthetic community reports rising festival demand.", observed_at=NOW,
    )
    with connect(path) as conn:
        original_version = Repository(conn).get_active_version("aspen").id

    result = run_region_cycle(path, "aspen", now=NOW)

    assert result.cycle.status is expected_status
    assert result.cycle.rejection_reason == expected_reason
    if expected_delta is None:
        assert result.metrics["rejections"][0]["reason"] == "low_confidence"
        assert result.cycle.evaluation.get("active_version_preserved", True)
    else:
        assert result.metrics["weight_changes"]["festival"] == expected_delta
    with connect(path) as conn:
        active_version = Repository(conn).get_active_version("aspen").id
    if confidence == 0.70:
        assert active_version != original_version
        before = result.metrics["active_top_five"]
        after = result.metrics["candidate_top_five"]
        assert "lights" not in before and "lights" in after
    else:
        assert active_version == original_version


def test_pause_excludes_region_and_scheduler_isolates_regions(demo_db, monkeypatch):
    pause_region(demo_db, "aspen")
    with pytest.raises(ValueError, match="active region"):
        run_region_cycle(demo_db, "aspen", now=NOW)
    resume_region(demo_db, "aspen")
    results = run_all_active_regions(demo_db, now=NOW, max_attempts=1)
    assert {item.cycle.region_id for item in results} == {"cedar", "aspen"}


def test_failed_region_does_not_stop_other_region(demo_db, monkeypatch):
    import adaptive_commerce.application.worker as worker_module

    real_run = worker_module.run_region_cycle

    def fail_cedar(path, region_id, **kwargs):
        if region_id == "cedar":
            raise RuntimeError("injected local failure")
        return real_run(path, region_id, **kwargs)

    monkeypatch.setattr(worker_module, "run_region_cycle", fail_cedar)
    results = worker_module.run_all_active_regions(demo_db, now=NOW, max_attempts=1)
    by_region = {item.cycle.region_id: item.cycle for item in results}
    assert by_region["cedar"].status is UpdateCycleStatus.failed
    assert by_region["aspen"].status is UpdateCycleStatus.rejected


def test_replay_uses_same_signals_as_new_cycle_and_reset_restores_fixtures(demo_db):
    result = run_region_cycle(demo_db, "cedar", now=NOW, cycle_id="cycle-to-replay")
    replay = replay_cycle(demo_db, result.cycle.id, now=NOW)
    assert replay.cycle.id != result.cycle.id
    assert replay.cycle.signal_ids == result.cycle.signal_ids
    assert replay.replay_of == result.cycle.id
    reset_demo(demo_db)
    with connect(demo_db) as conn:
        assert Repository(conn).get_update_cycle(result.cycle.id) is None
        assert Repository(conn).get_region("cedar").state is RegionState.active
    assert len(list_regions(demo_db)) == 3
