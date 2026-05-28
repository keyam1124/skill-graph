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
    html, body { height: 100%; overflow: hidden; }
    body { margin: 0; font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--text); background: #fff; }
    .app { display: grid; grid-template-columns: minmax(260px, 320px) minmax(360px, 1fr) minmax(280px, 360px); height: 100vh; min-height: 0; overflow: hidden; }
    aside, main { min-width: 0; min-height: 0; }
    aside { padding: 16px; border-right: 1px solid var(--line); background: var(--panel); overflow: auto; }
    .details { border-right: 0; border-left: 1px solid var(--line); }
    main { display: grid; grid-template-rows: auto minmax(0, 1fr) auto; overflow: hidden; }
    header { padding: 14px 16px; border-bottom: 1px solid var(--line); display: flex; gap: 16px; align-items: center; justify-content: space-between; }
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
    .item .item-meta { display: flex; flex-wrap: wrap; gap: 4px; margin-bottom: 4px; }
    .group { display: grid; gap: 6px; }
    .group + .group { margin-top: 12px; }
    .group-title { display: flex; align-items: center; justify-content: space-between; gap: 8px; color: var(--muted); font-size: 12px; font-weight: 700; letter-spacing: .04em; text-transform: uppercase; }
    .badge { display: inline-block; font-size: 11px; padding: 2px 6px; border-radius: 999px; background: #eaf1ff; color: #174ea6; margin-right: 4px; }
    .badge.file { background: #f1f5f9; color: #475569; }
    .badge.edge-type { background: #fff7ed; color: #9a3412; }
    .badge.warning { background: #fff7ed; color: var(--warn); }
    .badge.error { background: #fef2f2; color: var(--error); }
    .summary-card { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 10px; }
    .summary-card h2 { margin-top: 0; }
    .summary-card p { margin: 8px 0; }
    .meta-grid { display: grid; grid-template-columns: auto 1fr; gap: 6px 10px; font-size: 13px; }
    .meta-grid dt { color: var(--muted); }
    .meta-grid dd { margin: 0; overflow-wrap: anywhere; }
    .description { color: var(--text); line-height: 1.45; }
    details.raw-json { margin-top: 10px; }
    details.raw-json summary { cursor: pointer; color: var(--muted); font-size: 13px; }
    .warning { color: var(--warn); }
    .error { color: var(--error); }
    button { min-height: 32px; border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px; background: #fff; color: var(--text); cursor: pointer; }
    button:hover { border-color: var(--accent); }
    .graph-actions { display: flex; align-items: center; justify-content: flex-end; flex-wrap: wrap; gap: 8px; color: var(--muted); font-size: 13px; }
    .graph-actions .icon-button { width: 32px; padding: 6px 0; }
    .view-state { min-width: 42px; text-align: center; }
    #graph { width: 100%; height: 100%; min-height: 0; background-color: #fbfdff; background-image: linear-gradient(#edf2f7 1px, transparent 1px), linear-gradient(90deg, #edf2f7 1px, transparent 1px); background-size: 28px 28px; touch-action: none; }
    .status { padding: 10px 16px; border-top: 1px solid var(--line); color: var(--muted); font-size: 13px; }
    .node { cursor: grab; }
    .node:active { cursor: grabbing; }
    .graph-layer { transform-origin: 0 0; }
    .node circle { fill: #f8fbff; stroke: #2563eb; stroke-width: 2; filter: drop-shadow(0 6px 10px rgba(15, 23, 42, .18)); vector-effect: non-scaling-stroke; }
    .node.asset circle { fill: #f8fafc; stroke: #64748b; }
    .node:hover circle { fill: #eff6ff; stroke-width: 3; }
    .node.related circle { fill: #fff7ed; stroke: #f97316; stroke-width: 3; }
    .node.selected circle { fill: #dbeafe; stroke: #b42318; stroke-width: 4; }
    .node text { font-size: 12px; paint-order: stroke; stroke: #fff; stroke-width: 4px; stroke-linejoin: round; fill: var(--text); pointer-events: none; }
    .edge { fill: none; stroke: #64748b; stroke-width: 2.2; cursor: pointer; opacity: .9; vector-effect: non-scaling-stroke; }
    .edge.mentions { stroke-dasharray: 4 3; }
    .edge.high { stroke-width: 2.8; }
    .edge.related { stroke: #f97316; stroke-width: 4; }
    .edge.selected { stroke: #b42318; stroke-width: 5; }
    .edge-hit { fill: none; stroke: transparent; stroke-width: 18; cursor: pointer; pointer-events: stroke; vector-effect: non-scaling-stroke; }
    pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #0f172a; color: #e5e7eb; padding: 10px; border-radius: 6px; font-size: 12px; }
    @media (max-width: 980px) {
      html, body { height: auto; overflow: auto; }
      .app { grid-template-columns: 1fr; height: auto; min-height: 100vh; overflow: visible; }
      main { order: -1; min-height: 560px; }
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
      <label for="nodeKind">Node Type</label>
      <select id="nodeKind">
        <option value="">All nodes</option>
        <option value="skill">SKILL.md</option>
        <option value="file">Files / variants</option>
      </select>
      <label for="extension">Extension</label>
      <select id="extension"><option value="">All extensions</option></select>
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
        <div class="graph-actions">
          <button id="zoomOut" class="icon-button" type="button" title="Zoom out">-</button>
          <span id="viewState" class="view-state">100%</span>
          <button id="zoomIn" class="icon-button" type="button" title="Zoom in">+</button>
          <button id="panUp" class="icon-button" type="button" title="Pan up">&uarr;</button>
          <button id="panLeft" class="icon-button" type="button" title="Pan left">&larr;</button>
          <button id="panRight" class="icon-button" type="button" title="Pan right">&rarr;</button>
          <button id="panDown" class="icon-button" type="button" title="Pan down">&darr;</button>
          <button id="resetView" type="button">Reset view</button>
          <button id="resetLayout" type="button">Reset layout</button>
          <div id="summary"></div>
        </div>
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
    const state = {
      selected: null,
      positions: {},
      layoutKey: "",
      dragging: null,
      panning: null,
      view: { x: 0, y: 0, scale: 1 },
    };
    const nodeIndex = new Map(graph.nodes.map(node => [node.id, node]));
    const nodeKind = document.getElementById("nodeKind");
    const extension = document.getElementById("extension");
    const edgeType = document.getElementById("edgeType");
    const confidence = document.getElementById("confidence");
    const search = document.getElementById("search");
    const showMentions = document.getElementById("showMentions");
    const resetLayout = document.getElementById("resetLayout");
    const resetView = document.getElementById("resetView");
    const zoomIn = document.getElementById("zoomIn");
    const zoomOut = document.getElementById("zoomOut");
    const panUp = document.getElementById("panUp");
    const panDown = document.getElementById("panDown");
    const panLeft = document.getElementById("panLeft");
    const panRight = document.getElementById("panRight");
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
    for (const value of [...new Set(graph.nodes.map(nodeExtension))].sort()) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value || "No extension";
      extension.append(option);
    }

    function nodeExtension(node) {
      const filename = String(node.path || node.id || "").split("/").pop() || "";
      const dotIndex = filename.lastIndexOf(".");
      return dotIndex > 0 ? filename.slice(dotIndex).toLowerCase() : "";
    }

    function nodePassesControls(node, keepSelected = false) {
      if (keepSelected && state.selected?.kind === "node" && node.id === state.selected.value.id) return true;
      const q = search.value.trim().toLowerCase();
      if (nodeKind.value === "skill" && node.kind !== "skill") return false;
      if (nodeKind.value === "file" && node.kind === "skill") return false;
      if (extension.value && nodeExtension(node) !== extension.value) return false;
      if (!q) return true;
      return [node.id, node.label, node.path, node.description, ...(node.aliases || [])]
        .filter(Boolean)
        .some(value => String(value).toLowerCase().includes(q));
    }

    function edgePassesControls(edge) {
      if (!showMentions.checked && edge.type === "mentions") return false;
      if (edgeType.value && edge.type !== edgeType.value) return false;
      if (confidence.value && edge.confidence !== confidence.value) return false;
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
        return graph.nodes.filter(node => relatedIds.has(node.id) && nodePassesControls(node, true));
      }
      return graph.nodes.filter(node => nodePassesControls(node));
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

    function nodeTypeLabel(node) {
      if (node.kind === "skill") return "SKILL.md";
      const filename = String(node.path || node.id || "").split("/").pop() || "";
      if (filename.startsWith("SKILL.")) return `Skill variant ${nodeExtension(node) || ""}`.trim();
      return `Reference ${nodeExtension(node) || "file"}`;
    }

    function nodeTypeClass(node) {
      return node.kind === "skill" ? "" : "file";
    }

    function nodeDisplay(id) {
      const node = nodeIndex.get(id);
      return node?.label || id;
    }

    function nodePath(node) {
      return node.path || node.id || "";
    }

    function nodeRadius(node) {
      return node?.kind === "skill" ? 18 : 13;
    }

    function relationLabel(type) {
      return {
        invokes: "Invokes",
        related_to: "Related skill",
        uses_reference: "Uses reference",
        uses_template: "Uses template",
        mentions: "Mentions",
        language_variant: "Language variant",
        should_not_co_trigger: "Should not co-trigger",
      }[type] || type.replaceAll("_", " ");
    }

    function edgeMarker(isSelected, isRelated) {
      if (isSelected) return "url(#arrow-selected)";
      if (isRelated) return "url(#arrow-related)";
      return "url(#arrow-default)";
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
      return Math.min(3, Math.max(.35, scale));
    }

    function viewTransform() {
      return `translate(${state.view.x}, ${state.view.y}) scale(${state.view.scale})`;
    }

    function resetGraphViewState() {
      state.view = { x: 0, y: 0, scale: 1 };
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

    function layoutKey(nodes, edges, width, height) {
      return [
        Math.round(width),
        Math.round(height),
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

    function ensureLayout(nodes, edges, width, height) {
      nodes.forEach((node, index) => {
        if (!state.positions[node.id]) {
          state.positions[node.id] = initialPosition(index, nodes.length, width, height);
        }
      });
      const key = layoutKey(nodes, edges, width, height);
      if (state.layoutKey !== key && !state.dragging) {
        runForceLayout(nodes, edges, width, height);
        state.layoutKey = key;
      }
      return new Map(nodes.map(node => [node.id, state.positions[node.id]]));
    }

    function runForceLayout(nodes, edges, width, height) {
      const ids = new Set(nodes.map(node => node.id));
      const centerX = width / 2;
      const centerY = Math.min(height * .42, Math.max(140, height / 2));
      const visibleEdges = edges.filter(edge => ids.has(edge.source) && ids.has(edge.target));
      for (let step = 0; step < 90; step += 1) {
        const velocity = new Map(nodes.map(node => [node.id, { x: 0, y: 0 }]));
        for (let i = 0; i < nodes.length; i += 1) {
          for (let j = i + 1; j < nodes.length; j += 1) {
            const a = state.positions[nodes[i].id];
            const b = state.positions[nodes[j].id];
            const dx = a.x - b.x || .01;
            const dy = a.y - b.y || .01;
            const distance = Math.max(24, Math.hypot(dx, dy));
            const force = Math.min(90, 4200 / (distance * distance));
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
          const desired = edge.type === "mentions" ? 180 : 140;
          const force = (distance - desired) * .018;
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
          v.x += (centerX - point.x) * .012;
          v.y += (centerY - point.y) * .012;
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
      return `M ${start.x} ${start.y} Q ${mx} ${my} ${end.x} ${end.y}`;
    }

    function draw() {
      const svg = document.getElementById("graph");
      const width = Math.max(svg.clientWidth, 360);
      const height = Math.max(svg.clientHeight, 420);
      svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
      svg.innerHTML = `<defs>
        <marker id="arrow-default" markerWidth="14" markerHeight="14" refX="12" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,12 L13,6 z" fill="#64748b"></path></marker>
        <marker id="arrow-related" markerWidth="14" markerHeight="14" refX="12" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,12 L13,6 z" fill="#f97316"></path></marker>
        <marker id="arrow-selected" markerWidth="14" markerHeight="14" refX="12" refY="6" orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L0,12 L13,6 z" fill="#b42318"></path></marker>
      </defs>`;
      const layer = document.createElementNS("http://www.w3.org/2000/svg", "g");
      layer.setAttribute("class", "graph-layer");
      layer.setAttribute("transform", viewTransform());
      svg.append(layer);
      const nodes = visibleNodes();
      const edges = visibleEdges(nodes);
      const positions = ensureLayout(nodes, edges, width, height);
      const selected = state.selected;
      const selectedKey = selected?.key || "";
      const selectedNodeId = selected?.kind === "node" ? selected.value.id : "";
      const selectedEdge = selected?.kind === "edge" ? selected.value : null;
      const selectedEdgeNodes = selectedEdge ? new Set([selectedEdge.source, selectedEdge.target]) : new Set();
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
        const pathData = edgePath(source, target, occurrenceIndex, nodeRadius(sourceNode), nodeRadius(targetNode));
        const isSelected = selected?.kind === "edge" && edge.id === selectedKey;
        const isRelated = selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);
        const edgeClass = `edge ${edge.type} ${edge.confidence}${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`;
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
      for (const node of nodes) {
        const point = positions.get(node.id);
        if (!point) continue;
        const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
        const isSelected = selected?.kind === "node" && node.id === selectedKey;
        const isRelated = selectedEdgeNodes.has(node.id);
        group.setAttribute("class", `node ${node.kind !== "skill" ? "asset" : ""}${isSelected ? " selected" : ""}${isRelated ? " related" : ""}`);
        group.setAttribute("transform", `translate(${point.x}, ${point.y})`);
        group.addEventListener("pointerdown", event => beginNodeDrag(event, node));
        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("r", node.kind === "skill" ? "18" : "13");
        const text = document.createElementNS("http://www.w3.org/2000/svg", "text");
        text.setAttribute("text-anchor", "middle");
        text.setAttribute("y", "34");
        text.textContent = node.label || node.id;
        group.append(circle, text);
        layer.append(group);
      }
      document.getElementById("summary").textContent = `${nodes.length} nodes / ${edges.length} edges`;
      document.getElementById("viewState").textContent = `${Math.round(state.view.scale * 100)}%`;
      document.getElementById("status").textContent = `Generated ${graph.generatedAt} from ${graph.root}`;
      renderLists(nodes, edges);
    }

    function nodeItemHtml(node) {
      const description = node.description ? `<span>${escapeHtml(node.description)}</span>` : "";
      return `
        <div class="item-meta">
          <span class="badge ${nodeTypeClass(node)}">${escapeHtml(nodeTypeLabel(node))}</span>
          ${node.category ? `<span class="badge">${escapeHtml(node.category)}</span>` : ""}
        </div>
        <strong>${escapeHtml(node.label || node.id)}</strong>
        <span>${escapeHtml(nodePath(node))}</span>
        ${description}
      `;
    }

    function edgeItemHtml(edge) {
      const confidenceClass = edge.confidence === "high" ? "" : edge.confidence;
      return `
        <div class="item-meta">
          <span class="badge edge-type">${escapeHtml(relationLabel(edge.type))}</span>
          ${edge.confidence ? `<span class="badge ${escapeHtml(confidenceClass)}">${escapeHtml(edge.confidence)}</span>` : ""}
        </div>
        <strong>${escapeHtml(edgeSummary(edge))}</strong>
        <span>${escapeHtml(edge.origin || "inferred")}</span>
      `;
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
      const groups = groupBy(nodes, nodeTypeLabel);
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

    function renderEdgeGroups(edgeBox, edges) {
      const selectedNodeId = state.selected?.kind === "node" ? state.selected.value.id : "";
      if (selectedNodeId) {
        const outgoing = edges.filter(edge => edge.source === selectedNodeId);
        const incoming = edges.filter(edge => edge.target === selectedNodeId);
        if (outgoing.length) appendEdgeGroup(edgeBox, `Outgoing from ${nodeDisplay(selectedNodeId)}`, outgoing);
        if (incoming.length) appendEdgeGroup(edgeBox, `Incoming to ${nodeDisplay(selectedNodeId)}`, incoming);
        const other = edges.filter(edge => edge.source !== selectedNodeId && edge.target !== selectedNodeId);
        if (other.length) appendEdgeGroup(edgeBox, "Other related edges", other);
        return;
      }
      for (const [title, values] of groupBy(edges, edge => relationLabel(edge.type))) {
        appendEdgeGroup(edgeBox, title, values);
      }
    }

    function appendEdgeGroup(edgeBox, title, edges) {
      appendGroup(edgeBox, title, edges, edge => {
        const item = document.createElement("div");
        item.className = `item ${state.selected?.kind === "edge" && edge.id === state.selected.key ? "selected" : ""}`;
        item.innerHTML = edgeItemHtml(edge);
        item.addEventListener("click", () => select(edge, "edge"));
        return item;
      });
    }

    function renderLists(nodes, edges) {
      const nodeBox = document.getElementById("nodes");
      const edgeBox = document.getElementById("edges");
      nodeBox.innerHTML = "";
      edgeBox.innerHTML = "";
      renderNodeGroups(nodeBox, nodes);
      renderEdgeGroups(edgeBox, edges);
      renderDiagnostics();
    }

    function renderDiagnostics() {
      const box = document.getElementById("diagnostics");
      box.innerHTML = "";
      const diagnostics = visibleDiagnostics();
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
      return `
        <section class="summary-card">
          <h2>Node</h2>
          <div class="item-meta">
            <span class="badge ${nodeTypeClass(node)}">${escapeHtml(nodeTypeLabel(node))}</span>
            ${node.category ? `<span class="badge">${escapeHtml(node.category)}</span>` : ""}
          </div>
          <p><strong>${escapeHtml(node.label || node.id)}</strong></p>
          ${node.description ? `<p class="description">${escapeHtml(node.description)}</p>` : ""}
          <dl class="meta-grid">
            <dt>ID</dt><dd>${escapeHtml(node.id)}</dd>
            <dt>Path</dt><dd>${escapeHtml(nodePath(node))}</dd>
            <dt>Outgoing</dt><dd>${outgoing.length} dependencies / references</dd>
            <dt>Incoming</dt><dd>${incoming.length} dependents / mentions</dd>
            ${node.aliases?.length ? `<dt>Aliases</dt><dd>${node.aliases.map(escapeHtml).join(", ")}</dd>` : ""}
          </dl>
          <button id="clearSelection" type="button">Clear selection</button>
          ${rawJson(node)}
        </section>
      `;
    }

    function renderEdgeDetails(edge) {
      return `
        <section class="summary-card">
          <h2>Edge</h2>
          <div class="item-meta">
            <span class="badge edge-type">${escapeHtml(relationLabel(edge.type))}</span>
            ${edge.confidence ? `<span class="badge">${escapeHtml(edge.confidence)}</span>` : ""}
          </div>
          <p><strong>${escapeHtml(edgeSummary(edge))}</strong></p>
          <dl class="meta-grid">
            <dt>From</dt><dd>${escapeHtml(nodeDisplay(edge.source))}<br>${escapeHtml(edge.source)}</dd>
            <dt>To</dt><dd>${escapeHtml(nodeDisplay(edge.target))}<br>${escapeHtml(edge.target)}</dd>
            <dt>Origin</dt><dd>${escapeHtml(edge.origin || "inferred")}</dd>
            <dt>Evidence</dt><dd>${escapeHtml((edge.evidence || []).map(item => item.path || item.text).filter(Boolean).join(", ") || "N/A")}</dd>
          </dl>
          <button id="clearSelection" type="button">Clear selection</button>
          ${rawJson(edge)}
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
      document.getElementById("details").innerHTML = "";
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
      draw();
    }

    function endGraphPan() {
      window.removeEventListener("pointermove", panGraph);
      window.removeEventListener("pointerup", endGraphPan);
      window.removeEventListener("pointercancel", endGraphPan);
      state.panning = null;
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
      const width = Math.max(svg.clientWidth, 360);
      const height = Math.max(svg.clientHeight, 420);
      const point = graphPoint(svg, event);
      const distance = Math.hypot(point.x - state.dragging.startX, point.y - state.dragging.startY);
      if (!state.dragging.moved && distance <= 3) return;
      state.dragging.moved = true;
      state.positions[state.dragging.id] = clampPosition(
        {
          x: point.x + state.dragging.offsetX,
          y: point.y + state.dragging.offsetY,
        },
        width,
        height,
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

    for (const input of [nodeKind, extension, edgeType, confidence, search, showMentions]) {
      input.addEventListener("input", draw);
      input.addEventListener("change", draw);
    }
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
