"""Unit tests for the parameterized PostgreSQL query boundary."""

from datetime import datetime, timezone

from metrics_mcp import queries, server


class FakeCursor:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params):
        self.executed.append((statement, params))

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakeConnection:
    def __init__(self, cursor):
        self.fake_cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.fake_cursor


def test_query_statements_are_parameterized_and_target_metrics_table():
    for statement in (queries.GET_METRICS, queries.COMPARE_MODELS, queries.GET_MQI):
        assert "metrics_tasks" in statement
        assert "%s" in statement
        assert "f\"" not in statement


def test_get_metrics_uses_bound_model_and_timestamp(monkeypatch):
    cursor = FakeCursor(one={
        "total": 4,
        "ok_count": 3,
        "failed_count": 1,
        "avg_sec": 12,
        "avg_cost": 0.0125,
    })
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = server.get_metrics("claude-sonnet-5", "1d")

    assert result["status"] == "ok"
    assert result["echo"]["total"] == 4
    assert result["echo"]["success_rate"] == 0.75
    statement, params = cursor.executed[0]
    assert statement == queries.GET_METRICS
    assert params[0] == "claude-sonnet-5"
    assert isinstance(params[1], datetime)
    assert params[1].tzinfo == timezone.utc


def test_compare_models_maps_each_database_row(monkeypatch):
    cursor = FakeCursor(many=[
        {"model": "model-a", "total": 2, "ok_count": 2, "avg_sec": 5, "avg_cost": None},
        {"model": "model-b", "total": 4, "ok_count": 3, "avg_sec": 8, "avg_cost": 0.25},
    ])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = server.compare_models("scenario-1")

    assert result["echo"]["spec_id"] == "scenario-1"
    assert result["echo"]["models"] == [
        {
            "model": "model-a",
            "total": 2,
            "success_rate": 1.0,
            "avg_sec": 5,
            "avg_cost_usd": None,
        },
        {
            "model": "model-b",
            "total": 4,
            "success_rate": 0.75,
            "avg_sec": 8,
            "avg_cost_usd": 0.25,
        },
    ]
    assert cursor.executed[0] == (queries.COMPARE_MODELS, ("scenario-1",))


def test_get_mqi_returns_success_rate_for_rows(monkeypatch):
    cursor = FakeCursor(one={"total": 10, "ok_count": 8, "avg_cost": 0.1})
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = server.get_mqi("model-a", "30d")

    assert result["echo"]["mqi"] == 0.8
    assert result["echo"]["components"] == {"success_rate": 0.8}
    assert cursor.executed[0][0] == queries.GET_MQI
    assert cursor.executed[0][1][0] == "model-a"


def test_unknown_period_falls_back_to_seven_days():
    assert server._parse_period("not-a-period") == 7
    assert server._parse_period(None) == 7
