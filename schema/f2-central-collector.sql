-- F2 Central Collector schema (задача #1199 → #1176, BizDNAI Metrics Federation).
-- Source: /home/bizdnai/docs/bizdnai-metrics-federation.md
--
-- The central endpoint at https://bizdnai.com/metrics/api_central.php receives
-- anonymised batches from opt-in mcp-metrics instances (Phase F1) and stores
-- them here. Tokens for revocation live in central_tokens.
--
-- The UNIQUE (batch_id, opaque_id) constraint prevents the same submission from
-- being double-counted if a client retries after a transient network failure.

CREATE TABLE IF NOT EXISTS central_metrics (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL,
    opaque_id TEXT NOT NULL,
    submitter_hash TEXT NOT NULL,
    submitted_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    model TEXT NOT NULL,
    task_type TEXT,
    started_at_day DATE NOT NULL,
    duration_sec INT,
    cost_usd NUMERIC(10,4),
    status TEXT NOT NULL,
    files_changed_count INT,
    source_token_prefix TEXT,
    UNIQUE (batch_id, opaque_id)
);

CREATE INDEX IF NOT EXISTS idx_central_model_day
    ON central_metrics (model, started_at_day);
CREATE INDEX IF NOT EXISTS idx_central_task_type
    ON central_metrics (task_type, started_at_day);
CREATE INDEX IF NOT EXISTS idx_central_submitter
    ON central_metrics (submitter_hash, received_at);

CREATE TABLE IF NOT EXISTS central_tokens (
    token_hash TEXT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    note TEXT,
    issued_to_ip TEXT
);

-- anti-spam: track when an IP last requested a token to enforce 1/hour
CREATE INDEX IF NOT EXISTS idx_central_tokens_issued_ip_created
    ON central_tokens (issued_to_ip, created_at);