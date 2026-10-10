"""Domain enums for Atlas Commerce."""

from enum import Enum


class RegionState(str, Enum):
    """Lifecycle states for a region."""

    candidate = "candidate"
    assessing = "assessing"
    awaiting_approval = "awaiting_approval"
    active = "active"
    rejected = "rejected"
    monitoring = "monitoring"
    update_candidate = "update_candidate"
    rejected_update = "rejected_update"
    paused = "paused"
    archived = "archived"


class SignalSourceType(str, Enum):
    """Source types for signal ingestion."""

    news = "news"
    forum = "forum"
    social = "social"
    event = "event"


class UpdateCycleStatus(str, Enum):
    """Status of an autonomous update cycle."""

    pending = "pending"
    evaluating = "evaluating"
    activated = "activated"
    rejected = "rejected"
    failed = "failed"


class FinanceDecision(str, Enum):
    """Financial viability decision for a candidate region."""

    approve = "approve"
    reject = "reject"


class VersionStatus(str, Enum):
    """Status of a recommendation version."""

    candidate = "candidate"
    active = "active"
    rejected = "rejected"
    rolled_back = "rolled_back"