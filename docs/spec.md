# BizDNAi AI Agent Metrics Platform — Спецификация (фундамент проекта)

**Версия:** 1.0
**Дата:** 2026-07-24
**Связанные задачи:** #1163 (MVP), будущие #1XXX

---

## 1. Назначение проекта

BizDNAi AI Agent Metrics Platform — система объективного измерения эффективности AI-моделей и AI-агентов при выполнении реальных инженерных задач.

Система должна измерять не количество сгенерированного кода и не субъективное впечатление пользователя, а реальный полезный результат:

- выполнена ли задача;
- прошла ли она автоматическую проверку;
- была ли выполнена с первой попытки;
- сколько исправлений потребовалось;
- сколько раз вмешивался человек;
- сколько времени и денег было затрачено;
- насколько стабилен полученный код;
- продвинул ли агент проект вперёд;
- произошло ли ухудшение качества модели или агентской конфигурации.

Система должна поддерживать сравнение Claude, Codex, Gemini, MiniMax, локальных open-source-моделей и любых других агентов.

---

## 2. Главная ценность продукта

Платформа должна отвечать не на вопрос:

> Какая модель умнее?

А на практические вопросы:

> Какая модель надёжнее для конкретного типа задач?

> Сколько стоит одна действительно успешно выполненная задача?

> Какая модель требует меньше вмешательств разработчика?

> Стало ли качество модели хуже после обновления?

> Какой агент лучше подходит для архитектуры, исправления багов, frontend, backend, документации или генерации контента?

> Ускоряет ли AI разработку или создаёт дополнительную работу?

Краткое продуктовое позиционирование:

> **GitHub Actions и аналитика производительности для AI-агентов.**

---

## 3. Архитектура системы

Система должна состоять из пяти основных компонентов:

1. **Event Collector** — приём событий от агентов, CLI, IDE, MCP, CI/CD
2. **Metrics MCP Server** — read-only MCP API для AI-агентов и внешних систем
3. **Metrics Calculation Engine** — расчёт метрик (SQL + Python, без LLM)
4. **Benchmark Runner** — параллельный запуск одной спецификации на разных моделях в песочницах
5. **Web Analytics Dashboard** — UI (https://bizdnai.com/metrics и расширения)

Обмен данными событийный.

LLM не должна использоваться для базового сбора и расчёта метрик. Все основные показатели рассчитываются обычным программным кодом и SQL.

LLM может подключаться опционально только для:

- текстового объяснения результатов;
- формирования аналитических выводов;
- поиска аномалий;
- составления рекомендаций;
- классификации неструктурированных событий, которые нельзя определить правилами.

---

## 4. Event Collector

### 4.1. Назначение

Event Collector принимает события от AI-агентов, CLI-инструментов, IDE, MCP-серверов, CI/CD и тестовых сред. Работает в фоне, не требует дополнительных токенов модели.

### 4.2. Источники событий

- Claude Code hooks
- Codex hooks
- пользовательские CLI hooks
- MCP-вызовы
- shell wrappers
- Git hooks
- CI/CD
- тестовые runners
- ручной REST API
- SDK для Python
- SDK для JavaScript/TypeScript

### 4.3. Основные типы событий

**Жизненный цикл задачи**

```
task_created
task_started
task_paused
task_resumed
task_completed
task_failed
task_cancelled
task_reopened
```

**Работа со спецификацией**

```
specification_read
architecture_read
documentation_read
plan_created
plan_updated
clarification_requested
```

**Работа с кодом**

```
file_read
file_created
file_modified
file_deleted
command_started
command_completed
build_started
build_completed
test_started
test_completed
lint_started
lint_completed
```

**Использование инструментов**

```
mcp_called
database_read
memory_read
web_search
tool_error
permission_error
context_limit_warning
```

**Вмешательство человека**

```
human_clarification
human_correction
human_repeated_instruction
human_manual_edit
human_plan_change
human_stop
human_approval
human_rejection
```

**Проверка результата**

```
agent_declared_complete
build_passed
build_failed
tests_passed
tests_failed
lint_passed
lint_failed
acceptance_passed
acceptance_failed
review_approved
review_rejected
```

**Документация**

```
documentation_update_required
documentation_updated
documentation_validation_passed
documentation_validation_failed
documentation_drift_detected
```

---

## 5. Основные сущности данных

### 5.1. Project

```
id, name, repository, default_branch, created_at, metadata
```

### 5.2. Task

```
id, project_id, spec_id, title, description,
task_type, complexity, priority,
expected_result, acceptance_criteria, progress_weight,
created_at, completed_at, status
```

`task_type`: `architecture | feature | bugfix | refactoring | testing | documentation | frontend | backend | integration | devops | content_generation | research`

### 5.3. Task Run

Одна попытка выполнения задачи определённой моделью и агентом.

```
id, task_id, benchmark_id,
provider, model, model_version, model_snapshot,
agent_name, agent_version, configuration_id,
started_at, completed_at, status,
input_tokens, output_tokens, cached_tokens,
context_size, cost, duration
```

### 5.4. Run Event

Событие внутри запуска.

```
id, run_id, event_type, timestamp,
source, tool_name, file_path, command,
duration, success, error_code, metadata
```

### 5.5. Agent Configuration

```
id, agent_name, agent_version,
system_prompt_hash, settings_hash,
reasoning_effort, permission_mode, sandbox_mode,
enabled_mcp_servers, enabled_tools,
memory_configuration, context_configuration,
environment_hash, created_at
```

### 5.6. Run Evaluation

```
id, run_id,
agent_completed, build_passed, tests_passed,
lint_passed, acceptance_passed,
human_accepted, review_approved,
stable_after_7d, stable_after_30d,
reopened, evaluation_score, created_at
```

### 5.7. Human Intervention

```
id, run_id, timestamp,
intervention_type, severity, description,
estimated_minutes
```

`severity`: `minor | moderate | critical`

### 5.8. Run Artifact

```
id, run_id, artifact_type, path, hash, size, metadata
```

`artifact_type`: `git_diff | commit | test_report | build_report | coverage_report | generated_file | final_response | documentation_change`

### 5.9. Benchmark

```
id, name, description,
project_id, spec_id,
created_at, status, baseline_configuration
```

---

## 6. Разделение модели и агента

В аналитике обязательно разделять:

- provider, model, точную версию/snapshot
- агентскую оболочку и её версию
- системный промпт (hash)
- набор MCP-серверов и инструментов
- права доступа, режим reasoning, размер контекста
- конфигурацию памяти и среды

Нельзя считать изменение результата деградацией модели, если одновременно изменилась агентская конфигурация. Система должна фильтровать данные по полной конфигурации.

---

## 7. Уровни успешности задачи

Единого `success` недостаточно. Используются уровни:

```
agent_completed
build_passed
tests_passed
lint_passed
acceptance_passed
human_accepted
review_approved
stable_after_7d
stable_after_30d
```

- **Agent Completed**: агент заявил, что закончил
- **Machine Verified**: build + tests + lint + acceptance прошли
- **Human Accepted**: пользователь/reviewer принял
- **Stable**: задача не переоткрыта, код не правился N дней

Основной показатель — **проверенный результат**, не заявление агента.

---

## 8. Основные метрики MVP

### 8.1. Verified Success Rate

```
Verified Success Rate = проверенно успешных / всего завершённых
```

Набор обязательных проверок задаётся конфигурацией проекта.

### 8.2. First Pass Success Rate

```
First Pass Success Rate = задач с первой попытки / всего завершённых
```

### 8.3. Rework Ratio

Один из вариантов:

```
rework итераций / всего итераций
время после agent_completed / полное время
повторно изменённых строк / всего изменённых строк
```

В UI указывать используемый метод.

### 8.4. Human Intervention Rate

```
Human Intervention Rate = корректирующих вмешательств / запусков
```

Отдельно: уточнения, указания на ошибку, повторные инструкции, изменение плана, ручные правки, остановки.

### 8.5. Median Completion Time

Медиана (P50), плюс P75, P90. Не среднее.

### 8.6. Cost per Verified Success

```
Cost per Verified Success = полная стоимость / проверенно успешных
```

В стоимость: input/output tokens, cached tokens, retries, infra.

### 8.7. Retry Rate

```
Retry Rate = задач с повторными запусками / всего
```

### 8.8. Tool Failure Rate

```
Tool Failure Rate = неуспешных tool-вызовов / всего
```

С причинами (permission, timeout, MCP error, и т.д.).

### 8.9. Context Efficiency

```
Context Efficiency = подтверждённый прогресс / контекст
```

Упрощённо: `Verified Successes / 1M tokens`.

### 8.10. Documentation Synchronization Rate

```
DocSync Rate = задач с обновлённой и проверенной документацией / задач, требовавших обновления
```

---

## 9. Documentation Quality Index

```
DQI =
30% Documentation Synchronization +
25% Reference Validity +
20% API Consistency +
15% Example Validation +
10% Architecture Consistency
```

В MVP — отдельные компоненты, не единый балл.

---

## 10. Progress Velocity

Каждая задача имеет `progress_weight`.

```
Progress Velocity = Σ(подтверждённый прогресс) / время
```

```
подтверждённый прогресс = progress_weight × completion% × verification_coeff × stability_coeff
```

`verification_coeff`:
- `agent_completed`: 0.3
- `build_passed`: 0.5
- `tests_passed`: 0.7
- `human_accepted`: 0.9
- `stable_after_7d`: 1.0

Не использовать строки кода как основной показатель.

---

## 11. Code Stability Index

```
CSI = стабильные изменения / все принятые изменения
```

Учитывать: повторное открытие, исправления в тех же файлах, rollback, regression.

---

## 12. Architectural Consistency Index

Статические правила: структура каталогов, отсутствие запрещённых зависимостей, границы модулей, ADR.

Основные проверки — статические, не LLM.

---

## 13. Model Quality Index

В MVP — `Observed Quality Score` или `MQI Preview`. Не выдавать за полноценный индекс.

После калибровки:

```
MQI =
25% Verified Success Rate +
20% First Pass Success Rate +
15% Code Stability +
15% Low Rework Score +
10% Human Independence +
10% Context Efficiency +
5% Documentation Quality
```

Веса должны быть откалиброваны на реальной истории.

---

## 14. Engineering Productivity Index

Только после реализации и калибровки базовых метрик:

```
EPI =
30% MQI +
25% Progress Velocity +
15% Code Stability +
10% Human Independence +
10% Cost Efficiency +
10% Time Efficiency
```

Шкала: 90-100 очень высокая, 80-89 высокая, 70-79 рабочая, 60-69 снижение, 50-59 деградация, 0-49 непригодна.

EPI всегда с расшифровкой компонентов.

---

## 15. Benchmark Runner

Запускает одну задачу на нескольких моделях в одинаковых условиях.

Фиксировать:
- одинаковую версию репо
- одинаковую спецификацию и acceptance criteria
- одинаковую тестовую среду, права, инструменты
- полную конфигурацию агента
- дату/время, версию модели, seed

Изоляция: Docker container / VM snapshot / Git worktree / temporary clone.

Результат: таблица `Model × Verified Success × FPS × Time × Rework × Interventions × Cost`, diff, timeline, тесты, итоговая оценка, выбор победителя вручную.

---

## 16. Обнаружение деградации

Сравнивать current window (7d) с baseline (30d):

- Verified Success Rate
- First Pass Success
- Rework Ratio
- Human Intervention Rate
- Median Completion Time
- Cost per Verified Success
- Tool Failure Rate
- Documentation Synchronization Rate
- Code Stability

Показывать `sample_size`, `confidence_level`, статусы:
- `insufficient_data`
- `possible_change`
- `meaningful_change`
- `high_confidence_change`

Не утверждать "модель деградировала" — корректно: "зафиксировано статистически значимое снижение First Pass Success Rate". Проверять изменения модели/агента/промпта/MCP/среды/проекта/сложности отдельно.

---

## 17. Metrics MCP Server

Read-only MCP для AI-агентов. Не основное хранилище, не запускает фоновые процессы.

Tools:
- `get_task_metrics(task_id)`
- `get_run_metrics(run_id)`
- `compare_runs(run_ids[])`
- `compare_models(model_a, model_b, filters)`
- `get_model_profile(model)`
- `get_degradation_report(model, window, baseline)`
- `get_benchmark_result(benchmark_id)`
- `get_project_metrics(project_id)`
- `get_documentation_health(project_id)`
- `recommend_model(task_type, constraints)` — с `confidence` и предупреждением при малом `sample_size`

---

## 18. Веб-интерфейс

### 18.1. Главная

Продуктовое объяснение + сценарии:

> Измеряйте не количество сгенерированного кода, а стоимость надёжно завершённой работы.

> BizDNAi Metrics сравнивает AI-модели и агентов по проверенному успеху, переделкам, вмешательствам человека, стабильности, времени и стоимости.

Сценарии: сравнить модели, найти деградацию, выбрать модель под задачу, ROI AI.

### 18.2. KPI-карточки

Основные: Verified Success, First Pass, Median Time, Cost/Success, Interventions/Task, Rework Ratio.

Второстепенные: Total Tasks, Total Runs, Total Cost, Total Tokens.

### 18.3. Model Comparison

Фильтры: период, проект, тип задачи, сложность, provider, model, model_version, agent, agent_version, configuration, benchmark, success_level.

### 18.4. Benchmark Page

Название, спека, acceptance, сравнение моделей, live status, timeline, артефакты, тесты, diff, стоимость, вмешательства, победитель, confidence.

### 18.5. Degradation Timeline

Для каждого показателя: current, baseline, absolute delta, % delta, sample_size, confidence.

### 18.6. Run Details

Модель+версия, агент+конфиг, время, стоимость, токены, timeline, команды, MCP, ошибки, изменения файлов, тесты, вмешательства, документация, итог проверки, причины провала.

### 18.7. Project Progress

Progress Velocity, завершённые блоки, откаты, доля rework, стабильность, плановый vs фактический прогресс.

---

## 19. Live Analytics

События почти в реальном времени: WebSocket / SSE / polling fallback.

Во время выполнения: статус, продолжительность, последние события, команды, MCP, ошибки, изменения, стоимость, токены, вмешательства.

После завершения — автопересчёт метрик (без LLM).

---

## 20. REST API

```
POST /api/v1/events
POST /api/v1/events/batch
GET  /api/v1/tasks
POST /api/v1/tasks
GET  /api/v1/tasks/{id}
POST /api/v1/runs
GET  /api/v1/runs/{id}
POST /api/v1/runs/{id}/complete
GET  /api/v1/metrics/overview
GET  /api/v1/metrics/models
GET  /api/v1/metrics/projects
GET  /api/v1/metrics/degradation
GET  /api/v1/metrics/documentation
POST /api/v1/benchmarks
POST /api/v1/benchmarks/{id}/run
GET  /api/v1/benchmarks/{id}
GET  /api/v1/benchmarks/{id}/results
```

---

## 21. Безопасность

Логи могут содержать: код, API-ключи, пароли, PII, внутреннюю документацию, shell-команды, секреты окружения.

Требования:

- автоматическое удаление секретов (regex: `sk-…`, `ghp_…`, `AKIA…`, и т.д.)
- configurable redaction rules
- TLS everywhere
- шифрование чувствительных данных at-rest
- read-only MCP
- RBAC
- project isolation
- audit log
- self-hosting
- опциональное отключение хранения prompt/response
- настройка срока хранения
- удаление данных по проекту
- экспорт

---

## 22. Open-source и self-hosting

Docker Compose, PostgreSQL, отдельные backend/frontend, MCP, Benchmark Runner, миграции, `.env.example`, seed demo, документация по подключению Claude Code / Codex / кастомного агента.

`docker compose up -d` → Dashboard, REST API, MCP endpoint, PostgreSQL.

---

## 23. Этапы реализации

1. **Нормализация модели данных** — все 8 сущностей + миграция
2. **Базовые проверенные метрики** — Verified Success, FPS, Rework, Interventions, Median Time, Cost/Success, Retry, Tool Failure
3. **Обновление дашборда** — KPI, Model Comparison, Run Details, Benchmark Results, Degradation Timeline, фильтры
4. **Benchmark Runner** — песочницы, параллельный запуск, snapshot, acceptance, авто-сравнение
5. **Документация и стабильность** — DocSync, CSI, reopened, regression, drift
6. **Продвинутые индексы** — PV, DQI, ACI, MQI, EPI
7. **Рекомендации** — model profile, confidence, warning insufficient data

---

## 24. Что необходимо изменить в текущей версии

**Добавить:** Task Run, agent config, model version, verification levels, FPS, Rework, Human Interventions, Verified Success, Cost/Success, median/percentile duration, benchmark, sample size, confidence, doc-sync, live timeline.

**Переименовать:** MQI → `Observed Success Score` / `MQI Preview`.

**Убрать из главного фокуса:** Total Tasks, Total Cost, Avg Duration, строки кода, файлы, сообщения, единый MQI без раскрытия компонентов.

**Заменить:** Avg Duration → Median/P75/P90; Total Cost → Cost per Verified Success.

---

## 25. Критерии готовности MVP

Пользователь может:

1. Подключить ≥2 разных AI-агента
2. Записать все запуски одной задачи
3. Видеть точную модель, агента, конфигурацию
4. Запустить одну спецификацию в нескольких песочницах
5. Получить результаты сборки и тестов
6. Видеть Verified Success
7. Видеть First Pass Success
8. Видеть количество вмешательств человека
9. Видеть объём переделки
10. Сравнить время и стоимость моделей
11. Открыть timeline конкретного запуска
12. Видеть изменение метрик относительно baseline
13. Получать предупреждение при недостаточной выборке
14. Получать данные через MCP и REST API
15. Развернуть локально через Docker Compose

---

## 26. Итоговый результат

Платформа должна позволять: сравнивать модели в одинаковых условиях, находить сильные стороны каждой, выявлять деградацию, считать реальную стоимость AI-разработки, измерять человеческую работу, анализировать переделки, контролировать документацию, прогнозировать риски, выбирать модель под задачу, доказывать эффективность воспроизводимыми данными.

**Главный принцип:** высокую оценку получает не агент, который произвёл больше активности, а агент, который с минимальными затратами, вмешательствами и переделками создал проверенный и стабильный результат.