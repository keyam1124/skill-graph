"""Skill registry discovery and alias indexing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .shared import (
    SCHEMA_VERSION,
    Diagnostic,
    SkillRecord,
    first_h1,
    normalize_id_part,
    parse_frontmatter,
    read_text,
    rel_path,
    should_skip,
    unique,
    utc_now,
)


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
