"""Smoke tests that never require a live database."""

import pytest

from metrics_mcp import server as server_module


def test_package_imports_and_server_constructs_without_database():
    """MCP server construction is independent from PostgreSQL availability."""
    if server_module.Server is None:
        pytest.skip("mcp dependency is not installed")

    server = server_module.create_server()

    assert server is not None
    assert callable(server_module.get_metrics)
    assert {tool["name"] for tool in server_module.TOOL_DEFS} == {
        "get_metrics",
        "compare_models",
        "recommend_model",
        "get_mqi",
        "get_degradation_report",
        "get_task_metrics",
        "get_run_metrics",
        "compare_runs",
        "get_model_profile",
        "get_benchmark_result",
        "get_documentation_health",
        "get_trust_health",   # Phase 7.7 (задача #1211, §10.1, §10.3, §10.4)
    }


def test_required_database_setting_has_clear_startup_error(monkeypatch):
    monkeypatch.setattr(server_module, "DB_HOST", None)

    with pytest.raises(RuntimeError, match="METRICS_DB_HOST is not set"):
        server_module.validate_db_config()


def test_argument_validation_does_not_connect_to_database():
    assert server_module.get_metrics("") == {
        "status": "error",
        "message": "model is required",
    }
    assert server_module.compare_models("") == {
        "status": "error",
        "message": "spec_id is required",
    }
    assert server_module.recommend_model("") == {
        "status": "error",
        "message": "task_type is required",
    }
    assert server_module.get_mqi("") == {
        "status": "error",
        "message": "model is required",
    }
