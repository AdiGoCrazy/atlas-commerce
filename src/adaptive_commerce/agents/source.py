"""Replaceable interface for region signal sources."""

from typing import Protocol

from adaptive_commerce.domain import Signal
from adaptive_commerce.persistence import Repository


class SignalSource(Protocol):
    """Fetch normalized signals for one region without changing ranking policy."""

    def fetch_unprocessed(self, repository: Repository, region_id: str) -> list[Signal]:
        """Return new signals ready for deterministic proposal gates."""


class PersistedSignalSource:
    """Default local source backed by normalized SQLite signal records."""

    def fetch_unprocessed(self, repository: Repository, region_id: str) -> list[Signal]:
        """Return unconsumed signals stored for this region."""
        return repository.get_unprocessed_signals(region_id)
