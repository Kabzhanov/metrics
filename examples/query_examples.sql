-- Read-only examples for dashboards and ad-hoc analysis.
-- Replace the interval or model filter for your reporting window.

-- Daily success rate and cost by model.
SELECT
    date_trunc('day', started_at) AS day,
    model,
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE status = 'success') AS successful_runs,
    ROUND(
        100.0 * COUNT(*) FILTER (WHERE status = 'success') / NULLIF(COUNT(*), 0),
        2
    ) AS success_rate_pct,
    ROUND(AVG(cost_usd) FILTER (WHERE ended_at IS NOT NULL)::numeric, 4) AS avg_cost_usd,
    ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)::numeric, 2) AS avg_duration_sec
FROM metrics_tasks
WHERE started_at >= now() - interval '30 days'
GROUP BY day, model
ORDER BY day DESC, model;

-- Recent failed or interrupted runs for an operations view.
SELECT task_id, model, project, status, started_at, ended_at, notes
FROM metrics_tasks
WHERE status <> 'success'
ORDER BY started_at DESC
LIMIT 20;

-- Pre-aggregated view used by the dashboard.
SELECT *
FROM metrics_tasks_v
WHERE day >= current_date - 30
ORDER BY day DESC, model;
