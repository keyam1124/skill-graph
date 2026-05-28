#!/usr/bin/env python3
"""Build a lightweight graph of SKILL.md relationships.

The tool is intentionally deterministic. It does not call external LLM APIs;
agents read the generated JSON/HTML and decide how to improve the skill set.
"""

from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import posixpath
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - exercised only when PyYAML is absent.
    yaml = None


SCHEMA_VERSION = "skillgraph-lite.v1"
OUT_DIR = ".skillgraph"
REGISTRY_FILE = "registry.json"
GRAPH_FILE = "graph.json"
DIAGNOSTICS_FILE = "diagnostics.json"
HTML_FILE = "skillgraph.html"

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
RELATION_TYPES = {
    "routes_to",
    "invokes",
    "related_to",
    "uses_reference",
    "uses_template",
    "uses_script",
    "mentions",
    "should_not_co_trigger",
    "language_variant",
}
SKILL_RELATION_TYPES = {
    "routes_to",
    "invokes",
    "related_to",
    "mentions",
    "should_not_co_trigger",
    "language_variant",
}
PATH_RELATION_TYPES = {
    "uses_reference",
    "uses_template",
    "uses_script",
}
RELATED_HEADINGS = {
    "related skills",
    "関連 skill",
    "関連スキル",
    "see also",
}
REFERENCE_HEADINGS = {"references", "参考"}
TEMPLATE_HEADINGS = {"templates", "output format"}
SCRIPT_HEADINGS = {"scripts", "tools"}
HEADING_TO_PATH_RELATION = {
    **{heading: "uses_reference" for heading in REFERENCE_HEADINGS},
    **{heading: "uses_template" for heading in TEMPLATE_HEADINGS},
    **{heading: "uses_script" for heading in SCRIPT_HEADINGS},
}
FUTURE_CONFIG_PATHS = (
    "AGENTS.md",
    ".github/copilot-instructions.md",
    ".cursor/rules",
)
PATH_RE = re.compile(
    r"(?P<path>(?:\.\./|\.\/)?(?:skills|references|templates|scripts)/[^\s)`'\"<>]+|"
    r"(?:\.\./|\./)[^\s)`'\"<>]*(?:SKILL(?:\.en)?\.md|skillgraph\.yaml|\.md))"
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
    sidecar: str | None = None
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
        }
        if self.sidecar:
            payload["sidecar"] = self.sidecar
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

    def key(self) -> tuple[str, str, str, str, str]:
        evidence_key = "|".join(
            f"{item.get('path', '')}:{item.get('section', '')}:{item.get('text', '')}"
            for item in self.evidence
        )
        return (self.source, self.target, self.type, self.origin, evidence_key)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def rel_path(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def normalize_id_part(value: str) -> str:
    return value.strip().lower().replace("_", "-")


def normalize_skill_id(skill_file: Path, root: Path) -> str:
    rel = rel_path(skill_file.parent, root / "skills")
    return ".".join(normalize_id_part(part) for part in Path(rel).parts)


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


def ensure_out_dir(root: Path) -> Path:
    out = root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    return out


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_simple_yaml(text: str) -> dict[str, Any]:
    """Parse the small YAML subset used by SKILL frontmatter/skillgraph.yaml."""
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


def as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


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


def sidecar_for(skill_file: Path) -> tuple[dict[str, Any], Path | None]:
    sidecar = skill_file.parent / "skillgraph.yaml"
    if not sidecar.exists():
        return {}, None
    return load_yaml_text(read_text(sidecar)), sidecar


def detect_future_config_files(root: Path) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for candidate in FUTURE_CONFIG_PATHS:
        path = root / candidate
        exists = path.exists() if candidate.endswith(".md") else any(path.glob("**/*")) if path.exists() else False
        if exists:
            diagnostics.append(
                Diagnostic(
                    type="future_input_detected",
                    severity="info",
                    message=f"{candidate} exists but is not analyzed in v0.1.",
                    path=candidate,
                )
            )
    return diagnostics


def scan_registry(root: Path) -> dict[str, Any]:
    root = root.resolve()
    diagnostics: list[Diagnostic] = []
    skills_root = root / "skills"
    skill_files = sorted(skills_root.glob("**/SKILL.md")) if skills_root.exists() else []
    skill_files = [path for path in skill_files if not should_skip(path, root)]
    by_id: dict[str, list[Path]] = {}
    records: list[SkillRecord] = []

    for skill_file in skill_files:
        skill_id = normalize_skill_id(skill_file, root)
        by_id.setdefault(skill_id, []).append(skill_file)
        text = read_text(skill_file)
        frontmatter, has_frontmatter = parse_frontmatter(text)
        sidecar, sidecar_path = sidecar_for(skill_file)
        name = str(frontmatter.get("name") or skill_file.parent.name)
        description = str(frontmatter.get("description") or "")
        if not has_frontmatter or "name" not in frontmatter or "description" not in frontmatter:
            diagnostics.append(
                Diagnostic(
                    type="missing_frontmatter",
                    severity="warning",
                    message="SKILL.md is missing frontmatter name and/or description.",
                    path=rel_path(skill_file, root),
                    skill=skill_id,
                )
            )
        if not sidecar_path:
            diagnostics.append(
                Diagnostic(
                    type="missing_sidecar",
                    severity="info",
                    message="skillgraph.yaml is not present; inferred relations will still be analyzed.",
                    path=rel_path(skill_file.parent, root),
                    skill=skill_id,
                )
            )
        h1 = first_h1(text)
        category = str(sidecar.get("category") or ".".join(Path(rel_path(skill_file.parent, skills_root)).parts[:-1]))
        aliases = unique(
            [
                skill_id,
                name,
                skill_file.parent.name,
                *(as_list(sidecar.get("aliases"))),
                *([h1] if h1 else []),
            ]
        )
        variants: list[dict[str, str]] = []
        for variant in sorted(skill_file.parent.glob("SKILL.*.md")):
            if variant.name == "SKILL.md" or should_skip(variant, root):
                continue
            lang = variant.name.removeprefix("SKILL.").removesuffix(".md")
            variants.append({"lang": lang, "path": rel_path(variant, root)})
        records.append(
            SkillRecord(
                id=skill_id,
                kind="skill",
                label=name or skill_file.parent.name,
                path=rel_path(skill_file, root),
                dir=rel_path(skill_file.parent, root),
                category=category,
                description=description,
                aliases=aliases,
                name=name,
                sidecar=rel_path(sidecar_path, root) if sidecar_path else None,
                language_variants=variants,
                frontmatter=frontmatter,
            )
        )

    for skill_id, paths in by_id.items():
        if len(paths) <= 1:
            continue
        for path in paths:
            diagnostics.append(
                Diagnostic(
                    type="duplicate_skill_id",
                    severity="error",
                    message=f"Multiple SKILL.md files resolve to skill id {skill_id}.",
                    path=rel_path(path, root),
                    skill=skill_id,
                )
            )

    diagnostics.extend(detect_future_config_files(root))
    registry = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "root": str(root),
        "skills": [record.to_json() for record in records],
        "diagnostics": [diag.to_json() for diag in diagnostics],
    }
    out = ensure_out_dir(root)
    (out / REGISTRY_FILE).write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return registry


def load_registry(root: Path) -> dict[str, Any]:
    registry_path = root / OUT_DIR / REGISTRY_FILE
    if registry_path.exists():
        return json.loads(read_text(registry_path))
    return scan_registry(root)


def skill_maps(registry: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    skills = {skill["id"]: skill for skill in registry.get("skills", [])}
    alias_map: dict[str, list[str]] = {}
    for skill in skills.values():
        candidates = [skill["id"], skill.get("name", ""), skill.get("label", ""), skill.get("path", ""), skill.get("dir", "")]
        candidates.extend(skill.get("aliases", []))
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


def resolve_path_reference(raw: str, source_file: Path, root: Path) -> str:
    target = clean_target(raw)
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return target
    if target.startswith(("skills/", "references/", "templates/", "scripts/")):
        candidate = (root / target).resolve()
    else:
        candidate = (source_file.parent / target).resolve()
    try:
        return candidate.relative_to(root.resolve()).as_posix()
    except ValueError:
        if target.startswith("./"):
            return target[2:]
        return target


def path_relation_for(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if normalized.endswith("SKILL.md") or normalized.endswith("SKILL.en.md"):
        return "related_to"
    if normalized.startswith("references/") or "/references/" in normalized:
        return "uses_reference"
    if normalized.startswith("templates/") or "/templates/" in normalized:
        return "uses_template"
    if normalized.startswith("scripts/") or "/scripts/" in normalized:
        return "uses_script"
    return None


def resolve_link_target(
    raw: str,
    source_file: Path,
    root: Path,
    path_to_skill: dict[str, str],
) -> tuple[str | None, str | None]:
    resolved = resolve_path_reference(raw, source_file, root)
    relation_type = path_relation_for(resolved)
    if relation_type == "related_to":
        skill_id = path_to_skill.get(resolved)
        return relation_type, skill_id or resolved
    if relation_type:
        return relation_type, resolved
    return None, None


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


def sidecar_relations(
    root: Path,
    skill: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
) -> tuple[list[Edge], list[Diagnostic]]:
    sidecar_path = skill.get("sidecar")
    if not sidecar_path:
        return [], []
    path = root / sidecar_path
    data = load_yaml_text(read_text(path))
    diagnostics: list[Diagnostic] = []
    edges: list[Edge] = []
    relations = data.get("relations") or {}
    if not isinstance(relations, dict):
        return [], diagnostics
    for relation_type, raw_targets in relations.items():
        if relation_type not in RELATION_TYPES:
            continue
        for raw_target in as_list(raw_targets):
            if relation_type in SKILL_RELATION_TYPES:
                target = resolve_skill(raw_target, skills, alias_map)
                if not target:
                    diagnostics.append(
                        Diagnostic(
                            type="dangling_reference",
                            severity="warning",
                            message=f"Relation target {raw_target} does not resolve to a skill.",
                            path=sidecar_path,
                            skill=skill["id"],
                            target=raw_target,
                        )
                    )
                    target = raw_target
            else:
                target = clean_target(raw_target)
                if not (root / target).exists():
                    diagnostics.append(
                        Diagnostic(
                            type="dangling_reference",
                            severity="warning",
                            message=f"Referenced path {target} does not exist.",
                            path=sidecar_path,
                            skill=skill["id"],
                            target=target,
                        )
                    )
            edges.append(
                make_edge(
                    skill["id"],
                    target,
                    relation_type,
                    "sidecar",
                    "high",
                    [evidence(sidecar_path, raw_target, f"relations.{relation_type}")],
                )
            )
    return edges, diagnostics


def language_variant_edges(skill: dict[str, Any]) -> list[Edge]:
    edges: list[Edge] = []
    for variant in skill.get("languageVariants", []):
        edges.append(
            make_edge(
                skill["id"],
                variant["path"],
                "language_variant",
                "language_variant",
                "high",
                [evidence(skill["path"], variant["path"], "language_variant")],
            )
        )
    return edges


def markdown_link_edges(
    root: Path,
    skill: dict[str, Any],
    path_to_skill: dict[str, str],
) -> tuple[list[Edge], list[Diagnostic]]:
    skill_file = root / skill["path"]
    text = read_text(skill_file)
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    for match in LINK_RE.finditer(text):
        raw = match.group(1)
        relation_type, target = resolve_link_target(raw, skill_file, root, path_to_skill)
        if not relation_type or not target:
            continue
        if relation_type == "related_to" and target not in path_to_skill.values():
            diagnostics.append(
                Diagnostic(
                    type="dangling_reference",
                    severity="warning",
                    message=f"Markdown link target {raw} does not resolve to a skill.",
                    path=skill["path"],
                    skill=skill["id"],
                    target=target,
                )
            )
        elif relation_type in PATH_RELATION_TYPES and not (root / target).exists():
            diagnostics.append(
                Diagnostic(
                    type="dangling_reference",
                    severity="warning",
                    message=f"Markdown link target {target} does not exist.",
                    path=skill["path"],
                    skill=skill["id"],
                    target=target,
                )
            )
        edges.append(
            make_edge(
                skill["id"],
                target,
                relation_type,
                "markdown_link",
                "high",
                [evidence(skill["path"], raw)],
            )
        )
    return edges, diagnostics


def path_reference_edges(
    root: Path,
    skill: dict[str, Any],
    path_to_skill: dict[str, str],
    source_file: Path,
    section_name: str | None = None,
    source_text: str | None = None,
) -> tuple[list[Edge], list[Diagnostic]]:
    text = source_text if source_text is not None else read_text(source_file)
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    for match in PATH_RE.finditer(text):
        raw = match.group("path")
        resolved = resolve_path_reference(raw, source_file, root)
        relation_type = path_relation_for(resolved)
        if not relation_type:
            continue
        target = path_to_skill.get(resolved, resolved)
        origin = "heading_context_match" if section_name else "path_reference"
        confidence = "medium" if origin == "heading_context_match" else "high"
        if relation_type == "related_to" and target == resolved:
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
        elif relation_type in PATH_RELATION_TYPES and not (root / resolved).exists():
            diagnostics.append(
                Diagnostic(
                    type="dangling_reference",
                    severity="warning",
                    message=f"Path reference {resolved} does not exist.",
                    path=rel_path(source_file, root),
                    skill=skill["id"],
                    target=resolved,
                )
            )
        edges.append(
            make_edge(
                skill["id"],
                target,
                relation_type,
                origin,
                confidence,
                [evidence(rel_path(source_file, root), raw, section_name)],
            )
        )
    return edges, diagnostics


def heading_edges(
    root: Path,
    skill: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
    path_to_skill: dict[str, str],
) -> tuple[list[Edge], list[Diagnostic]]:
    skill_file = root / skill["path"]
    text = read_text(skill_file)
    sections = section_ranges(text)
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    has_related = False
    for section in sections:
        heading = section["normalized"]
        if heading in RELATED_HEADINGS:
            has_related = True
            for target, raw in find_section_mentions(section["text"], skills, alias_map):
                if target == skill["id"]:
                    continue
                edges.append(
                    make_edge(
                        skill["id"],
                        target,
                        "related_to",
                        "related_section",
                        "high",
                        [evidence(skill["path"], raw, section["heading"])],
                    )
                )
        relation_type = HEADING_TO_PATH_RELATION.get(heading)
        if relation_type:
            section_edges, section_diags = path_reference_edges(
                root,
                skill,
                path_to_skill,
                skill_file,
                section["heading"],
                section["text"],
            )
            for edge in section_edges:
                edge.type = relation_type if edge.type != "related_to" else edge.type
                if edge.type == relation_type:
                    edge.confidence = "medium"
                    edge.origin = "heading_context_match"
            edges.extend(section_edges)
            diagnostics.extend(section_diags)
    if not has_related:
        diagnostics.append(
            Diagnostic(
                type="missing_related_section",
                severity="info",
                message="Related Skills section is not present.",
                path=skill["path"],
                skill=skill["id"],
            )
        )
    return edges, diagnostics


def term_pattern(term: str) -> re.Pattern[str] | None:
    term = term.strip()
    if len(term) < 3:
        return None
    if re.match(r"^[A-Za-z0-9_.\-/]+$", term):
        return re.compile(rf"(?<![A-Za-z0-9_.\-/]){re.escape(term)}(?![A-Za-z0-9_.\-/])", re.IGNORECASE)
    return re.compile(re.escape(term), re.IGNORECASE)


def skill_search_files(root: Path, skill: dict[str, Any]) -> list[Path]:
    skill_dir = root / skill["dir"]
    files: list[Path] = []
    for path in iter_files(skill_dir, ["**/*.md", "**/*.yaml", "**/*.yml"]):
        files.append(path)
    return sorted(set(files))


def body_mention_edges(
    root: Path,
    skill: dict[str, Any],
    skills: dict[str, dict[str, Any]],
    alias_map: dict[str, list[str]],
) -> tuple[list[Edge], list[Diagnostic]]:
    sidecar_path = skill.get("sidecar")
    sidecar_data = load_yaml_text(read_text(root / sidecar_path)) if sidecar_path else {}
    ignore_ids = {
        resolved
        for item in as_list(sidecar_data.get("ignore_mentions"))
        if (resolved := resolve_skill(item, skills, alias_map))
    }
    edges: list[Edge] = []
    diagnostics: list[Diagnostic] = []
    seen_edges: set[tuple[str, str]] = set()
    ignored_seen: set[str] = set()
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
            if target_id in ignore_ids:
                if target_id not in ignored_seen:
                    diagnostics.append(
                        Diagnostic(
                            type="ignored_mention",
                            severity="info",
                            message=f"Mention of {target_id} is ignored by sidecar.",
                            path=sidecar_path,
                            skill=skill["id"],
                            target=target_id,
                            evidence={"term": term},
                        )
                    )
                    ignored_seen.add(target_id)
                continue
            edge_key = (target_id, rel)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)
            edges.append(
                make_edge(
                    skill["id"],
                    target_id,
                    "mentions",
                    "body_name_match",
                    "low",
                    [evidence(rel, match.group(0))],
                )
            )
            diagnostics.append(
                Diagnostic(
                    type="possible_relation",
                    severity="info",
                    message=f"{skill['id']} mentions {target_id}; confirm whether this should be an explicit relation.",
                    path=rel,
                    skill=skill["id"],
                    target=target_id,
                    evidence={"term": match.group(0)},
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


def asset_nodes(root: Path, edges: list[Edge], existing_node_ids: set[str]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in edges:
        if edge.target in existing_node_ids or edge.target in seen:
            continue
        if edge.type not in PATH_RELATION_TYPES and edge.type != "language_variant":
            continue
        path = edge.target
        kind = "file"
        if edge.type == "uses_reference":
            kind = "reference"
        elif edge.type == "uses_template":
            kind = "template"
        elif edge.type == "uses_script":
            kind = "script"
        elif edge.type == "language_variant":
            kind = "skill_variant"
        nodes.append(
            {
                "id": path,
                "kind": kind,
                "label": posixpath.basename(path),
                "path": path,
                "dir": posixpath.dirname(path),
                "category": kind,
                "description": "",
                "aliases": [path],
            }
        )
        seen.add(path)
    return nodes


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


def analyze_graph(root: Path) -> dict[str, Any]:
    root = root.resolve()
    registry = load_registry(root)
    skills, alias_map = skill_maps(registry)
    path_to_skill = {skill["path"]: skill_id for skill_id, skill in skills.items()}
    path_to_skill.update({skill["dir"]: skill_id for skill_id, skill in skills.items()})
    diagnostics = [Diagnostic(**diag) for diag in registry.get("diagnostics", []) if diag.get("type") != "missing_sidecar"]
    diagnostics.extend(Diagnostic(**diag) for diag in registry.get("diagnostics", []) if diag.get("type") == "missing_sidecar")
    diagnostics.extend(duplicate_alias_diagnostics(skills, alias_map))
    edges: list[Edge] = []

    for skill in skills.values():
        side_edges, side_diags = sidecar_relations(root, skill, skills, alias_map)
        edges.extend(side_edges)
        diagnostics.extend(side_diags)
        edges.extend(language_variant_edges(skill))
        link_edges, link_diags = markdown_link_edges(root, skill, path_to_skill)
        edges.extend(link_edges)
        diagnostics.extend(link_diags)
        path_edges, path_diags = path_reference_edges(root, skill, path_to_skill, root / skill["path"])
        edges.extend(path_edges)
        diagnostics.extend(path_diags)
        section_edges, section_diags = heading_edges(root, skill, skills, alias_map, path_to_skill)
        edges.extend(section_edges)
        diagnostics.extend(section_diags)
        mention_edges, mention_diags = body_mention_edges(root, skill, skills, alias_map)
        edges.extend(mention_edges)
        diagnostics.extend(mention_diags)

    deduped: list[Edge] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for edge in edges:
        if edge.source == edge.target:
            continue
        key = edge.key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)
    edges = deduped

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
    node_ids = {node["id"] for node in skill_nodes}
    nodes = skill_nodes + asset_nodes(root, edges, node_ids)
    graph = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": utc_now(),
        "root": str(root),
        "nodes": nodes,
        "edges": assign_edge_ids(edges),
        "diagnostics": [diag.to_json() for diag in diagnostics],
    }
    out = ensure_out_dir(root)
    (out / GRAPH_FILE).write_text(json.dumps(graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / DIAGNOSTICS_FILE).write_text(
        json.dumps(graph["diagnostics"], ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return graph


def render_html(root: Path) -> Path:
    root = root.resolve()
    graph_path = root / OUT_DIR / GRAPH_FILE
    if not graph_path.exists():
        analyze_graph(root)
    graph = json.loads(read_text(graph_path))
    graph_json = json.dumps(graph, ensure_ascii=False)
    html_text = HTML_TEMPLATE.replace("__GRAPH_JSON__", graph_json.replace("</", "<\\/"))
    out = ensure_out_dir(root) / HTML_FILE
    out.write_text(html_text, encoding="utf-8")
    return out


def command_scan(args: argparse.Namespace) -> int:
    registry = scan_registry(Path(args.root))
    print(f"Wrote {Path(args.root) / OUT_DIR / REGISTRY_FILE} ({len(registry.get('skills', []))} skills)")
    return 0


def command_analyze(args: argparse.Namespace) -> int:
    graph = analyze_graph(Path(args.root))
    print(
        f"Wrote {Path(args.root) / OUT_DIR / GRAPH_FILE} "
        f"({len(graph.get('nodes', []))} nodes, {len(graph.get('edges', []))} edges)"
    )
    return 0


def command_render(args: argparse.Namespace) -> int:
    path = render_html(Path(args.root))
    print(f"Wrote {path}")
    return 0


def command_all(args: argparse.Namespace) -> int:
    scan_registry(Path(args.root))
    analyze_graph(Path(args.root))
    render_html(Path(args.root))
    print(f"Wrote {Path(args.root) / OUT_DIR}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SkillGraph Lite registry, graph, diagnostics, and HTML.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, handler in {
        "scan": command_scan,
        "analyze": command_analyze,
        "render": command_render,
        "all": command_all,
    }.items():
        sub = subparsers.add_parser(name)
        sub.add_argument("--root", default=".", help="Repository root containing skills/")
        sub.set_defaults(func=handler)
    return parser


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SkillGraph Lite</title>
  <style>
    :root { color-scheme: light; --line: #d7dee8; --text: #17202a; --muted: #5f6f82; --accent: #0d6efd; --warn: #a15c00; --error: #b42318; --panel: #f6f8fb; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--text); background: #fff; }
    .app { display: grid; grid-template-columns: minmax(260px, 320px) minmax(360px, 1fr) minmax(280px, 360px); min-height: 100vh; }
    aside, main { min-width: 0; }
    aside { padding: 16px; border-right: 1px solid var(--line); background: var(--panel); overflow: auto; }
    .details { border-right: 0; border-left: 1px solid var(--line); }
    main { display: grid; grid-template-rows: auto 1fr auto; }
    header { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; gap: 16px; align-items: baseline; justify-content: space-between; }
    h1 { font-size: 18px; margin: 0; }
    h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; margin: 18px 0 8px; color: var(--muted); }
    label { display: block; font-size: 13px; color: var(--muted); margin: 12px 0 4px; }
    input[type="search"], select { width: 100%; min-height: 34px; border: 1px solid var(--line); border-radius: 6px; padding: 6px 8px; background: #fff; color: var(--text); }
    .check { display: flex; align-items: center; gap: 8px; margin-top: 10px; color: var(--text); font-size: 14px; }
    .list { display: grid; gap: 6px; }
    .item { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 8px; cursor: pointer; }
    .item:hover { border-color: var(--accent); }
    .item.selected { border-color: var(--accent); background: #eef5ff; box-shadow: 0 0 0 1px var(--accent); }
    .item strong { display: block; font-size: 13px; overflow-wrap: anywhere; }
    .item span { display: block; color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
    .badge { display: inline-block; font-size: 11px; padding: 2px 6px; border-radius: 999px; background: #eaf1ff; color: #174ea6; margin-right: 4px; }
    .warning { color: var(--warn); }
    .error { color: var(--error); }
    #graph { width: 100%; height: 100%; min-height: 520px; background: linear-gradient(#fff, #f9fbfd); }
    .status { padding: 10px 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }
    .node { cursor: pointer; }
    .node circle { fill: #fff; stroke: #0d6efd; stroke-width: 2; }
    .node.asset circle { stroke: #667085; }
    .node.related circle { fill: #fff7ed; stroke: #f97316; stroke-width: 3; }
    .node.selected circle { fill: #eaf1ff; stroke: #b42318; stroke-width: 4; }
    .node text { font-size: 12px; paint-order: stroke; stroke: #fff; stroke-width: 4px; stroke-linejoin: round; fill: var(--text); pointer-events: none; }
    .edge { stroke: #98a2b3; stroke-width: 1.5; marker-end: url(#arrow); cursor: pointer; }
    .edge.mentions { stroke-dasharray: 4 3; }
    .edge.high { stroke-width: 2.2; }
    .edge.related { stroke: #f97316; stroke-width: 3; }
    .edge.selected { stroke: #b42318; stroke-width: 4; }
    .edge-hit { stroke: transparent; stroke-width: 14; cursor: pointer; pointer-events: stroke; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #0f172a; color: #e5e7eb; padding: 10px; border-radius: 6px; font-size: 12px; }
    @media (max-width: 980px) {
      .app { grid-template-columns: 1fr; }
      aside, .details { border: 0; border-bottom: 1px solid var(--line); }
      #graph { min-height: 420px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <h1>SkillGraph Lite</h1>
      <label for="search">Skill Search</label>
      <input id="search" type="search" placeholder="id, name, path">
      <label for="edgeType">Edge Type</label>
      <select id="edgeType"><option value="">All non-mentions</option></select>
      <label for="confidence">Confidence</label>
      <select id="confidence"><option value="">All</option></select>
      <label class="check"><input id="showMentions" type="checkbox"> Show mentions edges</label>
      <h2>Diagnostics</h2>
      <div id="diagnostics" class="list"></div>
    </aside>
    <main>
      <header>
        <h1>Graph</h1>
        <div id="summary"></div>
      </header>
      <svg id="graph" role="img" aria-label="Skill graph"></svg>
      <div class="status" id="status"></div>
    </main>
    <aside class="details">
      <h1>Details</h1>
      <div id="details"></div>
      <h2>Nodes</h2>
      <div id="nodes" class="list"></div>
      <h2>Edges</h2>
      <div id="edges" class="list"></div>
    </aside>
  </div>
  <script>
    const graph = __GRAPH_JSON__;
    const showMentionsDefault = false;
    const state = { selected: null };
    const byId = new Map(graph.nodes.map(node => [node.id, node]));
    const edgeType = document.getElementById("edgeType");
    const confidence = document.getElementById("confidence");
    const search = document.getElementById("search");
    const showMentions = document.getElementById("showMentions");
    showMentions.checked = showMentionsDefault;

    for (const type of [...new Set(graph.edges.map(edge => edge.type))].sort()) {
      const option = document.createElement("option");
      option.value = type;
      option.textContent = type;
      edgeType.append(option);
    }
    for (const value of [...new Set(graph.edges.map(edge => edge.confidence))].sort()) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      confidence.append(option);
    }

    function visibleNodes() {
      const q = search.value.trim().toLowerCase();
      const nodes = graph.nodes.filter(node => {
        if (!q) return true;
        return [node.id, node.label, node.path, node.description, ...(node.aliases || [])]
          .filter(Boolean)
          .some(value => String(value).toLowerCase().includes(q));
      });
      if (state.selected?.kind === "edge") {
        for (const id of [state.selected.value.source, state.selected.value.target]) {
          const node = byId.get(id);
          if (node && !nodes.some(item => item.id === id)) nodes.push(node);
        }
      }
      return nodes;
    }

    function visibleEdges(nodes) {
      const nodeIds = new Set(nodes.map(node => node.id));
      return graph.edges.filter(edge => {
        if (!showMentions.checked && edge.type === "mentions") return false;
        if (edgeType.value && edge.type !== edgeType.value) return false;
        if (confidence.value && edge.confidence !== confidence.value) return false;
        if (state.selected?.kind === "edge" && edge.id === state.selected.value.id) return true;
        return nodeIds.has(edge.source) || nodeIds.has(edge.target);
      });
    }

    function selectionKey(kind, value) {
      if (!value) return "";
      if (kind === "node" || kind === "edge") return value.id || "";
      return [value.type, value.path, value.skill, value.target, value.message].filter(Boolean).join("|");
    }

    function layout(nodes, width, height) {
      const positions = new Map();
      const centerX = width / 2;
      const radius = Math.max(120, Math.min(width, height) * 0.38);
      const topPadding = 72;
      const centerY = Math.min(height / 2, radius + topPadding);
      nodes.forEach((node, index) => {
        const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
        positions.set(node.id, {
          x: centerX + Math.cos(angle) * radius,
          y: centerY + Math.sin(angle) * radius,
        });
      });
      return positions;
    }

    function draw() {
      const svg = document.getElementById("graph");
      const width = Math.max(svg.clientWidth, 360);
      const height = Math.max(svg.clientHeight, 420);
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.innerHTML = `<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#98a2b3"></path></marker></defs>`;
      const nodes = visibleNodes();
      const edges = visibleEdges(nodes);
      const positions = layout(nodes, width, height);
      const selected = state.selected;
      const selectedKey = selected?.key || "";
      const selectedNodeId = selected?.kind === "node" ? selected.value.id : "";
      const selectedEdge = selected?.kind === "edge" ? selected.value : null;
      const selectedEdgeNodes = selectedEdge ? new Set([selectedEdge.source, selectedEdge.target]) : new Set();
      for (const edge of edges) {
        const source = positions.get(edge.source);
        const target = positions.get(edge.target);
        if (!source || !target) continue;
        const isSelected = selected?.kind === "edge" && edge.id === selectedKey;
        const isRelated = selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const edgeClass = `edge ${edge.type} ${edge.confidence}${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`;
        const hitLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
        hitLine.setAttribute("x1", source.x);
        hitLine.setAttribute("y1", source.y);
        hitLine.setAttribute("x2", target.x);
        hitLine.setAttribute("y2", target.y);
        hitLine.setAttribute("class", "edge-hit");
        hitLine.addEventListener("click", () => select(edge, "edge"));
        svg.append(hitLine);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
        line.setAttribute("x1", source.x);
        line.setAttribute("y1", source.y);
        line.setAttribute("x2", target.x);
        line.setAttribute("y2", target.y);
        line.setAttribute("class", edgeClass);
        line.addEventListener("click", () => select(edge, "edge"));
        svg.append(line);
      }
      for (const node of nodes) {
        const point = positions.get(node.id);
        if (!point) continue;
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const isSelected = selected?.kind === "node" && node.id === selectedKey;
        const isRelated = selectedEdgeNodes.has(node.id);
        group.setAttribute("class", `node ${node.kind !== "skill" ? "asset" : ""}${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`);
        group.setAttribute("transform", `translate(${point.x}, ${point.y})`);
        group.addEventListener("click", () => select(node, "node"));
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("r", node.kind === "skill" ? "18" : "13");
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("y", "34");
        text.textContent = node.label || node.id;
        group.append(circle, text);
        svg.append(group);
      }
      document.getElementById("summary").textContent = `${nodes.length} nodes / ${edges.length} edges`;
      document.getElementById("status").textContent = `Generated ${graph.generatedAt} from ${graph.root}`;
      renderLists(nodes, edges);
    }

    function renderLists(nodes, edges) {
      const nodeBox = document.getElementById("nodes");
      const edgeBox = document.getElementById("edges");
      nodeBox.innerHTML = "";
      edgeBox.innerHTML = "";
      for (const node of nodes) {
        const item = document.createElement("div");
        item.className = `item ${state.selected?.kind === "node" && node.id === state.selected.key ? "selected" : ""}`;
        item.innerHTML = `<strong>${escapeHtml(node.label || node.id)}</strong><span>${escapeHtml(node.id)}</span>`;
        item.addEventListener("click", () => select(node, "node"));
        nodeBox.append(item);
      }
      for (const edge of edges) {
        const item = document.createElement("div");
        item.className = `item ${state.selected?.kind === "edge" && edge.id === state.selected.key ? "selected" : ""}`;
        item.innerHTML = `<strong>${escapeHtml(edge.type)}</strong><span>${escapeHtml(edge.source)} -> ${escapeHtml(edge.target)}</span>`;
        item.addEventListener("click", () => select(edge, "edge"));
        edgeBox.append(item);
      }
      renderDiagnostics();
    }

    function renderDiagnostics() {
      const box = document.getElementById("diagnostics");
      box.innerHTML = "";
      for (const diag of graph.diagnostics) {
        const item = document.createElement("div");
        item.className = `item ${state.selected?.kind === "diagnostic" && selectionKey("diagnostic", diag) === state.selected.key ? "selected" : ""}`;
        const cls = diag.severity === "error" ? "error" : diag.severity === "warning" ? "warning" : "";
        item.innerHTML = `<strong class="${cls}">${escapeHtml(diag.type)}</strong><span>${escapeHtml(diag.message)}</span>`;
        item.addEventListener("click", () => select(diag, "diagnostic"));
        box.append(item);
      }
    }

    function select(value, kind) {
      state.selected = { kind, value, key: selectionKey(kind, value) };
      const details = document.getElementById("details");
      const title = kind === "edge" ? `${value.type}: ${value.source} -> ${value.target}` : value.id || value.type;
      details.innerHTML = `<h2>${escapeHtml(kind)}</h2><p>${escapeHtml(title || "")}</p><pre>${escapeHtml(JSON.stringify(value, null, 2))}</pre>`;
      draw();
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "\"": "&quot;", "'": "&#39;" }[char]));
    }

    for (const input of [edgeType, confidence, search, showMentions]) {
      input.addEventListener("input", draw);
      input.addEventListener("change", draw);
    }
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
