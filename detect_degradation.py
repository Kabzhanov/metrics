#!/usr/bin/env python3
"""Детектор деградации AI-моделей (задача #1163).

Сравнивает MQI (Model Quality Index) за сегодня vs вчера, при падении >threshold
шлёт алерт (email Рашиду через `/home/bizdnai/send_email.py`).

MVP-формула MQI:
    MQI = 100 * success_rate
    success_rate = COUNT(where status='success') / COUNT(*)
Phase 2 добавит cost/latency/quality-score.

CLI:
    detect_degradation.py [--dry-run] [--period 1d] [--threshold 0.10]

Возвращает JSON в stdout:
    {"status": "ok"|"degradation"|"error", "mqi_today": ..., "mqi_yesterday": ...}

Логи: ~/.mcp-metrics/detect.log
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

# --- Configuration ---------------------------------------------------------
DB_HOST = os.environ.get("METRICS_DB_HOST")
DB_PORT = int(os.environ.get("METRICS_DB_PORT", "5432"))
DB_NAME = os.environ.get("METRICS_DB_NAME", "bizdnai")
DB_USER = os.environ.get("METRICS_DB_USER")
DB_PASS = os.environ.get("METRICS_DB_PASSWORD")

LOG_DIR = Path.home() / ".mcp-metrics"
LOG_FILE = LOG_DIR / "detect.log"

# Override these for deployments that use a different mail helper.
SEND_EMAIL = os.environ.get("DEGRADATION_SEND_EMAIL", "send_email.py")
ALERT_TO = os.environ.get("DEGRADATION_ALERT_TO")

PERIOD_SECONDS = {
    "1d": 86400,
    "7d": 86400 * 7,
    "30d": 86400 * 30,
}


def validate_db_config() -> None:
    """Fail fast when a required database setting is absent."""
    for name, value in (
        ("METRICS_DB_HOST", DB_HOST),
        ("METRICS_DB_USER", DB_USER),
        ("METRICS_DB_PASSWORD", DB_PASS),
    ):
        if value is None or not str(value).strip():
            raise RuntimeError(f"{name} is not set")


# --- Logging ---------------------------------------------------------------
def setup_logging() -> logging.Logger:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("detect_degradation")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
        fh.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        ))
        logger.addHandler(fh)
    return logger


# --- Database --------------------------------------------------------------
def connect_db():
    validate_db_config()
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASS, connect_timeout=5,
    )


def compute_mqi(cur, period_seconds: int) -> dict:
    """MQI за последние `period_seconds` секунд (агрегировано по всем моделям)."""
    cur.execute(
        """
        SELECT
            COUNT(*)                                            AS total,
            COUNT(*) FILTER (WHERE status = 'success')          AS ok
        FROM metrics_tasks
        WHERE started_at >= now() - (%s || ' seconds')::interval
        """,
        (str(period_seconds),),
    )
    row = cur.fetchone()
    total, ok = (row or (0, 0))
    total = int(total or 0)
    ok = int(ok or 0)
    success_rate = (ok / total) if total > 0 else None
    mqi = round(100.0 * success_rate, 2) if success_rate is not None else None
    return {"total": total, "ok": ok, "success_rate": success_rate, "mqi": mqi}


def mqi_breakdown(cur, period_seconds: int) -> list[dict]:
    """MQI в разрезе моделей — для алерта."""
    cur.execute(
        """
        SELECT
            model,
            COUNT(*)                                            AS total,
            COUNT(*) FILTER (WHERE status = 'success')          AS ok
        FROM metrics_tasks
        WHERE started_at >= now() - (%s || ' seconds')::interval
        GROUP BY model
        ORDER BY model
        """,
        (str(period_seconds),),
    )
    out = []
    for model, total, ok in cur.fetchall():
        total = int(total or 0)
        ok = int(ok or 0)
        rate = (ok / total) if total > 0 else None
        out.append({
            "model": model,
            "total": total,
            "ok": ok,
            "mqi": round(100.0 * rate, 2) if rate is not None else None,
        })
    return out


# --- Алерты --------------------------------------------------------------
def build_alert_body(mqi_today: dict, mqi_yesterday: dict,
                     threshold: float, period_label: str,
                     breakdown_today: list[dict], breakdown_yesterday: list[dict]) -> str:
    delta_abs = (mqi_today["mqi"] or 0) - (mqi_yesterday["mqi"] or 0)
    delta_pct = (
        (mqi_today["mqi"] - mqi_yesterday["mqi"]) / mqi_yesterday["mqi"] * 100
        if mqi_yesterday["mqi"] not in (None, 0) else None
    )
    lines = [
        "⚠️ ДЕТЕКТОР ДЕГРАДАЦИИ МОДЕЛЕЙ (задача #1163)",
        "",
        f"Период сравнения: {period_label}",
        f"Порог: {threshold * 100:.0f}% падения день-к-дню",
        f"Время проверки: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"MQI сегодня:     {mqi_today['mqi']}  ({mqi_today['ok']}/{mqi_today['total']} success)",
        f"MQI вчера:       {mqi_yesterday['mqi']}  ({mqi_yesterday['ok']}/{mqi_yesterday['total']} success)",
        f"Δ абс:           {delta_abs:+.2f}",
        f"Δ %:             {delta_pct:+.2f}%" if delta_pct is not None else "Δ %:             n/a",
        "",
        "Разрез по моделям (сегодня):",
    ]
    for row in breakdown_today:
        lines.append(f"  {row['model']:<28} MQI={row['mqi']}  ({row['ok']}/{row['total']})")
    if breakdown_yesterday:
        lines += ["", "Разрез по моделям (вчера):"]
        for row in breakdown_yesterday:
            lines.append(f"  {row['model']:<28} MQI={row['mqi']}  ({row['ok']}/{row['total']})")
    lines += [
        "",
        "What to do: inspect failed runs in the metrics database or dashboard.",
        "Это автоматическое уведомление, отвечать на него не нужно.",
    ]
    return "\n".join(lines)


def send_alert(subject: str, body: str, logger: logging.Logger) -> bool:
    """Send an email through the configured helper without shell interpolation."""
    if not ALERT_TO:
        logger.error("DEGRADATION_ALERT_TO is not set; alert was not sent")
        return False
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
            f.write(body)
            tmp_path = f.name
        env = os.environ.copy()
        env["TO"] = ALERT_TO
        result = subprocess.run(
            ["python3", SEND_EMAIL, subject, f"@{tmp_path}"],
            capture_output=True, text=True, env=env, timeout=30, check=False,
        )
        if result.returncode != 0:
            logger.error("send_email failed: rc=%s stderr=%s", result.returncode, result.stderr)
            return False
        logger.info("Alert sent to configured recipient: %s", result.stdout.strip())
        return True
    except Exception:
        logger.exception("send_alert crashed")
        return False
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# --- Main ----------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Detect AI model degradation (task #1163)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Только посчитать MQI, не слать алерт")
    parser.add_argument("--period", default="1d", choices=list(PERIOD_SECONDS.keys()),
                        help="Период для агрегации (по умолчанию 1d)")
    parser.add_argument("--threshold", type=float, default=0.10,
                        help="Порог деградации (по умолчанию 0.10 = 10%%)")
    args = parser.parse_args()

    logger = setup_logging()
    logger.info(f"start dry_run={args.dry_run} period={args.period} threshold={args.threshold}")

    period_seconds = PERIOD_SECONDS[args.period]
    result: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "period": args.period,
        "threshold": args.threshold,
        "dry_run": args.dry_run,
    }

    try:
        with connect_db() as conn, conn.cursor() as cur:
            mqi_today = compute_mqi(cur, period_seconds)
            mqi_yesterday = compute_mqi(cur, period_seconds * 2)
            # `compute_mqi` берёт последние period_seconds; для "вчера" повторно
            # посчитаем за окно (period_seconds..period_seconds*2).
            cur.execute(
                """
                SELECT
                    COUNT(*)                                            AS total,
                    COUNT(*) FILTER (WHERE status = 'success')          AS ok
                FROM metrics_tasks
                WHERE started_at >= now() - (%s || ' seconds')::interval
                  AND started_at <  now() - (%s || ' seconds')::interval
                """,
                (str(period_seconds * 2), str(period_seconds)),
            )
            row = cur.fetchone()
            total_y, ok_y = (int(row[0] or 0), int(row[1] or 0))
            rate_y = (ok_y / total_y) if total_y > 0 else None
            mqi_yesterday = {
                "total": total_y, "ok": ok_y,
                "success_rate": rate_y,
                "mqi": round(100.0 * rate_y, 2) if rate_y is not None else None,
            }
            breakdown_today = mqi_breakdown(cur, period_seconds)
            breakdown_yesterday = mqi_breakdown_for_yesterday(cur, period_seconds)
    except psycopg2.OperationalError as e:
        logger.error(f"DB unavailable: {e}")
        result.update({"status": "error", "reason": "db_unavailable", "detail": str(e)})
        print(json.dumps(result, ensure_ascii=False))
        return 0  # не падать — cron молча запишет в лог
    except Exception as error:
        logger.exception("Unexpected error")
        result.update({"status": "error", "reason": "unexpected", "detail": str(error)})
        print(json.dumps(result, ensure_ascii=False))
        return 1

    result["mqi_today"] = mqi_today
    result["mqi_yesterday"] = mqi_yesterday

    # --- Решение ---
    if mqi_today["mqi"] is None or mqi_yesterday["mqi"] is None or mqi_yesterday["mqi"] == 0:
        result["status"] = "ok"
        result["reason"] = "no_data"
        logger.info(f"no comparable data: today={mqi_today} yesterday={mqi_yesterday}")
        print(json.dumps(result, ensure_ascii=False))
        return 0

    drop_ratio = (mqi_yesterday["mqi"] - mqi_today["mqi"]) / mqi_yesterday["mqi"]
    result["drop_ratio"] = round(drop_ratio, 4)
    if drop_ratio >= args.threshold:
        result["status"] = "degradation"
        logger.warning(
            f"DEGRADATION: {mqi_yesterday['mqi']} -> {mqi_today['mqi']} "
            f"(drop {drop_ratio * 100:.1f}% >= threshold {args.threshold * 100:.0f}%)"
        )
        if args.dry_run:
            logger.info("dry-run: alert not sent")
            result["alert_sent"] = False
        else:
            subject = (
                f"[BizDNAi] Деградация модели: "
                f"MQI {mqi_yesterday['mqi']} -> {mqi_today['mqi']} "
                f"({drop_ratio * 100:.1f}%)"
            )
            body = build_alert_body(
                mqi_today, mqi_yesterday, args.threshold, args.period,
                breakdown_today, breakdown_yesterday,
            )
            sent = send_alert(subject, body, logger)
            result["alert_sent"] = sent
    else:
        result["status"] = "ok"
        logger.info(f"OK: {mqi_yesterday['mqi']} -> {mqi_today['mqi']} (drop {drop_ratio * 100:.1f}%)")

    print(json.dumps(result, ensure_ascii=False))
    return 0


def mqi_breakdown_for_yesterday(cur, period_seconds: int) -> list[dict]:
    cur.execute(
        """
        SELECT
            model,
            COUNT(*)                                            AS total,
            COUNT(*) FILTER (WHERE status = 'success')          AS ok
        FROM metrics_tasks
        WHERE started_at >= now() - (%s || ' seconds')::interval
          AND started_at <  now() - (%s || ' seconds')::interval
        GROUP BY model
        ORDER BY model
        """,
        (str(period_seconds * 2), str(period_seconds)),
    )
    out = []
    for model, total, ok in cur.fetchall():
        total = int(total or 0)
        ok = int(ok or 0)
        rate = (ok / total) if total > 0 else None
        out.append({
            "model": model,
            "total": total,
            "ok": ok,
            "mqi": round(100.0 * rate, 2) if rate is not None else None,
        })
    return out


if __name__ == "__main__":
    sys.exit(main())
