-- Metrics schema (задача #1163, MVP)
-- DB: bizdnai :5434
-- Создаёт две таблицы: metrics_tasks + metrics_model_pricing
-- Безопасный прогон: IF NOT EXISTS, без ALTER существующих таблиц.

CREATE TABLE IF NOT EXISTS metrics_tasks (
    id              BIGSERIAL PRIMARY KEY,
    task_id         TEXT NOT NULL,                  -- внешний ID из task-start.sh
    spec_id         TEXT,                           -- группа запусков одного сценария на разных моделях
    model           TEXT NOT NULL,                  -- opus / sonnet / haiku / ...
    provider        TEXT,                           -- anthropic / openai / google / ...
    project         TEXT,                           -- project_id
    agent           TEXT,                           -- какой агент запустил
    status          TEXT NOT NULL DEFAULT 'in_progress',  -- in_progress / success / failed / interrupted / human_stop
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at        TIMESTAMPTZ,
    duration_sec    INTEGER,                        -- ended_at - started_at
    files_created   INTEGER DEFAULT 0,
    files_modified  INTEGER DEFAULT 0,
    files_deleted   INTEGER DEFAULT 0,
    tokens_in       INTEGER,                        -- оценка (точно считает только провайдер)
    tokens_out      INTEGER,
    cost_usd        NUMERIC(10, 4),                 -- tokens × $/1k
    spec_label      TEXT,                           -- человекочитаемое имя сценария
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_id)
);

CREATE INDEX IF NOT EXISTS metrics_tasks_spec_idx        ON metrics_tasks (spec_id, model);
CREATE INDEX IF NOT EXISTS metrics_tasks_model_time_idx  ON metrics_tasks (model, started_at DESC);
CREATE INDEX IF NOT EXISTS metrics_tasks_project_time_idx ON metrics_tasks (project, started_at DESC);
CREATE INDEX IF NOT EXISTS metrics_tasks_status_idx      ON metrics_tasks (status, started_at DESC);
CREATE INDEX IF NOT EXISTS metrics_tasks_started_idx     ON metrics_tasks (started_at DESC);

CREATE TABLE IF NOT EXISTS metrics_model_pricing (
    model              TEXT PRIMARY KEY,
    provider           TEXT,
    input_per_1k_usd   NUMERIC(10, 6) NOT NULL,
    output_per_1k_usd  NUMERIC(10, 6) NOT NULL,
    effective_from     DATE NOT NULL DEFAULT CURRENT_DATE,
    notes              TEXT
);

-- Прайс-лист (приблизительные значения, обновляются через SQL).
-- ON CONFLICT DO NOTHING — повторный прогон безопасен.
INSERT INTO metrics_model_pricing (model, provider, input_per_1k_usd, output_per_1k_usd) VALUES
    ('claude-opus-4-8',         'anthropic',  0.015,    0.075),
    ('claude-sonnet-5',         'anthropic',  0.003,    0.015),
    ('claude-haiku-4-5',        'anthropic',  0.0008,   0.004),
    ('gpt-4o',                  'openai',     0.0025,   0.010),
    ('gpt-4o-mini',             'openai',     0.00015,  0.0006),
    ('o1',                      'openai',     0.015,    0.060),
    ('o1-mini',                 'openai',     0.003,    0.012),
    ('gemini-1.5-pro',          'google',     0.00125,  0.005),
    ('gemini-1.5-flash',        'google',     0.000075, 0.0003),
    ('gemini-2.0-flash',        'google',     0.0001,   0.0004),
    ('deepseek-chat',           'deepseek',   0.00014,  0.00028),
    ('deepseek-reasoner',       'deepseek',   0.00055,  0.00219),
    ('qwen-coder',              'qwen',       0.0001,   0.0003)
ON CONFLICT (model) DO NOTHING;

-- View для быстрого дашборда (success rate по модели за период)
CREATE OR REPLACE VIEW metrics_tasks_v AS
SELECT
    model,
    provider,
    project,
    date_trunc('day', started_at) AS day,
    COUNT(*)                                AS total,
    COUNT(*) FILTER (WHERE status='success')        AS ok,
    COUNT(*) FILTER (WHERE status='failed')         AS failed,
    COUNT(*) FILTER (WHERE status='human_stop')     AS human_stop,
    ROUND(AVG(duration_sec) FILTER (WHERE ended_at IS NOT NULL))                 AS avg_duration_sec,
    ROUND(AVG(cost_usd)    FILTER (WHERE ended_at IS NOT NULL)::numeric, 4)     AS avg_cost_usd,
    SUM(COALESCE(cost_usd, 0))                                                  AS total_cost_usd,
    SUM(COALESCE(tokens_in, 0))                                                 AS sum_tokens_in,
    SUM(COALESCE(tokens_out, 0))                                                AS sum_tokens_out
FROM metrics_tasks
GROUP BY model, provider, project, date_trunc('day', started_at);

COMMENT ON TABLE  metrics_tasks            IS 'Задачи AI-агентов с метаданными модели/проекта/стоимости (задача #1163)';
COMMENT ON COLUMN metrics_tasks.spec_id    IS 'Группа запусков одного сценария на разных моделях (battle-mode)';
COMMENT ON COLUMN metrics_tasks.task_id    IS 'Совпадает с task_id из task_log (mcp-events)';
COMMENT ON COLUMN metrics_tasks.cost_usd   IS 'Оценка на основе tokens_in/out и metrics_model_pricing';