#!/usr/bin/env python3
"""Push PostgreSQL metric events to dashboard WebSocket clients."""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import psycopg2
from aiohttp import WSMsgType, web
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

HOST = "127.0.0.1"
PORT = 8765
CHANNEL = "bizdnai_metrics_events"
DB_DSN = "host=localhost port=5434 dbname=bizdnai user=bizdnai password=bizdnai"
RECONNECT_DELAY_SECONDS = 2

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger("bizdnai-live")


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect_postgres():
    connection = psycopg2.connect(DB_DSN)
    connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    with connection.cursor() as cursor:
        cursor.execute(f"LISTEN {CHANNEL};")
    return connection


async def broadcast(app: web.Application, raw_payload: str) -> None:
    try:
        payload: dict[str, Any] = json.loads(raw_payload)
    except (TypeError, json.JSONDecodeError):
        LOGGER.warning("Ignoring malformed NOTIFY payload: %r", raw_payload)
        return

    payload.setdefault("timestamp", utc_timestamp())
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    active_clients = [client for client in app["clients"] if not client.closed]
    if not active_clients:
        return

    results = await asyncio.gather(
        *(client.send_str(serialized) for client in active_clients),
        return_exceptions=True,
    )
    for client, result in zip(active_clients, results, strict=False):
        if isinstance(result, Exception):
            LOGGER.warning("WebSocket send failed: %s", result)
            app["clients"].discard(client)


async def listen_for_notifications(app: web.Application) -> None:
    loop = asyncio.get_running_loop()
    while True:
        connection = None
        readable = asyncio.Event()
        try:
            connection = await asyncio.to_thread(connect_postgres)
            loop.add_reader(connection.fileno(), readable.set)
            LOGGER.info("Listening for PostgreSQL NOTIFY on %s", CHANNEL)

            while True:
                await readable.wait()
                readable.clear()
                connection.poll()
                while connection.notifies:
                    notification = connection.notifies.pop(0)
                    await broadcast(app, notification.payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            LOGGER.exception(
                "PostgreSQL listener failed; reconnecting in %ss",
                RECONNECT_DELAY_SECONDS,
            )
            await asyncio.sleep(RECONNECT_DELAY_SECONDS)
        finally:
            if connection is not None:
                with suppress(Exception):
                    loop.remove_reader(connection.fileno())
                with suppress(Exception):
                    connection.close()


async def websocket_handler(request: web.Request) -> web.WebSocketResponse:
    websocket = web.WebSocketResponse(heartbeat=30)
    await websocket.prepare(request)
    request.app["clients"].add(websocket)
    LOGGER.info("WebSocket connected; clients=%d", len(request.app["clients"]))

    try:
        async for message in websocket:
            if message.type == WSMsgType.TEXT and message.data == "ping":
                await websocket.send_str('{"event_type":"pong"}')
            elif message.type == WSMsgType.ERROR:
                LOGGER.warning("WebSocket error: %s", websocket.exception())
    finally:
        request.app["clients"].discard(websocket)
        LOGGER.info("WebSocket disconnected; clients=%d", len(request.app["clients"]))

    return websocket


async def health_handler(request: web.Request) -> web.Response:
    return web.json_response(
        {"status": "ok", "clients": len(request.app["clients"]), "timestamp": utc_timestamp()}
    )


async def start_listener(app: web.Application) -> None:
    app["listener_task"] = asyncio.create_task(listen_for_notifications(app))


async def stop_listener(app: web.Application) -> None:
    task = app.get("listener_task")
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
    for client in list(app["clients"]):
        await client.close(code=1001, message=b"server shutdown")


def create_app() -> web.Application:
    app = web.Application()
    app["clients"] = set()
    app.router.add_get("/", websocket_handler)
    app.router.add_get("/ws", websocket_handler)
    app.router.add_get("/health", health_handler)
    app.on_startup.append(start_listener)
    app.on_cleanup.append(stop_listener)
    return app


if __name__ == "__main__":
    LOGGER.info("Starting BizDNAi live analytics on ws://%s:%d", HOST, PORT)
    web.run_app(create_app(), host=HOST, port=PORT, access_log=LOGGER)
