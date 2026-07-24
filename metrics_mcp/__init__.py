"""MCP server package for AI-agent performance metrics."""

from .server import (
    DB_CONFIG,
    DB_HOST,
    DB_NAME,
    DB_PASSWORD,
    DB_PORT,
    DB_USER,
    HANDLERS,
    TOOL_DEFS,
    compare_models,
    create_server,
    get_metrics,
    get_mqi,
    main,
    recommend_model,
    run,
    validate_db_config,
)

__version__ = "0.1.0"

__all__ = [
    "DB_CONFIG",
    "DB_HOST",
    "DB_NAME",
    "DB_PASSWORD",
    "DB_PORT",
    "DB_USER",
    "HANDLERS",
    "TOOL_DEFS",
    "__version__",
    "compare_models",
    "create_server",
    "get_metrics",
    "get_mqi",
    "main",
    "recommend_model",
    "run",
    "validate_db_config",
]
