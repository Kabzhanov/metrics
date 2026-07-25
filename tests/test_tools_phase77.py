"""Phase 7.7 tests for the six normalized-schema MCP metric tools."""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from metrics_mcp import queries, server


class FakeCursor:
    def __init__(self, *, one=None, many=None, ones=None, manys=None):
        self.one = one
        self.many = many or []
        self.ones = list(ones or [])
        self.manys = list(manys or [])
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params):
        self.executed.append((statement, params))

    def fetchone(self):
        return self.ones.pop(0) if self.ones else self.one

    def fetchall(self):
        return self.manys.pop(0) if self.manys else self.many


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.fake_cursor


def run(coro):
    return asyncio.run(coro)


def test_phase77_tool_definitions_and_queries_are_registered():
    names = [tool["name"] for tool in server.TOOL_DEFS]

    # 12 tools: 5 phase1 + 6 phase7.7 normalized + 1 phase7.7 trust (задача #1211).
    assert len(names) == 12
    # Phase 7.7 normalized-schema tools (positions 5-10).
    assert set(names[5:11]) == {
        "get_task_metrics",
        "get_run_metrics",
        "compare_runs",
        "get_model_profile",
        "get_benchmark_result",
        "get_documentation_health",
    }
    # Phase 7.7 Trust & Integrity tool (задача #1211, §10.1, §10.3, §10.4).
    assert names[11] == "get_trust_health"
    assert set(names) == set(server.HANDLERS)
    for statement in (
        queries.GET_TASK_METRICS,
        queries.GET_RUN_METRICS,
        queries.COMPARE_RUNS,
        queries.GET_MODEL_PROFILE,
        queries.GET_BENCHMARK_RESULT,
        queries.GET_DOCUMENTATION_HEALTH,
    ):
        assert "%s" in statement


def test_get_task_metrics_formats_aggregated_runs(monkeypatch):
    cursor = FakeCursor(many=[{
        "model": "claude-opus-4-8",
        "n": 3,
        "avg_duration": Decimal("12.5"),
        "avg_cost": Decimal("0.1250"),
        "build_passed": True,
        "tests_passed": True,
        "human_accepted": False,
        "stable_runs": 2,
    }])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_task_metrics(42))

    assert result == {
        "task_id": 42,
        "runs": [{
            "model": "claude-opus-4-8",
            "n": 3,
            "avg_duration_sec": 12.5,
            "avg_cost_usd": 0.125,
            "build_passed": True,
            "tests_passed": True,
            "human_accepted": False,
            "stable_runs": 2,
        }],
    }
    assert cursor.executed == [(queries.GET_TASK_METRICS, (42,))]


def test_get_run_metrics_formats_evaluation_and_interventions(monkeypatch):
    started = datetime(2026, 7, 24, 10, 0, tzinfo=timezone.utc)
    completed = datetime(2026, 7, 24, 10, 1, tzinfo=timezone.utc)
    cursor = FakeCursor(many=[{
        "run_id": 7,
        "model": "claude-sonnet-5",
        "agent": "claude-code",
        "started_at": started,
        "completed_at": completed,
        "duration_sec": 60,
        "cost_usd": Decimal("0.2500"),
        "input_tokens": 100,
        "output_tokens": 20,
        "cached_tokens": 50,
        "evaluation_id": 90,
        "agent_completed": True,
        "build_passed": True,
        "tests_passed": True,
        "lint_passed": None,
        "acceptance_passed": True,
        "human_accepted": True,
        "review_approved": False,
        "stable_after_7d": True,
        "stable_after_30d": False,
        "reopened": False,
        "evaluation_score": Decimal("0.900"),
        "intervention_id": 8,
        "intervention_timestamp": started,
        "intervention_type": "clarification",
        "intervention_severity": "minor",
        "intervention_description": "Asked one question",
        "estimated_minutes": 2,
    }])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_run_metrics(7))

    assert result["run_id"] == 7
    assert result["agent"] == "claude-code"
    assert result["cost_usd"] == 0.25
    assert result["tokens"] == {"input": 100, "output": 20, "cached": 50, "total": 170}
    assert result["evaluation"]["evaluation_score"] == 0.9
    assert result["human_interventions"] == [{
        "id": 8,
        "timestamp": started,
        "type": "clarification",
        "severity": "minor",
        "description": "Asked one question",
        "estimated_minutes": 2,
    }]
    assert cursor.executed == [(queries.GET_RUN_METRICS, (7,))]


def test_compare_runs_preserves_requested_order(monkeypatch):
    cursor = FakeCursor(many=[
        {"id": 2, "model": "b", "started_at": None, "duration_sec": 20,
         "cost_usd": Decimal("0.2"), "status": "success", "build_passed": True,
         "tests_passed": True, "human_accepted": True, "reopened": False},
        {"id": 1, "model": "a", "started_at": None, "duration_sec": 10,
         "cost_usd": Decimal("0.1"), "status": "failed", "build_passed": False,
         "tests_passed": False, "human_accepted": False, "reopened": True},
    ])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.compare_runs([1, 2]))

    assert [item["run_id"] for item in result["runs"]] == [1, 2]
    assert result["runs"][0]["cost_usd"] == 0.1
    assert cursor.executed == [(queries.COMPARE_RUNS, ([1, 2],))]


def test_get_model_profile_formats_task_type_metrics(monkeypatch):
    cursor = FakeCursor(many=[{
        "task_type": "bugfix", "n": 5, "success_rate": Decimal("0.8"),
        "avg_dur": Decimal("30.5"), "avg_cost": Decimal("0.12"),
    }])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_model_profile("claude-opus-4-8", 30))

    assert result == {
        "model": "claude-opus-4-8",
        "period_days": 30,
        "by_task_type": [{
            "task_type": "bugfix", "n": 5, "success_rate": 0.8,
            "avg_duration_sec": 30.5, "avg_cost_usd": 0.12,
        }],
    }
    assert cursor.executed == [(queries.GET_MODEL_PROFILE, ("claude-opus-4-8", 30))]


def test_get_benchmark_result_returns_benchmark_and_runs(monkeypatch):
    benchmark = {"id": 3, "name": "phase77", "status": "completed"}
    runs = [{"id": 10, "benchmark_id": 3, "model": "model-a", "cost": Decimal("0.4")}]
    cursor = FakeCursor(ones=[benchmark], manys=[runs])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_benchmark_result(3))

    assert result == {
        "benchmark": benchmark,
        "runs": [{"id": 10, "benchmark_id": 3, "model": "model-a", "cost": 0.4}],
    }
    assert cursor.executed == [
        (queries.GET_BENCHMARK_RESULT, (3,)),
        (queries.GET_BENCHMARK_RUNS, (3,)),
    ]


def test_get_benchmark_result_returns_not_found(monkeypatch):
    cursor = FakeCursor(one=None)
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    assert run(server.get_benchmark_result(999)) == {"error": "not_found"}


def test_get_documentation_health_uses_human_accepted_proxy(monkeypatch):
    day = datetime(2026, 7, 24, tzinfo=timezone.utc)
    cursor = FakeCursor(many=[{
        "day": day, "docs_synced": 3, "total_tasks": 4,
        "doc_sync_rate": Decimal("0.7500"),
    }])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_documentation_health(1, 30))

    assert result == {
        "project_id": 1,
        "period_days": 30,
        "by_day": [{
            "day": day,
            "docs_synced": 3,
            "total_tasks": 4,
            "doc_sync_rate": 0.75,
        }],
        "avg_doc_sync_rate": 0.75,
    }
    assert "human_accepted" in queries.GET_DOCUMENTATION_HEALTH
    assert "docs_validated" not in queries.GET_DOCUMENTATION_HEALTH
    assert cursor.executed == [(queries.GET_DOCUMENTATION_HEALTH, (1, 30))]


@pytest.mark.parametrize(
    ("call", "args"),
    [
        (server.get_task_metrics, (1,)),
        (server.get_run_metrics, (1,)),
        (server.compare_runs, ([1],)),
        (server.get_model_profile, ("model", 30)),
        (server.get_benchmark_result, (1,)),
        (server.get_documentation_health, (1, 30)),
    ],
)
def test_phase77_tools_redact_database_password(monkeypatch, call, args):
    monkeypatch.setattr(server, "DB_PASSWORD", "top-secret")

    def fail_connect():
        raise RuntimeError("connection failed for top-secret")

    monkeypatch.setattr(server, "_connect", fail_connect)

    result = run(call(*args))

    assert result["status"] == "error"
    assert "top-secret" not in result["detail"]
    assert "***" in result["detail"]
