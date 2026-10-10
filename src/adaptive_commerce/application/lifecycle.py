"""Validated region lifecycle transitions from the product state machine."""

import logging
from contextlib import closing
from pathlib import Path

from adaptive_commerce.domain import Region, RegionState
from adaptive_commerce.persistence import Repository, connect

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[RegionState, frozenset[RegionState]] = {
    RegionState.candidate: frozenset({RegionState.assessing}),
    RegionState.assessing: frozenset({RegionState.awaiting_approval, RegionState.rejected}),
    RegionState.awaiting_approval: frozenset({RegionState.active, RegionState.rejected}),
    RegionState.active: frozenset({
        RegionState.monitoring, RegionState.paused, RegionState.archived,
    }),
    RegionState.monitoring: frozenset({RegionState.monitoring, RegionState.update_candidate}),
    RegionState.update_candidate: frozenset({RegionState.active, RegionState.rejected_update}),
    RegionState.rejected_update: frozenset({RegionState.active}),
    RegionState.paused: frozenset({RegionState.active}),
    RegionState.rejected: frozenset(),
    RegionState.archived: frozenset(),
}


def _transition(
    repository: Repository,
    region_id: str,
    target_state: RegionState,
    *,
    expected_state: RegionState | None = None,
) -> Region:
    """Apply one legal transition inside the caller's transaction."""
    region = repository.get_region(region_id)
    if region is None:
        raise ValueError(f"region not found: {region_id}")
    if expected_state is not None and region.state is not expected_state:
        raise ValueError(
            f"region {region_id} is {region.state.value}, expected {expected_state.value}"
        )
    if region.state is target_state:
        return region
    if target_state not in ALLOWED_TRANSITIONS[region.state]:
        raise ValueError(
            f"invalid region transition: {region.state.value} -> {target_state.value}"
        )
    if not repository.update_region_state(
        region_id, target_state, expected_state=region.state
    ):
        raise RuntimeError(f"region changed during transition: {region_id}")
    updated = repository.get_region(region_id)
    if updated is None:
        raise RuntimeError(f"region disappeared during transition: {region_id}")
    logger.info(
        "region_lifecycle_transitioned",
        extra={
            "region_id": region_id,
            "from_state": region.state.value,
            "to_state": target_state.value,
            "result_status": "success",
        },
    )
    return updated


def transition_region(
    path: str | Path,
    region_id: str,
    target_state: RegionState,
    *,
    expected_state: RegionState | None = None,
) -> Region:
    """Transition one region while enforcing the documented state machine."""
    with closing(connect(path)) as conn, conn:
        return _transition(
            Repository(conn), region_id, target_state, expected_state=expected_state
        )


def approve_region(path: str | Path, region_id: str) -> Region:
    """Activate a region only from the human approval state."""
    return transition_region(
        path, region_id, RegionState.active,
        expected_state=RegionState.awaiting_approval,
    )


def reject_region(path: str | Path, region_id: str) -> Region:
    """Reject a region only while it awaits human approval."""
    return transition_region(
        path, region_id, RegionState.rejected,
        expected_state=RegionState.awaiting_approval,
    )


__all__ = [
    "ALLOWED_TRANSITIONS",
    "approve_region",
    "reject_region",
    "transition_region",
]
