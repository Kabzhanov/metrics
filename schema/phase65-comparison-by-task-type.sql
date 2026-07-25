-- Phase 6.5: Comparison by Task Type (задача #1176, фаза 6.5)
-- DB: bizdnai :5434
-- View: metrics_by_task_type_v — агрегаты по model × task_type.
-- Phase 1 done: tasks.task_type есть, task_runs.task_id FK.
-- Идемпотентно: CREATE OR REPLACE VIEW.
--
-- Группировка: model × task_type
-- Окно: последние 90 дней (затем фильтр period на уровне API).
--
-- Колонки:
--   model                          — модель (claude-opus-4-8, etc.)
--   task_type                      — architecture / feature / bugfix / refactoring / testing / unknown
--   total_runs                     — всего запусков
--   successful_runs                — со status='success'
--   success_rate                   — successful_runs / total_runs (0..1)
--   avg_duration_sec               — среднее duration_sec (NULLIF 0)
--   avg_cost_usd                   — средний cost (NULLIF 0)
--   stable_runs                    — runs без reopened
--   stability_rate                 — stable_runs / total_runs
--   mqi_preview                    — Model Quality Index preview (Phase 6.5)
--                                    0.25 * verified (build+tests+success)
--                                  + 0.20 * stable (success AND NOT reopened)
--                                  + 0.15 * (1 - hi_count/10)
--                                  + 0.10 * review_approved
--                                  + 0.30 * cost+context placeholders
--                                  NB: cost+context == 1.0 заглушка, пересмотрим в Phase 7.

CREATE OR REPLACE VIEW metrics_by_task_type_v AS
SELECT
    tr.model,
    COALESCE(t.task_type, 'unknown') AS task_type,
    COUNT(*)::INT AS total_runs,
    COUNT(*) FILTER (WHERE tr.status = 'success')::INT AS successful_runs,
    ROUND(
        AVG(CASE WHEN tr.status = 'success' THEN 1.0 ELSE 0.0 END)::numeric,
        4
    ) AS success_rate,
    ROUND(AVG(NULLIF(tr.duration_sec, 0))::numeric, 1) AS avg_duration_sec,
    ROUND(AVG(NULLIF(tr.cost, 0))::numeric, 4) AS avg_cost_usd,
    COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false))::INT AS stable_runs,
    ROUND(
        COUNT(*) FILTER (WHERE NOT COALESCE(re.reopened, false))::numeric
        / NULLIF(COUNT(*), 0),
        4
    ) AS stability_rate,
    ROUND(
        AVG(
            0.25 * (CASE WHEN tr.status = 'success'
                           AND COALESCE(re.build_passed, false)
                           AND COALESCE(re.tests_passed, false)
                         THEN 1.0 ELSE 0.0 END)
          + 0.20 * (CASE WHEN tr.status = 'success'
                           AND NOT COALESCE(re.reopened, false)
                         THEN 1.0 ELSE 0.0 END)
          + 0.15 * (1.0 - LEAST(
                          COALESCE((
                              SELECT COUNT(*)::numeric
                              FROM human_interventions hi
                              WHERE hi.run_id = tr.id
                          ), 0) / 10.0,
                          1.0
                      ))
          + 0.10 * (CASE WHEN COALESCE(re.review_approved, false) THEN 1.0 ELSE 0.0 END)
          + 0.30 * 1.0
        )::numeric,
        4
    ) AS mqi_preview
FROM task_runs tr
LEFT JOIN tasks t  ON t.id = tr.task_id
LEFT JOIN run_evaluations re ON re.run_id = tr.id
WHERE tr.started_at > now() - interval '90 days'
GROUP BY tr.model, COALESCE(t.task_type, 'unknown');

COMMENT ON VIEW metrics_by_task_type_v IS 'Сравнение моделей по task_type (задача #1176, фаза 6.5). Колонки: model, task_type, total_runs, successful_runs, success_rate, avg_duration_sec, avg_cost_usd, stable_runs, stability_rate, mqi_preview. Окно: 90 дней.';
