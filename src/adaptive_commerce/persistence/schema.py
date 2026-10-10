"""SQLite schema for Atlas Commerce."""

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Regions table
CREATE TABLE IF NOT EXISTS regions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    locale TEXT NOT NULL,
    available_source_types TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT
);

-- Region profiles table
CREATE TABLE IF NOT EXISTS region_profiles (
    region_id TEXT PRIMARY KEY REFERENCES regions(id) ON DELETE CASCADE,
    weights TEXT NOT NULL DEFAULT '{}',
    active_version_id TEXT NOT NULL,
    max_weight_delta TEXT NOT NULL DEFAULT '5'
);

-- Products table
CREATE TABLE IF NOT EXISTS products (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '[]',
    price TEXT NOT NULL,
    cost TEXT NOT NULL DEFAULT '0',
    in_stock INTEGER NOT NULL DEFAULT 1,
    eligible INTEGER NOT NULL DEFAULT 1,
    base_popularity TEXT NOT NULL DEFAULT '0'
);

-- Source records table (raw source data before normalization)
CREATE TABLE IF NOT EXISTS source_records (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    source_type TEXT NOT NULL,
    raw_content TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);

-- Signals table (normalized signals from source records)
CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    topics TEXT NOT NULL DEFAULT '[]',
    confidence TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    evidence_excerpt TEXT NOT NULL,
    language TEXT NOT NULL DEFAULT 'en',
    normalized_text TEXT
);

-- Event windows table
CREATE TABLE IF NOT EXISTS event_windows (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    starts_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    boosts TEXT NOT NULL DEFAULT '{}'
);

-- Finance assessments table
CREATE TABLE IF NOT EXISTS finance_assessments (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    viability_score TEXT NOT NULL,
    projected_profit TEXT NOT NULL,
    decision TEXT NOT NULL,
    assumptions TEXT NOT NULL DEFAULT '{}',
    cfo_reference TEXT,
    created_at TEXT NOT NULL
);

-- Recommendation versions table
CREATE TABLE IF NOT EXISTS recommendation_versions (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    parent_id TEXT REFERENCES recommendation_versions(id),
    weights TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'candidate',
    created_at TEXT NOT NULL
);

-- Recommendation items table (ranked products per version)
CREATE TABLE IF NOT EXISTS recommendation_items (
    version_id TEXT NOT NULL REFERENCES recommendation_versions(id) ON DELETE CASCADE,
    product_id TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    rank INTEGER NOT NULL,
    score TEXT NOT NULL,
    breakdown TEXT NOT NULL,
    PRIMARY KEY (version_id, product_id)
);

-- Update cycles table
CREATE TABLE IF NOT EXISTS update_cycles (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    signal_ids TEXT NOT NULL DEFAULT '[]',
    proposed_changes TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    rejection_reason TEXT,
    evaluation TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT
);

-- Signal consumption is persistence metadata, not part of the Signal contract.
CREATE TABLE IF NOT EXISTS consumed_signals (
    signal_id TEXT PRIMARY KEY REFERENCES signals(id) ON DELETE CASCADE,
    cycle_id TEXT NOT NULL REFERENCES update_cycles(id) ON DELETE CASCADE
);

-- Evidence references table
CREATE TABLE IF NOT EXISTS evidence_references (
    id TEXT PRIMARY KEY,
    update_cycle_id TEXT NOT NULL REFERENCES update_cycles(id) ON DELETE CASCADE,
    neatlogs_trace_id TEXT,
    entire_checkpoint_id TEXT,
    cfo_scenario_id TEXT
);

-- Simulated events table (for demo/replay)
CREATE TABLE IF NOT EXISTS simulated_events (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    processed INTEGER NOT NULL DEFAULT 0
);

-- Human operations are retained as a small audit trail for the local product.
CREATE TABLE IF NOT EXISTS operator_actions (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

-- Immutable, trace-linked correction lineage for agent-repaired signals.
CREATE TABLE IF NOT EXISTS signal_corrections (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
    source_signal_id TEXT NOT NULL REFERENCES signals(id),
    corrected_signal_id TEXT NOT NULL UNIQUE REFERENCES signals(id),
    cycle_id TEXT NOT NULL REFERENCES update_cycles(id),
    trace_id TEXT NOT NULL,
    before_json TEXT NOT NULL,
    after_json TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_signal_id, cycle_id, after_json)
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_signals_region ON signals(region_id);
CREATE INDEX IF NOT EXISTS idx_signals_source ON signals(source_id);
CREATE INDEX IF NOT EXISTS idx_events_region_time ON event_windows(region_id, starts_at, expires_at);
CREATE INDEX IF NOT EXISTS idx_finance_region ON finance_assessments(region_id);
CREATE INDEX IF NOT EXISTS idx_versions_region ON recommendation_versions(region_id);
CREATE INDEX IF NOT EXISTS idx_cycles_region ON update_cycles(region_id);
CREATE INDEX IF NOT EXISTS idx_actions_region ON operator_actions(region_id, created_at);
CREATE INDEX IF NOT EXISTS idx_consumed_cycle ON consumed_signals(cycle_id);
CREATE INDEX IF NOT EXISTS idx_correction_region ON signal_corrections(region_id, created_at);
"""
