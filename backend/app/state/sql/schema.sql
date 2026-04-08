PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    status TEXT NOT NULL,
    config_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_id TEXT,
    depth INTEGER NOT NULL,
    objective TEXT NOT NULL,
    node_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    persona_id TEXT,
    checker_policy_json TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    consecutive_checker_failures INTEGER NOT NULL DEFAULT 0,
    ttft_ms INTEGER,
    duration_ms INTEGER,
    started_at TEXT,
    first_token_at TEXT,
    ended_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_id) REFERENCES nodes (node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS attempts (
    attempt_id TEXT PRIMARY KEY,
    node_id TEXT NOT NULL,
    attempt_index INTEGER NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    output_snapshot_json TEXT,
    checker_result_json TEXT,
    error_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (node_id) REFERENCES nodes (node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS interventions (
    intervention_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    note TEXT,
    payload_delta_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes (node_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    node_id TEXT,
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ts TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (run_id) ON DELETE CASCADE,
    FOREIGN KEY (node_id) REFERENCES nodes (node_id) ON DELETE SET NULL,
    UNIQUE (run_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);
CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs (created_at);

CREATE INDEX IF NOT EXISTS idx_nodes_run_id ON nodes (run_id);
CREATE INDEX IF NOT EXISTS idx_nodes_parent_id ON nodes (parent_id);
CREATE INDEX IF NOT EXISTS idx_nodes_run_status ON nodes (run_id, status);

CREATE INDEX IF NOT EXISTS idx_attempts_node_id ON attempts (node_id);
CREATE INDEX IF NOT EXISTS idx_attempts_node_attempt_index ON attempts (node_id, attempt_index);

CREATE INDEX IF NOT EXISTS idx_interventions_node_id ON interventions (node_id);
CREATE INDEX IF NOT EXISTS idx_interventions_run_id ON interventions (run_id);

CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events (run_id, seq);
CREATE INDEX IF NOT EXISTS idx_events_node_id ON events (node_id);
