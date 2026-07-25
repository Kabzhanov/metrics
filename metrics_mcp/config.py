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
    }
}

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

    try:
        interval = float(share["interval_hours"])
    except (TypeError, ValueError) as error:
        raise ValueError("share.interval_hours must be a positive number") from error
    if interval <= 0:
        raise ValueError("share.interval_hours must be a positive number")
    share["interval_hours"] = int(interval) if interval.is_integer() else interval
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
            try:
                parsed = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError("METRICS_SHARE_INTERVAL_HOURS must be positive") from error
            if parsed <= 0:
                raise ValueError("METRICS_SHARE_INTERVAL_HOURS must be positive")
            value = int(parsed) if parsed.is_integer() else parsed
        elif field == "token":
            value = value.strip() or None
        else:
            value = value.strip()
        share[field] = value
    return _normalize(config)


__all__ = ["DEFAULT_CONFIG", "DEFAULT_CONFIG_PATH", "load_config", "save_config"]
