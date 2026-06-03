"""Shared data models and low-level parsing helpers for SkillGraph."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised only when PyYAML is absent.
    yaml = None


SCHEMA_VERSION = "skillgraph-lite.v1"

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
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.DOTALL)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FENCE_RE = re.compile(r"```.*?```|~~~.*?~~~", re.DOTALL)


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
        return payload


@dataclass
class Edge:
    source: str
    target: str
    type: str
    origin: str
    confidence: str
    evidence: list[dict[str, Any]]


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


def evidence(path: str, text: str, section: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path, "text": text.strip()[:300]}
    if section:
        payload["section"] = section
    return payload
