"""Operator-controlled region lifecycle transitions."""

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from adaptive_commerce.domain import RegionState, RecommendationVersion, VersionStatus
from adaptive_commerce.integrations.observability import trace_step, traced_step
from adaptive_commerce.persistence import Repository, connect
from adaptive_commerce.persistence.database import _json_dumps


@traced_step("WORKFLOW", "approve_region", lambda args: {"region_id": args["region_id"]})
def approve_region(path: Path, region_id: str) -> str:
    """Activate a financially viable candidate after explicit operator approval."""
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        repository = Repository(conn)
        region = repository.get_region(region_id)
        if region is None or region.state is not RegionState.candidate:
            raise ValueError("only a candidate region can be approved")
        assessments = repository.get_finance_assessments_by_region(region_id)
        if not assessments or assessments[-1].decision.value != "approve":
            raise ValueError("the latest finance assessment does not recommend activation")

        with trace_step("CHAIN", "approve_region", region_id=region_id):
            version_id = f"v-{region_id}-{uuid4().hex[:10]}"
            defaults = {"outdoor": 3}
            existing = repository.get_regions_by_state(RegionState.active)
            for active in existing:
                profile = repository.get_region_profile(active.id)
                if profile:
                    defaults = dict(profile.weights)
                    break
            version = RecommendationVersion(
                id=version_id,
                region_id=region_id,
                weights=defaults,
                status=VersionStatus.active,
                created_at=now,
            )
            repository.create_recommendation_version(version)
            conn.execute(
                "INSERT INTO region_profiles (region_id, weights, active_version_id, max_weight_delta) "
                "VALUES (?, ?, ?, ?)",
                (region_id, _json_dumps(defaults), version_id, "5"),
            )
            repository.update_region_state(region_id, RegionState.active)
            repository.create_operator_action(
                str(uuid4()), region_id, "approved",
                {"version_id": version_id, "finance_decision": "approve"}, now,
            )
    return version_id


@traced_step("WORKFLOW", "reject_region", lambda args: {"region_id": args["region_id"]})
def reject_region(path: Path, region_id: str) -> None:
    """Reject a candidate region and record the operator decision."""
    _set_candidate_state(path, region_id, RegionState.rejected, "rejected")


@traced_step("WORKFLOW", "pause_region", lambda args: {"region_id": args["region_id"]})
def pause_region(path: Path, region_id: str) -> None:
    """Pause an active region so it is excluded from autonomous runs."""
    _set_state(path, region_id, RegionState.active, RegionState.paused, "paused")


@traced_step("WORKFLOW", "resume_region", lambda args: {"region_id": args["region_id"]})
def resume_region(path: Path, region_id: str) -> None:
    """Resume a paused region for subsequent autonomous runs."""
    _set_state(path, region_id, RegionState.paused, RegionState.active, "resumed")


@traced_step("WORKFLOW", "archive_region", lambda args: {"region_id": args["region_id"]})
def archive_region(path: Path, region_id: str) -> None:
    """Archive an active or paused region."""
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        repository = Repository(conn)
        region = repository.get_region(region_id)
        if region is None or region.state not in {RegionState.active, RegionState.paused}:
            raise ValueError("only active or paused regions can be archived")
        repository.update_region_state(region_id, RegionState.archived)
        repository.create_operator_action(str(uuid4()), region_id, "archived", {}, now)


@traced_step("WORKFLOW", "rollback_region", lambda args: {"region_id": args["region_id"]})
def rollback_region(path: Path, region_id: str) -> str:
    """Restore the parent of the current active recommendation version."""
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        repository = Repository(conn)
        active = repository.get_active_version(region_id)
        if active is None or active.parent_id is None:
            raise ValueError("there is no previous recommendation version to restore")
        if not repository.set_active_version(region_id, active.parent_id):
            raise ValueError("the previous recommendation version could not be restored")
        repository.create_operator_action(
            str(uuid4()), region_id, "rollback",
            {"from_version_id": active.id, "to_version_id": active.parent_id}, now,
        )
    return active.parent_id


def _set_candidate_state(path: Path, region_id: str, state: RegionState, action: str) -> None:
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        repository = Repository(conn)
        region = repository.get_region(region_id)
        if region is None or region.state is not RegionState.candidate:
            raise ValueError("only a candidate region can be rejected")
        repository.update_region_state(region_id, state)
        repository.create_operator_action(str(uuid4()), region_id, action, {}, now)


def _set_state(
    path: Path, region_id: str, expected: RegionState, target: RegionState, action: str,
) -> None:
    now = datetime.now(timezone.utc)
    with connect(path) as conn:
        repository = Repository(conn)
        region = repository.get_region(region_id)
        if region is None or region.state is not expected:
            raise ValueError(f"region must be {expected.value} to be {action}")
        repository.update_region_state(region_id, target)
        repository.create_operator_action(str(uuid4()), region_id, action, {}, now)
