"""Phase F1 tests for opt-in federated sharing."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
from datetime import datetime, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from metrics_mcp import server as server_module
from metrics_mcp.config import DEFAULT_CONFIG, load_config, save_config
from metrics_mcp.setup import configure_share
from metrics_mcp.share import ShareSender, prepare_events, sample_events, start_share_thread


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, statement, params):
        self.executed.append((statement, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def cursor(self):
        return self.cursor_instance


def _config(endpoint="https://example.invalid/share", **overrides):
    share = {
        **DEFAULT_CONFIG["share"],
        "enabled": True,
        "token": "community-secret",
        "endpoint": endpoint,
        **overrides,
    }
    return {"share": share}


def _row(task_id="42", model="claude-sonnet-5"):
    return {
        "task_id": task_id,
        "model": model,
        "task_type": "feature",
        "started_at": datetime(2026, 7, 25, 11, 42, tzinfo=timezone.utc),
        "duration_sec": 120,
        "cost_usd": Decimal("0.4500"),
        "status": "success",
        "files_changed_count": 3,
        # These deliberately private fields must never enter the payload.
        "project_id": "private-client",
        "agent_name": "rashid-agent",
        "file_path": "/home/private/secret.py",
    }


def test_missing_config_is_created_with_safe_defaults(tmp_path):
    path = tmp_path / "nested" / "config.yaml"

    config = load_config(path=path, environ={})

    assert config == DEFAULT_CONFIG
    assert path.exists()
    assert path.stat().st_mode & 0o777 == 0o600
    assert load_config(path=path, environ={}) == DEFAULT_CONFIG


def test_config_environment_overrides_and_save_round_trip(tmp_path):
    path = tmp_path / "config.yaml"
    config = load_config(
        path=path,
        environ={
            "METRICS_SHARE_ENABLED": "true",
            "METRICS_SHARE_TOKEN": "token-from-env",
            "METRICS_SHARE_INTERVAL_HOURS": "4",
            "METRICS_SHARE_INCLUDE_FILES_CHANGED_COUNT": "yes",
        },
    )
    assert config["share"]["enabled"] is True
    assert config["share"]["token"] == "token-from-env"
    assert config["share"]["interval_hours"] == 4
    assert config["share"]["include_files_changed_count"] is True

    config["share"]["endpoint"] = "https://central.example/api"
    save_config(config, path=path)
    saved = load_config(path=path, environ={})
    assert saved["share"]["endpoint"] == "https://central.example/api"


def test_config_rejects_plain_http_except_for_local_mock_servers(tmp_path):
    config = {"share": {**DEFAULT_CONFIG["share"], "endpoint": "http://central.example/api"}}
    with pytest.raises(ValueError, match="HTTPS"):
        save_config(config, path=tmp_path / "remote.yaml")

    config["share"]["endpoint"] = "http://127.0.0.1:8080/mock"
    save_config(config, path=tmp_path / "local.yaml")


def test_prepare_events_hashes_id_and_strips_all_private_fields():
    events = prepare_events(
        [_row()],
        token="community-secret",
        include_files_changed_count=False,
    )

    expected_hash = hashlib.sha256(b"community-secret42").hexdigest()[:16]
    assert events == [
        {
            "opaque_id": expected_hash,
            "model": "claude-sonnet-5",
            "task_type": "feature",
            "started_at_day": "2026-07-25",
            "duration_sec": 120,
            "cost_usd": 0.45,
            "status": "success",
        }
    ]
    encoded = json.dumps(events)
    assert "task_id" not in encoded
    assert events[0]["opaque_id"] != "42"
    for private_value in ("private-client", "rashid-agent", "/home/private"):
        assert private_value not in encoded


def test_prepare_events_optionally_includes_only_file_count():
    event = prepare_events(
        [_row()],
        token="community-secret",
        include_files_changed_count=True,
    )[0]

    assert event["files_changed_count"] == 3
    assert "file_path" not in event


def test_anti_poisoning_drops_unknown_models_and_logs_count(caplog):
    caplog.set_level(logging.WARNING, logger="mcp-metrics.share")

    events = prepare_events(
        [_row("1", "evil-injected-model"), _row("2", "evil-injected-model")],
        token="community-secret",
    )

    assert events == []
    assert "dropped 2 events with unknown model evil-injected-model" in caplog.text


def test_sampling_uses_one_percent_only_above_one_hundred():
    small = [{"opaque_id": str(index)} for index in range(100)]
    large = [{"opaque_id": str(index)} for index in range(250)]

    selected_small, deferred_small = sample_events(small, rng=random.Random(7))
    selected_large, deferred_large = sample_events(large, rng=random.Random(7))

    assert selected_small == small
    assert deferred_small == []
    assert len(selected_large) == 2
    assert len(deferred_large) == 248
    assert {event["opaque_id"] for event in selected_large}.isdisjoint(
        {event["opaque_id"] for event in deferred_large}
    )


def test_sender_posts_expected_batch_to_mock_http_endpoint(tmp_path):
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers["Content-Length"])
            received["path"] = self.path
            received["authorization"] = self.headers["Authorization"]
            received["body"] = json.loads(self.rfile.read(length))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}/metrics/api_central.php"
        sender = ShareSender(
            connect=lambda: FakeConnection([_row()]),
            config=_config(endpoint),
            queue_dir=tmp_path / "queue",
        )
        result = sender.run_once()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()

    assert result["status"] == "sent"
    assert received["path"] == "/metrics/api_central.php"
    assert received["authorization"] == "Bearer community-secret"
    body = received["body"]
    assert set(body) == {"batch_id", "submitter_hash", "submitted_at", "events"}
    assert len(body["submitter_hash"]) == 64
    assert len(body["events"]) == 1
    assert set(body["events"][0]) == {
        "opaque_id",
        "model",
        "task_type",
        "started_at_day",
        "duration_sec",
        "cost_usd",
        "status",
    }


def test_failed_post_is_queued_then_retried_successfully(tmp_path):
    queue_dir = tmp_path / "queue"

    def fail_post(*_args, **_kwargs):
        raise OSError("central unavailable")

    first = ShareSender(
        connect=lambda: FakeConnection([_row()]),
        config=_config(),
        queue_dir=queue_dir,
        http_post=fail_post,
    )
    result = first.run_once()

    queued = list(queue_dir.glob("*.json"))
    assert result["status"] == "failed"
    assert result["retry_in_seconds"] == 60
    assert len(queued) == 1
    queue_payload = json.loads(queued[0].read_text(encoding="utf-8"))
    assert queue_payload["attempts"] == 1
    assert queue_payload["events"][0]["opaque_id"]
    assert "task_id" not in json.dumps(queue_payload)

    premature_posts = []
    waiting = ShareSender(
        connect=lambda: FakeConnection([_row()]),
        config=_config(),
        queue_dir=queue_dir,
        http_post=lambda *_args: premature_posts.append(True),
    )
    waiting_result = waiting.run_once()
    assert waiting_result == {"status": "idle", "events": 0}
    assert premature_posts == []
    assert len(list(queue_dir.glob("*.json"))) == 1

    posted = []

    def succeed_post(_endpoint, payload, _token, _timeout):
        posted.append(payload)

    second = ShareSender(
        connect=lambda: FakeConnection([]),
        config=_config(),
        queue_dir=queue_dir,
        http_post=succeed_post,
    )
    result = second.run_once(ignore_retry_time=True)

    assert result["status"] == "sent"
    assert len(posted[0]["events"]) == 1
    assert list(queue_dir.glob("*.json")) == []


def test_disabled_share_neither_connects_nor_starts_thread(tmp_path):
    calls = []
    config = {"share": {**DEFAULT_CONFIG["share"], "enabled": False}}
    sender = ShareSender(
        connect=lambda: calls.append(True),
        config=config,
        queue_dir=tmp_path / "queue",
    )

    assert sender.run_once() == {"status": "disabled"}
    assert start_share_thread(lambda: calls.append(True), config=config) is None
    assert calls == []


def test_configure_wizard_is_explicit_opt_in_and_persists_token(tmp_path):
    path = tmp_path / "config.yaml"
    answers = iter(["yes", "new-community-token"])
    messages = []

    config = configure_share(
        path=path,
        input_fn=lambda prompt: (messages.append(prompt), next(answers))[1],
        output_fn=messages.append,
    )

    assert config["share"]["enabled"] is True
    assert config["share"]["token"] == "new-community-token"
    assert load_config(path=path, environ={})["share"]["enabled"] is True
    assert any("https://bizdnai.com/metrics/community" in message for message in messages)


def test_server_main_starts_opt_in_sender_before_stdio(monkeypatch):
    calls = []
    config = _config()
    monkeypatch.setattr(server_module, "validate_db_config", lambda: calls.append("validate"))
    monkeypatch.setattr(server_module, "load_config", lambda: config)
    monkeypatch.setattr(
        server_module,
        "start_share_thread",
        lambda connect, resolved: calls.append(("share", connect, resolved)),
    )

    def fake_asyncio_run(coroutine):
        calls.append("stdio")
        coroutine.close()

    monkeypatch.setattr(server_module.asyncio, "run", fake_asyncio_run)

    server_module.main()

    assert calls[0] == "validate"
    assert calls[1] == ("share", server_module._connect, config)
    assert calls[2] == "stdio"


def test_packaging_exposes_configure_cli_and_yaml_dependency():
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    project = project_file.read_text(encoding="utf-8")

    assert 'mcp-metrics = "metrics_mcp.setup:main"' in project
    assert '"PyYAML>=6.0"' in project
