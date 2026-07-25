# Federated Analytics — план

> Расширение BizDNAI Metrics Platform. Spec: `bizdnai-metrics-spec.md` + `bizdnai-metrics-plan.md`
> Задача: TBD (открыть после согласования)

---

## 1. Концепция

Сейчас `mcp-metrics` — **локальный** MCP-сервер. Каждый юзер ставит свой инстанс (Docker Compose), данные живут у него.

**Federated model:** юзер opt-in делится **обезличенными агрегатами** с центральной таблицей на `bizdnai.com`. Чем больше участников — тем сильнее выборка и показательнее метрики по моделям.

```
[user-A local]  [user-B local]  [user-C local]    ← opt-in share
       \            |              /
        \           |             /
         ↓          ↓            ↓
   https://bizdnai.com/metrics/api_central
              ↓
       central_metrics (no user_id)
              ↓
   https://bizdnai.com/metrics/community   ← aggregated view
```

---

## 2. Что отдаётся (и что НЕ отдаётся)

| Поле | Shared? | Причина |
|---|---|---|
| `model` | ✅ | core signal |
| `task_type` | ✅ | core signal |
| `started_at` (округлённый до дня) | ✅ | aggregation period |
| `duration_sec` | ✅ | core signal |
| `cost_usd` | ✅ | core signal |
| `status` (success/failed/interrupted) | ✅ | core signal |
| `hashed_opaque_id` | ✅ | SHA256(salt + task_id) — нельзя reverse, но позволяет коррелировать в пределах одного submission batch |
| `files_changed_count` | ⚠️ опционально | не raw paths, только число |
| `tokens_in` / `tokens_out` | ❌ | нет |
| `project_id` / `repo` | ❌ | идентифицирует юзера |
| `agent_name` / `agent_version` | ❌ | опционально идентифицирует (можем hash если надо) |
| `file_paths` / `commands` / `prompts` | ❌ | **никогда** — proprietary code |
| `user_task` (description) | ❌ | идентифицирует |
| `task_id` (raw) | ❌ | заменяется на hashed_opaque_id |

---

## 3. Архитектура

### 3.1 Локальная сторона (mcp-metrics пакет)

**Config (`~/.config/mcp-metrics/config.yaml`):**
```yaml
share:
  enabled: false            # default OFF
  endpoint: https://bizdnai.com/metrics/api_central.php
  token: null                # юзер получает на https://bizdnai.com/metrics/community
  interval_hours: 1         # batch interval
  include_files_changed_count: false  # default OFF
```

**Background sender (новый модуль `metrics_mcp/share.py`):**
- Каждые `interval_hours`:
  - SELECT агрегаты из локальной БД за последний час (по `started_at`)
  - Strip private fields (PII, file paths, raw task_id)
  - Hash task_id с server-side salt (per-user) → opaque_id
  - POST batch на central endpoint с token auth
- При ошибке — exponential backoff, store в `~/.local/share/mcp-metrics/queue/` (отправка при следующей попытке)
- При `share.enabled = false` — модуль disabled, ничего не отправляется

### 3.2 Центральная сторона (bizdnai.com)

**Endpoint:** `POST /metrics/api_central.php`
- Auth: Bearer token (юзер получает на community page)
- Rate limit: 100 requests / hour / token
- Body: JSON batch
  ```json
  {
    "batch_id": "uuid",
    "submitter_hash": "sha256(token+salt)",  // уникальный ID юзера, не reversible
    "submitted_at": "2026-07-25T12:00:00Z",
    "events": [
      {
        "opaque_id": "abc123...",
        "model": "claude-opus-4-8",
        "task_type": "feature",
        "started_at_day": "2026-07-25",
        "duration_sec": 120,
        "cost_usd": 0.45,
        "status": "success"
      },
      ...
    ]
  }
  ```
- Validation: только whitelisted поля, drop everything else
- INSERT в `central_metrics` (см. schema ниже)

### 3.3 Schema

```sql
-- Центральная таблица (на bizdnai.com DB)
CREATE TABLE central_metrics (
    id BIGSERIAL PRIMARY KEY,
    batch_id UUID NOT NULL,
    submitter_hash TEXT NOT NULL,  -- SHA256(token+salt), не reversible
    submitted_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    model TEXT NOT NULL,
    task_type TEXT,
    started_at_day DATE NOT NULL,  -- округлённый до дня
    
    duration_sec INT,
    cost_usd NUMERIC(10,4),
    status TEXT NOT NULL,
    files_changed_count INT  -- NULL если не отдано
    
    -- Аудит
    source_token_prefix TEXT,  -- первые 8 символов токена (для отладки отзыва)
    
    -- Индексы для агрегации
    INDEX idx_central_model_day (model, started_at_day),
    INDEX idx_central_task_type (task_type, started_at_day),
    INDEX idx_central_submitter (submitter_hash, submitted_at)
);

-- Запрос токенов (для отзыва)
CREATE TABLE central_tokens (
    token_hash TEXT PRIMARY KEY,  -- SHA256(token), нельзя reverse
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    note TEXT  -- "Rashid's local instance", etc.
);
```

### 3.4 Community dashboard

**URL:** `https://bizdnai.com/metrics/community`

**Содержимое:**
- "X contributors, Y total events" header
- KPI cards (community-wide):
  - Models compared: 6
  - Task types: 12
  - Total runs (community): 50000
  - Median cost per verified: $0.15
- Comparison table (top models by **community** MQI)
- Per-task-type breakdown: "for bugfix, here's what community says is best"
- Submissions last 30 days chart
- "How to participate" CTA: get token, install MCP server, opt-in

---

## 4. Privacy & юридические моменты

**Не делаем пока:**
- ❌ Анонимизация ≠ GDPR compliant. Юзер должен понимать что отдаёт
- ❌ Без явного согласия (double opt-in checkbox + privacy notice)
- ❌ Без возможности удалить свои данные (DSAR-like endpoint)

**Делаем:**
- ✅ Чекбокс opt-in (не opt-out) — default OFF
- ✅ Privacy notice на community page: «что отдаётся, что нет, как удалить»
- ✅ Token revocation endpoint: `DELETE /metrics/api_central.php?token=XXX`
- ✅ Минимум полей: нельзя re-identify юзера по submission
- ✅ Salt per-user (server-side), не клиентский

**В будущем (юрист нужен):**
- Terms of Service
- Privacy Policy для community
- DSAR endpoint (delete all my data)

---

## 5. План реализации (3 фазы)

### Phase F1 — Локальная сторона (M, 2-3 дня)
1. `config.yaml` schema с `share:` секцией
2. `metrics_mcp/share.py` — background sender с queue, retry, batch
3. Update CLI / setup wizard: «Want to share anonymous stats? Get token at https://bizdnai.com/metrics/community»
4. Tests: mock server, verify batch format, verify NO PII in payload

### Phase F2 — Центральная сторона (M, 2-3 дня)
1. `central_metrics` + `central_tokens` schema
2. `POST /metrics/api_central.php` — endpoint с auth, validation, rate limit
3. `DELETE /metrics/api_central.php` — token revocation (soft delete: sets revoked_at)
4. Cron cleanup: `DELETE FROM central_metrics WHERE submitted_at < now() - interval '2 years'` (или по retention policy)

### Phase F3 — Community dashboard (M, 2-3 дня)
1. `https://bizdnai.com/metrics/community` page (separate URL, не unified)
2. `GET /metrics/api_community.php` — aggregated JSON
3. «Get token» flow: simple form → email link → JWT-like token (NO email; just show once)
4. «How to participate» docs: https://github.com/Kabzhanov/metrics/blob/main/docs/COMMUNITY.md

**Total: 6-9 дней, ~1 неделя.**

---

## 6. Открытые вопросы — ответы Рашида (25.07.2026)

| # | Вопрос | Ответ |
|---|---|---|
| 1 | Visibility | **public** — `bizdnai.com/metrics/community` доступен всем |
| 2 | Token issuance | **click-and-copy** — без email, токен показывается один раз после формы |
| 3 | Retention | **навсегда** (храним), «понадобится когда-нибудь» — пока не делать cron cleanup |
| 4 | Sampling | **1%** для больших submissions (anti-overload), 100% для маленьких (<100 events) |
| 5 | Rate limit | **100 req/hour/token** (default) |
| 6 | Top contributors | **отложить** — пока без leaderboard |
| 7 | Anti-poisoning | **да** — whitelist моделей (`ALLOWED_MODEL_PREFIXES` уже есть в task-start.sh, использовать тот же), reject неизвестные |

### Решение по проекту
- **Не отдельный** `Kabzhanov/metrics-community` — фича в существующем `Kabzhanov/metrics`. Single repo.

---

## 7. Что нужно от тебя чтобы стартовать

- ✅ Подтверждение концепции — given
- ✅ Ответы на вопросы — given (см. таблицу выше)
- Решение по проекту — given (не отдельный)

**Стартую Phase F1 (локальная сторона) через агентов.**