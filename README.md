# metrics-mcp

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Security policy](https://img.shields.io/badge/security-policy-blue.svg)](SECURITY.md)

A read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io/)
server for AI-agent performance metrics. It reads PostgreSQL task telemetry and
exposes Model Quality Index (MQI), success rate, latency, cost, model comparison,
and recommendation tools over the standard MCP stdio transport.

> **Status:** 0.1.0 MVP. MQI currently equals success rate. Cost-, latency-, and
> quality-weighted scoring is planned for a later phase; see
> [`docs/future-metrics.md`](docs/future-metrics.md).

## Features

- Four MCP tools for model and scenario analysis.
- Parameterized PostgreSQL queries with no schema mutation at runtime.
- Fail-fast configuration: required database credentials have no code defaults.
- Importable Python package and `metrics-mcp` console entry point.
- Standalone degradation detector for scheduled day-over-day checks.
- SQL examples and architecture documentation for dashboard integrations.

## Installation

Python 3.10 or newer is required.

```bash
git clone https://github.com/Kabzhanov/metrics.git
cd metrics
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

For a production-style install, omit the `[dev]` extra:

```bash
python -m pip install .
```

## Quick start

1. Apply the schema with your migration process. The package never creates or
   alters database objects:

   ```bash
   psql "$DATABASE_URL" -f schema.sql
   ```

2. Create a local environment file and replace every placeholder with values
   from your secret manager:

   ```bash
   cp .env.example .env
   # edit .env; never commit it
   set -a
   . ./.env
   set +a
   ```

   `METRICS_DB_HOST`, `METRICS_DB_USER`, and `METRICS_DB_PASSWORD` are required.
   `METRICS_DB_PORT` defaults to `5432`; `METRICS_DB_NAME` defaults to `bizdnai`.

3. Start the stdio server:

   ```bash
   python -m metrics_mcp.server
   # or, after installation:
   metrics-mcp
   ```

The server validates required settings before opening a connection. Importing
`metrics_mcp` or constructing `create_server()` does not contact PostgreSQL.

### MCP client configuration

Point an MCP-compatible client at the installed package and pass credentials
through its environment configuration. The values below are placeholders.

```json
{
  "mcpServers": {
    "metrics": {
      "command": "python",
      "args": ["-m", "metrics_mcp.server"],
      "env": {
        "METRICS_DB_HOST": "db.example.internal",
        "METRICS_DB_PORT": "5432",
        "METRICS_DB_NAME": "bizdnai",
        "METRICS_DB_USER": "metrics_reader",
        "METRICS_DB_PASSWORD": "CHANGEME"
      }
    }
  }
}
```

## MCP tools

| Tool | Required arguments | Description |
| --- | --- | --- |
| `get_metrics` | `model`; optional `period` | Success rate, run count, average duration, and average cost. |
| `compare_models` | `spec_id` | Compares completed model runs in one scenario. |
| `recommend_model` | `task_type` | Returns the MVP recommendation contract and Phase 2 hint. |
| `get_mqi` | `model`; optional `period` | Returns the MVP MQI (`0..1`, currently success rate). |

Supported periods are `1d`, `7d`, `30d`, and `90d`. Unknown periods safely fall
back to `7d`. Empty required tool arguments return a structured error instead
of opening a database connection.

## Configuration reference

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `METRICS_DB_HOST` | Yes | — | PostgreSQL host. |
| `METRICS_DB_PORT` | No | `5432` | PostgreSQL port. |
| `METRICS_DB_NAME` | No | `bizdnai` | Database name. |
| `METRICS_DB_USER` | Yes | — | Read-only database user. |
| `METRICS_DB_PASSWORD` | Yes | — | Database password. |
| `METRICS_LOG_LEVEL` | No | `INFO` | Python logging level. |
| `DEGRADATION_ALERT_TO` | Detector only | — | Recipient for degradation alerts. |
| `DEGRADATION_SEND_EMAIL` | Detector only | `send_email.py` | Email helper executable path. |

The repository includes `.env.example` and `examples/sample.env`, both safe
templates. `.env`, `.env.local`, logs, caches, and build artifacts are ignored.

## Degradation detector

`detect_degradation.py` uses the same `METRICS_DB_*` variables and compares the
current and preceding window. It emits JSON on stdout and writes its local log
to `~/.mcp-metrics/detect.log`.

```bash
set -a; . ./.env; set +a
export DEGRADATION_ALERT_TO=alerts@example.com
python detect_degradation.py --dry-run --period 1d --threshold 0.10
```

Use a scheduler outside this repository for production runs. Do not add
`detect.log` or SMTP credentials to Git.

## Database and dashboard

- [`schema.sql`](schema.sql) defines `metrics_tasks`, model pricing, indexes,
  and the `metrics_tasks_v` daily aggregate view.
- [`dashboard.md`](dashboard.md) describes the companion dashboard contract.
- [`examples/query_examples.sql`](examples/query_examples.sql) contains
  read-only reporting queries.
- [`docs/architecture.md`](docs/architecture.md) explains the transport and
  database boundaries.
- [`docs/metrics.md`](docs/metrics.md) defines the current and planned metrics.

## Development

Run the test suite and linter before opening a pull request:

```bash
python -m pytest
ruff check .
```

Database tests use fakes/mocks and do not require a live PostgreSQL instance.
Keep new SQL parameterized, add a focused test, and document any schema
migration separately from the MCP process.

## Contributing

Issues and pull requests are welcome. Please include the motivation, a focused
change, tests, and any security or migration impact. Do not include production
telemetry, credentials, private connection strings, or customer data in issues
or patches.

## License

Released under the [MIT License](LICENSE). See [SECURITY.md](SECURITY.md) for
private vulnerability reporting.
