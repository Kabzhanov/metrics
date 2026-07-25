# REST API — event-collector (Phase 4.5)

Endpoint для manual ingestion событий от инструментов, которые не умеют
запускать stdin-хуки (Cursor, Continue.dev, GitHub Copilot, ChatGPT web, etc.).
Тот же эффект, что у `hook-collect.py`, но через HTTP POST.

> Задача #1176 · Phase 4.5 · MVP: **без auth, без rate-limit** (оба — Phase 9).

---

## Endpoint

```
POST https://bizdnai.com/metrics/api_events.php
```

Production путь в nginx: `location = /metrics/api_events.php`
(см. `/etc/nginx/sites-enabled/bizdnai`, рядом с `api.php`).

Альтернативный внутренний путь (если проксируется на другой origin) —
`/var/www/bizdnai.com/metrics/api_events.php`.

---

## Заголовки

| Header              | Значение              | Обязательно |
|---------------------|-----------------------|-------------|
| `Content-Type`      | `application/json`    | да          |
| `Accept`            | `application/json`    | рекомендуем |
| `Origin`            | любой (CORS `*`)      | —           |

`OPTIONS` возвращает 204 (preflight CORS).

---

## Тело запроса

JSON-объект. Поля `event_type` обязательно, остальные опциональны.

| Поле          | Тип      | Описание |
|---------------|----------|----------|
| `event_type`  | string   | **required**, whitelist (см. ниже) |
| `session_id`  | string   | трассировка, попадёт в `metadata.session_id` |
| `tool_name`   | string   | имя инструмента (`Edit`, `Bash`, …) |
| `file_path`   | string   | путь к файлу (для `file_*` событий) |
| `command`     | string   | shell-команда (для `command_*` событий). **Redacted** перед INSERT |
| `success`     | bool     | `true`/`false`, default `true` |
| `duration_ms` | int      | время выполнения, ≥ 0 |
| `metadata`    | object   | произвольные доп. поля. **Redacted** рекурсивно |

Лимиты: `tool_name` 256, `file_path` 1024, `command` 4096, `metadata` 8192 chars.

### Whitelist `event_type`

```
file_read | file_created | file_modified | file_deleted
command_started | command_completed
mcp_called
tool_called | tool_completed | tool_error
session_stopped | subagent_stopped | notification
manual
```

Не из whitelist → `400 event_type_not_in_whitelist: <value>`.

### Пример

```bash
curl -X POST 'https://bizdnai.com/metrics/api_events.php' \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type":  "file_modified",
    "tool_name":   "Edit",
    "file_path":   "/var/www/bizdnai.com/metrics/api_events.php",
    "success":     true,
    "duration_ms": 42,
    "metadata":    {"session_id": "abc-123", "agent": "cursor"}
  }'
```

Ответ:

```json
{"status":"ok","event_id":4217,"run_id":298}
```

---

## Что делает сервер

1. Валидирует `event_type` по whitelist; проверяет типы `duration_ms`/`success`/`metadata`.
2. **Secret redaction** (Phase 8.5) применяется к `command` и рекурсивно к
   каждой string-ноде `metadata` ДО INSERT:
   - `sk-[A-Za-z0-9_-]{20,}` → `sk-***REDACTED***` (OpenAI/Anthropic)
   - `ghp_[A-Za-z0-9]{36}` → `ghp_***REDACTED***` (GitHub PAT)
   - `AKIA[A-Z0-9]{16}` → `AKIA***REDACTED***` (AWS Access Key)
   - `xoxb-[A-Za-z0-9-]+` → `xoxb-***REDACTED***` (Slack bot)
3. `SELECT id FROM task_runs WHERE status='in_progress' ORDER BY started_at DESC LIMIT 1`
   — берём самый свежий активный run. Если нет — `503 no_in_progress_run`
   (нечего мониторить, клиент может ретраить позже).
4. `INSERT INTO run_events (...) VALUES (...)` с `source='rest-api'`.
   `metadata._source_detail='rest-api'` для отличия от hook-collect.
5. Возвращает `{status, event_id, run_id}`.

---

## Коды ответа

| Код | Когда | Тело |
|-----|-------|------|
| 200 | INSERT выполнен | `{"status":"ok","event_id":N,"run_id":M}` |
| 204 | `OPTIONS` preflight | пусто |
| 400 | `empty_body` / `invalid_json` / `event_type_required` / `event_type_not_in_whitelist` / `duration_ms_must_be_integer` / `duration_ms_negative` / `metadata_must_be_object` / `metadata_not_serializable` | `{"status":"error","message":"..."}` |
| 405 | метод ≠ POST/OPTIONS | `Allow: POST` |
| 500 | `db_unavailable` / `select_run_failed` / `insert_failed` | `{"status":"error","message":"..."}` |
| 503 | `no_in_progress_run` | `{"status":"error","message":"no_in_progress_run","hint":"..."}` |

---

## Пример с секретом (redaction test)

```bash
curl -X POST 'https://bizdnai.com/metrics/api_events.php' \
  -H 'Content-Type: application/json' \
  -d '{
    "event_type": "command_completed",
    "tool_name":  "Bash",
    "command":    "curl -H \"Authorization: Bearer sk-proj-abc123def456ghi789jkl012mno345pq\" https://api.openai.com/v1/models",
    "success":    true
  }'
```

В БД попадёт:

```sql
SELECT command FROM run_events WHERE event_type='command_completed' AND source='rest-api' ORDER BY id DESC LIMIT 1;
-- command: curl -H "Authorization: Bearer sk-***REDACTED***" https://...
```

То же для metadata:

```bash
curl ... -d '{"event_type":"mcp_called","metadata":{"token":"ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456789","note":"ok"}}'
```

После редикта `metadata.token` → `ghp_***REDACTED***`.

---

## Пример из Python

```python
import json, urllib.request

req = urllib.request.Request(
    "https://bizdnai.com/metrics/api_events.php",
    data=json.dumps({
        "event_type":  "tool_completed",
        "tool_name":   "Edit",
        "file_path":   "/tmp/foo.py",
        "success":     True,
        "duration_ms": 137,
    }).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=5) as r:
    print(r.status, r.read().decode())
```

---

## Пример из Node.js (Cursor / Continue / Copilot)

```js
await fetch("https://bizdnai.com/metrics/api_events.php", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    event_type: "tool_completed",
    tool_name: "Edit",
    file_path: editPath,
    success: !err,
    duration_ms: Date.now() - t0,
    metadata: { session_id, editor: "cursor" }
  })
});
```

---

## Когда нет активной задачи

Endpoint **не создаёт** новую `task_runs`. Если ни одна задача сейчас
не в статусе `in_progress`, INSERT не происходит — вернётся `503`.
Это сознательное решение: REST-события должны привязываться к
существующему наблюдаемому запуску агента, а не плодить orphan-события.

Если нужен «fire-and-forget» для CI/скриптов — стартуйте задачу через
стандартный `task-start.sh` сначала.

---

## Roadmap

- Phase 8.5 — расширенный redaction (base64, JSON-in-string, JWT, GCP keys).
- Phase 9 — auth (HMAC API-key в `Authorization: Bearer …`) + rate-limit
  per IP/per key (Redis token bucket).
- Phase 10 — batch endpoint `POST /api_events_batch.php` для массовой отправки.
