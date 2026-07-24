#!/usr/bin/env python3
"""Benchmark Runner — параллельный запуск одной спецификации на нескольких моделях.

Phase 4 задачи #1176 (BizDNAi Metrics Platform).
Каждая модель запускается в собственном git-worktree (Docker не используем —
Yandex Cloud без быстрых snapshots, worktree проще и быстрее).

Использование:
    python3 benchmark_runner.py --spec refactor-auth \\
        --models claude-opus-4-8 claude-sonnet-5 claude-haiku-4-5 \\
        --task-command "make test"

Артефакты:
    /tmp/benchmarks/<bench_id>/<model>/  — git worktree
    results/<bench_id>.json             — агрегированный результат
    DB: benchmarks, task_runs, run_evaluations, run_artifacts
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
WORKTREE_BASE = Path("/tmp/benchmarks")
RESULTS_DIR = REPO_ROOT / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_BASE_BRANCH = "main"
DEFAULT_TASK_COMMAND = "python3 -m pytest tests/ -v --tb=short"
DEFAULT_PROJECT_ID = 1  # "bizdnai"
DEFAULT_PROJECT_NAME = "bizdnai"

DB_HOST = os.environ.get("METRICS_DB_HOST", "localhost")
DB_PORT = int(os.environ.get("METRICS_DB_PORT", "5434"))
DB_NAME = os.environ.get("METRICS_DB_NAME", "bizdnai")
DB_USER = os.environ.get("METRICS_DB_USER", "bizdnai")
DB_PASSWORD = os.environ.get("METRICS_DB_PASSWORD", "bizdnai")

LOGGER = logging.getLogger("benchmark_runner")
logging.basicConfig(
    level=os.environ.get("BENCHMARK_LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ModelRunResult:
    """Результат запуска одной модели в её worktree."""

    model: str
    worktree_path: str
    status: str  # success / failed / error
    exit_code: int
    duration_sec: float
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    total: int = 0
    stdout_tail: str = ""
    stderr_tail: str = ""
    diff_path: Optional[str] = None
    diff_size: int = 0
    started_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    benchmark_id: Optional[int] = None
    run_id: Optional[int] = None


@dataclass
class BenchmarkResult:
    """Агрегированный результат запуска benchmark (все модели)."""

    benchmark_id: str
    spec_id: str
    project_id: int
    task_command: str
    base_branch: str
    models: list[str]
    status: str
    started_at: str
    completed_at: str
    duration_sec: float
    runs: list[dict[str, Any]] = field(default_factory=list)
    db_benchmark_id: Optional[int] = None
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Git worktree helpers
# ---------------------------------------------------------------------------

def _run_git(args: list[str], cwd: Optional[Path] = None, timeout: int = 60) -> subprocess.CompletedProcess:
    """Запустить git с таймаутом, без shell, без интерактива."""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def detect_default_branch(repo: Path) -> str:
    """Вернуть текущую ветку (или main/master)."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo)
    branch = (result.stdout or "").strip()
    if branch and result.returncode == 0:
        return branch
    for candidate in ("main", "master"):
        if _run_git(["rev-parse", "--verify", candidate], cwd=repo).returncode == 0:
            return candidate
    return DEFAULT_BASE_BRANCH


def create_worktree(parent_dir: Path, model: str, base_branch: str, repo: Path,
                    branch_suffix: str = "") -> Path:
    """Создать git worktree для модели. Возвращает путь.

    branch_suffix добавляется к имени ветки, чтобы разные benchmark'и
    не конфликтовали по имени ветки (одна модель — разные прогоны).
    """
    safe_model = model.replace("/", "_").replace(":", "_")
    worktree_path = parent_dir / safe_model
    parent_dir.mkdir(parents=True, exist_ok=True)

    if worktree_path.exists():
        # Переиспользуем существующий worktree, если он валиден
        check = _run_git(["worktree", "list"], cwd=repo)
        if str(worktree_path) in (check.stdout or ""):
            LOGGER.info("Worktree %s already exists, reusing", worktree_path)
            return worktree_path
        # Невалидный — удаляем
        shutil.rmtree(worktree_path, ignore_errors=True)
        _run_git(["worktree", "prune"], cwd=repo)

    suffix = f"-{branch_suffix}" if branch_suffix else ""
    branch_name = f"bench/{safe_model}{suffix}"
    result = _run_git(
        ["worktree", "add", "-B", branch_name, str(worktree_path), base_branch],
        cwd=repo,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed for {model}: {result.stderr.strip()}"
        )
    LOGGER.info("Created worktree %s on branch %s", worktree_path, branch_name)
    return worktree_path


def cleanup_worktree(worktree_path: Path, repo: Path) -> None:
    """Удалить worktree и его ветку."""
    if not worktree_path.exists():
        return
    safe_model = worktree_path.name
    branch_name = f"bench/{safe_model}"
    _run_git(["worktree", "remove", "--force", str(worktree_path)], cwd=repo, timeout=60)
    _run_git(["branch", "-D", branch_name], cwd=repo, timeout=30)
    _run_git(["worktree", "prune"], cwd=repo)
    LOGGER.info("Cleaned up worktree %s", worktree_path)


def get_worktree_size(path: Path) -> int:
    """Примерный размер worktree (без .git, в байтах)."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            # Пропускаем .git и тяжёлые служебные каталоги
            if "/.git" in root or root.endswith("/.git"):
                continue
            for fname in files:
                try:
                    total += (Path(root) / fname).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


# ---------------------------------------------------------------------------
# Test execution
# ---------------------------------------------------------------------------

def _parse_pytest_summary(output: str) -> dict[str, int]:
    """Извлечь из вывода pytest сводку (passed/failed/errors/skipped)."""
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "total": 0}
    for line in output.splitlines():
        line = line.strip()
        # Пример: "5 passed, 2 failed, 1 error in 1.23s"
        if "passed" in line or "failed" in line or "error" in line:
            for key in ("passed", "failed", "error", "skipped"):
                token = f"{key}"
                # Используем явные слова, чтобы не считать "errors" внутри "error"
            import re
            for key in ("passed", "failed", "errors", "skipped"):
                m = re.search(rf"(\d+)\s+{key}", line)
                if m:
                    n = int(m.group(1))
                    if key == "errors":
                        summary["errors"] += n
                    else:
                        summary[key] += n
            # Последняя строка вида "===== 5 passed, 2 failed in 0.12s ====="
            if "in " in line and "passed" in line:
                m = re.search(r"=+\s*(.+?)\s+in\s+", line)
                if m:
                    for key in ("passed", "failed", "errors", "skipped"):
                        rm = re.search(rf"(\d+)\s+{key}", m.group(1))
                        if rm:
                            n = int(rm.group(1))
                            if key == "errors":
                                summary["errors"] = max(summary["errors"], n)
                            else:
                                summary[key] = max(summary[key], n)
    summary["total"] = summary["passed"] + summary["failed"] + summary["errors"] + summary["skipped"]
    return summary


def _truncate(text: str, max_lines: int = 50) -> str:
    """Оставить хвост из max_lines строк."""
    lines = (text or "").splitlines()
    if len(lines) <= max_lines:
        return text or ""
    return "\n".join(lines[-max_lines:])


def run_task(worktree_path: Path, task_command: str, timeout_sec: int = 1800) -> dict[str, Any]:
    """Запустить task_command в worktree, вернуть словарь с результатами."""
    started = datetime.now(timezone.utc)
    started_iso = started.isoformat()

    # shell=True нужно для команд типа "make test" с аргументами
    cmd = task_command
    proc = subprocess.run(
        cmd,
        cwd=str(worktree_path),
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
        check=False,
    )

    completed = datetime.now(timezone.utc)
    duration = (completed - started).total_seconds()
    summary = _parse_pytest_summary(proc.stdout + "\n" + proc.stderr)
    success = proc.returncode == 0 and summary["failed"] == 0 and summary["errors"] == 0

    return {
        "exit_code": proc.returncode,
        "duration_sec": round(duration, 3),
        "started_at": started_iso,
        "completed_at": completed.isoformat(),
        "passed": summary["passed"],
        "failed": summary["failed"],
        "errors": summary["errors"],
        "skipped": summary["skipped"],
        "total": summary["total"],
        "stdout_tail": _truncate(proc.stdout, 50),
        "stderr_tail": _truncate(proc.stderr, 50),
        "success": success,
    }


def capture_diff(worktree_path: Path, benchmark_id: str, model: str) -> Optional[str]:
    """Сохранить git diff (если есть изменения) как artifact."""
    result = _run_git(["diff", "HEAD"], cwd=worktree_path, timeout=60)
    if result.returncode != 0 or not (result.stdout or "").strip():
        return None
    diff_dir = RESULTS_DIR / "diffs" / benchmark_id
    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"{model}.patch"
    diff_path.write_text(result.stdout, encoding="utf-8")
    return str(diff_path)


# ---------------------------------------------------------------------------
# Async wrapper
# ---------------------------------------------------------------------------

async def run_one_model(
    model: str,
    parent_dir: Path,
    base_branch: str,
    task_command: str,
    repo: Path,
    timeout_sec: int,
    branch_suffix: str = "",
) -> ModelRunResult:
    """Запустить task_command в worktree для одной модели (async)."""
    loop = asyncio.get_running_loop()
    result = ModelRunResult(
        model=model,
        worktree_path="",
        status="pending",
        exit_code=-1,
        duration_sec=0.0,
    )

    try:
        worktree_path = await loop.run_in_executor(
            None, create_worktree, parent_dir, model, base_branch, repo, branch_suffix
        )
        result.worktree_path = str(worktree_path)

        task_result = await loop.run_in_executor(
            None, run_task, worktree_path, task_command, timeout_sec
        )
        result.exit_code = task_result["exit_code"]
        result.duration_sec = task_result["duration_sec"]
        result.passed = task_result["passed"]
        result.failed = task_result["failed"]
        result.errors = task_result["errors"]
        result.skipped = task_result["skipped"]
        result.total = task_result["total"]
        result.stdout_tail = task_result["stdout_tail"]
        result.stderr_tail = task_result["stderr_tail"]
        result.started_at = task_result["started_at"]
        result.completed_at = task_result["completed_at"]
        result.status = "success" if task_result["success"] else "failed"

        # diff — отдельный шаг, ошибки не валят весь run
        try:
            benchmark_id = parent_dir.name
            diff_path = await loop.run_in_executor(
                None, capture_diff, worktree_path, benchmark_id, model
            )
            result.diff_path = diff_path
            if diff_path:
                result.diff_size = Path(diff_path).stat().st_size
        except Exception as exc:  # pragma: no cover
            LOGGER.warning("diff capture failed for %s: %s", model, exc)

    except subprocess.TimeoutExpired as exc:
        result.status = "failed"
        result.error = f"timeout after {timeout_sec}s"
        result.stderr_tail = (exc.stderr or "")[-2000:] if hasattr(exc, "stderr") else ""
    except Exception as exc:
        LOGGER.exception("run_one_model failed for %s", model)
        result.status = "error"
        result.error = str(exc)

    return result


# ---------------------------------------------------------------------------
# DB integration
# ---------------------------------------------------------------------------

def _connect_db():
    """Открыть psycopg2-соединение, импортируем лениво."""
    import psycopg2
    from psycopg2.extras import RealDictCursor

    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursor_factory=RealDictCursor,
    )


def ensure_project(project_name: str) -> int:
    """Получить id проекта по имени (создать, если нет)."""
    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM projects WHERE name = %s", (project_name,))
        row = cur.fetchone()
        if row:
            return int(row["id"])
        cur.execute(
            "INSERT INTO projects (name, repository, default_branch) "
            "VALUES (%s, %s, %s) RETURNING id",
            (project_name, "mcp-metrics", "main"),
        )
        new_id = int(cur.fetchone()["id"])
        conn.commit()
        LOGGER.info("Created project %s id=%s", project_name, new_id)
        return new_id


def insert_benchmark(
    project_id: int,
    name: str,
    description: str,
    spec_id: str,
    status: str,
    baseline_configuration: dict[str, Any],
) -> int:
    """INSERT в benchmarks, вернуть id."""
    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO benchmarks (name, description, project_id, spec_id, status, baseline_configuration)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            RETURNING id
            """,
            (
                name,
                description,
                project_id,
                spec_id,
                status,
                json.dumps(baseline_configuration),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        return int(row["id"])


def update_benchmark_status(benchmark_id: int, status: str) -> None:
    """Обновить status бенчмарка."""
    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE benchmarks SET status = %s WHERE id = %s",
            (status, benchmark_id),
        )
        conn.commit()


def insert_task_run(
    benchmark_id: int,
    model: str,
    task_result: ModelRunResult,
    task_command: str,
) -> int:
    """INSERT в task_runs, вернуть id."""
    provider = "anthropic" if model.startswith("claude-") else None
    status = task_result.status
    # Нормализуем даты: пустые строки → NULL, иначе psycopg2 ругается на TIMESTAMPTZ
    started_at = task_result.started_at or None
    if not started_at:
        started_at = datetime.now(timezone.utc).isoformat()
    completed_at = task_result.completed_at or None
    duration_sec = int(task_result.duration_sec) if task_result.duration_sec else 0

    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO task_runs (
                benchmark_id, provider, model, started_at, completed_at,
                status, duration_sec, agent_name, agent_version
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                benchmark_id,
                provider,
                model,
                started_at,
                completed_at,
                status,
                duration_sec,
                "benchmark-runner",
                "0.1.0",
            ),
        )
        run_id = int(cur.fetchone()["id"])
        conn.commit()
        return run_id


def insert_run_evaluation(
    run_id: int,
    task_result: ModelRunResult,
) -> None:
    """INSERT в run_evaluations."""
    agent_completed = task_result.status in ("success", "failed")
    # tests_passed = True если pytest прошёл успешно (exit_code == 0 и нет failed)
    tests_passed = task_result.status == "success"
    build_passed = task_result.exit_code == 0
    # Эвристика: pass-ratio = passed / total (или 1.0 при пустых тестах)
    if task_result.total > 0:
        score = round(task_result.passed / task_result.total, 3)
    elif task_result.exit_code == 0:
        score = 1.0
    else:
        score = 0.0

    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO run_evaluations (
                run_id, agent_completed, build_passed, tests_passed,
                evaluation_score
            )
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                run_id,
                agent_completed,
                build_passed,
                tests_passed,
                score,
            ),
        )
        conn.commit()


def insert_run_artifact(run_id: int, artifact_type: str, path: str, size: int) -> None:
    """INSERT в run_artifacts."""
    abs_path = str(Path(path).resolve())
    with _connect_db() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO run_artifacts (run_id, artifact_type, path, size)
            VALUES (%s, %s, %s, %s)
            """,
            (run_id, artifact_type, abs_path, size),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_summary(runs: list[ModelRunResult]) -> dict[str, Any]:
    """Свести список результатов в компактную сводку."""
    if not runs:
        return {"total": 0, "success": 0, "failed": 0, "errors": 0}
    summary = {
        "total": len(runs),
        "success": sum(1 for r in runs if r.status == "success"),
        "failed": sum(1 for r in runs if r.status == "failed"),
        "errors": sum(1 for r in runs if r.status == "error"),
        "total_tests": sum(r.total for r in runs),
        "total_passed": sum(r.passed for r in runs),
        "total_failed": sum(r.failed for r in runs),
        "avg_duration_sec": round(
            sum(r.duration_sec for r in runs) / len(runs), 3
        ),
        "fastest_model": None,
        "slowest_model": None,
    }
    ranked = sorted(runs, key=lambda r: r.duration_sec)
    if ranked:
        summary["fastest_model"] = ranked[0].model
        summary["slowest_model"] = ranked[-1].model
    return summary


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

async def run_benchmark(
    spec_id: str,
    models: list[str],
    task_command: str = DEFAULT_TASK_COMMAND,
    project_name: str = DEFAULT_PROJECT_NAME,
    base_branch: Optional[str] = None,
    timeout_sec: int = 1800,
    cleanup: bool = False,
    save_to_db: bool = True,
) -> BenchmarkResult:
    """Главная оркестрация: worktree → run → diff → DB → JSON."""
    if not models:
        raise ValueError("models list is empty")

    benchmark_id = f"bench-{spec_id}-{uuid.uuid4().hex[:8]}"
    parent_dir = WORKTREE_BASE / benchmark_id
    parent_dir.mkdir(parents=True, exist_ok=True)

    repo = REPO_ROOT
    base_branch = base_branch or detect_default_branch(repo)
    LOGGER.info(
        "run_benchmark: spec=%s models=%s base=%s parent=%s",
        spec_id, models, base_branch, parent_dir,
    )

    started = datetime.now(timezone.utc)
    overall_status = "running"

    # 1. Создать запись benchmark
    project_id = DEFAULT_PROJECT_ID
    db_benchmark_id: Optional[int] = None
    if save_to_db:
        try:
            project_id = ensure_project(project_name)
            db_benchmark_id = insert_benchmark(
                project_id=project_id,
                name=benchmark_id,
                description=f"Benchmark for spec={spec_id} on {len(models)} models",
                spec_id=spec_id,
                status="running",
                baseline_configuration={
                    "task_command": task_command,
                    "base_branch": base_branch,
                    "models": models,
                    "project_id": project_id,
                },
            )
            LOGGER.info("Created benchmark id=%s", db_benchmark_id)
        except Exception as exc:
            LOGGER.exception("DB benchmark insert failed (continuing): %s", exc)

    # 2. Параллельный запуск
    runs = await asyncio.gather(
        *[
            run_one_model(
                model=m,
                parent_dir=parent_dir,
                base_branch=base_branch,
                task_command=task_command,
                repo=repo,
                timeout_sec=timeout_sec,
                branch_suffix=benchmark_id,
            )
            for m in models
        ],
        return_exceptions=False,
    )

    completed = datetime.now(timezone.utc)
    duration = (completed - started).total_seconds()

    # 3. Записать результаты в DB
    if save_to_db and db_benchmark_id is not None:
        for run in runs:
            try:
                run_id = insert_task_run(
                    benchmark_id=db_benchmark_id,
                    model=run.model,
                    task_result=run,
                    task_command=task_command,
                )
                run.run_id = run_id
                run.benchmark_id = db_benchmark_id
                insert_run_evaluation(run_id=run_id, task_result=run)
                if run.diff_path and Path(run.diff_path).exists():
                    insert_run_artifact(
                        run_id=run_id,
                        artifact_type="git_diff",
                        path=run.diff_path,
                        size=run.diff_size,
                    )
            except Exception as exc:
                LOGGER.exception("DB write failed for run %s: %s", run.model, exc)

        # 4. Обновить статус benchmark
        any_failed = any(r.status in ("failed", "error") for r in runs)
        overall_status = "failed" if any_failed else "completed"
        try:
            update_benchmark_status(db_benchmark_id, overall_status)
        except Exception as exc:  # pragma: no cover
            LOGGER.exception("benchmark status update failed: %s", exc)

    # 5. Cleanup worktrees (если попросили)
    if cleanup:
        for run in runs:
            try:
                wt = Path(run.worktree_path)
                if wt.exists():
                    cleanup_worktree(wt, repo)
                    run.worktree_path = ""
            except Exception as exc:  # pragma: no cover
                LOGGER.warning("cleanup failed for %s: %s", run.model, exc)

    # 6. Сериализация
    result = BenchmarkResult(
        benchmark_id=benchmark_id,
        spec_id=spec_id,
        project_id=project_id,
        task_command=task_command,
        base_branch=base_branch,
        models=list(models),
        status=overall_status,
        started_at=started.isoformat(),
        completed_at=completed.isoformat(),
        duration_sec=round(duration, 3),
        runs=[asdict(r) for r in runs],
        db_benchmark_id=db_benchmark_id,
        summary=aggregate_summary(runs),
    )

    # 7. Сохранить JSON
    json_path = RESULTS_DIR / f"{benchmark_id}.json"
    json_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    LOGGER.info("Saved %s", json_path)
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="benchmark_runner.py",
        description="Параллельный запуск одной спецификации на нескольких моделях в git worktree.",
    )
    parser.add_argument(
        "--spec", required=True,
        help="Уникальный id спецификации (например, refactor-auth).",
    )
    parser.add_argument(
        "--models", nargs="+", required=True,
        help="Список моделей (через пробел).",
    )
    parser.add_argument(
        "--task-command", default=DEFAULT_TASK_COMMAND,
        help=f"Команда для запуска в worktree (default: {DEFAULT_TASK_COMMAND}).",
    )
    parser.add_argument(
        "--project", default=DEFAULT_PROJECT_NAME,
        help="Имя проекта (default: bizdnai).",
    )
    parser.add_argument(
        "--base-branch", default=None,
        help="Базовая ветка для worktree (default: текущая).",
    )
    parser.add_argument(
        "--timeout", type=int, default=1800,
        help="Таймаут на запуск одной модели в секундах (default: 1800).",
    )
    parser.add_argument(
        "--cleanup", action="store_true",
        help="Удалить worktree после прогона.",
    )
    parser.add_argument(
        "--no-db", action="store_true",
        help="Не сохранять результаты в БД.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    LOGGER.info(
        "Starting benchmark: spec=%s models=%s command=%s",
        args.spec, args.models, args.task_command,
    )
    t0 = time.time()
    try:
        result = asyncio.run(
            run_benchmark(
                spec_id=args.spec,
                models=args.models,
                task_command=args.task_command,
                project_name=args.project,
                base_branch=args.base_branch,
                timeout_sec=args.timeout,
                cleanup=args.cleanup,
                save_to_db=not args.no_db,
            )
        )
    except Exception as exc:
        LOGGER.exception("Benchmark failed")
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    elapsed = time.time() - t0
    print(json.dumps({
        "benchmark_id": result.benchmark_id,
        "spec_id": result.spec_id,
        "status": result.status,
        "db_benchmark_id": result.db_benchmark_id,
        "duration_sec": result.duration_sec,
        "elapsed_wallclock_sec": round(elapsed, 3),
        "summary": result.summary,
        "runs": [
            {
                "model": r["model"],
                "status": r["status"],
                "exit_code": r["exit_code"],
                "passed": r["passed"],
                "failed": r["failed"],
                "errors": r["errors"],
                "total": r["total"],
                "duration_sec": r["duration_sec"],
                "run_id": r.get("run_id"),
                "diff_path": r.get("diff_path"),
            }
            for r in result.runs
        ],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
