"""Command-line interface for SkillGraph collection and viewing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .analysis import analyze_graph
from .enrichment import enrich_graph
from .viewer import serve_viewer


def command_collect(args: argparse.Namespace) -> int:
    graph = analyze_graph(Path(args.repo))
    print(json.dumps(graph, ensure_ascii=False, indent=2))
    return 0


def read_graph_from_stdin() -> dict[str, Any]:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"failed to read graph JSON from stdin: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("graph JSON must be an object")
    return data


def command_view(args: argparse.Namespace) -> int:
    if not args.stdin:
        raise SystemExit("view requires --stdin")
    graph = enrich_graph(read_graph_from_stdin())
    return serve_viewer(graph, args.host, args.port, not args.no_open)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect read-only SkillGraph JSON or display it in a local viewer.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Write base graph JSON to stdout.")
    collect.add_argument("repo", nargs="?", default=".", help="Repository root to inspect.")
    collect.set_defaults(func=command_collect)

    view = subparsers.add_parser("view", help="Serve graph JSON from stdin in a local viewer.")
    view.add_argument("--stdin", action="store_true", help="Read base or enriched graph JSON from stdin.")
    view.add_argument("--host", default="127.0.0.1", help="Viewer bind host.")
    view.add_argument("--port", type=int, default=0, help="Viewer port; 0 chooses a free port.")
    view.add_argument("--no-open", action="store_true", help="Print the viewer URL without opening a browser.")
    view.set_defaults(func=command_view)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
