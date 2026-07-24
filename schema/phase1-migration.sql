-- Phase 1: Seed data migration (задача #1176, фаза 1/7)
-- DB: bizdnai :5434
-- Идемпотентно: ON CONFLICT DO NOTHING.
-- НЕ трогает существующие таблицы (task_log, metrics_tasks, task_steps, metrics_tasks_v, metrics_model_pricing).

BEGIN;

-- =====================================================================
-- 1. PROJECTS: одна запись на каждый distinct project из metrics_tasks
-- =====================================================================
-- 'bizdnai' ПЕРВЫМ с id=1 (требование ТЗ)
INSERT INTO projects (id, name, repository, default_branch, metadata)
VALUES (1, 'bizdnai', 'bizdnai-orchestrator', 'main', '{"source":"seed","phase":1}')
ON CONFLICT (id) DO NOTHING;

-- Sequence теперь строго после id=1
SELECT setval('projects_id_seq', 1, true);

-- Остальные проекты (получат id 2, 3, ...)
INSERT INTO projects (id, name, repository, default_branch, metadata)
SELECT
    nextval('projects_id_seq'),
    p.project,
    CASE p.project
        WHEN 'planet'         THEN 'bizdnai-orchestrator'
        WHEN 'client-morozov' THEN 'morozov-crm'
        WHEN 'client-tts'     THEN 'tts-aggregator'
        ELSE NULL
    END,
    'main',
    jsonb_build_object('source', 'metrics_tasks', 'seeded_at', now())
FROM (SELECT DISTINCT project FROM metrics_tasks WHERE project IS NOT NULL AND project <> 'bizdnai') p
ON CONFLICT (name) DO NOTHING;

-- На случай NULL-проекта (метрика без project)
INSERT INTO projects (id, name, repository, default_branch, metadata)
SELECT 100, 'unspecified', NULL, 'main', '{"source":"seed","phase":1,"note":"no project"}'
WHERE EXISTS (SELECT 1 FROM metrics_tasks WHERE project IS NULL)
  AND NOT EXISTS (SELECT 1 FROM projects WHERE name = 'unspecified')
ON CONFLICT (id) DO NOTHING;

SELECT setval('projects_id_seq', GREATEST((SELECT MAX(id) FROM projects), 1), true);

-- =====================================================================
-- 2. TASKS: одна запись 'MVP seed data' (id=1) + по одной на каждый проект
-- =====================================================================
-- id=1 — 'MVP seed data' для проекта bizdnai (требование ТЗ)
INSERT INTO tasks (id, project_id, spec_id, title, description, task_type, complexity, priority, progress_weight, status)
VALUES (1, 1, 'mvp-seed-2026-07', 'MVP seed data', 'Базовый seed для миграции из metrics_tasks (задача #1176, фаза 1)', 'other', 'simple', 'low', 1.0, 'in_progress')
ON CONFLICT (id) DO NOTHING;

SELECT setval('tasks_id_seq', 1, true);

-- Per-project: по одной обобщающей задаче (id 2, 3, ...)
INSERT INTO tasks (id, project_id, spec_id, title, description, task_type, complexity, priority, progress_weight, status)
SELECT
    nextval('tasks_id_seq'),
    p.id,
    'mvp-seed-' || p.name,
    'MVP seed: ' || p.name,
    'Агрегированная задача для запусков проекта ' || p.name,
    'other', 'simple', 'low', 1.0, 'in_progress'
FROM projects p
WHERE p.id <> 1
ON CONFLICT DO NOTHING;

SELECT setval('tasks_id_seq', GREATEST((SELECT MAX(id) FROM tasks), 1), true);

-- =====================================================================
-- 3. AGENT_CONFIGURATIONS: дефолтная запись для main (id=1)
-- =====================================================================
-- Дефолтная запись 'main' (id=1) — точная вставка
INSERT INTO agent_configurations (
    id, agent_name, agent_version, system_prompt_hash, settings_hash,
    reasoning_effort, permission_mode, sandbox_mode,
    enabled_mcp_servers, enabled_tools,
    memory_configuration, context_configuration, environment_hash
)
VALUES (
    1, 'main', '1.0.0', 'seed-' || md5('main-default'), 'seed-' || md5('settings-default'),
    'medium', 'bypassPermissions', 'worktree',
    ARRAY['context7','flux','bizdnai-metrics'],
    ARRAY['Read','Write','Edit','Bash','Grep','Glob','Agent'],
    '{"enabled": true, "backend": "agent_memory"}'::jsonb,
    '{"max_tokens": 200000, "compaction": "auto"}'::jsonb,
    'seed-' || md5('env-default')
)
ON CONFLICT (id) DO NOTHING;

-- Sequence уже на 1, следующий nextval() даст 2
SELECT setval('agent_configurations_id_seq', 1, true);

-- Per-agent конфиги со DEFAULT id (получат 2, 3, ...)
INSERT INTO agent_configurations (
    agent_name, agent_version, system_prompt_hash, settings_hash,
    reasoning_effort, permission_mode, sandbox_mode,
    enabled_mcp_servers, enabled_tools, environment_hash
)
SELECT DISTINCT
    mt.agent, 'unknown', 'seed-' || md5(COALESCE(mt.agent, 'unknown')), 'seed-default',
    'medium', 'default', 'worktree',
    ARRAY['context7','flux','bizdnai-metrics'],
    ARRAY['Read','Write','Edit','Bash','Grep','Glob','Agent'],
    'seed-' || md5(COALESCE(mt.agent, 'unknown') || '-env')
FROM metrics_tasks mt
WHERE mt.agent IS NOT NULL
ON CONFLICT (agent_name, agent_version, system_prompt_hash, settings_hash, environment_hash) DO NOTHING;

SELECT setval('agent_configurations_id_seq', GREATEST((SELECT MAX(id) FROM agent_configurations), 1), true);

-- =====================================================================
-- 4. TASK_RUNS: одна запись на каждую строку metrics_tasks
-- =====================================================================
-- Все 176 строк metrics_tasks → task_id=1 (MVP seed data)
INSERT INTO task_runs (
    id, task_id, benchmark_id, provider, model, model_version, model_snapshot,
    agent_name, agent_version, configuration_id,
    started_at, completed_at, status,
    input_tokens, output_tokens, cached_tokens, context_size, cost, duration_sec
)
SELECT
    mt.id,                                              -- preserve metrics_tasks.id
    1,                                                  -- MVP seed task
    NULL,
    mt.provider,
    mt.model,
    NULL,                                               -- model_version unknown
    NULL,                                               -- model_snapshot unknown
    mt.agent,
    NULL,                                               -- agent_version unknown
    COALESCE(
        (SELECT id FROM agent_configurations WHERE agent_name = mt.agent LIMIT 1),
        1
    ),
    mt.started_at,
    mt.ended_at,
    mt.status,
    mt.tokens_in,
    mt.tokens_out,
    NULL,                                               -- cached_tokens unknown
    NULL,                                               -- context_size unknown
    mt.cost_usd,
    mt.duration_sec
FROM metrics_tasks mt
ON CONFLICT (id) DO NOTHING;

SELECT setval('task_runs_id_seq', GREATEST((SELECT MAX(id) FROM task_runs), 1), true);

-- =====================================================================
-- 5. RUN_EVALUATIONS: по одной на каждый task_run (дефолт success mapping)
-- =====================================================================
INSERT INTO run_evaluations (
    run_id, agent_completed, build_passed, tests_passed, lint_passed,
    acceptance_passed, human_accepted, review_approved,
    stable_after_7d, stable_after_30d, reopened, evaluation_score
)
SELECT
    tr.id,
    -- agent_completed: TRUE для terminal статусов
    (tr.status IN ('success','failed','human_stop','interrupted')),
    -- build_passed: TRUE только для success
    (tr.status = 'success'),
    (tr.status = 'success'),
    (tr.status = 'success'),
    (tr.status = 'success'),
    -- human_accepted: TRUE только для human_stop
    (tr.status = 'human_stop'),
    -- review_approved: TRUE только для success
    (tr.status = 'success'),
    NULL, NULL,                                        -- stability: unknown
    FALSE,                                             -- reopened
    CASE tr.status
        WHEN 'success'    THEN 1.000
        WHEN 'human_stop' THEN 0.500
        WHEN 'failed'     THEN 0.000
        WHEN 'interrupted' THEN 0.200
        ELSE NULL
    END
FROM task_runs tr
WHERE tr.task_id = 1
ON CONFLICT (run_id) DO NOTHING;

-- =====================================================================
-- Done. Старые таблицы не тронуты.
-- =====================================================================
COMMIT;
