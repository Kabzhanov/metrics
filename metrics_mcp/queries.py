"""Parameterized SQL statements used by the metrics MCP server.

Keeping SQL in this module makes the database boundary easy to review and
allows query-focused tests without starting an MCP transport or PostgreSQL.
"""

GET_METRICS = """
SELECT model,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE status = 'success') AS ok_count,
       COUNT(*) FILTER (WHERE status = 'failed') AS failed_count,
       ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)) AS avg_sec,
       ROUND(AVG(cost_usd) FILTER (WHERE ended_at IS NOT NULL)::numeric, 4) AS avg_cost
FROM metrics_tasks
WHERE model = %s AND started_at >= %s
GROUP BY model
"""

COMPARE_MODELS = """
SELECT model,
       COUNT(*) AS total,
       COUNT(*) FILTER (WHERE status = 'success') AS ok_count,
       ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)) AS avg_sec,
       ROUND(AVG(cost_usd) FILTER (WHERE ended_at IS NOT NULL)::numeric, 4) AS avg_cost
FROM metrics_tasks
WHERE spec_id = %s AND ended_at IS NOT NULL
GROUP BY model
ORDER BY model
"""

GET_MQI = """
SELECT COUNT(*) AS total,
       COUNT(*) FILTER (WHERE status = 'success') AS ok_count,
       ROUND(AVG(cost_usd) FILTER (WHERE ended_at IS NOT NULL)::numeric, 4) AS avg_cost
FROM metrics_tasks
WHERE model = %s AND started_at >= %s
"""

# Phase 7: recommend_model + degradation_report.
# NOTE: task_type is mapped to the `project` column (closest existing category).
RECOMMEND_MODEL = """
SELECT
    model,
    COUNT(*)                                                    AS n,
    COUNT(*) FILTER (WHERE status = 'success')                  AS ok_count,
    ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)) AS avg_sec,
    ROUND(AVG(cost_usd) FILTER (WHERE ended_at IS NOT NULL)::numeric, 4) AS avg_cost
FROM metrics_tasks
WHERE project = %s
  AND started_at >= %s
  AND ended_at IS NOT NULL
GROUP BY model
ORDER BY ok_count DESC, avg_sec ASC NULLS LAST, avg_cost ASC NULLS LAST
"""

DEGRADATION_REPORT = """
WITH recent AS (
    SELECT
        COUNT(*)                                                    AS n_recent,
        COUNT(*) FILTER (WHERE status = 'success')                  AS ok_recent,
        ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)) AS avg_sec_recent
    FROM metrics_tasks
    WHERE model = %s
      AND started_at >= now() - make_interval(days => %s)
      AND ended_at IS NOT NULL
),
prior AS (
    SELECT
        COUNT(*)                                                    AS n_prior,
        COUNT(*) FILTER (WHERE status = 'success')                  AS ok_prior,
        ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL)) AS avg_sec_prior
    FROM metrics_tasks
    WHERE model = %s
      AND started_at >= now() - make_interval(days => %s * 2)
      AND started_at <  now() - make_interval(days => %s)
      AND ended_at IS NOT NULL
)
SELECT
    r.n_recent,
    r.ok_recent,
    r.avg_sec_recent,
    p.n_prior,
    p.ok_prior,
    p.avg_sec_prior,
    CASE WHEN p.ok_prior > 0
         THEN ROUND(((r.ok_recent::numeric / NULLIF(r.n_recent,0)) -
                     (p.ok_prior::numeric / p.n_prior))::numeric, 4)
         ELSE NULL END AS success_rate_delta,
    CASE WHEN p.avg_sec_prior > 0
         THEN ROUND(((r.avg_sec_recent::numeric - p.avg_sec_prior) /
                     p.avg_sec_prior)::numeric, 4)
         ELSE NULL END AS latency_delta_pct
FROM recent r CROSS JOIN prior p
"""

__all__ = [
    "COMPARE_MODELS",
    "DEGRADATION_REPORT",
    "GET_METRICS",
    "GET_MQI",
    "RECOMMEND_MODEL",
]
