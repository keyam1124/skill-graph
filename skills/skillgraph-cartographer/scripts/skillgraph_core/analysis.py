"""Deterministic SkillGraph analysis and edge construction."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .registry import load_registry, skill_maps
from .shared import (
    SCHEMA_VERSION,
    SKILL_PATH_RE,
    LINK_RE,
    Diagnostic,
    Edge,
    clean_target,
    evidence,
    read_text,
    rel_path,
    strip_code_blocks,
    unique,
    utc_now,
)


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
        text = strip_code_blocks(read_text(skill_file))
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
    text = strip_code_blocks(source_text if source_text is not None else read_text(source_file))
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

    edges = merge_dependency_edges(edges)

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
