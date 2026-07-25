"""Interactive configuration CLI for mcp-metrics federation."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .config import load_config, save_config

COMMUNITY_URL = "https://bizdnai.com/metrics/community"


def configure_share(
    path: str | Path | None = None,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], Any] = print,
) -> dict[str, dict[str, Any]]:
    """Ask for explicit consent and persist the resulting share settings."""
    config = load_config(path=path, environ={})
    output_fn(
        "Want to share anonymous stats? "
        f"Get token at {COMMUNITY_URL}"
    )
    answer = input_fn("Enable anonymous metrics sharing? [y/N]: ").strip().lower()
    enabled = answer in {"y", "yes"}
    config["share"]["enabled"] = enabled

    if enabled:
        token = input_fn("Community token: ").strip()
        if not token:
            raise ValueError("a community token is required to enable sharing")
        config["share"]["token"] = token
        output_fn("Anonymous metrics sharing enabled.")
    else:
        output_fn("Anonymous metrics sharing remains disabled.")

    save_config(config, path=path)
    return config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcp-metrics",
        description="Configure the local BizDNAI Metrics installation.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser(
        "configure",
        help="configure opt-in anonymous federation sharing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "configure":
        configure_share()
        return 0
    return 2


__all__ = ["COMMUNITY_URL", "build_parser", "configure_share", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
