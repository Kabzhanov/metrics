"""Opt-in background sender for anonymized federated metrics."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from .config import load_config

logger = logging.getLogger("mcp-metrics.share")

# Keep synchronized with /home/bizdnai/mcp-events/task-start.sh.
ALLOWED_MODEL_PREFIXES = (
    "claude-",
    "gpt-",
    "chatgpt-",
    "MiniMax-",
    "gemini-",
    "qwen-",
    "deepseek-",
    "codestral-",
    "llama-",
    "mistral-",
    "coder-",
    "code-",
)

DEFAULT_QUEUE_DIR = Path.home() / ".local" / "share" / "mcp-metrics" / "queue"

# A row-level query is intentional: opaque_id and the central F1 payload are
# per-run. PostgreSQL's to_jsonb lookup remains compatible with legacy
# metrics_tasks tables that predate the optional task_type column, without ever
# substituting the private project identifier.
FETCH_EVENTS_QUERY = """
SELECT
    mt.task_id,
    mt.model,
    COALESCE(NULLIF(to_jsonb(mt)->>'task_type', ''), 'unknown') AS task_type,
    mt.started_at,
    mt.duration_sec,
    mt.cost_usd,
    mt.status,
    COALESCE(mt.files_created, 0)
      + COALESCE(mt.files_modified, 0)
      + COALESCE(mt.files_deleted, 0) AS files_changed_count
FROM metrics_tasks AS mt
WHERE mt.started_at > now() - (%s * interval '1 hour')
  AND mt.status <> 'in_progress'
ORDER BY mt.started_at, mt.task_id
"""

HttpPost = Callable[[str, Mapping[str, Any], str, float], None]
Connect = Callable[[], Any]
Now = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _started_at_day(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if len(text) < 10:
        raise ValueError("started_at is missing or invalid")
    return text[:10]


def is_allowed_model(model: Any) -> bool:
    """Return whether a model uses a trusted producer prefix."""
    return isinstance(model, str) and any(
        model.startswith(prefix) for prefix in ALLOWED_MODEL_PREFIXES
    )


def prepare_events(
    rows: Iterable[Mapping[str, Any]],
    token: str,
    include_files_changed_count: bool = False,
) -> list[dict[str, Any]]:
    """Convert local rows to the strict central payload without private fields."""
    events: list[dict[str, Any]] = []
    dropped: Counter[str] = Counter()
    for row in rows:
        model = row.get("model")
        if not is_allowed_model(model):
            dropped[str(model)] += 1
            continue

        status = str(row.get("status") or "").strip().lower()
        if status == "human_stop":
            status = "interrupted"
        if status not in {"success", "failed", "interrupted"}:
            logger.warning("dropped event with unsupported status %s", status or "<empty>")
            continue

        raw_task_id = row.get("task_id")
        if raw_task_id is None:
            logger.warning("dropped event without task_id for model %s", model)
            continue
        opaque_id = hashlib.sha256(
            (token + str(raw_task_id)).encode("utf-8")
        ).hexdigest()[:16]

        event: dict[str, Any] = {
            "opaque_id": opaque_id,
            "model": model,
            "task_type": str(row.get("task_type") or "unknown"),
            "started_at_day": _started_at_day(row.get("started_at")),
            "duration_sec": _number(row.get("duration_sec")),
            "cost_usd": _number(row.get("cost_usd")),
            "status": status,
        }
        if include_files_changed_count:
            event["files_changed_count"] = int(row.get("files_changed_count") or 0)
        events.append(event)

    for model, count in sorted(dropped.items()):
        logger.warning("dropped %d events with unknown model %s", count, model)
    return events


def sample_events(
    events: Sequence[dict[str, Any]],
    rng: random.Random | Any = random,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Send all small batches and a 1% sample when a batch exceeds 100."""
    copied = list(events)
    if len(copied) <= 100:
        return copied, []
    sample_size = max(1, len(copied) // 100)
    selected_indices = set(rng.sample(range(len(copied)), sample_size))
    selected = [event for index, event in enumerate(copied) if index in selected_indices]
    deferred = [event for index, event in enumerate(copied) if index not in selected_indices]
    return selected, deferred


def build_batch(
    events: Sequence[dict[str, Any]],
    token: str,
    submitted_at: datetime | None = None,
) -> dict[str, Any]:
    """Create the whitelisted central API envelope."""
    timestamp = submitted_at or _utc_now()
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return {
        "batch_id": str(uuid.uuid4()),
        "submitter_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "submitted_at": timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "events": list(events),
    }


def _post_json(endpoint: str, payload: Mapping[str, Any], token: str, timeout: float) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "mcp-metrics-federation/1",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise OSError(f"central endpoint returned HTTP {response.status}")


@dataclass(frozen=True)
class QueueItem:
    path: Path
    events: list[dict[str, Any]]
    attempts: int
    retry_at: datetime


class QueueStore:
    """Durable JSON queue that never stores the bearer token or raw task IDs."""

    def __init__(self, directory: str | Path = DEFAULT_QUEUE_DIR):
        self.directory = Path(directory).expanduser()

    def enqueue(
        self,
        events: Sequence[dict[str, Any]],
        *,
        attempts: int,
        retry_at: datetime,
    ) -> Path | None:
        if not events:
            return None
        self.directory.mkdir(parents=True, exist_ok=True)
        filename = f"{int(retry_at.timestamp())}-{uuid.uuid4().hex}.json"
        target = self.directory / filename
        temporary = target.with_suffix(".tmp")
        payload = {
            "attempts": attempts,
            "retry_at": retry_at.astimezone(timezone.utc).isoformat(),
            "events": list(events),
        }
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(target)
        return target

    def items(self) -> list[QueueItem]:
        if not self.directory.exists():
            return []
        items: list[QueueItem] = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                retry_at = datetime.fromisoformat(payload["retry_at"])
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                events = payload["events"]
                if not isinstance(events, list):
                    raise TypeError("events is not a list")
                items.append(
                    QueueItem(path, events, int(payload.get("attempts", 0)), retry_at)
                )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                logger.exception("invalid share queue file retained: %s", path)
        return items

    def ready(self, now: datetime, *, ignore_retry_time: bool = False) -> list[QueueItem]:
        return [
            item
            for item in self.items()
            if ignore_retry_time or item.retry_at <= now
        ]

    @staticmethod
    def remove(items: Sequence[QueueItem]) -> None:
        for item in items:
            try:
                item.path.unlink(missing_ok=True)
            except OSError:
                logger.exception("could not remove sent queue file %s", item.path)


class ShareSender:
    """Fetch, sanitize, sample, queue, and submit one federation batch."""

    def __init__(
        self,
        connect: Connect,
        config: Mapping[str, Any],
        queue_dir: str | Path = DEFAULT_QUEUE_DIR,
        http_post: HttpPost = _post_json,
        rng: random.Random | Any = random,
        now: Now = _utc_now,
    ):
        self.connect = connect
        self.config = config
        self.share_config = config.get("share", {})
        self.queue = QueueStore(queue_dir)
        self.http_post = http_post
        self.rng = rng
        self.now = now

    def _fetch_rows(self) -> list[Mapping[str, Any]]:
        interval_hours = self.share_config.get("interval_hours", 1)
        with self.connect() as connection, connection.cursor() as cursor:
            cursor.execute(FETCH_EVENTS_QUERY, (interval_hours,))
            return list(cursor.fetchall())

    @staticmethod
    def _deduplicate(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        unique: dict[str, dict[str, Any]] = {}
        for event in events:
            opaque_id = event.get("opaque_id")
            if opaque_id:
                if opaque_id in unique:
                    # Hash collision or accidental reuse — log so it's detectable
                    logger.warning(
                        "opaque_id collision: dropping duplicate %s (64-bit prefix space)",
                        opaque_id,
                    )
                    continue
                unique[str(opaque_id)] = event
        return list(unique.values())

    def run_once(self, *, ignore_retry_time: bool = False) -> dict[str, Any]:
        if not self.share_config.get("enabled", False):
            return {"status": "disabled"}
        token = str(self.share_config.get("token") or "").strip()
        if not token:
            logger.error("share is enabled but no token is configured")
            return {"status": "misconfigured"}

        now = self.now()
        all_queue_items = self.queue.items()
        queued_items = [
            item
            for item in all_queue_items
            if ignore_retry_time or item.retry_at <= now
        ]
        # HIGH #1 fix: identify pending items by path, not dataclass equality
        # (retry_at difference shouldn't make us re-send events already queued for retry)
        queued_paths = {item.path for item in queued_items}
        pending_items = [item for item in all_queue_items if item.path not in queued_paths]
        queued_events = [event for item in queued_items for event in item.events]
        pending_ids = {
            str(event.get("opaque_id"))
            for item in pending_items
            for event in item.events
            if event.get("opaque_id")
        }
        try:
            rows = self._fetch_rows()
            fresh_events = prepare_events(
                rows,
                token,
                bool(self.share_config.get("include_files_changed_count", False)),
            )
            fresh_events = [
                event
                for event in fresh_events
                if str(event.get("opaque_id")) not in pending_ids
            ]
        except Exception as error:  # noqa: BLE001 - database adapter boundary
            logger.warning("could not collect local share events: %s", error)
            return {"status": "failed", "retry_in_seconds": 60, "queued": len(queued_events)}

        # HIGH #2 fix: separate already-sampled events (in queue with sampled=True) from new ones
        # so the 1% sample invariant holds across cycles (don't re-sample the same opaque_id).
        already_sampled: list[dict[str, Any]] = []
        pending_sample_pool: list[dict[str, Any]] = []
        for event in queued_events:
            (already_sampled if event.get("_sampled") else pending_sample_pool).append(event)
        pending_sample_pool.extend(fresh_events)
        events = self._deduplicate([*already_sampled, *pending_sample_pool])
        if not events:
            return {"status": "idle", "events": 0}

        new_sample_pool = [e for e in events if not e.get("_sampled")]
        selected_new, deferred_new = sample_events(new_sample_pool, self.rng)
        # Mark new_selected as sampled so it won't be re-sampled on next cycle
        for event in selected_new:
            event["_sampled"] = True
        selected = [e for e in events if e.get("_sampled")] + selected_new
        if deferred_new:
            self.queue.enqueue(deferred_new, attempts=0, retry_at=now)

        payload = build_batch(selected, token, now)
        attempts = max((item.attempts for item in queued_items), default=0) + 1
        try:
            self.http_post(
                str(self.share_config["endpoint"]),
                payload,
                token,
                15.0,
            )
        except Exception as error:  # noqa: BLE001 - injectable HTTP adapter boundary
            retry_seconds = min(3600, 60 * (2 ** (attempts - 1)))
            self.queue.enqueue(
                selected,
                attempts=attempts,
                retry_at=now + timedelta(seconds=retry_seconds),
            )
            self.queue.remove(queued_items)
            logger.warning("central share failed; retry in %ss: %s", retry_seconds, error)
            return {
                "status": "failed",
                "events": len(selected),
                "deferred": len(deferred_new),
                "retry_in_seconds": retry_seconds,
            }

        self.queue.remove(queued_items)
        logger.info("shared %d anonymous metrics events", len(selected))
        return {"status": "sent", "events": len(selected), "deferred": len(deferred_new)}

    def run_forever(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        interval_seconds = float(self.share_config.get("interval_hours", 1)) * 3600
        while not stop.is_set():
            result = self.run_once()
            delay = float(result.get("retry_in_seconds", interval_seconds))
            stop.wait(delay)


_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def start_share_thread(
    connect: Connect,
    config: Mapping[str, Any] | None = None,
) -> threading.Thread | None:
    """Start one daemon sender only when the user has explicitly opted in."""
    global _worker_thread
    resolved = load_config() if config is None else config
    share = resolved.get("share", {})
    if not share.get("enabled", False):
        return None
    if not str(share.get("token") or "").strip():
        logger.error("share is enabled but no token is configured; sender not started")
        return None

    with _worker_lock:
        if _worker_thread is not None and _worker_thread.is_alive():
            return _worker_thread
        sender = ShareSender(connect=connect, config=resolved)
        _worker_thread = threading.Thread(
            target=sender.run_forever,
            name="mcp-metrics-share",
            daemon=True,
        )
        _worker_thread.start()
        return _worker_thread


__all__ = [
    "ALLOWED_MODEL_PREFIXES",
    "DEFAULT_QUEUE_DIR",
    "FETCH_EVENTS_QUERY",
    "QueueStore",
    "ShareSender",
    "build_batch",
    "is_allowed_model",
    "prepare_events",
    "sample_events",
    "start_share_thread",
]
