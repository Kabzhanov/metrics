# BizDNAI Metrics Platform — Self-hosting via Docker Compose

This directory contains a self-hosting template for the BizDNAI Metrics Platform.
A single `docker compose up -d` brings up the full stack: PostgreSQL, the
metrics MCP server, the PHP dashboard, and nginx as a reverse proxy.

The compose file is intentionally a **template**. It is not the production
deployment used on the Yandex Cloud VM. It is meant for users who want to run
the same dashboard on their own machine or a private server.

## Quickstart

```bash
cp .env.example .env
# edit .env and set a secure POSTGRES_PASSWORD
docker compose up -d
```

After the stack is up:

- Dashboard:    http://localhost:8080/
- MCP server:   **stdio transport** — connect via `docker compose run --rm -i metrics-mcp` (NOT HTTP)
- PostgreSQL:   localhost:5434 (user/password from .env)

`docker compose logs -f` tails logs from all services. `docker compose down`
stops the stack. To wipe the database, also run `docker volume rm
docker_metrics_db`.

## Services

| Service       | Image / Build        | Port (host) | Purpose                          |
|---------------|----------------------|-------------|----------------------------------|
| postgres      | postgres:15          | 5434        | Metrics database                 |
| metrics-mcp   | build from ./..      | (stdio)     | Python MCP server (read-only, stdio transport) |
| dashboard     | php:8.3-fpm          | (internal)  | PHP dashboard + JSON APIs        |
| nginx         | nginx:alpine         | 8080        | Reverse proxy / static + PHP     |

**MCP server transport:** stdio only (per `server.py:541-552`). MCP clients
(Claude Code, Codex) launch the server as a subprocess and communicate
via stdin/stdout. There is **no HTTP listener** on the `metrics-mcp`
container. Connecting to `localhost:8000/mcp/` will fail (no listener there).

## Volumes

- `metrics_db` — named volume, persists PostgreSQL data across restarts.
- `./../schema/` — mounted into `/docker-entrypoint-initdb.d/`, so the schema
  is applied automatically on the first boot of an empty database.
- Dashboard source is bind-mounted from `var/www/bizdnai.com/metrics/` so
  edits are reflected immediately without rebuilding.

## Healthchecks

`postgres` uses `pg_isready` to confirm it is accepting connections before
`metrics-mcp` starts. The MCP server then connects with the credentials from
`.env` via the standard `METRICS_DB_*` environment variables.

## Logs

```bash
docker compose logs -f metrics-mcp   # MCP server
docker compose logs -f postgres      # PostgreSQL
docker compose logs -f nginx         # Proxy access / error logs
```

## Production caveats

This template does **not** include SSL/TLS. Put it behind a reverse proxy
(Caddy, Traefik, Cloudflare) or expose it only on a trusted network. The
default `POSTGRES_PASSWORD=bizdnai` in `.env.example` is a placeholder — change
it before exposing the stack to anything other than localhost.

The self-hosting path mounts the dashboard source read-only on the host. In
production on the Yandex Cloud VM the dashboard is served directly by the
local nginx + php-fpm, not by this compose stack.
