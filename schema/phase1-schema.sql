-- Phase 1: Data Model Normalization (задача #1176, фаза 1/7)
-- DB: bizdnai :5434
-- Безопасный прогон: IF NOT EXISTS, без ALTER существующих таблиц.
-- Существующие таблицы (task_log, metrics_tasks, task_steps, metrics_tasks_v,
-- metrics_model_pricing) НЕ ТРОГАЕМ — это обратная совместимость.

-- =====================================================================
-- 1. PROJECTS
-- =====================================================================
CREATE TABLE IF NOT EXISTS projects (
    id              BIGSERIAL PRIMARY KEY,
    name            TEXT NOT NULL,                       -- 'bizdnai', 'planet', 'client-morozov'
    repository      TEXT,                                -- 'bizdnai-orchestrator'
    default_branch  TEXT DEFAULT 'main',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata        JSONB DEFAULT '{}'::jsonb,
    UNIQUE (name)
);

CREATE INDEX IF NOT EXISTS projects_name_idx ON projects (name);

COMMENT ON TABLE  projects             IS 'Проекты, в которых запускаются AI-агенты (задача #1176, фаза 1)';
COMMENT ON COLUMN projects.name         IS 'Логический id проекта (slug), совпадает с metrics_tasks.project';
COMMENT ON COLUMN projects.metadata     IS 'Произвольные метаданные (url, описание, ответственные)';

-- =====================================================================
-- 2. TASKS
-- =====================================================================
CREATE TABLE IF NOT EXISTS tasks (
    id                       BIGSERIAL PRIMARY KEY,
    project_id               BIGINT REFERENCES projects(id) ON DELETE CASCADE,
    spec_id                  TEXT,                            -- батч-группа (battle-mode)
    title                    TEXT NOT NULL,
    description              TEXT,
    task_type                TEXT,                            -- architecture / feature / bugfix / ...
    complexity               TEXT,                            -- trivial / simple / moderate / complex / expert
    priority                 TEXT,                            -- low / medium / high / critical
    expected_result          TEXT,
    acceptance_criteria      JSONB DEFAULT '[]'::jsonb,       -- список проверяемых условий
    progress_weight          NUMERIC(6, 3) DEFAULT 1.0,       -- для Progress Velocity
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,
    status                   TEXT NOT NULL DEFAULT 'pending'  -- pending / in_progress / completed / failed / cancelled
);

CREATE INDEX IF NOT EXISTS tasks_project_idx         ON tasks (project_id, status);
CREATE INDEX IF NOT EXISTS tasks_spec_idx            ON tasks (spec_id);
CREATE INDEX IF NOT EXISTS tasks_status_idx          ON tasks (status, created_at DESC);
CREATE INDEX IF NOT EXISTS tasks_type_idx            ON tasks (task_type);
CREATE INDEX IF NOT EXISTS tasks_created_idx         ON tasks (created_at DESC);

COMMENT ON TABLE  tasks                 IS 'Задачи проекта (задача #1176, фаза 1)';
COMMENT ON COLUMN tasks.task_type       IS 'architecture | feature | bugfix | refactoring | testing | documentation | frontend | backend | integration | devops | content_generation | research';
COMMENT ON COLUMN tasks.complexity      IS 'trivial | simple | moderate | complex | expert';
COMMENT ON COLUMN tasks.priority        IS 'low | medium | high | critical';
COMMENT ON COLUMN tasks.progress_weight IS 'Вес для Progress Velocity (по умолчанию 1.0)';

-- =====================================================================
-- 3. AGENT_CONFIGURATIONS
-- =====================================================================
CREATE TABLE IF NOT EXISTS agent_configurations (
    id                       BIGSERIAL PRIMARY KEY,
    agent_name               TEXT NOT NULL,                  -- 'claude-code', 'codex', 'mcp-orchestrator'
    agent_version            TEXT NOT NULL,                  -- '2.1.217', '0.47.0'
    system_prompt_hash       TEXT,                           -- sha256 системного промпта
    settings_hash            TEXT,                           -- sha256 settings.json
    reasoning_effort         TEXT,                           -- low / medium / high / max
    permission_mode          TEXT,                           -- default / acceptEdits / bypassPermissions / plan
    sandbox_mode             TEXT,                           -- worktree / container / vm / none
    enabled_mcp_servers      TEXT[] DEFAULT '{}',            -- ['context7','flux','bizdnai-metrics']
    enabled_tools            TEXT[] DEFAULT '{}',            -- ['Read','Write','Bash', ...]
    memory_configuration     JSONB DEFAULT '{}'::jsonb,
    context_configuration    JSONB DEFAULT '{}'::jsonb,
    environment_hash         TEXT,                           -- sha256 окружения (OS, версии, env)
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (agent_name, agent_version, system_prompt_hash, settings_hash, environment_hash)
);

CREATE INDEX IF NOT EXISTS agent_configurations_name_idx ON agent_configurations (agent_name, agent_version);

COMMENT ON TABLE  agent_configurations                IS 'Полная конфигурация агента (задача #1176, фаза 1)';
COMMENT ON COLUMN agent_configurations.enabled_mcp_servers  IS 'Массив имён подключённых MCP-серверов';
COMMENT ON COLUMN agent_configurations.enabled_tools        IS 'Массив разрешённых tools';

-- =====================================================================
-- 4. BENCHMARKS
-- =====================================================================
CREATE TABLE IF NOT EXISTS benchmarks (
    id                       BIGSERIAL PRIMARY KEY,
    name                     TEXT NOT NULL,
    description              TEXT,
    project_id               BIGINT REFERENCES projects(id) ON DELETE SET NULL,
    spec_id                  TEXT,                           -- группа related tasks
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    status                   TEXT NOT NULL DEFAULT 'created', -- created / running / completed / failed / cancelled
    baseline_configuration   JSONB DEFAULT '{}'::jsonb,
    UNIQUE (name, project_id)
);

CREATE INDEX IF NOT EXISTS benchmarks_project_idx ON benchmarks (project_id, status);
CREATE INDEX IF NOT EXISTS benchmarks_spec_idx    ON benchmarks (spec_id);

COMMENT ON TABLE  benchmarks                  IS 'Бенчмарк — запуск одной спеки на N моделях (задача #1176, фаза 1)';
COMMENT ON COLUMN benchmarks.status            IS 'created | running | completed | failed | cancelled';

-- =====================================================================
-- 5. TASK_RUNS
-- =====================================================================
CREATE TABLE IF NOT EXISTS task_runs (
    id                       BIGSERIAL PRIMARY KEY,
    task_id                  BIGINT REFERENCES tasks(id) ON DELETE CASCADE,
    benchmark_id             BIGINT REFERENCES benchmarks(id) ON DELETE SET NULL,
    provider                 TEXT,                           -- anthropic / openai / google / deepseek
    model                    TEXT NOT NULL,                  -- 'claude-opus-4-8', 'gpt-5', ...
    model_version            TEXT,                           -- '2026-05-14'
    model_snapshot           TEXT,                           -- точный snapshot id модели
    agent_name               TEXT,
    agent_version            TEXT,
    configuration_id         BIGINT REFERENCES agent_configurations(id) ON DELETE SET NULL,
    started_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at             TIMESTAMPTZ,
    status                   TEXT NOT NULL DEFAULT 'in_progress', -- in_progress / success / failed / interrupted / human_stop
    input_tokens             INTEGER,
    output_tokens            INTEGER,
    cached_tokens            INTEGER,
    context_size             INTEGER,
    cost                     NUMERIC(10, 4),
    duration_sec             INTEGER
);

CREATE INDEX IF NOT EXISTS task_runs_task_idx       ON task_runs (task_id);
CREATE INDEX IF NOT EXISTS task_runs_model_time_idx ON task_runs (model, started_at DESC);
CREATE INDEX IF NOT EXISTS task_runs_benchmark_idx  ON task_runs (benchmark_id);
CREATE INDEX IF NOT EXISTS task_runs_status_idx     ON task_runs (status, started_at DESC);
CREATE INDEX IF NOT EXISTS task_runs_started_idx    ON task_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS task_runs_configuration_idx ON task_runs (configuration_id);
CREATE INDEX IF NOT EXISTS task_runs_spec_idx       ON task_runs (task_id, model);

COMMENT ON TABLE  task_runs                IS 'Один запуск задачи на конкретной модели+агенте (задача #1176, фаза 1)';
COMMENT ON COLUMN task_runs.status         IS 'in_progress | success | failed | interrupted | human_stop';
COMMENT ON COLUMN task_runs.cost           IS 'USD, input+output tokens × $/1k';

-- =====================================================================
-- 6. RUN_EVENTS
-- =====================================================================
CREATE TABLE IF NOT EXISTS run_events (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    event_type      TEXT NOT NULL,                  -- task_started / file_modified / build_completed / ...
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT now(),
    source          TEXT,                           -- claude-code / codex / mcp / hook / cli / api
    tool_name       TEXT,                           -- Read / Write / Bash / mcp__context7__...
    file_path       TEXT,
    command         TEXT,
    duration_ms     INTEGER,
    success         BOOLEAN,
    error_code      TEXT,
    metadata        JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS run_events_run_idx       ON run_events (run_id, timestamp);
CREATE INDEX IF NOT EXISTS run_events_type_idx      ON run_events (event_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS run_events_success_idx   ON run_events (success, event_type) WHERE success IS NOT NULL;
CREATE INDEX IF NOT EXISTS run_events_time_idx      ON run_events (timestamp DESC);

COMMENT ON TABLE  run_events              IS 'События запуска (жизненный цикл, инструменты, проверки, вмешательства)';

-- =====================================================================
-- 7. RUN_EVALUATIONS
-- =====================================================================
CREATE TABLE IF NOT EXISTS run_evaluations (
    id                       BIGSERIAL PRIMARY KEY,
    run_id                   BIGINT NOT NULL UNIQUE REFERENCES task_runs(id) ON DELETE CASCADE,
    agent_completed          BOOLEAN DEFAULT FALSE,
    build_passed             BOOLEAN,
    tests_passed             BOOLEAN,
    lint_passed              BOOLEAN,
    acceptance_passed        BOOLEAN,
    human_accepted           BOOLEAN,
    review_approved          BOOLEAN,
    stable_after_7d          BOOLEAN,
    stable_after_30d         BOOLEAN,
    reopened                 BOOLEAN DEFAULT FALSE,
    evaluation_score         NUMERIC(5, 3),                -- 0.000 - 1.000
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS run_evaluations_success_idx ON run_evaluations (agent_completed, build_passed, tests_passed);
CREATE INDEX IF NOT EXISTS run_evaluations_score_idx   ON run_evaluations (evaluation_score DESC);

COMMENT ON TABLE  run_evaluations                         IS 'Уровни проверки результата запуска (задача #1176, фаза 1)';
COMMENT ON COLUMN run_evaluations.evaluation_score        IS '0.000-1.000, агрегированный Observed Success Score';

-- =====================================================================
-- 8. HUMAN_INTERVENTIONS
-- =====================================================================
CREATE TABLE IF NOT EXISTS human_interventions (
    id                       BIGSERIAL PRIMARY KEY,
    run_id                   BIGINT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    timestamp                TIMESTAMPTZ NOT NULL DEFAULT now(),
    intervention_type        TEXT NOT NULL,                -- clarification / correction / repeated_instruction / manual_edit / plan_change / stop / approval / rejection
    severity                 TEXT,                         -- minor / moderate / critical
    description              TEXT,
    estimated_minutes        INTEGER
);

CREATE INDEX IF NOT EXISTS human_interventions_run_idx  ON human_interventions (run_id, timestamp);
CREATE INDEX IF NOT EXISTS human_interventions_type_idx ON human_interventions (intervention_type, timestamp DESC);
CREATE INDEX IF NOT EXISTS human_interventions_severity_idx ON human_interventions (severity, timestamp DESC);

COMMENT ON TABLE  human_interventions              IS 'Вмешательства человека в процесс агента (задача #1176, фаза 1)';
COMMENT ON COLUMN human_interventions.severity     IS 'minor | moderate | critical';

-- =====================================================================
-- 9. RUN_ARTIFACTS
-- =====================================================================
CREATE TABLE IF NOT EXISTS run_artifacts (
    id              BIGSERIAL PRIMARY KEY,
    run_id          BIGINT NOT NULL REFERENCES task_runs(id) ON DELETE CASCADE,
    artifact_type   TEXT NOT NULL,                  -- git_diff / commit / test_report / build_report / coverage_report / generated_file / final_response / documentation_change
    path            TEXT,
    hash            TEXT,                           -- sha256 содержимого
    size            BIGINT,
    metadata        JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS run_artifacts_run_idx  ON run_artifacts (run_id);
CREATE INDEX IF NOT EXISTS run_artifacts_type_idx ON run_artifacts (artifact_type);

COMMENT ON TABLE  run_artifacts            IS 'Артефакты запуска (diff, отчёты, сгенерированные файлы)';

-- =====================================================================
-- Готово. Существующие таблицы не тронуты.
-- =====================================================================
