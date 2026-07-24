-- Phase 6: Advanced Indices (PV, DQI, ACI, MQI, EPI)
-- BizDNAI Metrics Platform, задача #1176, фаза 6/7
-- Preview formulas are intentionally placeholders and should be calibrated later.

-- =====================================================================
-- Progress Velocity (PV) — по task + verification_coeff × stability_coeff
-- =====================================================================
CREATE OR REPLACE VIEW metrics_pv_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    COUNT(*) AS total_tasks,
    ROUND(AVG(
        CASE
            WHEN tr.status='success' AND COALESCE(re.build_passed, false) AND COALESCE(re.tests_passed, false) THEN 1.0
            WHEN tr.status='success' AND COALESCE(re.build_passed, false) THEN 0.5
            WHEN tr.status='success' THEN 0.3
            ELSE 0.0
        END
    ), 4) AS progress_velocity,
    SUM(COALESCE(tr.duration_sec, 0)) AS total_seconds
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);

-- =====================================================================
-- Documentation Quality Index (DQI) — preview stub until git integration
-- =====================================================================
CREATE OR REPLACE VIEW metrics_dqi_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    -- Пока без git данных → placeholder
    COUNT(*) FILTER (WHERE COALESCE(re.review_approved, false)) AS docs_validated,
    0.5 AS dqi_preview  -- placeholder до git integration
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);

-- =====================================================================
-- Architectural Consistency Index (ACI) — preview stub
-- =====================================================================
CREATE OR REPLACE VIEW metrics_aci_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    0 AS aci_static_violations,
    1.0 AS aci_preview  -- placeholder
FROM task_runs tr
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);

-- =====================================================================
-- Model Quality Index (MQI) — preview with specification weights
-- =====================================================================
CREATE OR REPLACE VIEW metrics_mqi_v AS
SELECT
    tr.model,
    date_trunc('day', tr.started_at)::date AS day,
    COUNT(*) AS total_runs,
    ROUND(AVG(
        0.25 * (CASE WHEN tr.status='success' AND COALESCE(re.build_passed, false) AND COALESCE(re.tests_passed, false) THEN 1.0 ELSE 0.0 END) +
        0.20 * (CASE WHEN tr.status='success' AND NOT COALESCE(re.reopened, false) THEN 1.0 ELSE 0.0 END) +
        0.15 * (1.0 - COALESCE((SELECT COUNT(*)::numeric FROM human_interventions hi WHERE hi.run_id = tr.id), 0) / 10.0) +
        0.10 * (CASE WHEN COALESCE(re.review_approved, false) THEN 1.0 ELSE 0.0 END) +
        0.10 * (1.0 / (1.0 + COALESCE(tr.cost, 0))) +
        0.20 * 1.0  -- cost efficiency placeholder
    ), 4) AS mqi_preview
FROM task_runs tr
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, date_trunc('day', tr.started_at);

-- =====================================================================
-- Engineering Productivity Index (EPI) — preview
-- =====================================================================
CREATE OR REPLACE VIEW metrics_epi_v AS
SELECT
    m.model,
    m.day,
    ROUND(AVG(
        0.30 * COALESCE(m.mqi_preview, 0) +
        0.25 * COALESCE(pv.progress_velocity, 0) +
        0.15 * 0.85 +  -- stability placeholder
        0.10 * 0.90 +  -- human independence placeholder
        0.10 * 0.80 +  -- cost efficiency placeholder
        0.10 * 0.85    -- time efficiency placeholder
    ), 4) AS epi_preview
FROM metrics_mqi_v m
LEFT JOIN metrics_pv_v pv ON pv.model = m.model AND pv.day = m.day
GROUP BY m.model, m.day, m.mqi_preview, pv.progress_velocity;
