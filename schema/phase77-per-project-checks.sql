-- Phase 7.7: Per-project required_checks (задача #1207)
-- DB: bizdnai :5434
-- Per spec §8.1: набор обязательных проверок задаётся конфигурацией проекта.
--
-- 1) projects.required_checks — массив имён проверок, определяющих
--    "verified success" для runs этого проекта. По умолчанию
--    ['build_passed', 'tests_passed'] — backward-compatible с
--    прежним контрактом (раньше требовались все 5, теперь — настраивается).
--
-- 2) View metrics_verified_per_project_v — построчный расчёт is_verified
--    с учётом required_checks проекта. Используется новым
--    api_verified.php?project_id=N.
--
-- Старая view metrics_verified_v остаётся как есть (default
-- "build_passed AND tests_passed") — это сохраняет backward compat
-- с уже работающим фронтом / метриками.

ALTER TABLE projects
    ADD COLUMN IF NOT EXISTS required_checks TEXT[] NOT NULL
    DEFAULT ARRAY['build_passed', 'tests_passed'];

COMMENT ON COLUMN projects.required_checks IS 'Массив обязательных проверок run_evaluations для verified_success (build_passed|tests_passed|lint_passed|acceptance_passed|agent_completed). Задача #1207, спек §8.1.';

-- Backfill: убедиться, что у всех существующих строк есть дефолт
UPDATE projects
SET required_checks = ARRAY['build_passed', 'tests_passed']
WHERE required_checks IS NULL;

CREATE INDEX IF NOT EXISTS projects_required_checks_idx ON projects USING GIN (required_checks);

-- View: per-run verified flag с учётом project.required_checks.
-- Джойнит task → project, вытаскивает флаги из run_evaluations и
-- сводит их по списку required_checks.
CREATE OR REPLACE VIEW metrics_verified_per_project_v AS
WITH per_run AS (
    SELECT
        tr.id                                          AS run_id,
        tr.task_id,
        tr.model,
        date_trunc('day', tr.started_at)::date         AS day,
        tr.status,
        t.project_id,
        p.required_checks,
        COALESCE(re.agent_completed,   FALSE)          AS agent_completed,
        COALESCE(re.build_passed,      FALSE)          AS build_passed,
        COALESCE(re.tests_passed,      FALSE)          AS tests_passed,
        COALESCE(re.lint_passed,       FALSE)          AS lint_passed,
        COALESCE(re.acceptance_passed, FALSE)          AS acceptance_passed
    FROM task_runs tr
    LEFT JOIN tasks t          ON t.id = tr.task_id
    LEFT JOIN projects p       ON p.id = t.project_id
    LEFT JOIN run_evaluations re ON re.run_id = tr.id
    WHERE tr.started_at >= now() - interval '90 days'
),
flagged AS (
    SELECT
        per_run.*,
        -- "verified" iff status='success' и ВСЕ checks из required_checks = TRUE.
        -- Через AND цепочки ANY(...) без JSON — совместимо с PG ≥ 11.
        (
            per_run.status = 'success'
            AND (
                NOT ('agent_completed'   = ANY(per_run.required_checks)) OR per_run.agent_completed
            )
            AND (
                NOT ('build_passed'      = ANY(per_run.required_checks)) OR per_run.build_passed
            )
            AND (
                NOT ('tests_passed'      = ANY(per_run.required_checks)) OR per_run.tests_passed
            )
            AND (
                NOT ('lint_passed'       = ANY(per_run.required_checks)) OR per_run.lint_passed
            )
            AND (
                NOT ('acceptance_passed' = ANY(per_run.required_checks)) OR per_run.acceptance_passed
            )
        ) AS is_verified
    FROM per_run
)
SELECT
    model,
    day,
    project_id,
    required_checks,
    COUNT(*)::INT                                  AS total_runs,
    SUM(CASE WHEN is_verified THEN 1 ELSE 0 END)::INT AS verified_success,
    SUM(CASE WHEN is_verified AND status = 'success' THEN 1 ELSE 0 END)::INT
        AS verified_success_strict
FROM flagged
GROUP BY model, day, project_id, required_checks;

COMMENT ON VIEW metrics_verified_per_project_v IS 'Verified-success по model × day × project (задача #1207, спек §8.1). required_checks берётся из projects и определяет набор проверок. Используется api_verified.php?project_id=N.';

-- Готово. Существующие таблицы и view не модифицируются — только
-- additive: новая колонка + новый view.