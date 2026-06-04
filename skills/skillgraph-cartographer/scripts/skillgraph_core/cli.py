"""Command-line interface for SkillGraph collection and viewing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .analysis import analyze_graph
from .enrichment import enrich_graph, enrichment_template, merge_enrichment
from .viewer import serve_viewer


def command_collect(args: argparse.Namespace) -> int:
    graph = analyze_graph(
        Path(args.repo),
        agent_context=args.agent_context,
        max_chars_per_skill=args.max_chars_per_skill,
        respect_gitignore=args.respect_gitignore,
        exclude_patterns=args.exclude,
        include_patterns=args.include,
        max_skill_files=args.max_skill_files,
    )
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


def read_json_file(path: str) -> dict[str, Any]:
    if path == "-":
        return read_graph_from_stdin()
    try:
        with Path(path).open(encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"failed to read JSON from {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path} JSON must be an object")
    return data


def read_graph_argument(args: argparse.Namespace) -> dict[str, Any]:
    if getattr(args, "stdin", False):
        return read_graph_from_stdin()
    if getattr(args, "file", None):
        return read_json_file(args.file)
    raise SystemExit("command requires --stdin or --file")


def command_view(args: argparse.Namespace) -> int:
    graph = enrich_graph(read_graph_argument(args))
    return serve_viewer(graph, args.host, args.port, not args.no_open, allow_non_loopback=args.allow_non_loopback)


def command_enrichment_template(args: argparse.Namespace) -> int:
    graph = read_json_file(args.base)
    print(json.dumps(enrichment_template(graph, args.max_node_summary_chars), ensure_ascii=False, indent=2))
    return 0


def command_merge(args: argparse.Namespace) -> int:
    base = read_json_file(args.base)
    annotations = read_json_file(args.annotations)
    merged = merge_enrichment(base, annotations)
    print(json.dumps(enrich_graph(merged), ensure_ascii=False, indent=2))
    return 0


def add_json_input_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--stdin", action="store_true", help="Read graph JSON from stdin.")
    group.add_argument("--file", help="Read graph JSON from a file.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Collect read-only SkillGraph JSON or display it in a local viewer.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    collect = subparsers.add_parser("collect", help="Write base graph JSON to stdout.")
    collect.add_argument("repo", nargs="?", default=".", help="Repository root to inspect.")
    collect.add_argument("--agent-context", action="store_true", help="Include deterministic host-agent reading context.")
    collect.add_argument("--max-chars-per-skill", type=int, default=1200, help="Maximum first paragraph characters in agentContext.")
    collect.add_argument("--respect-gitignore", action="store_true", help="Skip SKILL.md files matched by the target repo .gitignore.")
    collect.add_argument("--exclude", action="append", default=[], help="Exclude SKILL.md files whose relative path matches this glob; repeatable.")
    collect.add_argument("--include", action="append", default=[], help="Include matching SKILL.md files even if an exclude or .gitignore pattern matches; repeatable.")
    collect.add_argument("--max-skill-files", type=int, help="Maximum number of SKILL.md files to scan.")
    collect.set_defaults(func=command_collect)

    view = subparsers.add_parser("view", help="Serve graph JSON from stdin in a local viewer.")
    add_json_input_arguments(view)
    view.add_argument("--host", default="127.0.0.1", help="Viewer bind host.")
    view.add_argument("--port", type=int, default=0, help="Viewer port; 0 chooses a free port.")
    view.add_argument("--no-open", action="store_true", help="Print the viewer URL without opening a browser.")
    view.add_argument("--allow-non-loopback", action="store_true", help="Allow serving graph JSON on a non-loopback host.")
    view.set_defaults(func=command_view)

    template = subparsers.add_parser("enrichment-template", help="Create a small host-agent enrichment input template from a base graph.")
    template.add_argument("base", help="Base graph JSON file, or - for stdin.")
    template.add_argument("--max-node-summary-chars", type=int, default=800)
    template.set_defaults(func=command_enrichment_template)

    merge = subparsers.add_parser("merge", help="Merge base graph JSON and enrichment JSON without rewriting deterministic graph facts.")
    merge.add_argument("--base", required=True, help="Base graph JSON file, or - for stdin.")
    merge.add_argument("--annotations", required=True, help="Enrichment JSON file, or - for stdin.")
    merge.set_defaults(func=command_merge)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
