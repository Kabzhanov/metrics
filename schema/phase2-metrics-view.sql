-- Phase 2: Verified Metrics View (задача #1176, фаза 2/7)
-- DB: bizdnai :5434
-- View: metrics_verified_v — агрегаты по model × day на основе Phase 1 таблиц.
-- Не модифицирует таблицы; CREATE OR REPLACE VIEW — идемпотентно.
--
-- Метрики:
--   verified_success_rate  — agent_completed AND build AND tests AND lint AND acceptance
--   first_pass_success     — runs без human_intervention / total runs
--   rework_count           — runs, где task_id имеет > 1 run
--   rework_time_ratio      — NULL пока (нет данных по времени переработки)
--   rework_lines_ratio     — NULL пока (нет git diff)
--   human_intervention_rate — count(human_interventions) / count(runs)
--   median / p75 / p90 duration — percentile_cont
--   cost_per_verified_success — sum(cost) / verified_success_count
--   retry_rate             — distinct task_id с runs>1 / distinct task_id
--   tool_failure_rate      — tool_error / tool* events
--
-- Группировка: model × date_trunc('day', started_at)
-- Окно: последние 90 дней.

CREATE OR REPLACE VIEW metrics_verified_v AS
WITH
-- Каждый run с присоединёнными флагами eval/intervention.
runs_base AS (
    SELECT
        tr.id              AS run_id,
        tr.task_id,
        tr.model,
        date_trunc('day', tr.started_at)::date AS day,
        tr.status,
        tr.cost,
        tr.duration_sec,
        COALESCE(re.agent_completed, FALSE)  AS agent_completed,
        COALESCE(re.build_passed,    FALSE)  AS build_passed,
        COALESCE(re.tests_passed,    FALSE)  AS tests_passed,
        COALESCE(re.lint_passed,     FALSE)  AS lint_passed,
        COALESCE(re.acceptance_passed, FALSE) AS acceptance_passed
    FROM task_runs tr
    LEFT JOIN run_evaluations re ON re.run_id = tr.id
    WHERE tr.started_at >= now() - interval '90 days'
),
-- Сколько human_interventions на каждый run.
runs_human AS (
    SELECT run_id, COUNT(*) AS hi_count
    FROM human_interventions
    GROUP BY run_id
),
-- task_id → сколько всего runs.
task_run_counts AS (
    SELECT task_id, COUNT(*) AS runs_n
    FROM task_runs
    WHERE started_at >= now() - interval '90 days'
    GROUP BY task_id
),
-- distinct task_id'ов по (model, day) для retry_rate.
distinct_tasks_per_md AS (
    SELECT model, day, COUNT(DISTINCT task_id) AS distinct_tasks
    FROM runs_base
    GROUP BY model, day
),
retry_tasks_per_md AS (
    SELECT rb.model, rb.day, COUNT(DISTINCT rb.task_id) AS retried_tasks
    FROM runs_base rb
    JOIN task_run_counts trc ON trc.task_id = rb.task_id
    WHERE trc.runs_n > 1
    GROUP BY rb.model, rb.day
),
-- tool* события по (model, day) — НЕ джойним напрямую к runs_base,
-- чтобы избежать fan-out. Сначала агрегируем на уровне run.
run_tool_stats AS (
    SELECT
        tr.model,
        date_trunc('day', tr.started_at)::date AS day,
        SUM(CASE WHEN re.event_type = 'tool_error' THEN 1 ELSE 0 END) AS tool_errors,
        SUM(CASE WHEN re.event_type LIKE 'tool%' THEN 1 ELSE 0 END) AS tool_events
    FROM run_events re
    JOIN task_runs tr ON tr.id = re.run_id
    WHERE tr.started_at >= now() - interval '90 days'
    GROUP BY tr.model, date_trunc('day', tr.started_at)
)
SELECT
    rb.model,
    rb.day,
    -- total runs
    COUNT(*)::INT AS total_runs,
    -- verified success (все 5 флагов TRUE)
    SUM(
        CASE WHEN rb.agent_completed
              AND rb.build_passed
              AND rb.tests_passed
              AND rb.lint_passed
              AND rb.acceptance_passed
             THEN 1 ELSE 0 END
    )::INT AS verified_success,
    -- first-pass success: runs без human_intervention / total
    SUM(CASE WHEN COALESCE(rh.hi_count, 0) = 0 THEN 1 ELSE 0 END)::INT
        AS first_pass_runs,
    -- rework count (iterations): runs, у которых task_id имеет > 1 run
    SUM(CASE WHEN COALESCE(trc.runs_n, 1) > 1 THEN 1 ELSE 0 END)::INT
        AS rework_count,
    -- rework time ratio — NULL пока
    NULL::NUMERIC AS rework_time_ratio,
    -- rework lines ratio — NULL пока
    NULL::NUMERIC AS rework_lines_ratio,
    -- human interventions
    COALESCE(SUM(rh.hi_count), 0)::INT AS human_interventions,
    -- median / p75 / p90 duration_sec
    percentile_cont(0.5) WITHIN GROUP (ORDER BY rb.duration_sec)
        FILTER (WHERE rb.duration_sec IS NOT NULL)::NUMERIC AS median_duration_sec,
    percentile_cont(0.75) WITHIN GROUP (ORDER BY rb.duration_sec)
        FILTER (WHERE rb.duration_sec IS NOT NULL)::NUMERIC AS p75_duration_sec,
    percentile_cont(0.90) WITHIN GROUP (ORDER BY rb.duration_sec)
        FILTER (WHERE rb.duration_sec IS NOT NULL)::NUMERIC AS p90_duration_sec,
    -- total cost (USD)
    COALESCE(SUM(rb.cost), 0)::NUMERIC(12,4) AS total_cost_usd,
    -- cost per verified success
    CASE
        WHEN SUM(
            CASE WHEN rb.agent_completed
                  AND rb.build_passed
                  AND rb.tests_passed
                  AND rb.lint_passed
                  AND rb.acceptance_passed
                 THEN 1 ELSE 0 END
        ) > 0
        THEN (COALESCE(SUM(rb.cost), 0)
              / SUM(
                  CASE WHEN rb.agent_completed
                        AND rb.build_passed
                        AND rb.tests_passed
                        AND rb.lint_passed
                        AND rb.acceptance_passed
                       THEN 1 ELSE 0 END
              ))::NUMERIC(12,4)
        ELSE NULL
    END AS cost_per_verified_success,
    -- retry_rate (distinct task_id с runs>1 / distinct task_id)
    CASE
        WHEN COALESCE(dtp.distinct_tasks, 0) > 0
        THEN ROUND(COALESCE(rtp.retried_tasks, 0)::NUMERIC / dtp.distinct_tasks, 4)
        ELSE 0
    END AS retry_rate,
    -- tool_failure_rate (tool_error / tool*)
    CASE
        WHEN COALESCE(rts.tool_events, 0) > 0
        THEN ROUND(rts.tool_errors::NUMERIC / rts.tool_events, 4)
        ELSE NULL
    END AS tool_failure_rate
FROM runs_base rb
LEFT JOIN runs_human         rh  ON rh.run_id = rb.run_id
LEFT JOIN task_run_counts    trc ON trc.task_id = rb.task_id
LEFT JOIN distinct_tasks_per_md dtp ON dtp.model = rb.model AND dtp.day = rb.day
LEFT JOIN retry_tasks_per_md    rtp ON rtp.model = rb.model AND rtp.day = rb.day
LEFT JOIN run_tool_stats        rts ON rts.model = rb.model AND rts.day = rb.day
GROUP BY rb.model, rb.day, dtp.distinct_tasks, rtp.retried_tasks, rts.tool_events, rts.tool_errors;

COMMENT ON VIEW metrics_verified_v IS 'Verified-metrics агрегаты по model × day (задача #1176, фаза 2). Колонки: model, day, total_runs, verified_success, first_pass_runs, rework_count, rework_time_ratio, rework_lines_ratio, human_interventions, median/p75/p90_duration_sec, total_cost_usd, cost_per_verified_success, retry_rate, tool_failure_rate.';