"""Shared data models and low-level parsing helpers for SkillGraph."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, NamedTuple

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised only when PyYAML is absent.
    yaml = None


SCHEMA_VERSION = "skillgraph-lite.v1.1"

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "dist",
    "build",
    "__pycache__",
}
EXCLUDED_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".pdf",
    ".zip",
    ".tar",
    ".gz",
}
EXCLUDED_NAMES = {".env"}
SKILL_PATH_RE = re.compile(
    r"(?P<path>(?:\.\./|\.\/)?[^\s)`'\"<>]*SKILL(?:\.[A-Za-z0-9_-]+)?\.md|"
    r"(?:skills|\.codex/skills|\.claude/skills|\.agents/skills)/[^\s)`'\"<>]*SKILL(?:\.[A-Za-z0-9_-]+)?\.md)"
)
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
REFERENCE_LINK_DEFINITION_RE = re.compile(r"^\s*\[([^\]]+)\]:\s+(.+?)\s*$")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


class MarkdownReference(NamedTuple):
    raw: str
    line: int
    text: str
    match_kind: str


@dataclass
class Diagnostic:
    type: str
    severity: str
    message: str
    path: str | None = None
    skill: str | None = None
    target: str | None = None
    evidence: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        payload = {
            "type": self.type,
            "severity": self.severity,
            "message": self.message,
        }
        if self.path is not None:
            payload["path"] = self.path
        if self.skill is not None:
            payload["skill"] = self.skill
        if self.target is not None:
            payload["target"] = self.target
        if self.evidence is not None:
            payload["evidence"] = self.evidence
        return payload


@dataclass
class SkillRecord:
    id: str
    kind: str
    label: str
    path: str
    dir: str
    category: str
    description: str
    aliases: list[str]
    name: str
    paths: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    language_variants: list[dict[str, str]] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    copies: list[dict[str, str]] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "dir": self.dir,
            "category": self.category,
            "description": self.description,
            "aliases": self.aliases,
            "name": self.name,
            "paths": self.paths or [self.path],
            "dirs": self.dirs or [self.dir],
        }
        if self.language_variants:
            payload["languageVariants"] = self.language_variants
        if self.copies:
            payload["copies"] = self.copies
        return payload


@dataclass
class Edge:
    source: str
    target: str
    type: str
    origin: str
    evidence: list[dict[str, Any]]
    legacy_type: str | None = None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def normalize_id_part(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def normalize_heading(value: str) -> str:
    value = re.sub(r"`([^`]+)`", r"\1", value)
    return " ".join(value.strip().lower().split())


def should_skip(path: Path, root: Path) -> bool:
    try:
        rel_parts = path.resolve().relative_to(root.resolve()).parts
    except ValueError:
        rel_parts = path.parts
    if any(part in EXCLUDED_DIRS for part in rel_parts):
        return True
    if path.name in EXCLUDED_NAMES or path.name.startswith(".env."):
        return True
    return any(path.name.lower().endswith(suffix) for suffix in EXCLUDED_SUFFIXES)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def content_digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def graph_digest(graph: dict[str, Any]) -> str:
    import json

    payload = json.dumps(graph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return content_digest(payload)


def host_scope_for_path(path: str) -> str:
    if path.startswith(".codex/skills/"):
        return "codex"
    if path.startswith(".claude/skills/"):
        return "claude-code"
    if path.startswith(".agents/skills/"):
        return "agents"
    if path.startswith("skills/"):
        return "repo"
    return "other"


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by SKILL frontmatter."""
    result: dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, result)]
    pending_key_by_indent: dict[int, tuple[dict[str, Any], str]] = {}
    lines = text.splitlines()

    def scalar(value: str) -> Any:
        value = value.strip()
        if value in {"", "null", "Null", "NULL", "~"}:
            return None
        if value in {"true", "True", "TRUE"}:
            return True
        if value in {"false", "False", "FALSE"}:
            return False
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        return value

    def block_scalar(start: int, parent_indent: int, folded: bool) -> tuple[str, int]:
        collected: list[str] = []
        index = start
        while index < len(lines):
            line = lines[index]
            if line.strip():
                indent = len(line) - len(line.lstrip(" "))
                if indent <= parent_indent:
                    break
                collected.append(line.strip())
            else:
                collected.append("")
            index += 1
        separator = " " if folded else "\n"
        return separator.join(collected).strip(), index

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        index += 1
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            item = scalar(line[2:])
            if not isinstance(parent, list):
                pending = pending_key_by_indent.get(indent)
                if pending:
                    container, key = pending
                    new_list: list[Any] = []
                    container[key] = new_list
                    stack.append((indent - 1, new_list))
                    parent = new_list
            if isinstance(parent, list):
                parent.append(item)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        if isinstance(parent, dict):
            if value in {">", "|", ">-", "|-", ">+", "|+"}:
                block_value, index = block_scalar(index, indent, value.startswith(">"))
                parent[key] = block_value
            elif value == "":
                container: dict[str, Any] = {}
                parent[key] = container
                pending_key_by_indent[indent + 2] = (parent, key)
                stack.append((indent, container))
            else:
                parent[key] = scalar(value)
    return result


def load_yaml_text(text: str) -> dict[str, Any]:
    if yaml is not None:
        data = yaml.safe_load(text)  # type: ignore[attr-defined]
    else:
        data = parse_simple_yaml(text)
    return data if isinstance(data, dict) else {}


def parse_frontmatter(text: str) -> tuple[dict[str, Any], bool]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, False
    return load_yaml_text(match.group(1)), True


def strip_frontmatter(text: str) -> str:
    return FRONTMATTER_RE.sub("", text, count=1)


def first_h1(text: str) -> str | None:
    for line in strip_frontmatter(text).splitlines():
        match = re.match(r"^#\s+(.+?)\s*$", line)
        if match:
            return match.group(1).strip()
    return None


def first_meaningful_paragraph(text: str) -> str:
    body = strip_frontmatter(text)
    paragraphs: list[str] = []
    current: list[str] = []
    in_fence = False
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if line.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not line or line.startswith("#"):
            if current:
                paragraphs.append(" ".join(current))
                current = []
            continue
        if line.startswith(("-", "*", "+", ">", "|")):
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(current))
    return paragraphs[0] if paragraphs else ""


def unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = value.strip()
        if not item:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def clean_target(value: str) -> str:
    value = value.strip().strip("`'\"")
    value = value.split("#", 1)[0]
    value = value.split("?", 1)[0]
    return value.rstrip(".,;:").strip("`'\"")


def strip_code_blocks(text: str) -> str:
    return FENCE_RE.sub("", text)


def evidence(
    path: str,
    text: str,
    section: str | None = None,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
    match_kind: str | None = None,
    raw: str | None = None,
    normalized_target: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path, "text": text.strip()[:300]}
    if section:
        payload["section"] = section
    if start_line is not None:
        payload["startLine"] = start_line
        payload["endLine"] = end_line or start_line
    if match_kind:
        payload["matchKind"] = match_kind
    if raw is not None:
        payload["raw"] = raw
    if normalized_target is not None:
        payload["normalizedTarget"] = normalized_target
    return payload


def is_fence_line(line: str) -> bool:
    stripped = line.lstrip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def strip_inline_code_spans(line: str) -> str:
    return re.sub(r"`[^`]*`", "", line)


def markdown_reference_key(value: str) -> str:
    return " ".join(value.strip().lower().split())


def split_markdown_destination(value: str) -> str:
    text = value.strip()
    if text.startswith("<"):
        end = text.find(">")
        if end >= 0:
            return text[1:end].strip()
    match = re.match(r"([^\s]+)", text)
    return match.group(1).strip() if match else text


def _collect_reference_link_definitions(lines: list[str]) -> dict[str, str]:
    references: dict[str, str] = {}
    in_fence = False
    for line in lines:
        if is_fence_line(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = REFERENCE_LINK_DEFINITION_RE.match(strip_inline_code_spans(line))
        if not match:
            continue
        references[markdown_reference_key(match.group(1))] = split_markdown_destination(match.group(2))
    return references


def _closing_paren_index(line: str, start: int) -> int:
    depth = 0
    for index in range(start, len(line)):
        char = line[index]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return -1


def iter_markdown_skill_links(text: str) -> Iterator[MarkdownReference]:
    lines = text.splitlines()
    references = _collect_reference_link_definitions(lines)
    in_fence = False
    for line_number, raw_line in enumerate(lines, start=1):
        if is_fence_line(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = strip_inline_code_spans(raw_line)
        index = 0
        while index < len(line):
            if line[index] != "[" or (index > 0 and line[index - 1] == "!"):
                index += 1
                continue
            close_label = line.find("]", index + 1)
            if close_label < 0:
                break
            label = line[index + 1 : close_label]
            if close_label + 1 < len(line) and line[close_label + 1] == "(":
                close_paren = _closing_paren_index(line, close_label + 1)
                if close_paren < 0:
                    index = close_label + 1
                    continue
                destination = split_markdown_destination(line[close_label + 2 : close_paren])
                if "SKILL" in destination and destination.endswith(".md"):
                    yield MarkdownReference(destination, line_number, raw_line.strip(), "markdown_link")
                index = close_paren + 1
                continue
            if close_label + 1 < len(line) and line[close_label + 1] == "[":
                close_ref = line.find("]", close_label + 2)
                if close_ref < 0:
                    index = close_label + 1
                    continue
                key = markdown_reference_key(line[close_label + 2 : close_ref] or label)
                destination = references.get(key)
                if destination and "SKILL" in destination and destination.endswith(".md"):
                    yield MarkdownReference(destination, line_number, raw_line.strip(), "reference_link")
                index = close_ref + 1
                continue
            index = close_label + 1


def iter_skill_path_references(text: str) -> Iterator[MarkdownReference]:
    in_fence = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        if is_fence_line(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        line = strip_inline_code_spans(raw_line)
        for match in SKILL_PATH_RE.finditer(line):
            yield MarkdownReference(match.group("path"), line_number, raw_line.strip(), "path_reference")


def markdown_headings(text: str) -> list[str]:
    values: list[str] = []
    in_fence = False
    for raw_line in strip_frontmatter(text).splitlines():
        if is_fence_line(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(raw_line)
        if match:
            values.append(match.group(2).strip())
    return values
