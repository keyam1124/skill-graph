#!/usr/bin/env python3
"""Read-only SkillGraph viewer runtime.

The tool is intentionally deterministic. It does not call external LLM APIs;
agents can run ``collect`` to get a base graph, enrich it themselves, and pipe
the result into ``view`` for local display.
"""

from __future__ import annotations

import argparse
import datetime as dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import sys
import webbrowser
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
RELATED_HEADINGS = {
    "related skills",
    "関連 skill",
    "関連スキル",
    "see also",
}
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


def skill_identity_parts(skill_file: Path, root: Path) -> tuple[str, ...]:
    rel_parts = rel_path(skill_file.parent, root).split("/")
    for index in range(len(rel_parts) - 1, -1, -1):
        if rel_parts[index] == "skills":
            return tuple(rel_parts[index + 1 :])
    return tuple(rel_parts)


def normalize_skill_id(skill_file: Path, root: Path) -> str:
    rel = "/".join(skill_identity_parts(skill_file, root))
    return ".".join(normalize_id_part(part) for part in Path(rel).parts)


def skill_category(skill_file: Path, root: Path) -> str:
    parts = skill_identity_parts(skill_file, root)[:-1]
    return ".".join(normalize_id_part(part) for part in parts) or "Uncategorized"


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


def iter_files(root: Path, patterns: Iterable[str] | None = None) -> Iterable[Path]:
    if not root.exists():
        return
    if patterns is None:
        candidates = root.rglob("*")
    else:
        pattern_candidates: list[Path] = []
        for pattern in patterns:
            pattern_candidates.extend(root.glob(pattern))
        candidates = iter(pattern_candidates)
    for path in candidates:
        if path.is_file() and not should_skip(path, root):
            yield path


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


def scan_registry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    diagnostics: list[Diagnostic] = []
    skill_files = sorted(root.glob("**/SKILL.md"), key=lambda path: skill_file_sort_key(path, root))
    skill_files = [path for path in skill_files if not should_skip(path, root)]
    records_by_id: dict[str, SkillRecord] = {}

    for skill_file in skill_files:
        skill_id = normalize_skill_id(skill_file, root)
        text = read_text(skill_file)
        frontmatter, _ = parse_frontmatter(text)
        name = str(frontmatter.get("name") or skill_file.parent.name)
        description = str(frontmatter.get("description") or "")
        path = rel_path(skill_file, root)
        skill_dir = rel_path(skill_file.parent, root)
        h1 = first_h1(text)
        category = skill_category(skill_file, root)
        aliases = unique(
            [
                skill_id,
                name,
                skill_file.parent.name,
                path,
                skill_dir,
                *([h1] if h1 else []),
            ]
        )
        variants: list[dict[str, str]] = []
        for variant in sorted(skill_file.parent.glob("SKILL.*.md")):
            if variant.name == "SKILL.md" or should_skip(variant, root):
                continue
            lang = variant.name.removeprefix("SKILL.").removesuffix(".md")
            variants.append({"lang": lang, "path": rel_path(variant, root)})
        record = SkillRecord(
            id=skill_id,
            kind="skill",
            label=name or skill_file.parent.name,
            path=path,
            dir=skill_dir,
            category=category,
            description=description,
            aliases=aliases,
            name=name,
            paths=[path],
            dirs=[skill_dir],
            language_variants=variants,
            frontmatter=frontmatter,
        )
        if skill_id in records_by_id:
            merge_skill_record(records_by_id[skill_id], record)
        else:
            records_by_id[skill_id] = record

    registry = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "root": str(root),
        "skills": [records_by_id[skill_id].to_json() for skill_id in sorted(records_by_id)],
        "diagnostics": [diag.to_json() for diag in diagnostics],
    }
    return registry


def skill_file_sort_key(path: Path, root: Path) -> tuple[int, str]:
    rel = rel_path(path, root)
    priority = 2
    if rel.startswith("skills/"):
        priority = 0
    elif "/skills/" in rel:
        priority = 1
    return (priority, rel)


def merge_skill_record(existing: SkillRecord, incoming: SkillRecord) -> None:
    existing.paths = unique([*existing.paths, *incoming.paths])
    existing.dirs = unique([*existing.dirs, *incoming.dirs])
    existing.aliases = unique([*existing.aliases, *incoming.aliases])
    existing.language_variants = merge_language_variants(existing.language_variants, incoming.language_variants)
    if not existing.description and incoming.description:
        existing.description = incoming.description
    if existing.category == "Uncategorized" and incoming.category != "Uncategorized":
        existing.category = incoming.category


def merge_language_variants(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    merged: list[dict[str, str]] = []
    for variant in [*existing, *incoming]:
        key = (variant.get("lang", ""), variant.get("path", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(variant)
    return merged


def load_registry(root: Path) -> dict[str, Any]:
    return scan_registry(root)


def skill_maps(registry: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    skills = {skill["id"]: skill for skill in registry.get("skills", [])}
    alias_map: dict[str, list[str]] = {}
    for skill in skills.values():
        candidates = [skill["id"], skill.get("name", ""), skill.get("label", ""), skill.get("path", ""), skill.get("dir", "")]
        candidates.extend(skill.get("aliases", []))
        candidates.extend(skill.get("paths", []))
        candidates.extend(skill.get("dirs", []))
        for alias in candidates:
            if not alias:
                continue
            alias_map.setdefault(str(alias).casefold(), []).append(skill["id"])
    for key, values in list(alias_map.items()):
        alias_map[key] = sorted(set(values))
    return skills, alias_map


def resolve_skill(value: str, skills: dict[str, dict[str, Any]], alias_map: dict[str, list[str]]) -> str | None:
    candidate = clean_target(value)
    if candidate in skills:
        return candidate
    if candidate.casefold() in alias_map and len(alias_map[candidate.casefold()]) == 1:
        return alias_map[candidate.casefold()][0]
    normalized = candidate.replace("/", ".").removesuffix(".skill.md").removesuffix(".md").casefold()
    if normalized in alias_map and len(alias_map[normalized]) == 1:
        return alias_map[normalized][0]
    return None


def clean_target(value: str) -> str:
    value = value.strip().strip("`'\"")
    value = value.split("#", 1)[0]
    value = value.split("?", 1)[0]
    return value.rstrip(".,;:").strip("`'\"")


def resolve_skill_path_reference(raw: str, source_file: Path, root: Path) -> str | None:
    target = clean_target(raw)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return None
    if "SKILL" not in target or not target.endswith(".md"):
        return None
    if target.startswith(("skills/", ".codex/skills/", ".claude/skills/", ".agents/skills/")):
        candidate = (root / target).resolve()
    else:
        candidate = (source_file.parent / target).resolve()
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except ValueError:
        if target.startswith("./"):
            return target[2:] if "SKILL" in target else None
        return target


def resolve_skill_link_target(
    raw: str,
    source_file: Path,
    root: Path,
    path_to_skill: dict[str, str],
) -> str | None:
    resolved = resolve_skill_path_reference(raw, source_file, root)
    if not resolved:
        return None
    return path_to_skill.get(resolved)


def section_ranges(text: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    headings: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        match = HEADING_RE.match(line)
        if not match:
            continue
        headings.append(
            {
                "line": index,
                "level": len(match.group(1)),
                "heading": match.group(2).strip(),
                "normalized": normalize_heading(match.group(2)),
            }
        )
    sections: list[dict[str, Any]] = []
    for i, heading in enumerate(headings):
        end = len(lines)
        for next_heading in headings[i + 1 :]:
            if next_heading["level"] <= heading["level"]:
                end = next_heading["line"]
                break
        sections.append({**heading, "text": "\n".join(lines[heading["line"] + 1 : end])})
    return sections


def find_section_mentions(
    section_text: str,
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in section_text.splitlines():
        candidates = re.findall(r"`([^`]+)`", line)
        candidates.extend(re.findall(r"\[([^\]]+)\]", line))
        cleaned = re.sub(r"^[\s*\-+>\d.)]+", "", line).strip()
        if cleaned:
            candidates.append(cleaned)
        for candidate in candidates:
            target = resolve_skill(candidate, skills, alias_map)
            if target and target not in seen:
                matches.append((target, candidate.strip()))
                seen.add(target)
    return matches


def strip_code_blocks(text: str) -> str:
    return FENCE_RE.sub("", text)


def evidence(path: str, text: str, section: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"path": path, "text": text.strip()[:300]}
    if section:
        payload["section"] = section
    return payload


def make_edge(
    source: str,
    target: str,
    relation_type: str,
    origin: str,
    confidence: str,
    evidence_items: list[dict[str, Any]],
) -> Edge:
    return Edge(
        source=source,
        target=target,
        type=relation_type,
        origin=origin,
        confidence=confidence,
        evidence=evidence_items,
    )


def markdown_link_edges(
    root: Path,
    skill: dict[str, Any],
    path_to_skill: dict[str, str],
) -> tuple[list[Edge], list[Diagnostic]]:
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    for path in skill.get("paths", [skill["path"]]):
        skill_file = root / path
        text = read_text(skill_file)
        for match in LINK_RE.finditer(text):
            raw = match.group(1)
            target = resolve_skill_link_target(raw, skill_file, root, path_to_skill)
            if not target or target == skill["id"]:
                continue
            edges.append(
                make_edge(
                    skill["id"],
                    target,
                    "depends_on",
                    "link",
                    "high",
                    [evidence(path, raw)],
                )
            )
    return edges, diagnostics


def skill_path_reference_edges(
    root: Path,
    skill: dict[str, Any],
    path_to_skill: dict[str, str],
    source_file: Path,
    source_text: str | None = None,
) -> tuple[list[Edge], list[Diagnostic]]:
    text = source_text if source_text is not None else read_text(source_file)
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    for match in SKILL_PATH_RE.finditer(text):
        raw = match.group("path")
        resolved = resolve_skill_path_reference(raw, source_file, root)
        if not resolved:
            continue
        target = path_to_skill.get(resolved, resolved)
        if target == skill["id"]:
            continue
        if target == resolved:
            diagnostics.append(
                Diagnostic(
                    type="dangling_reference",
                    severity="warning",
                    message=f"Path reference {raw} does not resolve to a skill.",
                    path=rel_path(source_file, root),
                    skill=skill["id"],
                    target=resolved,
                )
            )
            continue
        edges.append(
            make_edge(
                skill["id"],
                target,
                "depends_on",
                "path_reference",
                "high",
                [evidence(rel_path(source_file, root), raw)],
            )
        )
    return edges, diagnostics


def heading_edges(
    root: Path,
    skill: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
) -> tuple[list[Edge], list[Diagnostic]]:
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    for path in skill.get("paths", [skill["path"]]):
        text = read_text(root / path)
        sections = section_ranges(text)
        for section in sections:
            heading = section["normalized"]
            if heading not in RELATED_HEADINGS:
                continue
            for target, raw in find_section_mentions(section["text"], skills, alias_map):
                if target == skill["id"]:
                    continue
                edges.append(
                    make_edge(
                        skill["id"],
                        target,
                        "depends_on",
                        "mention",
                        "high",
                        [evidence(path, raw, section["heading"])],
                    )
                )
    return edges, diagnostics


def term_pattern(term: str) -> re.Pattern[str] | None:
    term = term.strip()
    if len(term) < 3:
        return None
    if re.match(r"^[A-Za-z0-9_.\-/]+$", term):
        return re.compile(rf"(?<![A-Za-z0-9_\-/]){re.escape(term)}(?![A-Za-z0-9_\-/])", re.IGNORECASE)
    return re.compile(re.escape(term), re.IGNORECASE)


def skill_search_files(root: Path, skill: dict[str, Any]) -> list[Path]:
    files: list[Path] = []
    for skill_dir in skill.get("dirs", [skill.get("dir", "")]):
        base = root / skill_dir
        for path in iter_files(base, ["SKILL*.md"]):
            files.append(path)
    return sorted(set(files))


def body_mention_edges(
    root: Path,
    skill: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
) -> tuple[list[Edge], list[Diagnostic]]:
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    seen_edges: set[tuple[str, str]] = set()
    files = skill_search_files(root, skill)
    target_terms: list[tuple[str, str]] = []
    for target_id, target in skills.items():
        if target_id == skill["id"]:
            continue
        values = [target_id, target.get("name", ""), target.get("label", ""), target.get("path", ""), target.get("dir", "")]
        values.extend(target.get("aliases", []))
        for value in unique(str(v) for v in values if v):
            target_terms.append((target_id, value))

    for file_path in files:
        text = strip_code_blocks(read_text(file_path))
        rel = rel_path(file_path, root)
        for target_id, term in target_terms:
            pattern = term_pattern(term)
            if not pattern:
                continue
            match = pattern.search(text)
            if not match:
                continue
            edge_key = (target_id, rel)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(
                make_edge(
                    skill["id"],
                    target_id,
                    "depends_on",
                    "mention",
                    "low",
                    [evidence(rel, match.group(0))],
                )
            )
    return edges, diagnostics


def duplicate_alias_diagnostics(skills: dict[str, dict[str, Any]], alias_map: dict[str, list[str]]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for alias, skill_ids in sorted(alias_map.items()):
        if len(skill_ids) <= 1:
            continue
        if alias in skill_ids:
            continue
        diagnostics.append(
            Diagnostic(
                type="duplicate_alias",
                severity="warning",
                message=f"Alias {alias} resolves to multiple skills: {', '.join(skill_ids)}.",
                target=alias,
                evidence={"skills": skill_ids},
            )
        )
    return diagnostics


def assign_edge_ids(edges: list[Edge]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for edge in edges:
        base = f"edge.{edge.source}.{edge.target}.{edge.type}"
        safe_base = re.sub(r"[^A-Za-z0-9_.-]+", "-", base).strip("-")
        counters[safe_base] = counters.get(safe_base, 0) + 1
        payload.append(
            {
                "id": f"{safe_base}.{counters[safe_base]}",
                "source": edge.source,
                "target": edge.target,
                "type": edge.type,
                "origin": edge.origin,
                "confidence": edge.confidence,
                "evidence": edge.evidence,
            }
        )
    return payload


def skill_path_map(skills: dict[str, dict[str, Any]]) -> dict[str, str]:
    path_to_skill: dict[str, str] = {}
    for skill_id, skill in skills.items():
        for value in [skill.get("path"), skill.get("dir"), *skill.get("paths", []), *skill.get("dirs", [])]:
            if value:
                path_to_skill[str(value)] = skill_id
        for variant in skill.get("languageVariants", []):
            path = variant.get("path")
            if path:
                path_to_skill[str(path)] = skill_id
    return path_to_skill


def confidence_rank(value: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(str(value), 1)


def merge_dependency_edges(edges: list[Edge]) -> list[Edge]:
    merged: dict[tuple[str, str, str], Edge] = {}
    for edge in edges:
        if edge.source == edge.target:
            continue
        key = (edge.source, edge.target, edge.type)
        current = merged.get(key)
        if current is None:
            merged[key] = edge
            continue
        current.origin = ", ".join(unique([*current.origin.split(", "), edge.origin]))
        if confidence_rank(edge.confidence) > confidence_rank(current.confidence):
            current.confidence = edge.confidence
        seen_evidence = {
            (item.get("path", ""), item.get("section", ""), item.get("text", ""))
            for item in current.evidence
        }
        for item in edge.evidence:
            evidence_key = (item.get("path", ""), item.get("section", ""), item.get("text", ""))
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
            current.evidence.append(item)
    return list(merged.values())


def analyze_graph(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = load_registry(root)
    skills, alias_map = skill_maps(registry)
    path_to_skill = skill_path_map(skills)
    diagnostics = [Diagnostic(**diag) for diag in registry.get("diagnostics", [])]
    diagnostics.extend(duplicate_alias_diagnostics(skills, alias_map))
    edges: list[Edge] = []

    for skill in skills.values():
        link_edges, link_diags = markdown_link_edges(root, skill, path_to_skill)
        edges.extend(link_edges)
        diagnostics.extend(link_diags)
        for path in skill.get("paths", [skill["path"]]):
            path_edges, path_diags = skill_path_reference_edges(root, skill, path_to_skill, root / path)
            edges.extend(path_edges)
            diagnostics.extend(path_diags)
        section_edges, section_diags = heading_edges(root, skill, skills, alias_map)
        edges.extend(section_edges)
        diagnostics.extend(section_diags)
        mention_edges, mention_diags = body_mention_edges(root, skill, skills, alias_map)
        edges.extend(mention_edges)
        diagnostics.extend(mention_diags)

    edges = merge_dependency_edges(edges)

    connected: set[str] = set()
    skill_ids = set(skills)
    for edge in edges:
        if edge.source in skill_ids:
            connected.add(edge.source)
        if edge.target in skill_ids:
            connected.add(edge.target)
    for skill_id in sorted(skill_ids - connected):
        diagnostics.append(
            Diagnostic(
                type="orphan_skill",
                severity="info",
                message="Skill has no incoming or outgoing relationship edges.",
                path=skills[skill_id]["path"],
                skill=skill_id,
            )
        )

    skill_nodes = [skills[skill_id] for skill_id in sorted(skills)]
    graph = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "root": str(root),
        "nodes": skill_nodes,
        "edges": assign_edge_ids(edges),
        "diagnostics": [diag.to_json() for diag in diagnostics],
    }
    return graph


def html_for_graph(graph: dict[str, Any] | None = None) -> str:
    graph_json = "null" if graph is None else json.dumps(graph, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__GRAPH_JSON__", graph_json.replace("</", "<\\/"))


def diagnostic_from_mapping(data: dict[str, Any], message: str) -> dict[str, Any]:
    payload = {
        "type": "invalid_agent_annotation",
        "severity": "warning",
        "message": message,
    }
    if "nodeId" in data:
        payload["target"] = data.get("nodeId")
    elif "source" in data or "target" in data:
        payload["target"] = " -> ".join(str(data.get(key, "")) for key in ("source", "target") if data.get(key))
    payload["evidence"] = {"value": data}
    return payload


def agent_list(graph: dict[str, Any], key: str, diagnostics: list[dict[str, Any]]) -> list[Any]:
    value = graph.get(key, [])
    if value is None:
        return []
    if isinstance(value, list):
        return value
    diagnostics.append(diagnostic_from_mapping({"value": value}, f"Agent {key} is not a list."))
    graph[key] = []
    return []


def normalize_relation_type(value: Any) -> str:
    relation_type = str(value or "depends_on")
    if relation_type in {"related_to", "mentions"}:
        return "depends_on"
    return relation_type


def enrich_graph(graph: dict[str, Any]) -> dict[str, Any]:
    graph = json.loads(json.dumps(graph))
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    diagnostics = graph.setdefault("diagnostics", [])
    node_ids = {node.get("id") for node in nodes if node.get("id")}
    node_index = {node.get("id"): node for node in nodes if node.get("id")}

    valid_annotations: list[dict[str, Any]] = []
    for annotation in agent_list(graph, "nodeAnnotations", diagnostics):
        if not isinstance(annotation, dict):
            diagnostics.append(diagnostic_from_mapping({"value": annotation}, "Agent node annotation is not an object."))
            continue
        node_id = str(annotation.get("nodeId") or "")
        if node_id not in node_ids:
            diagnostics.append(diagnostic_from_mapping(annotation, f"Agent node annotation target {node_id or '<missing>'} does not exist."))
            continue
        valid_annotations.append(annotation)
        node_index[node_id]["annotation"] = annotation
    graph["nodeAnnotations"] = valid_annotations

    valid_inferred_edges: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    for edge in agent_list(graph, "inferredEdges", diagnostics):
        if not isinstance(edge, dict):
            diagnostics.append(diagnostic_from_mapping({"value": edge}, "Agent inferred edge is not an object."))
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        edge_type = normalize_relation_type(edge.get("type"))
        if source not in node_ids or target not in node_ids:
            diagnostics.append(diagnostic_from_mapping(edge, f"Agent inferred edge {source or '<missing>'} -> {target or '<missing>'} references an unknown node."))
            continue
        base = re.sub(r"[^A-Za-z0-9_.-]+", "-", f"edge.inferred.{source}.{target}.{edge_type}").strip("-")
        counters[base] = counters.get(base, 0) + 1
        normalized = {
            "id": edge.get("id") or f"{base}.{counters[base]}",
            "source": source,
            "target": target,
            "type": edge_type,
            "origin": edge.get("origin") or "agent_inferred",
            "confidence": edge.get("confidence", "medium"),
            "evidence": edge.get("evidence") if isinstance(edge.get("evidence"), list) else [],
            "rationale": edge.get("rationale") or "",
            "inferred": True,
        }
        valid_inferred_edges.append(normalized)
        edges.append(normalized)
    graph["inferredEdges"] = valid_inferred_edges

    agent_list(graph, "viewSuggestions", diagnostics)
    return graph


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


def make_viewer_handler(graph: dict[str, Any]) -> type[BaseHTTPRequestHandler]:
    html_text = html_for_graph().encode("utf-8")
    graph_json = (json.dumps(graph, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    class ViewerHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path in {"/", "/index.html"}:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_text)))
                self.end_headers()
                self.wfile.write(html_text)
                return
            if self.path == "/graph.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(graph_json)))
                self.end_headers()
                self.wfile.write(graph_json)
                return
            self.send_error(404)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ViewerHandler


def serve_viewer(graph: dict[str, Any], host: str, port: int, open_browser: bool) -> int:
    server = ThreadingHTTPServer((host, port), make_viewer_handler(graph))
    url = f"http://{server.server_address[0]}:{server.server_address[1]}/"
    print(url, flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


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


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SkillGraph Viewer</title>
  <style>
    :root {
      color-scheme: light;
      --bg: oklch(98% 0.005 250);
      --surface: oklch(100% 0 0);
      --surface-2: oklch(96% 0.006 250);
      --surface-3: oklch(93% 0.01 250);
      --fg: oklch(22% 0.02 240);
      --muted: oklch(50% 0.018 240);
      --border: oklch(88% 0.01 240);
      --accent: oklch(58% 0.16 145);
      --accent-strong: oklch(47% 0.15 145);
      --accent-soft: oklch(93% 0.035 145);
      --secondary: oklch(58% 0.16 255);
      --secondary-soft: oklch(93% 0.04 255);
      --relation: oklch(64% 0.17 35);
      --warning: oklch(70% 0.16 75);
      --warning-soft: oklch(95% 0.05 75);
      --danger: oklch(58% 0.18 28);
      --danger-soft: oklch(94% 0.05 28);
      --shadow: 0 18px 50px rgba(20, 30, 50, 0.10);
      --font-display: -apple-system, BlinkMacSystemFont, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
      --font-body: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", system-ui, sans-serif;
      --font-mono: "SF Mono", "JetBrains Mono", "IBM Plex Mono", ui-monospace, Menlo, monospace;
    }

    * { box-sizing: border-box; }

    html,
    body {
      height: 100%;
      overflow: hidden;
    }

    body {
      margin: 0;
      background:
        linear-gradient(180deg, oklch(99% 0.004 250), var(--bg) 42%),
        var(--bg);
      color: var(--fg);
      font-family: var(--font-body);
      font-size: 14px;
      line-height: 1.45;
      -webkit-font-smoothing: antialiased;
    }

    button,
    input,
    select {
      font: inherit;
    }

    button {
      cursor: pointer;
    }

    .app {
      height: 100vh;
      min-height: 100vh;
      display: grid;
      grid-template-rows: auto 1fr;
      overflow: hidden;
    }

    .topbar {
      min-height: 64px;
      display: grid;
      grid-template-columns: minmax(240px, 1fr) minmax(260px, 520px) auto;
      align-items: center;
      gap: 16px;
      padding: 12px 18px;
      border-bottom: 1px solid var(--border);
      background: color-mix(in oklch, var(--surface) 88%, transparent);
      backdrop-filter: blur(14px);
      position: sticky;
      top: 0;
      z-index: 20;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      min-width: 0;
    }

    .brand-mark {
      width: 36px;
      height: 36px;
      display: grid;
      place-items: center;
      border: 1px solid color-mix(in oklch, var(--accent) 40%, var(--border));
      border-radius: 8px;
      background:
        radial-gradient(circle at 60% 38%, var(--accent) 0 9%, transparent 10%),
        radial-gradient(circle at 35% 62%, var(--secondary) 0 8%, transparent 9%),
        var(--surface);
      box-shadow: inset 0 0 0 4px color-mix(in oklch, var(--accent-soft) 55%, transparent);
      flex: 0 0 auto;
    }

    .brand h1 {
      margin: 0;
      font-family: var(--font-display);
      font-size: 18px;
      line-height: 1.15;
      font-weight: 760;
    }

    .brand p {
      margin: 2px 0 0;
      color: var(--muted);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .command {
      height: 42px;
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 0 12px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      box-shadow: 0 1px 0 rgba(10, 20, 40, 0.03);
    }

    .command span {
      color: var(--muted);
      font-family: var(--font-mono);
      font-size: 12px;
    }

    .command input {
      width: 100%;
      min-width: 0;
      border: 0;
      outline: 0;
      background: transparent;
      color: var(--fg);
    }

    .command:focus-within,
    .field select:focus {
      border-color: color-mix(in oklch, var(--accent) 68%, var(--border));
      box-shadow: 0 0 0 3px color-mix(in oklch, var(--accent-soft) 70%, transparent);
    }

    .command kbd {
      border: 1px solid var(--border);
      border-bottom-color: color-mix(in oklch, var(--border) 70%, var(--fg));
      border-radius: 6px;
      padding: 3px 6px;
      color: var(--muted);
      background: var(--surface-2);
      font: 11px/1 var(--font-mono);
    }

    .top-actions,
    .graph-actions {
      display: flex;
      align-items: center;
      justify-content: flex-end;
      gap: 8px;
      flex-wrap: wrap;
    }

    .icon-button,
    .text-button,
    .item,
    .summary-card button {
      border: 1px solid transparent;
      border-radius: 7px;
      min-height: 34px;
      background: transparent;
      color: var(--muted);
    }

    .icon-button {
      width: 36px;
      display: grid;
      place-items: center;
      padding: 0;
      border-color: var(--border);
      background: var(--surface);
      color: var(--fg);
    }

    .text-button,
    .summary-card button {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 0 12px;
      border-color: var(--border);
      background: var(--surface);
      color: var(--fg);
      font-weight: 680;
    }

    .text-button.primary {
      background: var(--fg);
      color: #fff;
      border-color: var(--fg);
    }

    .clear-category[hidden] {
      display: none;
    }

    .workspace {
      display: grid;
      grid-template-columns: minmax(260px, 320px) minmax(0, 1fr) minmax(320px, 400px);
      min-height: 0;
      overflow: hidden;
    }

    aside,
    main {
      min-width: 0;
      min-height: 0;
    }

    .panel {
      min-width: 0;
      background: color-mix(in oklch, var(--surface) 92%, var(--bg));
      border-right: 1px solid var(--border);
      display: flex;
      flex-direction: column;
      overflow: hidden;
    }

    .details {
      border-left: 1px solid var(--border);
      border-right: 0;
    }

    .panel-scroll {
      min-height: 0;
      overflow: auto;
      padding: 16px;
    }

    .section-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin: 0 0 10px;
    }

    .section-title h2,
    .section-title h3 {
      margin: 0;
      font-size: 12px;
      line-height: 1.1;
      color: color-mix(in oklch, var(--fg) 76%, var(--muted));
      text-transform: uppercase;
      font-weight: 760;
    }

    .count,
    .view-state {
      color: var(--muted);
      font: 12px/1 var(--font-mono);
      white-space: nowrap;
    }

    .view-state {
      min-width: 42px;
      text-align: center;
    }

    .field {
      position: relative;
      margin-bottom: 12px;
    }

    .field label {
      display: block;
      margin: 0 0 5px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 650;
    }

    .field select {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      color: var(--fg);
      outline: 0;
      padding: 0 12px;
    }

    .metric-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 8px;
      margin: 14px 0 18px;
    }

    .metric {
      min-width: 0;
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 11px 12px;
    }

    .metric strong {
      display: block;
      font: 760 20px/1.1 var(--font-display);
      font-variant-numeric: tabular-nums;
    }

    .metric span {
      color: var(--muted);
      font-size: 12px;
    }

    .check {
      display: flex;
      align-items: center;
      gap: 8px;
      min-height: 34px;
      margin: 2px 0 16px;
      color: var(--fg);
      font-weight: 650;
    }

    .list {
      display: grid;
      gap: 9px;
    }

    .item {
      width: 100%;
      display: block;
      text-align: left;
      border-color: var(--border);
      background: var(--surface);
      padding: 10px;
      color: var(--fg);
      cursor: pointer;
    }

    .item:hover,
    .icon-button:hover,
    .text-button:hover,
    .summary-card button:hover {
      border-color: color-mix(in oklch, var(--secondary) 42%, var(--border));
      box-shadow: 0 6px 18px rgba(30, 65, 110, 0.08);
    }

    .item.selected {
      border-color: color-mix(in oklch, var(--accent) 54%, var(--border));
      box-shadow: inset 3px 0 0 var(--accent);
    }

    .item strong {
      display: block;
      font-weight: 760;
      line-height: 1.25;
      overflow-wrap: anywhere;
    }

    .item span {
      display: block;
      margin-top: 4px;
      color: var(--muted);
      font-size: 12px;
      overflow-wrap: anywhere;
    }

    .item .item-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-bottom: 7px;
    }

    .item .item-meta span {
      margin-top: 0;
    }

    .group {
      display: grid;
      gap: 9px;
    }

    .group + .group {
      margin-top: 14px;
    }

    .group-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
      font-weight: 760;
      text-transform: uppercase;
    }

    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 22px;
      padding: 0 7px;
      border-radius: 999px;
      background: var(--surface-2);
      color: var(--muted);
      font: 650 11px/1 var(--font-body);
    }

    .badge.file {
      color: var(--muted);
      background: var(--surface-3);
    }

    .badge.inferred,
    .badge.info {
      color: oklch(43% 0.15 255);
      background: var(--secondary-soft);
    }

    .badge.warning {
      color: oklch(45% 0.12 70);
      background: var(--warning-soft);
    }

    .badge.error {
      color: var(--danger);
      background: var(--danger-soft);
    }

    .summary-card {
      border: 1px solid var(--border);
      border-radius: 8px;
      background: var(--surface);
      padding: 12px;
    }

    .summary-card + .summary-card {
      margin-top: 12px;
    }

    .summary-card h2 {
      margin: 0 0 10px;
      color: color-mix(in oklch, var(--fg) 76%, var(--muted));
      font-size: 12px;
      text-transform: uppercase;
    }

    .summary-card p {
      margin: 8px 0;
    }

    .meta-grid {
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      gap: 10px;
      margin: 14px 0 18px;
      font-size: 13px;
    }

    .meta-grid dt {
      color: var(--muted);
      font-weight: 650;
    }

    .meta-grid dd {
      margin: 0;
      overflow-wrap: anywhere;
    }

    .description {
      color: var(--fg);
      line-height: 1.5;
    }

    details.raw-json {
      margin-top: 10px;
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }

    details.raw-json summary {
      cursor: pointer;
      padding: 10px 12px;
      color: var(--fg);
      font-weight: 760;
    }

    pre {
      margin: 0;
      padding: 12px;
      max-height: 240px;
      overflow: auto;
      border-top: 1px solid var(--border);
      background: var(--surface-2);
      color: var(--fg);
      white-space: pre-wrap;
      overflow-wrap: anywhere;
      font: 12px/1.55 var(--font-mono);
    }

    .graph-workspace {
      min-width: 0;
      min-height: 0;
      display: grid;
      grid-template-rows: auto minmax(0, 1fr) auto;
      container-type: inline-size;
      background:
        linear-gradient(var(--border) 1px, transparent 1px),
        linear-gradient(90deg, var(--border) 1px, transparent 1px),
        var(--surface-2);
      background-size: 34px 34px;
      background-position: -1px -1px;
      overflow: hidden;
    }

    .graph-toolbar {
      min-width: 0;
      min-height: 56px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
      border-bottom: 1px solid var(--border);
      background: color-mix(in oklch, var(--surface) 92%, transparent);
      backdrop-filter: blur(12px);
    }

    .graph-title {
      min-width: 0;
    }

    .graph-title h2 {
      margin: 0;
      font-size: 18px;
      line-height: 1.1;
    }

    .graph-title p {
      margin: 2px 0 0;
      color: var(--muted);
      font: 12px/1.35 var(--font-mono);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    #graph {
      display: block;
      min-width: 0;
      max-width: 100%;
      width: 100%;
      height: 100%;
      min-height: 420px;
      touch-action: none;
      filter: drop-shadow(0 14px 32px rgba(20, 30, 60, 0.08));
    }

    .graph-layer {
      transform-origin: 0 0;
    }

    .category-frame rect {
      pointer-events: none;
      stroke-width: .8;
      stroke-dasharray: 7 7;
      opacity: .72;
      vector-effect: non-scaling-stroke;
    }

    .category-frame.selected rect {
      stroke-width: 1.6;
      opacity: .9;
      stroke-dasharray: none;
    }

    .category-frame text {
      pointer-events: none;
      font: 700 12px/1 var(--font-mono);
      letter-spacing: 0;
      paint-order: stroke;
      stroke: color-mix(in oklch, var(--surface-2) 88%, transparent);
      stroke-width: 3px;
      stroke-linejoin: round;
    }

    .category-frame-hit {
      cursor: pointer;
      fill: none;
      pointer-events: stroke;
      stroke: transparent;
      stroke-width: 18;
      vector-effect: non-scaling-stroke;
    }

    .node {
      cursor: grab;
      transition: opacity 140ms ease;
    }

    .node:active {
      cursor: grabbing;
    }

    .node circle {
      fill: var(--surface);
      stroke: var(--secondary);
      stroke-width: 1.5;
      vector-effect: non-scaling-stroke;
    }

    .node:hover circle {
      fill: color-mix(in oklch, var(--secondary-soft) 55%, var(--surface));
    }

    .node.related circle {
      fill: color-mix(in oklch, var(--warning-soft) 70%, var(--surface));
      stroke: var(--relation);
    }

    .node.selected circle {
      fill: color-mix(in oklch, var(--accent-soft) 70%, var(--surface));
      stroke: var(--accent-strong);
      stroke-width: 2.5;
    }

    .node text {
      fill: var(--fg);
      font: 650 13px/1 var(--font-body);
      text-anchor: middle;
      paint-order: stroke;
      stroke: color-mix(in oklch, var(--surface) 88%, transparent);
      stroke-width: 3.5px;
      stroke-linejoin: round;
      pointer-events: none;
    }

    .edge {
      fill: none;
      stroke: var(--relation);
      stroke-width: 1.15;
      stroke-linecap: round;
      cursor: pointer;
      opacity: .48;
      vector-effect: non-scaling-stroke;
    }

    .edge.inferred {
      stroke: var(--secondary);
      stroke-dasharray: 8 4;
    }

    .edge.high {
      stroke-width: 1.25;
    }

    .edge.related {
      stroke: var(--accent-strong);
      stroke-width: 2.1;
      opacity: .78;
    }

    .edge.selected {
      stroke: var(--danger);
      stroke-width: 2.1;
      opacity: .84;
    }

    .edge-hit {
      fill: none;
      stroke: transparent;
      stroke-width: 18;
      cursor: pointer;
      pointer-events: stroke;
      vector-effect: non-scaling-stroke;
    }

    .graph-status {
      min-width: 0;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 10px 14px;
      border-top: 1px solid var(--border);
      background: color-mix(in oklch, var(--surface) 94%, transparent);
      color: var(--muted);
      font: 12px/1.35 var(--font-mono);
      overflow: hidden;
    }

    .status-points {
      min-width: 0;
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }

    #status {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    .dot {
      width: 8px;
      height: 8px;
      border-radius: 50%;
      background: var(--accent);
      display: inline-block;
      margin-right: 6px;
    }

    .dot.warn {
      background: var(--warning);
    }

    @media (max-width: 1180px) {
      html,
      body {
        height: auto;
        overflow: auto;
      }

      .topbar {
        grid-template-columns: 1fr auto;
      }

      .command {
        grid-column: 1 / -1;
      }

      .workspace {
        grid-template-columns: minmax(250px, 300px) minmax(0, 1fr);
        overflow: visible;
      }

      .details {
        grid-column: 1 / -1;
        border-left: 0;
        border-top: 1px solid var(--border);
      }
    }

    @media (max-width: 760px) {
      body {
        font-size: 13px;
      }

      .app {
        height: auto;
        overflow: visible;
      }

      .topbar {
        position: static;
        grid-template-columns: 1fr;
        gap: 10px;
        padding: 12px;
      }

      .brand p {
        white-space: normal;
      }

      .top-actions {
        justify-content: stretch;
        overflow-x: auto;
        padding-bottom: 2px;
      }

      .workspace {
        display: block;
      }

      .panel {
        border-right: 0;
        border-bottom: 1px solid var(--border);
        overflow: visible;
      }

      .panel-scroll {
        max-height: none;
        overflow: visible;
        padding: 14px;
      }

      .graph-workspace {
        min-height: 560px;
      }

      .graph-toolbar,
      .graph-status {
        align-items: flex-start;
        flex-direction: column;
      }

      .metric-grid {
        grid-template-columns: repeat(4, minmax(110px, 1fr));
        overflow-x: auto;
      }

      .meta-grid {
        grid-template-columns: 74px minmax(0, 1fr);
      }
    }
  </style>
</head>
<body>
  <div class="app">
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark" aria-hidden="true"></div>
        <div>
          <h1>SkillGraph Lite</h1>
          <p>Read-only skill topology viewer for local repositories</p>
        </div>
      </div>

      <label class="command" for="search">
        <span>Search</span>
        <input id="search" type="search" placeholder="id, name, path, diagnostics" autocomplete="off">
        <kbd>/</kbd>
      </label>

      <div class="top-actions" aria-label="Graph summary">
        <span class="count" id="summary"></span>
      </div>
    </header>

    <main class="workspace">
      <aside class="panel" aria-label="Filters and diagnostics">
        <div class="panel-scroll">
          <div class="section-title">
            <h2>Graph Scope</h2>
            <span class="count" id="scopeCount">0 nodes</span>
          </div>

          <div class="field">
            <label for="confidence">Confidence</label>
            <select id="confidence"><option value="">All</option></select>
          </div>

          <label class="check"><input id="categoryFrames" type="checkbox"> Category frames</label>

          <div class="metric-grid" aria-label="Graph metrics">
            <div class="metric">
              <strong id="metricNodes">0</strong>
              <span>nodes</span>
            </div>
            <div class="metric">
              <strong id="metricEdges">0</strong>
              <span>edges</span>
            </div>
            <div class="metric">
              <strong id="metricDiagnostics">0</strong>
              <span>diagnostics</span>
            </div>
            <div class="metric">
              <strong id="metricVisible">0</strong>
              <span>visible</span>
            </div>
          </div>

          <div class="section-title">
            <h3>Diagnostics Queue</h3>
            <span class="count" id="diagnosticCount">0</span>
          </div>
          <div id="diagnostics" class="list"></div>

          <div class="section-title" style="margin-top: 18px;">
            <h3>Matching Nodes</h3>
            <span class="count" id="nodeCount">0</span>
          </div>
          <div id="nodes" class="list"></div>
        </div>
      </aside>

      <section class="graph-workspace" aria-label="Skill graph">
        <div class="graph-toolbar">
          <div class="graph-title">
            <h2 id="graphHeading">Skill topology</h2>
            <p id="graphSubtitle">Generated graph</p>
          </div>
          <div class="graph-actions" aria-label="Canvas controls">
            <button id="zoomOut" class="icon-button" type="button" title="Zoom out">-</button>
            <span id="viewState" class="view-state">100%</span>
            <button id="zoomIn" class="icon-button" type="button" title="Zoom in">+</button>
            <button id="panUp" class="icon-button" type="button" title="Pan up">&uarr;</button>
            <button id="panLeft" class="icon-button" type="button" title="Pan left">&larr;</button>
            <button id="panRight" class="icon-button" type="button" title="Pan right">&rarr;</button>
            <button id="panDown" class="icon-button" type="button" title="Pan down">&darr;</button>
            <button id="resetView" class="text-button" type="button">Reset</button>
            <button id="resetLayout" class="text-button" type="button">Layout</button>
          </div>
        </div>

        <svg id="graph" role="img" aria-labelledby="graphHeading graphSubtitle"></svg>

        <footer class="graph-status">
          <div class="status-points">
            <span><i class="dot"></i><strong id="activeSelection">No selection</strong></span>
            <button id="clearCategory" class="text-button clear-category" type="button" hidden>Clear category</button>
            <span><i class="dot warn"></i><span id="activeDiagnostics">Diagnostics are scoped by selection</span></span>
          </div>
          <span id="status"></span>
        </footer>
      </section>

      <aside class="panel details" aria-label="Selected details">
        <div class="panel-scroll">
          <div class="section-title">
            <h2>Details</h2>
          </div>
          <div id="details">
            <section class="summary-card">
              <h2>Selection</h2>
              <p class="description">Select a node, edge, or diagnostic to inspect its metadata.</p>
            </section>
          </div>
        </div>
      </aside>
    </main>
  </div>
  <script>
    let graph = __GRAPH_JSON__;
    if (!graph) {
      const request = new XMLHttpRequest();
      request.open("GET", "/graph.json", false);
      request.send(null);
      graph = JSON.parse(request.responseText);
    }
    const categoryFramesDefault = true;
    const state = {
      selected: null,
      positions: {},
      layoutKey: "",
      layoutBounds: { width: 0, height: 0 },
      pendingViewFit: true,
      selectedCategory: "",
      dragging: null,
      panning: null,
      view: { x: 0, y: 0, scale: 1 },
    };
    const nodeIndex = new Map(graph.nodes.map(node => [node.id, node]));
    const graphDisplayNodes = graph.nodes;
    const graphDisplayNodeIds = new Set(graphDisplayNodes.map(node => node.id));
    const graphDisplayEdges = graph.edges.filter(edgeConnectsDisplayNodes);
    const confidence = document.getElementById("confidence");
    const search = document.getElementById("search");
    const categoryFrames = document.getElementById("categoryFrames");
    const clearCategory = document.getElementById("clearCategory");
    const resetLayout = document.getElementById("resetLayout");
    const resetView = document.getElementById("resetView");
    const zoomIn = document.getElementById("zoomIn");
    const zoomOut = document.getElementById("zoomOut");
    const panUp = document.getElementById("panUp");
    const panDown = document.getElementById("panDown");
    const panLeft = document.getElementById("panLeft");
    const panRight = document.getElementById("panRight");
    categoryFrames.checked = categoryFramesDefault;

    for (const value of [...new Set(graphDisplayEdges.map(edge => edge.confidence))].sort()) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      confidence.append(option);
    }

    function edgeConnectsDisplayNodes(edge) {
      return graphDisplayNodeIds.has(edge.source) && graphDisplayNodeIds.has(edge.target);
    }

    function nodePassesControls(node, keepSelected = false) {
      if (keepSelected && state.selected?.kind === "node" && node.id === state.selected.value.id) return true;
      const q = search.value.trim().toLowerCase();
      if (!q) return true;
      const annotation = node.annotation || {};
      return [node.id, node.label, node.path, node.description, annotation.label, annotation.summary, annotation.suggestedCategory, annotation.clusterId, ...(annotation.roleTags || []), ...(node.aliases || [])]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(q));
    }

    function edgePassesControls(edge) {
      if (!edgeConnectsDisplayNodes(edge)) return false;
      if (confidence.value && String(edge.confidence) !== confidence.value) return false;
      return true;
    }

    function visibleNodes() {
      if (state.selected?.kind === "node") {
        const selectedId = state.selected.value.id;
        const relatedIds = new Set([selectedId]);
        for (const edge of graph.edges) {
          if (!edgePassesControls(edge)) continue;
          if (edge.source === selectedId || edge.target === selectedId) {
            relatedIds.add(edge.source);
            relatedIds.add(edge.target);
          }
        }
        return graphDisplayNodes.filter(node => relatedIds.has(node.id) && nodePassesControls(node, true));
      }
      return graphDisplayNodes.filter(node => nodePassesControls(node));
    }

    function visibleEdges(nodes) {
      const nodeIds = new Set(nodes.map(node => node.id));
      return graph.edges.filter(edge => {
        if (!edgePassesControls(edge)) return false;
        if (state.selected?.kind === "node") {
          const selectedId = state.selected.value.id;
          if (edge.source !== selectedId && edge.target !== selectedId) return false;
        }
        if (state.selected?.kind === "edge" && edge.id === state.selected.value.id) return true;
        return nodeIds.has(edge.source) && nodeIds.has(edge.target);
      });
    }

    function diagnosticMatchesNode(diag, node) {
      if (!node) return true;
      const id = node.id;
      const paths = [node.path, node.dir].filter(Boolean);
      if (diag.skill === id || diag.target === id) return true;
      if (paths.some(path => diag.path === path || String(diag.path || "").startsWith(`${path}/`))) return true;
      const evidenceSkills = diag.evidence && Array.isArray(diag.evidence.skills) ? diag.evidence.skills : [];
      return evidenceSkills.includes(id);
    }

    function visibleDiagnostics() {
      if (state.selected?.kind !== "node") return graph.diagnostics;
      return graph.diagnostics.filter(diag => diagnosticMatchesNode(diag, state.selected.value));
    }

    function selectionKey(kind, value) {
      if (!value) return "";
      if (kind === "node" || kind === "edge") return value.id || "";
      return [value.type, value.path, value.skill, value.target, value.message].filter(Boolean).join("|");
    }

    function nodeDisplay(id) {
      const node = nodeIndex.get(id);
      return node?.annotation?.label || node?.label || id;
    }

    function nodePath(node) {
      return node.path || node.id || "";
    }

    function nodeRadius(node) {
      return 26;
    }

    function firstNonBlank(values, fallback) {
      for (const value of values) {
        const text = String(value || "").trim();
        if (text) return text;
      }
      return fallback;
    }

    function categoryKeyForNode(node) {
      const annotation = node.annotation || {};
      return firstNonBlank(
        [annotation.suggestedCategory, annotation.clusterId, node.category],
        "Uncategorized",
      );
    }

    function toggleCategorySelection(key) {
      state.selectedCategory = state.selectedCategory === key ? "" : key;
      state.selected = null;
      draw();
    }

    function clearCategorySelection() {
      if (!state.selectedCategory) return false;
      state.selectedCategory = "";
      draw();
      return true;
    }

    function categoryColor(key) {
      let hash = 0;
      for (const char of String(key)) {
        hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
      }
      const hue = Math.abs(hash) % 360;
      return {
        fill: `hsl(${hue} 70% 95% / 0.62)`,
        stroke: `hsl(${hue} 54% 44% / 0.72)`,
        text: `hsl(${hue} 44% 28%)`,
      };
    }

    function compactCategoryLabel(key, count, frameWidth) {
      const full = `${key} (${count})`;
      const maxChars = Math.max(10, Math.floor((frameWidth * state.view.scale - 12) / 5));
      if (full.length <= maxChars || state.view.scale >= .55) return full;
      const suffix = ` (${count})`;
      const available = Math.max(4, maxChars - suffix.length - 3);
      return `${String(key).slice(0, available).trim()}...${suffix}`;
    }

    function categoryFrameGroups(nodes, positions) {
      const groups = groupBy(nodes, categoryKeyForNode);
      const frames = [];
      for (const [key, values] of groups) {
        let minX = Infinity;
        let minY = Infinity;
        let maxX = -Infinity;
        let maxY = -Infinity;
        let count = 0;
        for (const node of values) {
          const point = positions.get(node.id);
          if (!point) continue;
          const radius = nodeRadius(node);
          const labelWidth = Math.min(180, Math.max(70, String(node.annotation?.label || node.label || node.id).length * 7));
          minX = Math.min(minX, point.x - Math.max(radius + 34, labelWidth / 2));
          maxX = Math.max(maxX, point.x + Math.max(radius + 34, labelWidth / 2));
          minY = Math.min(minY, point.y - radius - 36);
          maxY = Math.max(maxY, point.y + radius + 52);
          count += 1;
        }
        if (!count) continue;
        const padding = 24;
        const minWidth = 140;
        const minHeight = 106;
        let x = minX - padding;
        let y = minY - padding;
        let width = maxX - minX + padding * 2;
        let height = maxY - minY + padding * 2;
        if (width < minWidth) {
          x -= (minWidth - width) / 2;
          width = minWidth;
        }
        if (height < minHeight) {
          y -= (minHeight - height) / 2;
          height = minHeight;
        }
        frames.push({ key, count, x, y, width, height });
      }
      return frames.sort((a, b) => (b.width * b.height) - (a.width * a.height));
    }

    function layoutGroupForNode(node) {
      return categoryKeyForNode(node);
    }

    function sortedLayoutGroups(nodes) {
      return [...groupBy(nodes, layoutGroupForNode).entries()]
        .sort((a, b) => b[1].length - a[1].length || String(a[0]).localeCompare(String(b[0])));
    }

    function layoutGroupBoxes(nodes, width, height) {
      const groups = sortedLayoutGroups(nodes);
      const columns = Math.max(1, Math.ceil(Math.sqrt(groups.length)));
      const rows = Math.max(1, Math.ceil(groups.length / columns));
      const marginX = Math.min(120, Math.max(72, width * .06));
      const marginY = Math.min(120, Math.max(82, height * .07));
      const cellWidth = (width - marginX * 2) / columns;
      const cellHeight = (height - marginY * 2) / rows;
      const boxes = new Map();
      groups.forEach(([key, values], index) => {
        const col = index % columns;
        const row = Math.floor(index / columns);
        boxes.set(key, {
          key,
          values,
          x: marginX + col * cellWidth,
          y: marginY + row * cellHeight,
          width: cellWidth,
          height: cellHeight,
          centerX: marginX + col * cellWidth + cellWidth / 2,
          centerY: marginY + row * cellHeight + cellHeight / 2,
        });
      });
      return boxes;
    }

    function nodeAnchorMap(nodes, width, height) {
      const boxes = layoutGroupBoxes(nodes, width, height);
      const anchors = new Map();
      for (const box of boxes.values()) {
        const values = box.values;
        const columns = Math.max(1, Math.ceil(Math.sqrt(values.length)));
        const rows = Math.max(1, Math.ceil(values.length / columns));
        const innerX = Math.min(70, Math.max(40, box.width * .12));
        const innerY = Math.min(70, Math.max(44, box.height * .14));
        const usableWidth = Math.max(80, box.width - innerX * 2);
        const usableHeight = Math.max(80, box.height - innerY * 2);
        values.forEach((node, index) => {
          const col = index % columns;
          const row = Math.floor(index / columns);
          anchors.set(node.id, {
            x: box.x + innerX + ((col + 1) / (columns + 1)) * usableWidth,
            y: box.y + innerY + ((row + 1) / (rows + 1)) * usableHeight,
            groupX: box.centerX,
            groupY: box.centerY,
          });
        });
      }
      return anchors;
    }

    function drawCategoryFrames(layer, nodes, positions) {
      if (!categoryFrames.checked) return;
      const frameLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
      frameLayer.setAttribute("class", "category-frame-layer");
      for (const frame of categoryFrameGroups(nodes, positions)) {
        const colors = categoryColor(frame.key);
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const isSelected = state.selectedCategory === frame.key;
        group.setAttribute("class", `category-frame${isSelected ? " selected" : ""}`);

        const rect = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        rect.setAttribute("x", String(frame.x));
        rect.setAttribute("y", String(frame.y));
        rect.setAttribute("width", String(frame.width));
        rect.setAttribute("height", String(frame.height));
        rect.setAttribute("rx", "8");
        rect.setAttribute("fill", colors.fill);
        rect.setAttribute("stroke", colors.stroke);

        const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
        label.setAttribute("x", String(frame.x + 14));
        label.setAttribute("y", String(frame.y + 22));
        label.setAttribute("fill", colors.text);
        label.style.fontSize = `${Math.min(34, Math.max(10, 10 / state.view.scale))}px`;
        label.textContent = compactCategoryLabel(frame.key, frame.count, frame.width);

        group.append(rect, label);
        frameLayer.append(group);
      }
      layer.append(frameLayer);
    }

    function drawCategoryFrameHits(layer, nodes, positions) {
      if (!categoryFrames.checked) return;
      const hitLayer = document.createElementNS("http://www.w3.org/2000/svg", "g");
      hitLayer.setAttribute("class", "category-frame-hit-layer");
      for (const frame of categoryFrameGroups(nodes, positions)) {
        const hit = document.createElementNS("http://www.w3.org/2000/svg", "rect");
        hit.setAttribute("class", "category-frame-hit");
        hit.setAttribute("x", String(frame.x));
        hit.setAttribute("y", String(frame.y));
        hit.setAttribute("width", String(frame.width));
        hit.setAttribute("height", String(frame.height));
        hit.setAttribute("rx", "8");
        hit.addEventListener("click", event => {
          event.stopPropagation();
          toggleCategorySelection(frame.key);
        });
        hitLayer.append(hit);
      }
      layer.append(hitLayer);
    }

    function edgeMarker(isSelected, isRelated) {
      if (isSelected) return "url(#arrow-selected)";
      if (isRelated) return "url(#arrow-related)";
      return "url(#arrow-default)";
    }

    function setText(id, value) {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    }

    function edgeSummary(edge) {
      return `${nodeDisplay(edge.source)} -> ${nodeDisplay(edge.target)}`;
    }

    function diagnosticLabel(type) {
      return String(type || "diagnostic").replaceAll("_", " ");
    }

    function groupBy(values, keyFn) {
      const groups = new Map();
      for (const value of values) {
        const key = keyFn(value);
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(value);
      }
      return groups;
    }

    function clampZoom(scale) {
      return Math.min(3, Math.max(.18, scale));
    }

    function viewTransform() {
      return `translate(${state.view.x}, ${state.view.y}) scale(${state.view.scale})`;
    }

    function resetGraphViewState() {
      state.pendingViewFit = true;
    }

    function fitGraphView(bounds, viewportWidth, viewportHeight) {
      const padding = 36;
      const scale = clampZoom(Math.min(
        1,
        (viewportWidth - padding * 2) / Math.max(1, bounds.width),
        (viewportHeight - padding * 2) / Math.max(1, bounds.height),
      ));
      return {
        x: (viewportWidth - bounds.width * scale) / 2,
        y: (viewportHeight - bounds.height * scale) / 2,
        scale,
      };
    }

    function zoomGraphAt(origin, nextScale) {
      const scale = clampZoom(nextScale);
      const graphX = (origin.x - state.view.x) / state.view.scale;
      const graphY = (origin.y - state.view.y) / state.view.scale;
      state.view = {
        x: origin.x - graphX * scale,
        y: origin.y - graphY * scale,
        scale,
      };
      draw();
    }

    function zoomGraphBy(factor) {
      const svg = document.getElementById("graph");
      zoomGraphAt(
        { x: svg.clientWidth / 2, y: svg.clientHeight / 2 },
        state.view.scale * factor,
      );
    }

    function panGraphBy(dx, dy) {
      state.view = { ...state.view, x: state.view.x + dx, y: state.view.y + dy };
      draw();
    }

    function layoutBoundsFor(nodes, viewportWidth, viewportHeight) {
      const count = Math.max(1, nodes.length);
      const groups = sortedLayoutGroups(nodes);
      const groupCount = Math.max(1, groups.length);
      const groupColumns = Math.max(1, Math.ceil(Math.sqrt(groupCount)));
      const groupRows = Math.max(1, Math.ceil(groupCount / groupColumns));
      const maxGroupSize = Math.max(1, ...groups.map(([, values]) => values.length));
      const localColumns = Math.max(1, Math.ceil(Math.sqrt(maxGroupSize)));
      const localRows = Math.max(1, Math.ceil(maxGroupSize / localColumns));
      const cellWidth = Math.max(count >= 36 ? 520 : 420, localColumns * 170);
      const cellHeight = Math.max(count >= 36 ? 420 : 330, localRows * 145);
      return {
        width: Math.max(viewportWidth, 180 + groupColumns * cellWidth),
        height: Math.max(viewportHeight, 190 + groupRows * cellHeight),
      };
    }

    function layoutKey(nodes, edges, bounds) {
      return [
        Math.round(bounds.width),
        Math.round(bounds.height),
        nodes.map(node => node.id).join("|"),
        edges.map(edge => edge.id).join("|"),
      ].join("::");
    }

    function initialPosition(index, count, width, height) {
      const columns = Math.max(1, Math.ceil(Math.sqrt(count)));
      const rows = Math.max(1, Math.ceil(count / columns));
      const col = index % columns;
      const row = Math.floor(index / columns);
      const x = ((col + 1) / (columns + 1)) * width;
      const y = 72 + ((row + 1) / (rows + 1)) * Math.max(220, height - 144);
      return clampPosition({ x, y }, width, height);
    }

    function clampPosition(point, width, height) {
      const padding = 42;
      return {
        x: Math.min(width - padding, Math.max(padding, point.x)),
        y: Math.min(height - padding, Math.max(padding, point.y)),
      };
    }

    function ensureLayout(nodes, edges, viewportWidth, viewportHeight) {
      const bounds = layoutBoundsFor(nodes, viewportWidth, viewportHeight);
      const anchors = nodeAnchorMap(nodes, bounds.width, bounds.height);
      nodes.forEach((node, index) => {
        if (!state.positions[node.id]) {
          state.positions[node.id] = clampPosition(anchors.get(node.id) || initialPosition(index, nodes.length, bounds.width, bounds.height), bounds.width, bounds.height);
        }
      });
      const key = layoutKey(nodes, edges, bounds);
      if (state.layoutKey !== key && !state.dragging) {
        runForceLayout(nodes, edges, bounds.width, bounds.height);
        state.layoutKey = key;
        state.layoutBounds = bounds;
      }
      if (state.pendingViewFit) {
        state.view = fitGraphView(bounds, viewportWidth, viewportHeight);
        state.pendingViewFit = false;
      }
      return new Map(nodes.map(node => [node.id, state.positions[node.id]]));
    }

    function runForceLayout(nodes, edges, width, height) {
      const ids = new Set(nodes.map(node => node.id));
      const anchors = nodeAnchorMap(nodes, width, height);
      const centerX = width / 2;
      const centerY = height / 2;
      const visibleEdges = edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
      const edgeDistance = Math.min(300, 150 + Math.sqrt(Math.max(1, nodes.length)) * 18);
      for (let step = 0; step < 120; step += 1) {
        const velocity = new Map(nodes.map(node => [node.id, { x: 0, y: 0 }]));
        for (let i = 0; i < nodes.length; i += 1) {
          for (let j = i + 1; j < nodes.length; j += 1) {
            const a = state.positions[nodes[i].id];
            const b = state.positions[nodes[j].id];
            const dx = a.x - b.x || .01;
            const dy = a.y - b.y || .01;
            const distance = Math.max(24, Math.hypot(dx, dy));
            const force = Math.min(120, 9800 / (distance * distance));
            const fx = (dx / distance) * force;
            const fy = (dy / distance) * force;
            velocity.get(nodes[i].id).x += fx;
            velocity.get(nodes[i].id).y += fy;
            velocity.get(nodes[j].id).x -= fx;
            velocity.get(nodes[j].id).y -= fy;
          }
        }
        for (const edge of visibleEdges) {
          const source = state.positions[edge.source];
          const target = state.positions[edge.target];
          const dx = target.x - source.x || .01;
          const dy = target.y - source.y || .01;
          const distance = Math.max(1, Math.hypot(dx, dy));
          const desired = edgeDistance;
          const force = (distance - desired) * .014;
          const fx = (dx / distance) * force;
          const fy = (dy / distance) * force;
          velocity.get(edge.source).x += fx;
          velocity.get(edge.source).y += fy;
          velocity.get(edge.target).x -= fx;
          velocity.get(edge.target).y -= fy;
        }
        for (const node of nodes) {
          const point = state.positions[node.id];
          const v = velocity.get(node.id);
          const anchor = anchors.get(node.id) || { x: centerX, y: centerY, groupX: centerX, groupY: centerY };
          const anchorForce = .024;
          v.x += (anchor.x - point.x) * anchorForce + (anchor.groupX - point.x) * .004;
          v.y += (anchor.y - point.y) * anchorForce + (anchor.groupY - point.y) * .004;
          state.positions[node.id] = clampPosition(
            {
              x: point.x + Math.max(-16, Math.min(16, v.x)),
              y: point.y + Math.max(-16, Math.min(16, v.y)),
            },
            width,
            height,
          );
        }
      }
    }

    function edgePath(source, target, index = 0, sourceRadius = 18, targetRadius = 18) {
      return edgeGeometry(source, target, index, sourceRadius, targetRadius).path;
    }

    function edgeGeometry(source, target, index = 0, sourceRadius = 18, targetRadius = 18) {
      const dx = target.x - source.x;
      const dy = target.y - source.y;
      const distance = Math.max(1, Math.hypot(dx, dy));
      const available = Math.max(0, distance - 12);
      const sourceOffset = Math.min(sourceRadius + 8, available / 2);
      const targetOffset = Math.min(targetRadius + 16, available / 2);
      const start = {
        x: source.x + (dx / distance) * sourceOffset,
        y: source.y + (dy / distance) * sourceOffset,
      };
      const end = {
        x: target.x - (dx / distance) * targetOffset,
        y: target.y - (dy / distance) * targetOffset,
      };
      const curve = ((index % 5) - 2) * 16;
      const mx = (start.x + end.x) / 2 - (dy / distance) * curve;
      const my = (start.y + end.y) / 2 + (dx / distance) * curve;
      return {
        path: `M ${start.x} ${start.y} Q ${mx} ${my} ${end.x} ${end.y}`,
      };
    }

    function draw() {
      const svg = document.getElementById("graph");
      const width = Math.max(svg.clientWidth, 360);
      const height = Math.max(svg.clientHeight, 420);
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.innerHTML = `<defs>
        <marker id="arrow-default" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,8 L8,4 z" fill="#d87537"></path></marker>
        <marker id="arrow-related" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,8 L8,4 z" fill="#2f9860"></path></marker>
        <marker id="arrow-selected" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,8 L8,4 z" fill="#c63f33"></path></marker>
      </defs>`;
      const layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
      layer.setAttribute("class", "graph-layer");
      svg.append(layer);
      const nodes = visibleNodes();
      const edges = visibleEdges(nodes);
      const positions = ensureLayout(nodes, edges, width, height);
      layer.setAttribute("transform", viewTransform());
      const showNodeText = state.view.scale >= .42 || nodes.length <= 18;
      const selected = state.selected;
      const selectedKey = selected?.key || "";
      const selectedNodeId = selected?.kind === "node" ? selected.value.id : "";
      const selectedEdge = selected?.kind === "edge" ? selected.value : null;
      const selectedEdgeNodes = selectedEdge ? new Set([selectedEdge.source, selectedEdge.target]) : new Set();
      drawCategoryFrames(layer, nodes, positions);
      const edgeOccurrences = new Map();
      for (const edge of edges) {
        const source = positions.get(edge.source);
        const target = positions.get(edge.target);
        if (!source || !target) continue;
        const occurrenceKey = `${edge.source}->${edge.target}`;
        const occurrenceIndex = edgeOccurrences.get(occurrenceKey) || 0;
        edgeOccurrences.set(occurrenceKey, occurrenceIndex + 1);
        const sourceNode = nodeIndex.get(edge.source);
        const targetNode = nodeIndex.get(edge.target);
        const geometry = edgeGeometry(source, target, occurrenceIndex, nodeRadius(sourceNode), nodeRadius(targetNode));
        const pathData = geometry.path;
        const isSelected = selected?.kind === "edge" && edge.id === selectedKey;
        const isRelated = selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const edgeClass = `edge ${edgeConfidenceClass(edge)}${edge.inferred ? " inferred" : ""}${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`;
        const hitPath = document.createElementNS("http://www.w3.org/2000/svg", "path");
        hitPath.setAttribute("d", pathData);
        hitPath.setAttribute("class", "edge-hit");
        hitPath.addEventListener("click", () => select(edge, "edge"));
        layer.append(hitPath);
        const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
        path.setAttribute("d", pathData);
        path.setAttribute("class", edgeClass);
        path.setAttribute("marker-end", edgeMarker(isSelected, isRelated));
        path.addEventListener("click", () => select(edge, "edge"));
        layer.append(path);
      }
      drawCategoryFrameHits(layer, nodes, positions);
      for (const node of nodes) {
        const point = positions.get(node.id);
        if (!point) continue;
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const isSelected = selected?.kind === "node" && node.id === selectedKey;
        const isRelated = selectedEdgeNodes.has(node.id);
        group.setAttribute("class", `node${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`);
        group.setAttribute("transform", `translate(${point.x}, ${point.y})`);
        group.addEventListener("pointerdown", event => beginNodeDrag(event, node));
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        const radius = nodeRadius(node);
        circle.setAttribute("r", String(radius));
        const labelText = document.createElementNS("http://www.w3.org/2000/svg", "text");
        labelText.setAttribute("text-anchor", "middle");
        labelText.setAttribute("y", String(radius + 30));
        labelText.textContent = node.annotation?.label || node.label || node.id;
        group.append(circle);
        if (showNodeText || isSelected || isRelated) {
          group.append(labelText);
        }
        layer.append(group);
      }
      setText("graphHeading", "Skill graph");
      setText("graphSubtitle", `Generated ${graph.generatedAt} from ${graph.root}`);
      setText("summary", `${nodes.length} nodes / ${edges.length} edges`);
      setText("viewState", `${Math.round(state.view.scale * 100)}%`);
      setText("status", "Semantic skill relations and categories");
      setText("scopeCount", `${nodes.length} nodes`);
      setText("metricNodes", String(graphDisplayNodes.length));
      setText("metricEdges", String(graphDisplayEdges.length));
      setText("metricDiagnostics", String(graph.diagnostics.length));
      setText("metricVisible", String(nodes.length));
      const activeLabel = selected?.kind === "node"
        ? nodeDisplay(selected.value.id)
        : selected?.kind === "edge"
          ? edgeSummary(selected.value)
          : selected?.kind === "diagnostic"
            ? diagnosticLabel(selected.value.type)
            : state.selectedCategory
              ? `Category: ${state.selectedCategory}`
              : "No selection";
      setText("activeSelection", activeLabel);
      clearCategory.hidden = !state.selectedCategory;
      const scopedDiagnostics = visibleDiagnostics();
      setText("activeDiagnostics", scopedDiagnostics.length ? `${scopedDiagnostics.length} diagnostics in scope` : "No diagnostics in scope");
      renderLists(nodes);
    }

    function nodeItemHtml(node) {
      const annotation = node.annotation || {};
      const description = annotation.summary || node.description ? `<span>${escapeHtml(annotation.summary || node.description)}</span>` : "";
      const roleTags = Array.isArray(annotation.roleTags) ? annotation.roleTags : [];
      return `
        <div class="item-meta">
          ${annotation.suggestedCategory ? `<span class="badge inferred">${escapeHtml(annotation.suggestedCategory)}</span>` : node.category ? `<span class="badge">${escapeHtml(node.category)}</span>` : ""}
          ${annotation.clusterId ? `<span class="badge inferred">${escapeHtml(annotation.clusterId)}</span>` : ""}
        </div>
        <strong>${escapeHtml(annotation.label || node.label || node.id)}</strong>
        <span>${escapeHtml(nodePath(node))}</span>
        ${roleTags.length ? `<span>${roleTags.map(escapeHtml).join(", ")}</span>` : ""}
        ${description}
      `;
    }

    function edgeConfidenceClass(edge) {
      return String(edge.confidence || "").replace(/[^A-Za-z0-9_-]+/g, "-");
    }

    function diagnosticItemHtml(diag) {
      const severity = diag.severity || "info";
      const where = diag.skill || diag.path || diag.target || "";
      return `
        <div class="item-meta">
          <span class="badge ${escapeHtml(severity)}">${escapeHtml(severity)}</span>
          <span class="badge file">${escapeHtml(diagnosticLabel(diag.type))}</span>
        </div>
        <strong>${escapeHtml(diag.message || diag.type)}</strong>
        ${where ? `<span>${escapeHtml(where)}</span>` : ""}
      `;
    }

    function appendGroup(container, title, items, renderItem) {
      const group = document.createElement("section");
      group.className = "group";
      group.innerHTML = `<div class="group-title"><span>${escapeHtml(title)}</span><span>${items.length}</span></div>`;
      for (const itemValue of items) {
        group.append(renderItem(itemValue));
      }
      container.append(group);
    }

    function renderNodeGroups(nodeBox, nodes) {
      const groups = groupBy(nodes, categoryKeyForNode);
      for (const [title, values] of groups) {
        appendGroup(nodeBox, title, values, node => {
          const item = document.createElement("div");
          item.className = `item ${state.selected?.kind === "node" && node.id === state.selected.key ? "selected" : ""}`;
          item.innerHTML = nodeItemHtml(node);
          item.addEventListener("click", () => select(node, "node"));
          return item;
        });
      }
    }

    function renderLists(nodes) {
      const nodeBox = document.getElementById("nodes");
      nodeBox.innerHTML = "";
      renderNodeGroups(nodeBox, nodes);
      renderDiagnostics();
      setText("nodeCount", String(nodes.length));
    }

    function renderDiagnostics() {
      const box = document.getElementById("diagnostics");
      box.innerHTML = "";
      const diagnostics = visibleDiagnostics();
      setText("diagnosticCount", String(diagnostics.length));
      for (const [title, values] of groupBy(diagnostics, diag => `${diag.severity || "info"} / ${diagnosticLabel(diag.type)}`)) {
        appendGroup(box, title, values, diag => {
          const item = document.createElement("div");
          item.className = `item ${state.selected?.kind === "diagnostic" && selectionKey("diagnostic", diag) === state.selected.key ? "selected" : ""}`;
          item.innerHTML = diagnosticItemHtml(diag);
          item.addEventListener("click", () => select(diag, "diagnostic"));
          return item;
        });
      }
    }

    function select(value, kind) {
      state.selected = { kind, value, key: selectionKey(kind, value) };
      const details = document.getElementById("details");
      details.innerHTML = renderDetails(value, kind);
      document.getElementById("clearSelection").addEventListener("click", clearSelection);
      draw();
    }

    function renderDetails(value, kind) {
      if (kind === "node") return renderNodeDetails(value);
      if (kind === "edge") return renderEdgeDetails(value);
      return renderDiagnosticDetails(value);
    }

    function renderNodeDetails(node) {
      const outgoing = graph.edges.filter(edge => edge.source === node.id);
      const incoming = graph.edges.filter(edge => edge.target === node.id);
      const annotation = node.annotation || {};
      const roleTags = Array.isArray(annotation.roleTags) ? annotation.roleTags : [];
      const triggers = Array.isArray(annotation.triggerPhrases) ? annotation.triggerPhrases : [];
      return `
        <section class="summary-card">
          <h2>Node</h2>
          <div class="item-meta">
            ${annotation.suggestedCategory ? `<span class="badge inferred">${escapeHtml(annotation.suggestedCategory)}</span>` : node.category ? `<span class="badge">${escapeHtml(node.category)}</span>` : ""}
            ${annotation.clusterId ? `<span class="badge inferred">${escapeHtml(annotation.clusterId)}</span>` : ""}
          </div>
          <p><strong>${escapeHtml(annotation.label || node.label || node.id)}</strong></p>
          ${annotation.summary ? `<p class="description">${escapeHtml(annotation.summary)}</p>` : node.description ? `<p class="description">${escapeHtml(node.description)}</p>` : ""}
          <dl class="meta-grid">
            <dt>ID</dt><dd>${escapeHtml(node.id)}</dd>
            <dt>Path</dt><dd>${escapeHtml(nodePath(node))}</dd>
            <dt>Outgoing</dt><dd>${outgoing.length} dependencies</dd>
            <dt>Incoming</dt><dd>${incoming.length} dependents</dd>
            ${roleTags.length ? `<dt>Role tags</dt><dd>${roleTags.map(escapeHtml).join(", ")}</dd>` : ""}
            ${triggers.length ? `<dt>Trigger phrases</dt><dd>${triggers.map(escapeHtml).join(", ")}</dd>` : ""}
            ${node.aliases?.length ? `<dt>Aliases</dt><dd>${node.aliases.map(escapeHtml).join(", ")}</dd>` : ""}
          </dl>
          <button id="clearSelection" type="button">Clear selection</button>
          ${rawJson(node)}
        </section>
      `;
    }

    function edgeDetailsJson(edge) {
      const { id, type, ...payload } = edge;
      return rawJson(payload);
    }

    function renderEdgeDetails(edge) {
      return `
        <section class="summary-card">
          <h2>Edge</h2>
          <div class="item-meta">
            ${edge.confidence ? `<span class="badge">${escapeHtml(edge.confidence)}</span>` : ""}
          </div>
          <p><strong>${escapeHtml(edgeSummary(edge))}</strong></p>
          <dl class="meta-grid">
            <dt>From</dt><dd>${escapeHtml(nodeDisplay(edge.source))}<br>${escapeHtml(edge.source)}</dd>
            <dt>To</dt><dd>${escapeHtml(nodeDisplay(edge.target))}<br>${escapeHtml(edge.target)}</dd>
            <dt>Origin</dt><dd>${escapeHtml(edge.origin || "inferred")}</dd>
            ${edge.rationale ? `<dt>Rationale</dt><dd>${escapeHtml(edge.rationale)}</dd>` : ""}
            <dt>Evidence</dt><dd>${escapeHtml((edge.evidence || []).map(item => item.path || item.text).filter(Boolean).join(", ") || "N/A")}</dd>
          </dl>
          <button id="clearSelection" type="button">Clear selection</button>
          ${edgeDetailsJson(edge)}
        </section>
      `;
    }

    function renderDiagnosticDetails(diag) {
      const severity = diag.severity || "info";
      return `
        <section class="summary-card">
          <h2>Diagnostic</h2>
          <div class="item-meta">
            <span class="badge ${escapeHtml(severity)}">${escapeHtml(severity)}</span>
            <span class="badge file">${escapeHtml(diagnosticLabel(diag.type))}</span>
          </div>
          <p class="description">${escapeHtml(diag.message || "")}</p>
          <dl class="meta-grid">
            ${diag.skill ? `<dt>Skill</dt><dd>${escapeHtml(diag.skill)}</dd>` : ""}
            ${diag.path ? `<dt>Path</dt><dd>${escapeHtml(diag.path)}</dd>` : ""}
            ${diag.target ? `<dt>Target</dt><dd>${escapeHtml(diag.target)}</dd>` : ""}
          </dl>
          <button id="clearSelection" type="button">Clear selection</button>
          ${rawJson(diag)}
        </section>
      `;
    }

    function rawJson(value) {
      return `<details class="raw-json"><summary>Raw JSON</summary><pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre></details>`;
    }

    function clearSelection() {
      state.selected = null;
      document.getElementById("details").innerHTML = `
        <section class="summary-card">
          <h2>Selection</h2>
          <p class="description">Select a node, edge, or diagnostic to inspect its metadata.</p>
        </section>
      `;
      draw();
    }

    function resetGraphLayout() {
      state.positions = {};
      state.layoutKey = "";
      resetGraphViewState();
      draw();
    }

    function svgPoint(svg, event) {
      const point = svg.createSVGPoint();
      point.x = event.clientX;
      point.y = event.clientY;
      return point.matrixTransform(svg.getScreenCTM().inverse());
    }

    function graphPoint(svg, event) {
      const point = svgPoint(svg, event);
      return {
        x: (point.x - state.view.x) / state.view.scale,
        y: (point.y - state.view.y) / state.view.scale,
      };
    }

    function beginGraphPan(event) {
      if (event.button !== 0 || event.target !== event.currentTarget) return;
      event.preventDefault();
      const svg = document.getElementById("graph");
      const point = svgPoint(svg, event);
      state.panning = {
        startX: point.x,
        startY: point.y,
        viewX: state.view.x,
        viewY: state.view.y,
        moved: false,
      };
      window.addEventListener("pointermove", panGraph);
      window.addEventListener("pointerup", endGraphPan);
      window.addEventListener("pointercancel", endGraphPan);
    }

    function panGraph(event) {
      if (!state.panning) return;
      const svg = document.getElementById("graph");
      const point = svgPoint(svg, event);
      state.view = {
        ...state.view,
        x: state.panning.viewX + point.x - state.panning.startX,
        y: state.panning.viewY + point.y - state.panning.startY,
      };
      if (Math.hypot(point.x - state.panning.startX, point.y - state.panning.startY) > 3) {
        state.panning.moved = true;
      }
      draw();
    }

    function endGraphPan() {
      const panning = state.panning;
      window.removeEventListener("pointermove", panGraph);
      window.removeEventListener("pointerup", endGraphPan);
      window.removeEventListener("pointercancel", endGraphPan);
      state.panning = null;
      if (panning && !panning.moved) {
        clearCategorySelection();
      }
    }

    function wheelZoomGraph(event) {
      event.preventDefault();
      const svg = document.getElementById("graph");
      const origin = svgPoint(svg, event);
      const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;
      zoomGraphAt(origin, state.view.scale * factor);
    }

    function beginNodeDrag(event, node) {
      event.preventDefault();
      event.stopPropagation();
      const svg = document.getElementById("graph");
      const current = state.positions[node.id] || { x: 0, y: 0 };
      const point = graphPoint(svg, event);
      state.dragging = {
        id: node.id,
        node,
        startX: point.x,
        startY: point.y,
        offsetX: current.x - point.x,
        offsetY: current.y - point.y,
        moved: false,
      };
      window.addEventListener("pointermove", dragNode);
      window.addEventListener("pointerup", endNodeDrag);
      window.addEventListener("pointercancel", endNodeDrag);
    }

    function dragNode(event) {
      if (!state.dragging) return;
      const svg = document.getElementById("graph");
      const bounds = state.layoutBounds.width && state.layoutBounds.height
        ? state.layoutBounds
        : { width: Math.max(svg.clientWidth, 360), height: Math.max(svg.clientHeight, 420) };
      const point = graphPoint(svg, event);
      const distance = Math.hypot(point.x - state.dragging.startX, point.y - state.dragging.startY);
      if (!state.dragging.moved && distance <= 3) return;
      state.dragging.moved = true;
      state.positions[state.dragging.id] = clampPosition(
        {
          x: point.x + state.dragging.offsetX,
          y: point.y + state.dragging.offsetY,
        },
        bounds.width,
        bounds.height,
      );
      draw();
    }

    function endNodeDrag() {
      const dragging = state.dragging;
      window.removeEventListener("pointermove", dragNode);
      window.removeEventListener("pointerup", endNodeDrag);
      window.removeEventListener("pointercancel", endNodeDrag);
      state.dragging = null;
      if (dragging && !dragging.moved) {
        select(dragging.node, "node");
      }
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
    }

    for (const input of [confidence, search]) {
      input.addEventListener("input", draw);
      input.addEventListener("change", draw);
    }
    categoryFrames.addEventListener("change", () => {
      if (!categoryFrames.checked) {
        state.selectedCategory = "";
      }
      draw();
    });
    clearCategory.addEventListener("click", clearCategorySelection);
    document.addEventListener("keydown", event => {
      if (event.key === "/" && document.activeElement !== search) {
        event.preventDefault();
        search.focus();
      }
    });
    resetLayout.addEventListener("click", resetGraphLayout);
    resetView.addEventListener("click", () => {
      resetGraphViewState();
      draw();
    });
    zoomIn.addEventListener("click", () => zoomGraphBy(1.18));
    zoomOut.addEventListener("click", () => zoomGraphBy(1 / 1.18));
    panUp.addEventListener("click", () => panGraphBy(0, -72));
    panDown.addEventListener("click", () => panGraphBy(0, 72));
    panLeft.addEventListener("click", () => panGraphBy(-72, 0));
    panRight.addEventListener("click", () => panGraphBy(72, 0));
    document.getElementById("graph").addEventListener("pointerdown", beginGraphPan);
    document.getElementById("graph").addEventListener("wheel", wheelZoomGraph, { passive: false });
    window.addEventListener("resize", draw);
    draw();
  </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
