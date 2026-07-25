-- Phase 5.5: Git Integration — views для пересчёта метрик из run_artifacts
-- DB: bizdnai :5434
-- Зависимость: phase1-schema.sql (run_artifacts, run_evaluations, task_runs)
-- Безопасный прогон: DROP IF EXISTS + CREATE OR REPLACE.
-- ВАЖНО: psql -f запускает файл в одной транзакции; все CREATE VIEW
-- должны быть валидны, иначе весь файл откатится. Поэтому при локальной
-- правке рекомендуется запускать view-блоки по отдельности.

-- =====================================================================
-- 1. work_set_v — какие файлы менялись в каком запуске
-- =====================================================================
DROP VIEW IF EXISTS metrics_rework_lines_v CASCADE;
DROP VIEW IF EXISTS metrics_csi_v CASCADE;
DROP VIEW IF EXISTS metrics_doc_sync_v_v2 CASCADE;
DROP VIEW IF EXISTS metrics_git_overview_v CASCADE;
DROP VIEW IF EXISTS work_set_v CASCADE;

CREATE OR REPLACE VIEW work_set_v AS
SELECT
    ra.run_id,
    ra.path                                 AS file_path,
    (ra.metadata->>'add')::int              AS adds,
    (ra.metadata->>'del')::int              AS dels,
    (ra.metadata->>'add')::int
        + (ra.metadata->>'del')::int        AS churn,
    tr.model,
    tr.started_at
FROM run_artifacts ra
JOIN task_runs tr ON tr.id = ra.run_id
WHERE ra.artifact_type = 'git_diff';

-- =====================================================================
-- 2. metrics_rework_lines_v
--    Средний rework_lines на файл по моделям.
-- =====================================================================
CREATE OR REPLACE VIEW metrics_rework_lines_v AS
WITH first_run AS (
    SELECT file_path, MIN(started_at) AS first_at
    FROM work_set_v
    GROUP BY file_path
),
rework AS (
    SELECT
        ws.file_path,
        ws.model,
        SUM(ws.churn) FILTER (WHERE ws.started_at > fr.first_at) AS rework_lines
    FROM work_set_v ws
    JOIN first_run fr ON fr.file_path = ws.file_path
    GROUP BY ws.file_path, ws.model
)
SELECT
    model,
    COUNT(*)                                AS files_with_rework,
    ROUND(AVG(rework_lines)::numeric, 2)    AS avg_rework_lines,
    ROUND(SUM(rework_lines)::numeric, 2)    AS total_rework_lines,
    ROUND(AVG(rework_lines) FILTER (WHERE rework_lines > 0)::numeric, 2)
                                            AS avg_rework_when_present
FROM rework
GROUP BY model
ORDER BY avg_rework_lines DESC NULLS LAST;

COMMENT ON VIEW metrics_rework_lines_v IS
'Phase 5.5: средний rework на файл по моделям. rework = sum(add+del) '
'из run_artifacts после первого запуска этого файла.';

-- =====================================================================
-- 3. metrics_csi_v — Code Stability Index
--    CSI = 1 - (files_reworked / total_files_changed)
-- =====================================================================
CREATE OR REPLACE VIEW metrics_csi_v AS
WITH first_at AS (
    SELECT file_path, MIN(started_at) AS first_at
    FROM work_set_v GROUP BY file_path
),
rework AS (
    SELECT ws.file_path, ws.model,
           SUM(ws.churn) AS rework_lines
    FROM work_set_v ws
    JOIN first_at fr ON fr.file_path = ws.file_path
    WHERE ws.started_at > fr.first_at
    GROUP BY ws.file_path, ws.model
),
all_files AS (
    SELECT file_path, model FROM work_set_v GROUP BY file_path, model
)
SELECT a.model,
       COUNT(*) AS files_total,
       COUNT(r.file_path) AS files_reworked,
       ROUND((1.0 - COUNT(r.file_path)::numeric / NULLIF(COUNT(*), 0))::numeric, 3) AS csi,
       CASE
           WHEN (1.0 - COUNT(r.file_path)::numeric / NULLIF(COUNT(*), 0)) >= 0.80 THEN 'stable'
           WHEN (1.0 - COUNT(r.file_path)::numeric / NULLIF(COUNT(*), 0)) >= 0.50 THEN 'moderate'
           ELSE 'unstable'
       END AS stability_band
FROM all_files a
LEFT JOIN rework r ON r.file_path = a.file_path AND r.model = a.model
GROUP BY a.model
ORDER BY csi DESC NULLS LAST;

COMMENT ON VIEW metrics_csi_v IS
'Phase 5.5: Code Stability Index по моделям. 1.0 = стабильно, '
'0.5 = половина файлов переписана, <0.5 = нестабильно.';

-- =====================================================================
-- 4. metrics_doc_sync_v_v2 — синхронность правок кода и документации
-- =====================================================================
CREATE OR REPLACE VIEW metrics_doc_sync_v_v2 AS
WITH per_run AS (
    SELECT
        run_id,
        model,
        COUNT(*) FILTER (WHERE file_path ~* '\.(md|rst|txt)$'
                          OR file_path LIKE 'docs/%'
                          OR file_path LIKE 'README%') AS doc_files,
        COUNT(*) FILTER (WHERE file_path ~* '\.(py|js|jsx|ts|tsx|go|rs|'
                          'java|kt|swift|c|cpp|h|hpp|cs|rb|php|sql|sh)$'
                          AND file_path NOT LIKE 'docs/%') AS code_files,
        COUNT(*) AS total_files
    FROM work_set_v
    GROUP BY run_id, model
)
SELECT
    model,
    COUNT(*) AS runs,
    COUNT(*) FILTER (WHERE doc_files > 0 AND code_files > 0) AS synced_runs,
    COUNT(*) FILTER (WHERE code_files > 0) AS code_runs,
    ROUND(
        COUNT(*) FILTER (WHERE doc_files > 0 AND code_files > 0)::numeric
        / NULLIF(COUNT(*) FILTER (WHERE code_files > 0), 0),
        3
    ) AS doc_sync_rate,
    ROUND(AVG(doc_files), 2) AS avg_doc_files,
    ROUND(AVG(code_files), 2) AS avg_code_files
FROM per_run
GROUP BY model
ORDER BY doc_sync_rate DESC NULLS LAST;

COMMENT ON VIEW metrics_doc_sync_v_v2 IS
'Phase 5.5: доля запусков, где code и docs менялись вместе (синхронно).';

-- =====================================================================
-- 5. metrics_git_overview_v — one-shot сводка для дашборда
-- =====================================================================
CREATE OR REPLACE VIEW metrics_git_overview_v AS
SELECT
    (SELECT COUNT(*) FROM run_artifacts WHERE artifact_type='git_diff') AS total_artifacts,
    (SELECT COUNT(DISTINCT run_id) FROM run_artifacts WHERE artifact_type='git_diff') AS runs_with_diff,
    (SELECT COUNT(DISTINCT path) FROM run_artifacts WHERE artifact_type='git_diff') AS unique_files,
    (SELECT COALESCE(SUM(size), 0) FROM run_artifacts WHERE artifact_type='git_diff') AS total_churn,
    (SELECT COUNT(*) FROM run_evaluations WHERE reopened = TRUE) AS reopened_count,
    (SELECT COUNT(*) FROM run_evaluations WHERE stable_after_30d = TRUE) AS stable_30d_count;

COMMENT ON VIEW metrics_git_overview_v IS
'Phase 5.5: верхнеуровневая сводка — сколько артефактов, файлов, '
'строк churn, сколько ран-ов reopened/stable_30d.';
