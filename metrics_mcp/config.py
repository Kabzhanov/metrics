"""Configuration for the opt-in federated metrics sender."""

from __future__ import annotations

import copy
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

DEFAULT_CONFIG = {
    "share": {
        "enabled": False,
        "endpoint": "https://bizdnai.com/metrics/api_central.php",
        "token": None,
        "interval_hours": 1,
        "include_files_changed_count": False,
    },
    # Phase 7.7 (задача #1207, спек §8.1): per-project required_checks.
    # Канонический источник правды — колонка projects.required_checks в БД.
    # Этот блок — fallback / override для локального dev / override через env.
    # Имена проверок: build_passed | tests_passed | lint_passed |
    # acceptance_passed | agent_completed.
    "projects": {
        # "<project_name>": ["build_passed", "tests_passed", ...]
    },
}

# Имена валидных проверок — защита от опечаток при override.
VALID_CHECKS = frozenset({
    "agent_completed",
    "build_passed",
    "tests_passed",
    "lint_passed",
    "acceptance_passed",
})

# Дефолтный набор, если у проекта в БД нет override.
DEFAULT_REQUIRED_CHECKS: list[str] = ["build_passed", "tests_passed"]

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "mcp-metrics" / "config.yaml"

_ENV_KEYS = {
    "METRICS_SHARE_ENABLED": "enabled",
    "METRICS_SHARE_ENDPOINT": "endpoint",
    "METRICS_SHARE_TOKEN": "token",
    "METRICS_SHARE_INTERVAL_HOURS": "interval_hours",
    "METRICS_SHARE_INCLUDE_FILES_CHANGED_COUNT": "include_files_changed_count",
}
_BOOLEAN_FIELDS = {"enabled", "include_files_changed_count"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _parse_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"share.{field} must be true or false")


def _parse_interval_hours(value: Any, *, source: str) -> int | float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{source} must be a positive number") from error
    if parsed <= 0:
        raise ValueError(f"{source} must be a positive number")
    return int(parsed) if parsed.is_integer() else parsed


def _normalize_projects(raw_projects: Any) -> dict[str, list[str]]:
    """Validate the per-project required_checks block.

    Returns mapping {project_name: [check, ...]}. Unknown check names
    raise ValueError — config-level typo protection.
    """
    if raw_projects is None:
        return {}
    if not isinstance(raw_projects, Mapping):
        raise TypeError("projects config must be a mapping")
    normalized: dict[str, list[str]] = {}
    for name, checks in raw_projects.items():
        if not isinstance(name, str) or not name:
            raise ValueError("project names must be non-empty strings")
        if checks is None:
            normalized[name] = list(DEFAULT_REQUIRED_CHECKS)
            continue
        if not isinstance(checks, list):
            raise TypeError(f"projects.{name} must be a list of check names")
        cleaned: list[str] = []
        for check in checks:
            if not isinstance(check, str):
                raise TypeError(f"projects.{name} entries must be strings")
            if check not in VALID_CHECKS:
                raise ValueError(
                    f"projects.{name}: unknown check '{check}'; "
                    f"valid: {sorted(VALID_CHECKS)}"
                )
            if check not in cleaned:
                cleaned.append(check)
        normalized[name] = cleaned or list(DEFAULT_REQUIRED_CHECKS)
    return normalized


def _normalize(raw: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    if raw is None:
        return config
    if not isinstance(raw, Mapping):
        raise TypeError("config root must be a mapping")
    raw_share = raw.get("share", {})
    if raw_share is None:
        raw_share = {}
    if not isinstance(raw_share, Mapping):
        raise TypeError("share config must be a mapping")

    share = config["share"]
    for field in share:
        if field in raw_share:
            share[field] = raw_share[field]

    for field in _BOOLEAN_FIELDS:
        share[field] = _parse_bool(share[field], field)

    endpoint = str(share["endpoint"] or "").strip()
    parsed_endpoint = urlparse(endpoint)
    is_local_http = parsed_endpoint.scheme == "http" and parsed_endpoint.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if not parsed_endpoint.hostname or (parsed_endpoint.scheme != "https" and not is_local_http):
        raise ValueError("share.endpoint must use HTTPS (plain HTTP is allowed only on localhost)")
    share["endpoint"] = endpoint

    token = share["token"]
    share["token"] = str(token).strip() if token is not None and str(token).strip() else None

    share["interval_hours"] = _parse_interval_hours(share["interval_hours"], source="share.interval_hours")

    # Phase 7.7 (задача #1207): per-project required_checks override.
    raw_projects = raw.get("projects", None)
    if raw_projects is not None and not isinstance(raw_projects, Mapping):
        raise TypeError("projects config must be a mapping")
    config["projects"] = _normalize_projects(raw_projects)

    return config


def save_config(config: Mapping[str, Any], path: str | Path | None = None) -> Path:
    """Atomically persist normalized configuration with owner-only permissions."""
    target = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    normalized = _normalize(config)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(
        yaml.safe_dump(normalized, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    return target


def load_config(
    path: str | Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load YAML, creating an opt-out default file, then apply env overrides."""
    target = Path(path).expanduser() if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        save_config(DEFAULT_CONFIG, target)

    try:
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {target}: {error}") from error
    config = _normalize(loaded)

    source = os.environ if environ is None else environ
    share = config["share"]
    for env_name, field in _ENV_KEYS.items():
        if env_name not in source:
            continue
        value: Any = source[env_name]
        if field in _BOOLEAN_FIELDS:
            value = _parse_bool(value, field)
        elif field == "interval_hours":
            value = _parse_interval_hours(value, source="METRICS_SHARE_INTERVAL_HOURS")
        elif field == "token":
            value = value.strip() or None
        else:
            value = value.strip()
        share[field] = value
    return _normalize(config)


def get_required_checks(
    project_id: int,
    project_name: str | None = None,
    *,
    config: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return the list of required check names for a project.

    Phase 7.7 (задача #1207, спек §8.1).

    Resolution order:
      1. If ``project_name`` matches a key in ``config['projects']`` — use it.
      2. Otherwise fall back to :data:`DEFAULT_REQUIRED_CHECKS`.

    Note: the **canonical** source of truth is the ``projects.required_checks``
    column in the database (read by API endpoints via the
    ``metrics_verified_per_project_v`` view). This helper exists for
    Python callers that need the same defaults without a DB round-trip
    (e.g. CLI tools, validators).
    """
    if config is None:
        config = load_config()
    if project_name:
        projects_cfg = config.get("projects") or {}
        if isinstance(projects_cfg, Mapping):
            override = projects_cfg.get(project_name)
            if isinstance(override, list) and override:
                return list(override)
    return list(DEFAULT_REQUIRED_CHECKS)


__all__ = [
    "DEFAULT_CONFIG",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_REQUIRED_CHECKS",
    "VALID_CHECKS",
    "get_required_checks",
    "load_config",
    "save_config",
]
