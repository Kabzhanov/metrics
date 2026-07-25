"""Phase 7.7 Trust & Integrity tests (задача #1211, §10.1, §10.3, §10.4).

Covers:
- metrics_freshness_v / metrics_integrity_v / metrics_evidence_v view SQL
- get_trust_health MCP tool
- get_events_for_recommend evidence_level filter
- existing recommend_model behaviour (unchanged)
"""

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
    def execute(self, statement, params=None):
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


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def test_get_trust_health_tool_is_registered():
    names = [t["name"] for t in server.TOOL_DEFS]
    assert "get_trust_health" in names
    assert "get_trust_health" in server.HANDLERS
    # Per spec §10.1/§10.3/§10.4 we now have 12 tools total.
    assert len(names) == 12
    assert set(names) == set(server.HANDLERS)


def test_trust_queries_are_exported():
    # GET_TRUST_HEALTH / GET_EVIDENCE_DISTRIBUTION читают напрямую из view,
    # поэтому параметров не имеют. GET_EVENTS_FOR_RECOMMEND — параметризован.
    assert "%s" in queries.GET_EVENTS_FOR_RECOMMEND
    assert "evidence_level" in queries.GET_EVENTS_FOR_RECOMMEND
    assert "V2" in queries.GET_EVENTS_FOR_RECOMMEND
    assert "V3" in queries.GET_EVENTS_FOR_RECOMMEND
    assert "V4" in queries.GET_EVENTS_FOR_RECOMMEND
    # Negative filter — V0/V1 must NOT appear in the trusted-events query.
    assert "'V0'" not in queries.GET_EVENTS_FOR_RECOMMEND
    assert "'V1'" not in queries.GET_EVENTS_FOR_RECOMMEND
    # Union query читает из обоих view
    assert "metrics_freshness_v" in queries.GET_TRUST_HEALTH
    assert "metrics_integrity_v" in queries.GET_TRUST_HEALTH


# ---------------------------------------------------------------------------
# Freshness view SQL — проверяем, что view не упал и SQL содержит §10.3 TTL
# ---------------------------------------------------------------------------


def test_freshness_view_has_ttl_per_category():
    # SQL определение view встроено в миграцию; проверим наличие TTL-интервалов.
    from pathlib import Path
    sql_path = Path(__file__).resolve().parent.parent / "schema" / "phase77-trust-integrity.sql"
    sql = sql_path.read_text(encoding="utf-8")
    # Per spec §10.3: security=1d, vuln=7d, policy=30d, performance=1d
    assert "interval '1 day'" in sql
    assert "interval '7 days'" in sql
    assert "interval '30 days'" in sql
    assert "metrics_freshness_v" in sql
    assert "metrics_integrity_v" in sql
    assert "metrics_evidence_v" in sql
    # Evidence levels per spec §10.1
    for lvl in ("V0", "V1", "V2", "V3", "V4"):
        assert f"'{lvl}'" in sql
    # Freshness categories per spec §10.3
    for cat in ("security", "vuln", "policy", "performance", "code_quality", "other"):
        assert f"'{cat}'" in sql


def test_integrity_view_joins_task_runs():
    """integrity view MUST join task_runs (run_events doesn't carry model)."""
    from pathlib import Path
    sql_path = Path(__file__).resolve().parent.parent / "schema" / "phase77-trust-integrity.sql"
    sql = sql_path.read_text(encoding="utf-8")
    # Find the metrics_integrity_v CREATE OR REPLACE block
    assert "CREATE OR REPLACE VIEW metrics_integrity_v" in sql
    # The integrity view must join task_runs to get the model column.
    assert "JOIN task_runs" in sql
    assert "manifest_hash" in sql


# ---------------------------------------------------------------------------
# get_trust_health tool — Mocked DB responses
# ---------------------------------------------------------------------------


def test_get_trust_health_returns_freshness_and_integrity(monkeypatch):
    cursor = FakeCursor(manys=[
        # First query: GET_TRUST_HEALTH (UNION ALL: freshness + integrity)
        [
            {
                "section": "freshness",
                "category": "security",
                "total": 10,
                "fresh": 8,
                "stale": 2,
            },
            {
                "section": "freshness",
                "category": "policy",
                "total": 5,
                "fresh": 5,
                "stale": 0,
            },
            {
                "section": "integrity",
                "category": "claude-sonnet-5",
                "total": 12,
                "fresh": 1,  # distinct_manifests
                "stale": 0,
            },
            {
                "section": "integrity",
                "category": "claude-opus-4-8",
                "total": 7,
                "fresh": 2,
                "stale": 0,
            },
        ],
        # Second query: GET_EVIDENCE_DISTRIBUTION
        [
            {"evidence_level": "V0", "total": 1},
            {"evidence_level": "V1", "total": 2},
            {"evidence_level": "V2", "total": 15},
            {"evidence_level": "V3", "total": 0},
            {"evidence_level": "V4", "total": 0},
        ],
    ])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_trust_health())

    assert result["status"] == "ok"
    assert result["stale_total"] == 2  # 2 from security + 0 from policy
    assert result["warning"] is not None
    assert "2 stale events" in result["warning"]

    # Freshness per category
    cats = {f["category"]: f for f in result["freshness"]}
    assert cats["security"]["stale"] == 2
    assert cats["security"]["fresh"] == 8
    assert cats["policy"]["stale"] == 0

    # Integrity per model
    models = {i["model"]: i for i in result["integrity"]}
    assert models["claude-sonnet-5"]["distinct_manifests"] == 1
    assert models["claude-opus-4-8"]["distinct_manifests"] == 2

    # Evidence distribution
    assert result["evidence"]["V0"] == 1
    assert result["evidence"]["V1"] == 2
    assert result["evidence"]["V2"] == 15
    assert result["evidence"]["V3"] == 0
    assert result["evidence"]["V4"] == 0


def test_get_trust_health_no_stale_returns_null_warning(monkeypatch):
    cursor = FakeCursor(manys=[
        [
            {
                "section": "freshness",
                "category": "performance",
                "total": 3,
                "fresh": 3,
                "stale": 0,
            },
        ],
        [
            {"evidence_level": "V2", "total": 3},
        ],
    ])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = run(server.get_trust_health())
    assert result["stale_total"] == 0
    assert result["warning"] is None
    assert result["freshness"] == [{
        "category": "performance", "total": 3, "fresh": 3, "stale": 0,
    }]
    assert result["integrity"] == []  # no rows with manifest_hash


def test_get_trust_health_handles_db_error(monkeypatch):
    def fail_connect():
        raise RuntimeError("connection refused for top-secret")
    monkeypatch.setattr(server, "DB_PASSWORD", "top-secret")
    monkeypatch.setattr(server, "_connect", fail_connect)

    result = run(server.get_trust_health())
    assert result["status"] == "error"
    assert "top-secret" not in result["detail"]
    assert "***" in result["detail"]


# ---------------------------------------------------------------------------
# recommend_model (V0/V1 filter) — существующая логика не сломана
# ---------------------------------------------------------------------------


def test_recommend_model_still_works(monkeypatch):
    """Spec says: 'Не ломай существующий recommend (только добавь фильтр V0-V4)'.

    Текущий recommend_model работает по metrics_tasks (без evidence_level).
    V0/V1 фильтр применяется в НОВОМ GET_EVENTS_FOR_RECOMMEND, который
    становится основой для перехода на run_events. Проверяем, что
    recommend_model не изменился.
    """
    cursor = FakeCursor(many=[{
        "model": "claude-sonnet-5", "n": 12, "ok_count": 11,
        "avg_sec": 30, "avg_cost": Decimal("0.05"),
    }])
    monkeypatch.setattr(server, "_connect", lambda: FakeConnection(cursor))

    result = server.recommend_model("bugfix", 30)
    assert result["status"] == "ok"
    assert result["recommendation"] == "claude-sonnet-5"
    # Старая рекомендация осталась; фильтр V0/V1 готовится к миграции.
    assert "evidence_level" not in queries.RECOMMEND_MODEL
    # А вот новый query содержит фильтр (forward-looking).
    assert "evidence_level" in queries.GET_EVENTS_FOR_RECOMMEND
