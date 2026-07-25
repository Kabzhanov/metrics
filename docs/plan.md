# BizDNAi AI Agent Metrics Platform — План реализации

> Спецификация: [`bizdnai-metrics-spec.md`](./bizdnai-metrics-spec.md)
> Связанные задачи: #1163 (MVP), будущие #1XXX

---

## 0. Текущее состояние (MVP, задача #1163 — done)

- ✅ `task_log` (базовая) + `metrics_tasks` (частичная Task Run)
- ✅ 6 моделей seeded (1 июня → сегодня): opus-4-8 / sonnet-5 / haiku-4-5 / fable-5 / M3 / chatgpt-5.5
- ✅ `task-start.sh` — `--model` обязателен, валидация
- ✅ Публичный пакет `mcp-metrics` на GitHub (Apache-2.0) — https://github.com/Kabzhanov/metrics
- ✅ Дашборд https://bizdnai.com/metrics — 4 KPI, 3 line, 1 bar, sortable таблица
- ✅ Degradation detector — cron 09:00 daily, email алерт при drop ≥10%
- ✅ Contributors на GitHub: оба аккаунта (Kabzhanov + kabzhanov-wq)
- ✅ `metrics_tasks_v` view + прайс-лист (13 моделей)

## Чего MVP **не** хватает (по спеке)

- ❌ Project, Task (полная сущность), Task Run (полный), Run Event, Agent Configuration, Run Evaluation, Human Intervention, Run Artifact, Benchmark
- ❌ Verification levels (agent_completed / build_passed / tests_passed / lint_passed / acceptance / human_accepted / review_approved / stable_after_7d/30d)
- ❌ Verified Success Rate, First Pass Success Rate, Rework Ratio, Human Intervention Rate
- ❌ Median / P75 / P90 (есть только avg)
- ❌ Cost per Verified Success
- ❌ Sample size + confidence в degradation
- ❌ Benchmark Runner (sandbox + параллельный запуск)
- ❌ Live Analytics (WebSocket / SSE)
- ❌ REST API v1 endpoints
- ❌ Docker Compose
- ❌ Self-hosting
- ❌ Secret redaction в логах

---

## 1. Phase 1 — Data Model Normalization

**Цель:** расширить модель данных под полную спеку, перенести существующие данные.

**Tables:**
- `projects`
- `tasks` (полная: project_id, spec_id, task_type, complexity, priority, expected_result, acceptance_criteria, progress_weight)
- `task_runs` (полная: provider, model, model_version, model_snapshot, agent_name, agent_version, configuration_id, input/output/cached_tokens, context_size, cost, duration)
- `run_events`
- `agent_configurations`
- `run_evaluations` (полная: все verification levels + stable_after_7d/30d + reopened + evaluation_score)
- `human_interventions`
- `run_artifacts`
- `benchmarks`

**Migration:** task_log → projects/tasks/task_runs; metrics_tasks → task_runs (с маппингом старых колонок).

**Effort:** M (3-5 дней)
**Когда:** ASAP — фундамент для всего остального.

---

## 2. Phase 2 — Verified Metrics

**Цель:** считать проверенные метрики (не «success_rate», а Verified Success Rate).

- Verified Success Rate (настраиваемый набор проверок per project)
- First Pass Success Rate (без rework)
- Rework Ratio (3 метода, выбор в UI: iteration-based / time-based / lines-based)
- Human Intervention Rate (per severity: minor/moderate/critical)
- Median / P75 / P90 Completion Time (percentile в Postgres `percentile_cont`)
- Cost per Verified Success
- Retry Rate (task_runs на одну task)
- Tool Failure Rate (run_events where event_type = tool_error)

**API changes:** api.php — заменить `success_rate` на новые метрики.

**Effort:** M (3-5 дней)
**Когда:** после Phase 1.

---

## 3. Phase 3 — Dashboard Update

**Цель:** UI под новые данные.

- Новые KPI: Verified Success, First Pass, Median Time, Cost/Success, Interventions/Task, Rework Ratio
- Model Comparison таблица с расширенными фильтрами (project, task_type, complexity, model_version, agent_version, configuration, success_level)
- Run Details page (timeline событий, команды, MCP-вызовы, ошибки, артефакты)
- Benchmark Results page
- Degradation Timeline с sample_size + confidence
- Project Progress (Progress Velocity, завершённые блоки)

Частично сделано в /metrics MVP (KPI + графики + sortable таблица).

**Effort:** M (5-7 дней)
**Когда:** после Phase 2.

---

## 4. Phase 4 — Benchmark Runner

**Цель:** запустить одну спецификацию в нескольких песочницах.

- Sandbox: Docker container / Git worktree (Yandex Cloud — без VM snapshots, worktree проще)
- Параллельный запуск моделей через async Python
- Одинаковый snapshot репо + acceptance + тесты
- Acceptance tests (запуск pytest/lint/build)
- Авто-сравнение diff + артефакты
- Live status / WebSocket обновления

**New microservice** или job runner (можно отдельный Docker-контейнер).

**Effort:** L (1-2 недели)
**Когда:** после Phase 3.

---

## 5. Phase 5 — Documentation & Stability

**Цель:** отслеживать документацию и стабильность кода.

- Documentation Synchronization Rate (по git diffs: doc-файлы менялись вместе с code-файлами)
- Code Stability Index (git-based: файл → задачи → пересечения с последующими)
- Reopened tasks tracking (run_evaluations.reopened)
- Regression tracking (тесты в последующих задачах упали на том же модуле)
- Documentation drift detection (LLM-judge опционально для семантики)

**Effort:** M (1 неделя)
**Когда:** параллельно с Phase 4.

---

## 6. Phase 6 — Advanced Indices

**Цель:** PV, DQI, ACI, MQI, EPI.

- Progress Velocity (веса задач × verification_coeff × stability_coeff)
- Documentation Quality Index (5 компонентов)
- Architectural Consistency Index (статические правила: directory structure, запрещённые зависимости, ADR matching)
- Model Quality Index (полная формула после калибровки)
- Engineering Productivity Index

**Условие:** ≥1000 задач и ≥30 дней данных для калибровки.

**Effort:** L (2-3 недели)
**Когда:** через 1-2 месяца production usage.

---

## 7. Phase 7 — Recommendations

**Цель:** рекомендовать модель под задачу.

- Model Profile по типам задач (агрегаты по task_type × model)
- `recommend_model(task_type, constraints)` MCP tool
- Confidence score на основе sample_size + dispersion
- Warning при `insufficient_data` (sample_size < 30)

**Effort:** M (1 неделя)
**Когда:** после Phase 6 (нужна статистика для рекомендаций).

---

## Сквозные (cross-phase) требования

- **Security:** secret redaction в логах (regex `sk-…`, `ghp_…`, `AKIA…`), TLS, RBAC, project isolation, audit log
- **Self-hosting:** Docker Compose, отдельные backend/frontend/MCP/Benchmark, миграции, `.env.example`, seed demo
- **Live Analytics:** WebSocket / SSE / polling fallback
- **REST API v1:** все endpoints из §20 спеки

---

## Оценка сроков

| Phase | Effort | Когда |
|---|---|---|
| 1. Data Model | M (3-5 дн) | ASAP |
| 2. Verified Metrics | M (3-5 дн) | После Phase 1 |
| 3. Dashboard | M (5-7 дн) | После Phase 2 |
| 4. Benchmark Runner | L (1-2 нед) | После Phase 3 |
| 5. Doc & Stability | M (1 нед) | Параллельно с Phase 4 |
| 6. Advanced Indices | L (2-3 нед) | После накопления данных |
| 7. Recommendations | M (1 нед) | После Phase 6 |

**MVP → production-ready: ~2-3 месяца для одного инженера.**

---

## Открытые вопросы / риски

1. **Sandbox:** Docker есть; Git worktree проще VM snapshots (Yandex Cloud не даёт быстрые snapshots). Worktree — основной вариант.
2. **Stable after 7d/30d:** нужен cron «re-evaluation» — проход по task_runs, проверка git-активности по затронутым файлам. Пока нет механизма.
3. **Secret redaction:** regex-фильтр в Event Collector; сейчас нет.
4. **Verified Success:** какие проверки считать обязательными — per project config (нужно schema).
5. **EPI/MQI:** реально полезны только после ≥1000 runs и ≥30 дней истории. Не показывать раньше.
6. **CI/CD integration:** webhook для приёма build/test событий из pipelines.
7. **Multi-tenancy / RBAC:** пока не в скоупе MVP, но архитектуру заложить.
8. **Stable test:** требует git-истории всех проектов — нужно интегрировать с локальными репо или GitHub API.

---

## Рекомендованный старт

**Phase 1 (Data Model)** — это фундамент. 80% работы в нём — нормализация существующих `task_log` / `metrics_tasks` в новую схему. Без этого Phase 2-7 строятся на шатком основании.

**Что нужно от тебя:**
- Подтвердить приоритеты (Phase 1 сейчас?)
- Подтвердить лицензию / privacy для хранения git diff (для CSI)
- Подтвердить sandbox choice (Git worktree / Docker — оба доступны)
- Доступ к Docker compose registry (для self-hosting инструкций)