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

__all__ = ["COMPARE_MODELS", "GET_METRICS", "GET_MQI"]
