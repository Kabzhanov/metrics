#!/usr/bin/env python3
"""Git integration for BizDNAI Metrics (Phase 5.5, задача #1176).

Связывает `task_run` (из Phase 1) с git diff → позволяет считать
- rework_lines_ratio (строки, переписанные позже)
- CSI (Code Stability Index)
- doc_sync_rate (синхронность правок кода и документации)

Использование:
    from git_integration import GitProject
    proj = GitProject('/home/bizdnai/planet/', project_id=5)
    diff = proj.get_diff('HEAD~5', 'HEAD')
    files = proj.get_files_changed('HEAD~5', 'HEAD')

Кэш: ~/.cache/git_integration/{project_id}/{start_sha}__{end_sha}.json
Бинарные файлы (.png/.pdf/.jpg/.gif/.zip/.tar/.gz/.bin/.lock) пропускаются.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Файлы, которые НЕ интересуют (бинарь, lock-файлы, сгенерированные артефакты)
BINARY_EXT_RE = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|bmp|ico|pdf|zip|tar|tgz|gz|bz2|xz|7z|"
    r"bin|exe|so|dylib|dll|class|jar|war|ear|lock|pyc|woff2?|ttf|eot|"
    r"mp[34]|mov|avi|mkv|flac|ogg|wav|psd|ai|sketch|fig)$",
    re.IGNORECASE,
)

CACHE_ROOT = Path.home() / ".cache" / "git_integration"


def _is_binary(path: str) -> bool:
    """Возвращает True для бинарных/неинтересных файлов."""
    return bool(BINARY_EXT_RE.search(path))


class GitProject:
    """Обёртка над git для одного проекта."""

    def __init__(self, repo_path: str, project_id: int | str):
        self.repo_path = Path(repo_path).resolve()
        self.project_id = int(project_id)
        if not (self.repo_path / ".git").exists():
            raise FileNotFoundError(f"Не git-репозиторий: {self.repo_path}")
        self.cache_dir = CACHE_ROOT / str(self.project_id)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # низкоуровневый вызов git
    # ------------------------------------------------------------------
    def _run(self, *args: str, check: bool = True) -> str:
        """Запускает git и возвращает stdout. stderr идёт в лог."""
        cmd = ["git", "-C", str(self.repo_path), *args]
        try:
            out = subprocess.run(
                cmd,
                check=check,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return out.stdout
        except subprocess.CalledProcessError as e:
            sys.stderr.write(f"git error ({' '.join(cmd)}): {e.stderr}\n")
            if check:
                raise
            return ""

    # ------------------------------------------------------------------
    # публичный API
    # ------------------------------------------------------------------
    def current_head(self) -> str:
        """Возвращает полный SHA текущего HEAD."""
        return self._run("rev-parse", "HEAD").strip()

    def get_diff(
        self,
        start_sha: str,
        end_sha: str,
        max_files: Optional[int] = None,
        skip_binary: bool = True,
    ) -> Dict[str, Tuple[int, int]]:
        """Возвращает {file: (additions, deletions)}.

        `git diff --numstat start..end` — `-` `-` у бинарных файлов
        парсится как (0, 0) и при skip_binary=True такие файлы пропускаются.
        """
        cache_key = self._cache_key("diff", start_sha, end_sha, max_files, skip_binary)
        cached = self._cache_read(cache_key)
        if cached is not None:
            return cached

        out = self._run("diff", "--numstat", f"{start_sha}..{end_sha}")
        result: Dict[str, Tuple[int, int]] = {}
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            adds_raw, dels_raw, path = parts
            if skip_binary and _is_binary(path):
                continue
            if max_files is not None and len(result) >= max_files:
                break
            try:
                adds = int(adds_raw) if adds_raw != "-" else 0
                dels = int(dels_raw) if dels_raw != "-" else 0
            except ValueError:
                continue
            result[path] = (adds, dels)

        self._cache_write(cache_key, result)
        return result

    def get_files_changed(self, start_sha: str, end_sha: str) -> List[str]:
        """Список файлов, изменённых между start..end (без бинарных)."""
        cache_key = self._cache_key("files", start_sha, end_sha)
        cached = self._cache_read(cache_key)
        if cached is not None:
            return cached

        out = self._run("diff", "--name-only", f"{start_sha}..{end_sha}")
        files = [line for line in out.splitlines() if line and not _is_binary(line)]
        self._cache_write(cache_key, files)
        return files

    def changed_since(self, start_sha: str) -> List[str]:
        """Файлы, изменённые начиная с start_sha до HEAD."""
        return self.get_files_changed(start_sha, "HEAD")

    def file_last_commit(self, path: str) -> Optional[str]:
        """SHA последнего коммита, трогавшего файл. None если не найден."""
        try:
            out = self._run("log", "-1", "--format=%H", "--", path, check=False)
            return out.strip() or None
        except Exception:
            return None

    def file_last_commit_date(self, path: str) -> Optional[str]:
        """ISO дата последнего коммита файла."""
        try:
            out = self._run(
                "log", "-1", "--format=%cI", "--", path, check=False
            )
            return out.strip() or None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # кэш
    # ------------------------------------------------------------------
    def _cache_key(self, kind: str, start_sha: str, end_sha: str, *extra) -> str:
        items = [kind, start_sha, end_sha, *map(str, extra)]
        safe = "__".join(s.replace("/", "_").replace("..", "_") for s in items)
        return safe

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _cache_read(self, key: str):
        p = self._cache_path(key)
        if not p.exists():
            return None
        # кэш живёт 1 час — потом пересчитываем
        age = p.stat().st_mtime
        if (time.time() - age) > 3600:
            return None
        try:
            return json.loads(p.read_text())
        except Exception:
            return None

    def _cache_write(self, key: str, data) -> None:
        p = self._cache_path(key)
        try:
            p.write_text(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # саммари
    # ------------------------------------------------------------------
    def summary(self, start_sha: str, end_sha: str) -> dict:
        """Удобный dict для дашборда / one-shot вызова."""
        diff = self.get_diff(start_sha, end_sha)
        total_add = sum(v[0] for v in diff.values())
        total_del = sum(v[1] for v in diff.values())
        return {
            "project_id": self.project_id,
            "repo_path": str(self.repo_path),
            "start_sha": start_sha,
            "end_sha": end_sha,
            "files_changed": len(diff),
            "additions": total_add,
            "deletions": total_del,
            "files": diff,
        }


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------
def main() -> int:
    p = argparse.ArgumentParser(description="Git integration for BizDNAI Metrics")
    p.add_argument("repo_path", help="Путь к git-репозиторию")
    p.add_argument("range", help="Диапазон, например HEAD~5..HEAD")
    p.add_argument("--project-id", type=int, default=0)
    p.add_argument("--max-files", type=int, default=None,
                   help="Лимит количества файлов (например 100)")
    p.add_argument("--include-binary", action="store_true",
                   help="Не фильтровать бинарь")
    p.add_argument("--json", action="store_true", help="JSON в stdout")
    p.add_argument("--files-only", action="store_true",
                   help="Только список файлов, без статистики")
    args = p.parse_args()

    start, end = args.range.split("..", 1)
    proj = GitProject(args.repo_path, project_id=args.project_id or 0)

    if args.files_only:
        files = proj.get_files_changed(start, end)
        print("\n".join(files))
        return 0

    s = proj.summary(start, end)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2))
    else:
        print(f"repo:      {s['repo_path']}")
        print(f"range:     {args.range}")
        print(f"files:     {s['files_changed']}")
        print(f"additions: {s['additions']}")
        print(f"deletions: {s['deletions']}")
        print("--- top 10 ---")
        for f, (a, d) in sorted(s["files"].items(),
                                key=lambda x: -(x[1][0] + x[1][1]))[:10]:
            print(f"  +{a:5d} -{d:5d}  {f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
