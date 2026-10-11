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

-- Auditable catalog changes; product references remain even after soft removal.
CREATE TABLE IF NOT EXISTS catalog_actions (
    id TEXT PRIMARY KEY,
    product_id TEXT NOT NULL,
    action TEXT NOT NULL,
    before_json TEXT,
    after_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalog_actions_product_created
    ON catalog_actions(product_id, created_at DESC);

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

-- A pending signal may be withdrawn without erasing its operator audit record.
CREATE TABLE IF NOT EXISTS signal_deletions (
    id TEXT PRIMARY KEY,
    region_id TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    signal_json TEXT NOT NULL,
    removed_at TEXT NOT NULL
);

-- Operator-approved, multi-agent workflows and their durable execution ledger.
CREATE TABLE IF NOT EXISTS agent_workflows (
    id TEXT PRIMARY KEY,
    request TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL,
    plan_hash TEXT,
    plan_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT NOT NULL DEFAULT '{}',
    trace_id TEXT,
    error TEXT,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_workflow_steps (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES agent_workflows(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    duty TEXT NOT NULL,
    status TEXT NOT NULL,
    label TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    result_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(workflow_id, role)
);
CREATE TABLE IF NOT EXISTS agent_workflow_actions (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL REFERENCES agent_workflows(id) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    agent_role TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    rationale TEXT NOT NULL,
    impact TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    result_json TEXT NOT NULL DEFAULT '{}',
    error TEXT,
    created_at TEXT NOT NULL,
    finished_at TEXT,
    UNIQUE(workflow_id, ordinal)
);
CREATE TABLE IF NOT EXISTS agent_workflow_events (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_deletion_audit (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    dependency_summary_json TEXT NOT NULL DEFAULT '{}',
    approved_by TEXT NOT NULL,
    created_at TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_agent_workflows_updated ON agent_workflows(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_steps_workflow ON agent_workflow_steps(workflow_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_actions_workflow ON agent_workflow_actions(workflow_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_agent_workflow_events_workflow ON agent_workflow_events(workflow_id, created_at);
CREATE INDEX IF NOT EXISTS idx_workflow_deletions_target ON workflow_deletion_audit(target_type, target_id);
"""
