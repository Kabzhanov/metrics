-- Phase 7.7: Context Efficiency view (задача #1206, фаза 7.7)
-- Per spec §8.9: Context Efficiency = Verified Successes / 1M tokens
-- Per spec §8.5 (revised): Median/P75/P90 use real percentile_cont (already in phase2)

CREATE OR REPLACE VIEW metrics_context_efficiency_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE tr.status='success') AS verified_successes,
    -- 1M tokens (input + output) per spec §8.9
    ROUND(
        (SUM(COALESCE(tr.input_tokens, 0)) + SUM(COALESCE(tr.output_tokens, 0))) / 1000000.0,
        4
    ) AS total_tokens_m,
    ROUND(
        COUNT(*) FILTER (WHERE tr.status='success')::numeric /
        NULLIF((SUM(COALESCE(tr.input_tokens, 0)) + SUM(COALESCE(tr.output_tokens, 0))) / 1000000.0, 0),
        2
    ) AS context_efficiency  -- verified successes per 1M tokens
FROM task_runs tr
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);

COMMENT ON VIEW metrics_context_efficiency_v IS 'Context Efficiency per model × day (задача #1206, спек §8.9). verified_successes per 1M tokens. NULL если 0 tokens.';