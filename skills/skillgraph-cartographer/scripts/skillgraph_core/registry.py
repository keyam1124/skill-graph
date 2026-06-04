"""Skill registry discovery and alias indexing."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

from .shared import (
    SCHEMA_VERSION,
    Diagnostic,
    SkillRecord,
    content_digest,
    first_h1,
    host_scope_for_path,
    normalize_id_part,
    parse_frontmatter,
    read_text,
    rel_path,
    should_skip,
    unique,
    utc_now,
)


def path_matches_pattern(path: str, pattern: str) -> bool:
    normalized = pattern.strip()
    if not normalized or normalized.startswith("#"):
        return False
    normalized = normalized.rstrip("/")
    if "/" not in normalized:
        return any(part == normalized for part in path.split("/"))
    return fnmatch.fnmatch(path, normalized) or fnmatch.fnmatch(path, f"{normalized}/**")


def path_matches_any(path: str, patterns: list[str] | None) -> bool:
    return any(path_matches_pattern(path, pattern) for pattern in patterns or [])


def load_gitignore_patterns(root: Path) -> list[str]:
    path = root / ".gitignore"
    if not path.exists():
        return []
    return [
        line.strip()
        for line in read_text(path).splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


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


def scan_registry(
    root: Path,
    *,
    respect_gitignore: bool = False,
    exclude_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    max_skill_files: int | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    diagnostics: list[Diagnostic] = []
    gitignore_patterns = load_gitignore_patterns(root) if respect_gitignore else []
    skill_files: list[Path] = []
    for path in sorted(root.glob("**/SKILL.md"), key=lambda value: skill_file_sort_key(value, root)):
        rel = rel_path(path, root)
        included = path_matches_any(rel, include_patterns)
        excluded = (
            should_skip(path, root)
            or path_matches_any(rel, exclude_patterns)
            or path_matches_any(rel, gitignore_patterns)
        )
        if excluded and not included:
            continue
        skill_files.append(path)
    if max_skill_files is not None and len(skill_files) > max_skill_files:
        diagnostics.append(
            Diagnostic(
                type="scan_limit_exceeded",
                severity="warning",
                message=f"Found {len(skill_files)} skill files; scanning first {max_skill_files}.",
                evidence={"found": len(skill_files), "maxSkillFiles": max_skill_files},
            )
        )
        skill_files = skill_files[:max_skill_files]
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
            copies=[
                {
                    "host": host_scope_for_path(path),
                    "path": path,
                    "sha256": content_digest(text),
                }
            ],
        )
        if skill_id in records_by_id:
            merge_skill_record(records_by_id[skill_id], record)
        else:
            records_by_id[skill_id] = record

    diagnostics.extend(skill_copy_drift_diagnostics(records_by_id))

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
    existing.copies = merge_copies(existing.copies, incoming.copies)
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


def merge_copies(existing: list[dict[str, str]], incoming: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str, str]] = set()
    merged: list[dict[str, str]] = []
    for copy in [*existing, *incoming]:
        key = (copy.get("host", ""), copy.get("path", ""), copy.get("sha256", ""))
        if key in seen:
            continue
        seen.add(key)
        merged.append(copy)
    return merged


def skill_copy_drift_diagnostics(records_by_id: dict[str, SkillRecord]) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    for skill_id, record in sorted(records_by_id.items()):
        hosts = sorted({copy.get("host", "other") for copy in record.copies})
        digests = sorted({copy.get("sha256", "") for copy in record.copies if copy.get("sha256")})
        if len(hosts) <= 1 or len(digests) <= 1:
            continue
        diagnostics.append(
            Diagnostic(
                type="skill_copy_drift",
                severity="info",
                skill=skill_id,
                message="Same skill id appears in multiple host scopes with different content digests.",
                evidence={"copies": record.copies},
            )
        )
    return diagnostics


def load_registry(
    root: Path,
    *,
    respect_gitignore: bool = False,
    exclude_patterns: list[str] | None = None,
    include_patterns: list[str] | None = None,
    max_skill_files: int | None = None,
) -> dict[str, Any]:
    return scan_registry(
        root,
        respect_gitignore=respect_gitignore,
        exclude_patterns=exclude_patterns,
        include_patterns=include_patterns,
        max_skill_files=max_skill_files,
    )


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
