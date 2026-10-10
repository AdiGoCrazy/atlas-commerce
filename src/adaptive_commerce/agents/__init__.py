"""Deterministic signal extraction boundaries for Atlas Commerce."""

from .signals import (
    FixtureEvent,
    FixtureSourceRecord,
    NormalizedSignal,
    normalize_fixture_record,
    normalize_fixture_records,
)

__all__ = [
    "FixtureEvent",
    "FixtureSourceRecord",
    "NormalizedSignal",
    "normalize_fixture_record",
    "normalize_fixture_records",
]
"""Signal extraction and source adapters."""

from .source import PersistedSignalSource, SignalSource

__all__ = ["PersistedSignalSource", "SignalSource"]
