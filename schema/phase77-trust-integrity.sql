-- Phase 7.7: Trust & integrity (задача #1211, спека §10.1, §10.3, §10.4)
-- DB: bizdnai :5434
-- Безопасный прогон: IF NOT EXISTS, без ALTER существующих таблиц.

-- 1. Evidence level (V0-V4 per spec §10.1)
-- V0 = declared (default; no auto-verification)
-- V1 = documented (manual attestation)
-- V2 = auto observed (hooks / probes; default for new run_events)
-- V3 = independent verification (audit / 3rd-party)
-- V4 = continuous (live monitoring)
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS evidence_level TEXT NOT NULL DEFAULT 'V2'
    CHECK (evidence_level IN ('V0', 'V1', 'V2', 'V3', 'V4'));

-- 2. Freshness TTL (per spec §10.3)
-- Per-event-type: security=hours, vuln=days, policy=months, performance=days
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS freshness_category TEXT
    CHECK (freshness_category IS NULL OR freshness_category IN (
        'security', 'vuln', 'policy', 'performance', 'code_quality', 'other'
    ));
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS last_fresh_at TIMESTAMPTZ DEFAULT now();

-- 3. Integrity manifest hash (per spec §10.4)
-- SHA256 of: model + model_version + agent_name + agent_version + config snapshot
-- Joined from task_runs via run_id; we cache here for fast lookup.
ALTER TABLE run_events ADD COLUMN IF NOT EXISTS manifest_hash TEXT;

-- 4. Indexes for freshness + integrity lookups
CREATE INDEX IF NOT EXISTS run_events_freshness_idx
    ON run_events (freshness_category, last_fresh_at)
    WHERE freshness_category IS NOT NULL;
CREATE INDEX IF NOT EXISTS run_events_evidence_idx
    ON run_events (evidence_level);
CREATE INDEX IF NOT EXISTS run_events_manifest_idx
    ON run_events (manifest_hash)
    WHERE manifest_hash IS NOT NULL;

-- 5. View: Freshness status (per spec §10.3)
-- Returns per-category totals: total, fresh (within TTL), stale (past TTL).
CREATE OR REPLACE VIEW metrics_freshness_v AS
SELECT
    freshness_category,
    COUNT(*) AS total_events,
    COUNT(*) FILTER (
        WHERE freshness_category = 'security'
          AND last_fresh_at > now() - interval '1 day'
    ) AS fresh_security_1d,
    COUNT(*) FILTER (
        WHERE freshness_category = 'performance'
          AND last_fresh_at > now() - interval '1 day'
    ) AS fresh_performance_1d,
    COUNT(*) FILTER (
        WHERE freshness_category IN ('vuln', 'code_quality')
          AND last_fresh_at > now() - interval '7 days'
    ) AS fresh_vuln_7d,
    COUNT(*) FILTER (
        WHERE freshness_category = 'policy'
          AND last_fresh_at > now() - interval '30 days'
    ) AS fresh_policy_30d,
    COUNT(*) FILTER (
        WHERE (freshness_category = 'security' AND last_fresh_at <= now() - interval '1 day')
           OR (freshness_category = 'performance' AND last_fresh_at <= now() - interval '1 day')
           OR (freshness_category IN ('vuln', 'code_quality') AND last_fresh_at <= now() - interval '7 days')
           OR (freshness_category = 'policy' AND last_fresh_at <= now() - interval '30 days')
    ) AS stale_count
FROM run_events
WHERE freshness_category IS NOT NULL
GROUP BY freshness_category;

COMMENT ON VIEW metrics_freshness_v IS 'Per spec §10.3: per-category TTL. Stale events should be marked or excluded from aggregates.';

-- 6. View: Integrity manifest distribution (per spec §10.4)
-- Joins run_events with task_runs to get the model. distinct_manifests > 1
-- means the configuration changed mid-period for that model.
CREATE OR REPLACE VIEW metrics_integrity_v AS
SELECT
    tr.model,
    COUNT(DISTINCT re.manifest_hash) AS distinct_manifests,
    COUNT(*) AS total_events,
    MIN(re.manifest_hash) AS sample_manifest,
    MIN(re.last_fresh_at) AS earliest_event,
    MAX(re.last_fresh_at) AS latest_event
FROM run_events re
JOIN task_runs tr ON tr.id = re.run_id
WHERE re.manifest_hash IS NOT NULL
GROUP BY tr.model;

COMMENT ON VIEW metrics_integrity_v IS 'Per spec §10.4: distinct manifest hashes per model > 1 = config drift in the period.';

-- 7. View: Evidence level distribution (per spec §10.1)
-- Used by dashboard to filter V0/V1 vs V2-V4.
CREATE OR REPLACE VIEW metrics_evidence_v AS
SELECT
    evidence_level,
    COUNT(*) AS total_events,
    COUNT(*) FILTER (WHERE evidence_level IN ('V2', 'V3', 'V4')) AS trusted_events
FROM run_events
GROUP BY evidence_level
ORDER BY evidence_level;

COMMENT ON VIEW metrics_evidence_v IS 'Per spec §10.1: V0/V1 = weak (declared/manual), V2-V4 = trusted (auto/independent/continuous).';
