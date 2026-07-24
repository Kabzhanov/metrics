# Architecture

`metrics-mcp` is a read-only MCP server with three boundaries.

1. **MCP stdio transport.** `metrics_mcp.server:main` creates an MCP `Server`,
   advertises four tools, and serves JSON tool responses over stdin/stdout.
   Logs go to stderr through Python logging so they do not corrupt the MCP
   protocol stream.
2. **Application handlers.** `get_metrics`, `compare_models`, `recommend_model`,
   and `get_mqi` validate their arguments and return JSON-compatible dictionaries.
   The current MVP reports success-rate-based values; the response contract
   leaves room for cost, latency, and quality components in Phase 2.
3. **PostgreSQL boundary.** `_connect()` opens a short-lived psycopg2 connection
   using `METRICS_DB_*` environment variables. SQL statements live in
   `metrics_mcp/queries.py`, use bound parameters, and read the tables created
   by the root-level `schema.sql`.

## Startup sequence

```text
metrics-mcp / python -m metrics_mcp.server
        |
        +--> validate METRICS_DB_HOST, METRICS_DB_USER, METRICS_DB_PASSWORD
        |
        +--> create MCP Server (no database connection yet)
        |
        +--> stdio_server()
                    |
                    +--> tool call -> handler -> psycopg2 -> metrics_tasks
```

Importing the package and constructing `create_server()` do not require a live
PostgreSQL instance. A missing required setting raises a clear `RuntimeError`
when the process starts or a handler needs a connection.

## Data model

`metrics_tasks` stores one task execution and its model, status, timing, token,
and cost metadata. `metrics_model_pricing` stores optional per-model pricing.
`metrics_tasks_v` provides daily aggregates for dashboards. Apply `schema.sql`
with a database migration process before starting the server; the MCP process
never creates or alters schema.

## Extension points

- Add a parameterized statement to `metrics_mcp/queries.py`.
- Add a handler with a stable dictionary response contract in `server.py`.
- Register its input schema in `TOOL_DEFS` and dispatch it in `HANDLERS`.
- Add a mocked database test under `tests/` before connecting it to production.
