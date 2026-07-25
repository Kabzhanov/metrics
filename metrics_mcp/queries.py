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

# Phase 7.7: normalized task/run/benchmark metrics from the Phase 1 schema.
GET_TASK_METRICS = """
SELECT tr.model,
       COUNT(*) AS n,
       AVG(NULLIF(tr.duration_sec, 0)) AS avg_duration,
       AVG(NULLIF(tr.cost, 0)) AS avg_cost,
       COALESCE(re.build_passed, false) AS build_passed,
       COALESCE(re.tests_passed, false) AS tests_passed,
       COALESCE(re.human_accepted, false) AS human_accepted,
       COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false)) AS stable_runs
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.task_id = %s
GROUP BY tr.model, re.build_passed, re.tests_passed, re.human_accepted
ORDER BY tr.model
"""

GET_RUN_METRICS = """
SELECT tr.id AS run_id,
       tr.model,
       tr.agent_name AS agent,
       tr.started_at,
       tr.completed_at,
       tr.duration_sec,
       tr.cost AS cost_usd,
       tr.input_tokens,
       tr.output_tokens,
       tr.cached_tokens,
       re.id AS evaluation_id,
       re.agent_completed,
       re.build_passed,
       re.tests_passed,
       re.lint_passed,
       re.acceptance_passed,
       re.human_accepted,
       re.review_approved,
       re.stable_after_7d,
       re.stable_after_30d,
       re.reopened,
       re.evaluation_score,
       hi.id AS intervention_id,
       hi.timestamp AS intervention_timestamp,
       hi.intervention_type,
       hi.severity AS intervention_severity,
       hi.description AS intervention_description,
       hi.estimated_minutes
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
LEFT JOIN human_interventions hi ON hi.run_id = tr.id
WHERE tr.id = %s
ORDER BY hi.timestamp, hi.id
"""

COMPARE_RUNS = """
SELECT tr.id,
       tr.model,
       tr.started_at,
       tr.duration_sec,
       tr.cost AS cost_usd,
       tr.status,
       re.build_passed,
       re.tests_passed,
       re.human_accepted,
       re.reopened
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.id = ANY(%s)
ORDER BY tr.id
"""

GET_MODEL_PROFILE = """
SELECT t.task_type,
       COUNT(*) AS n,
       AVG(CASE WHEN tr.status = 'success' THEN 1.0 ELSE 0.0 END) AS success_rate,
       AVG(NULLIF(tr.duration_sec, 0)) AS avg_dur,
       AVG(NULLIF(tr.cost, 0)) AS avg_cost
FROM task_runs tr
LEFT JOIN tasks t ON t.id = tr.task_id
WHERE tr.model = %s
  AND tr.started_at > now() - make_interval(days => %s)
GROUP BY t.task_type
ORDER BY t.task_type NULLS LAST
"""

GET_BENCHMARK_RESULT = """
SELECT *
FROM benchmarks
WHERE id = %s
"""

GET_BENCHMARK_RUNS = """
SELECT *
FROM task_runs
WHERE benchmark_id = %s
ORDER BY id
"""

# Phase 1 has no run_evaluations.docs_validated column. V1 uses
# human_accepted as the documented-result proxy requested by the specification.
GET_DOCUMENTATION_HEALTH = """
SELECT date_trunc('day', tr.started_at) AS day,
       COUNT(*) FILTER (WHERE COALESCE(re.human_accepted, false)) AS docs_synced,
       COUNT(*) AS total_tasks,
       ROUND(
           COUNT(*) FILTER (WHERE COALESCE(re.human_accepted, false))::numeric
           / NULLIF(COUNT(*), 0),
           4
       ) AS doc_sync_rate
FROM task_runs tr
JOIN tasks t ON t.id = tr.task_id
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE t.project_id = %s
  AND tr.started_at > now() - make_interval(days => %s)
GROUP BY date_trunc('day', tr.started_at)
ORDER BY day
"""

__all__ = [
    "COMPARE_MODELS",
    "COMPARE_RUNS",
    "DEGRADATION_REPORT",
    "GET_BENCHMARK_RESULT",
    "GET_BENCHMARK_RUNS",
    "GET_DOCUMENTATION_HEALTH",
    "GET_METRICS",
    "GET_MODEL_PROFILE",
    "GET_MQI",
    "GET_RUN_METRICS",
    "GET_TASK_METRICS",
    "RECOMMEND_MODEL",
]
