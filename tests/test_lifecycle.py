"""Region lifecycle transition tests."""

from datetime import datetime, timezone

import pytest

from adaptive_commerce.application.lifecycle import (
    approve_region,
    reject_region,
    transition_region,
)
from adaptive_commerce.domain import Region, RegionState
from adaptive_commerce.persistence import Repository, connect, initialize


def create_region(path, state: RegionState = RegionState.candidate):
    conn = connect(path)
    initialize(conn)
    Repository(conn).create_region(Region(
        id="region-1",
        name="Test Region",
        state=state,
        locale="en_US",
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
    ))
    conn.commit()
    conn.close()


def test_documented_approval_lifecycle(tmp_path):
    path = tmp_path / "lifecycle.db"
    create_region(path)

    assert transition_region(path, "region-1", RegionState.assessing).state is RegionState.assessing
    assert transition_region(
        path, "region-1", RegionState.awaiting_approval,
        expected_state=RegionState.assessing,
    ).state is RegionState.awaiting_approval
    assert approve_region(path, "region-1").state is RegionState.active


def test_rejection_preserves_terminal_rejected_state(tmp_path):
    path = tmp_path / "rejected.db"
    create_region(path, RegionState.awaiting_approval)

    assert reject_region(path, "region-1").state is RegionState.rejected
    with pytest.raises(ValueError, match="invalid region transition"):
        transition_region(path, "region-1", RegionState.active)


@pytest.mark.parametrize(
    "state, target",
    [
        (RegionState.candidate, RegionState.active),
        (RegionState.active, RegionState.rejected),
        (RegionState.paused, RegionState.archived),
        (RegionState.archived, RegionState.active),
    ],
)
def test_invalid_transitions_are_rejected(tmp_path, state, target):
    path = tmp_path / f"{state.value}.db"
    create_region(path, state)
    with pytest.raises(ValueError, match="invalid region transition"):
        transition_region(path, "region-1", target)


def test_expected_state_prevents_stale_approval(tmp_path):
    path = tmp_path / "race.db"
    create_region(path, RegionState.awaiting_approval)
    assert approve_region(path, "region-1").state is RegionState.active
    with pytest.raises(ValueError, match="expected awaiting_approval"):
        approve_region(path, "region-1")
