"""Secure, stdio-based MCP server for AI-agent performance metrics.

The server reads the ``metrics_tasks`` tables created by ``schema.sql`` and
exposes four read-only tools. Database credentials are deliberately loaded
from environment variables; this module never supplies a password or user
default. Importing the module and constructing the MCP server do not connect to
PostgreSQL. Configuration is validated when the process starts or a database
connection is requested.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from .queries import COMPARE_MODELS, GET_METRICS, GET_MQI

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:  # pragma: no cover - dependency is installed by the package
    psycopg2 = None  # type: ignore[assignment]
    RealDictCursor = None  # type: ignore[assignment]

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:  # pragma: no cover - allows docs/tests without MCP installed
    Server = None  # type: ignore[assignment,misc]
    stdio_server = None  # type: ignore[assignment]
    TextContent = None  # type: ignore[assignment,misc]
    Tool = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_HOST = os.environ.get("METRICS_DB_HOST")
DB_PORT = int(os.environ.get("METRICS_DB_PORT", "5432"))
DB_NAME = os.environ.get("METRICS_DB_NAME", "bizdnai")
DB_USER = os.environ.get("METRICS_DB_USER")
DB_PASSWORD = os.environ.get("METRICS_DB_PASSWORD")

DB_CONFIG = {
    "host": DB_HOST,
    "port": DB_PORT,
    "dbname": DB_NAME,
    "user": DB_USER,
    "password": DB_PASSWORD,
}

_REQUIRED_DB_SETTINGS = (
    "METRICS_DB_HOST",
    "METRICS_DB_USER",
    "METRICS_DB_PASSWORD",
)

PERIOD_DAYS = {
    "1d": 1,
    "7d": 7,
    "30d": 30,
    "90d": 90,
}

logger = logging.getLogger("mcp-metrics")
logging.basicConfig(
    level=os.environ.get("METRICS_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


def validate_db_config() -> None:
    """Fail fast when a required database environment variable is missing."""
    values = {
        "METRICS_DB_HOST": DB_HOST,
        "METRICS_DB_USER": DB_USER,
        "METRICS_DB_PASSWORD": DB_PASSWORD,
    }
    for name in _REQUIRED_DB_SETTINGS:
        value = values[name]
        if value is None or not str(value).strip():
            raise RuntimeError(f"{name} is not set")


def _redact_detail(error: BaseException) -> str:
    """Return an exception message without exposing the configured password."""
    detail = str(error).strip()
    if DB_PASSWORD:
        detail = detail.replace(DB_PASSWORD, "***")
    return detail


# ---------------------------------------------------------------------------
# Database boundary
# ---------------------------------------------------------------------------


def _connect():
    """Open a PostgreSQL connection after validating startup configuration."""
    validate_db_config()
    if psycopg2 is None:  # pragma: no cover - covered by packaging metadata
        raise RuntimeError("psycopg2-binary is not installed")
    return psycopg2.connect(**DB_CONFIG, cursor_factory=RealDictCursor)


def _parse_period(period: str) -> int:
    """Convert a supported period such as ``7d`` to a number of days."""
    value = (period or "7d").strip().lower()
    if value not in PERIOD_DAYS:
        logger.warning("Unknown period %r, falling back to 7d", period)
        return PERIOD_DAYS["7d"]
    return PERIOD_DAYS[value]


def _since_clause(period: str) -> tuple[str, datetime]:
    """Return the parameterized timestamp predicate and its UTC boundary."""
    days = _parse_period(period)
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return "started_at >= %s", since


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _stub(payload: dict[str, Any]) -> dict[str, Any]:
    """Mark the response as the current MVP contract while retaining results."""
    return {
        "status": "ok",
        "stub": True,
        "message": "MVP metrics response; quality-weighted MQI is planned for Phase 2",
        "echo": payload,
    }


def _err(message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "message": message, **extra}


def get_metrics(model: str, period: str = "7d") -> dict[str, Any]:
    """Return success rate, latency, and cost aggregates for one model."""
    if not model or not model.strip():
        return _err("model is required")
    period = period or "7d"
    try:
        _, since = _since_clause(period)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(GET_METRICS, (model, since))
            row = cur.fetchone()
        if not row:
            return _stub({
                "model": model,
                "period": period,
                "hint": "no model runs found for the requested period",
            })
        return _stub({
            "model": model,
            "period": period,
            "since": since.isoformat(),
            "total": row["total"],
            "success_rate": round((row["ok_count"] or 0) / row["total"], 4)
            if row["total"]
            else None,
            "avg_sec": row["avg_sec"],
            "avg_cost_usd": float(row["avg_cost"])
            if row["avg_cost"] is not None
            else None,
        })
    except Exception as error:
        logger.exception("get_metrics failed")
        return _err("get_metrics failed", detail=_redact_detail(error))


def compare_models(spec_id: str) -> dict[str, Any]:
    """Compare completed model runs belonging to one scenario."""
    if not spec_id or not spec_id.strip():
        return _err("spec_id is required")
    try:
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(COMPARE_MODELS, (spec_id,))
            rows = cur.fetchall()
        if not rows:
            return _stub({"spec_id": spec_id, "hint": "no completed runs found"})
        return _stub({
            "spec_id": spec_id,
            "models": [
                {
                    "model": row["model"],
                    "total": row["total"],
                    "success_rate": round(
                        (row["ok_count"] or 0) / row["total"], 4
                    )
                    if row["total"]
                    else None,
                    "avg_sec": row["avg_sec"],
                    "avg_cost_usd": float(row["avg_cost"])
                    if row["avg_cost"] is not None
                    else None,
                }
                for row in rows
            ],
        })
    except Exception as error:
        logger.exception("compare_models failed")
        return _err("compare_models failed", detail=_redact_detail(error))


def recommend_model(task_type: str) -> dict[str, Any]:
    """Return the current recommendation contract for a task category."""
    if not task_type or not task_type.strip():
        return _err("task_type is required")
    return _stub({
        "task_type": task_type,
        "hint": "Phase 2 will combine success rate, cost, latency, and quality",
    })


def get_mqi(model: str, period: str = "7d") -> dict[str, Any]:
    """Return the MVP Model Quality Index for one model and period."""
    if not model or not model.strip():
        return _err("model is required")
    period = period or "7d"
    try:
        _, since = _since_clause(period)
        with _connect() as conn, conn.cursor() as cur:
            cur.execute(GET_MQI, (model, since))
            row = cur.fetchone()
        if not row or not row["total"]:
            return _stub({"model": model, "period": period, "hint": "no data"})
        success_rate = (row["ok_count"] or 0) / row["total"]
        mqi = round(success_rate, 4)
        return _stub({
            "model": model,
            "period": period,
            "mqi": mqi,
            "components": {"success_rate": mqi},
            "note": "MVP formula: mqi = success_rate; cost/latency/quality are Phase 2",
        })
    except Exception as error:
        logger.exception("get_mqi failed")
        return _err("get_mqi failed", detail=_redact_detail(error))


# ---------------------------------------------------------------------------
# MCP schema and transport
# ---------------------------------------------------------------------------

TOOL_DEFS = [
    {
        "name": "get_metrics",
        "description": "Metrics for one model over a period (success rate, latency, cost).",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {
                    "type": "string",
                    "description": "Model identifier, for example claude-sonnet-5",
                },
                "period": {
                    "type": "string",
                    "description": "1d, 7d, 30d, or 90d",
                    "default": "7d",
                },
            },
            "required": ["model"],
        },
    },
    {
        "name": "compare_models",
        "description": "Compare completed model runs for one battle-mode scenario.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spec_id": {"type": "string", "description": "Scenario identifier"},
            },
            "required": ["spec_id"],
        },
    },
    {
        "name": "recommend_model",
        "description": "Recommend a model for a task type using MQI inputs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_type": {
                    "type": "string",
                    "description": "Task category such as code, review, or research",
                },
            },
            "required": ["task_type"],
        },
    },
    {
        "name": "get_mqi",
        "description": "Return the Model Quality Index for one model and period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "description": "Model identifier"},
                "period": {
                    "type": "string",
                    "default": "7d",
                    "description": "1d, 7d, 30d, or 90d",
                },
            },
            "required": ["model"],
        },
    },
]

HANDLERS = {
    "get_metrics": lambda args: get_metrics(args.get("model", ""), args.get("period", "7d")),
    "compare_models": lambda args: compare_models(args.get("spec_id", "")),
    "recommend_model": lambda args: recommend_model(args.get("task_type", "")),
    "get_mqi": lambda args: get_mqi(args.get("model", ""), args.get("period", "7d")),
}


def create_server() -> Any:
    """Construct the MCP ``Server`` without opening a database connection."""
    if Server is None:
        raise RuntimeError("mcp SDK is not installed; install the package dependencies")
    server = Server("mcp-metrics")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=definition["name"],
                description=definition["description"],
                inputSchema=definition["input_schema"],
            )
            for definition in TOOL_DEFS
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handler = HANDLERS.get(name)
        if handler is None:
            result = _err(f"unknown tool: {name}")
        else:
            try:
                result = handler(arguments or {})
            except Exception as error:
                logger.exception("tool %s crashed", name)
                result = _err(f"tool {name} crashed", detail=_redact_detail(error))
        return [
            TextContent(
                type="text",
                text=json.dumps(result, ensure_ascii=False, default=str),
            )
        ]

    return server


# Backwards-compatible internal name used by early deployments.
_build_server = create_server


async def run() -> None:
    """Validate configuration and run the MCP stdio transport."""
    validate_db_config()
    if stdio_server is None:
        raise RuntimeError("mcp SDK is not installed; install the package dependencies")
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


# Backwards-compatible internal name used by early deployments.
_run = run


def main() -> None:
    """Process entry point; missing required settings fail before transport."""
    validate_db_config()
    logger.info("mcp-metrics starting (DB=%s:%s/%s)", DB_HOST, DB_PORT, DB_NAME)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("mcp-metrics stopped by user")


__all__ = [
    "DB_CONFIG",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_USER",
    "HANDLERS",
    "TOOL_DEFS",
    "compare_models",
    "create_server",
    "get_metrics",
    "get_mqi",
    "main",
    "recommend_model",
    "run",
    "validate_db_config",
]


if __name__ == "__main__":
    main()
