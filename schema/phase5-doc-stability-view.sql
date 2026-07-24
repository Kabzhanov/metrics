-- Phase 5: Documentation Synchronization + Code Stability views
-- BizDNAI Metrics Platform, задача #1176, фаза 5/7

CREATE OR REPLACE VIEW metrics_doc_sync_v AS
SELECT
    tr.model,
    t.project_id,
    date_trunc('day', tr.started_at)::date AS day,
    COUNT(DISTINCT tr.task_id) AS total_tasks,
    COUNT(*) FILTER (WHERE tr.status='success' AND COALESCE(re.review_approved, false)) AS synced_tasks,
    ROUND(
        COUNT(*) FILTER (WHERE tr.status='success' AND COALESCE(re.review_approved, false))::numeric /
        NULLIF(COUNT(DISTINCT tr.task_id), 0),
        4
    ) AS doc_sync_rate
FROM task_runs tr
LEFT JOIN tasks t ON t.id = tr.task_id
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, t.project_id, date_trunc('day', tr.started_at);

CREATE OR REPLACE VIEW metrics_code_stability_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    COUNT(*) AS total_runs,
    COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false)) AS stable_runs,
    ROUND(
        COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false))::numeric / NULLIF(COUNT(*), 0),
        4
    ) AS stability_rate
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);