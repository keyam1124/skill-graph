"""Schema metadata and lightweight validation for SkillGraph JSON."""

from __future__ import annotations

from typing import Any

from .enrichment import normalize_relation_type, valid_view_suggestion_filter
from .shared import CONFIDENCE_LABELS, SCHEMA_VERSION, normalize_confidence


GRAPH_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.invalid/skillgraph/graph.schema.json",
    "title": "SkillGraph Lite graph",
    "type": "object",
    "required": ["schemaVersion", "generatedAt", "root", "nodes", "edges", "diagnostics"],
    "properties": {
        "schemaVersion": {"const": SCHEMA_VERSION},
        "generatedAt": {"type": "string"},
        "root": {"type": "string"},
        "nodes": {"type": "array", "items": {"type": "object", "required": ["id", "path"]}},
        "edges": {"type": "array", "items": {"type": "object", "required": ["source", "target", "type", "origin"]}},
        "diagnostics": {"type": "array", "items": {"type": "object"}},
        "nodeAnnotations": {"type": "array", "items": {"type": "object"}},
        "inferredEdges": {"type": "array", "items": {"type": "object"}},
        "viewSuggestions": {"type": "array", "items": {"type": "object"}},
        "enrichmentCoverage": {"type": "object"},
    },
}


ENRICHMENT_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.invalid/skillgraph/enrichment.schema.json",
    "title": "SkillGraph Lite host-agent enrichment",
    "type": "object",
    "properties": {
        "annotationRun": {"type": "object"},
        "nodeAnnotations": {
            "type": "array",
            "items": {"type": "object", "required": ["nodeId"]},
        },
        "inferredEdges": {
            "type": "array",
            "items": {"type": "object", "required": ["source", "target"]},
        },
        "viewSuggestions": {"type": "array", "items": {"type": "object"}},
        "enrichmentCoverage": {"type": "object"},
    },
}


def validation_diag(severity: str, message: str, *, target: str | None = None, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "schema_validation",
        "severity": severity,
        "message": message,
    }
    if target:
        payload["target"] = target
    if evidence:
        payload["evidence"] = evidence
    return payload


def validate_graph_document(graph: dict[str, Any], *, strict: bool = False, enrichment_only: bool = False) -> list[dict[str, Any]]:
    diagnostics: list[dict[str, Any]] = []
    if not isinstance(graph, dict):
        return [validation_diag("error", "JSON document must be an object.")]

    if not enrichment_only:
        for key in ("schemaVersion", "generatedAt", "root", "nodes", "edges", "diagnostics"):
            if key not in graph:
                diagnostics.append(validation_diag("error", f"Missing required graph field: {key}.", target=key))

    nodes = graph.get("nodes", [])
    if nodes is None:
        nodes = []
    if not isinstance(nodes, list):
        diagnostics.append(validation_diag("error", "nodes must be a list.", target="nodes"))
        nodes = []
    node_ids: set[str] = set()
    known_paths: set[str] = set()
    for node in nodes:
        if not isinstance(node, dict):
            diagnostics.append(validation_diag("error", "node item must be an object.", target="nodes"))
            continue
        node_id = str(node.get("id") or "")
        if not node_id:
            diagnostics.append(validation_diag("error", "node.id is required.", target="nodes"))
        elif node_id in node_ids:
            diagnostics.append(validation_diag("error", f"node id {node_id} is duplicated.", target=node_id))
        node_ids.add(node_id)
        for value in [node.get("path"), node.get("dir"), *node.get("paths", []), *node.get("dirs", [])]:
            if value:
                known_paths.add(str(value))

    validate_edges(graph.get("edges", []), node_ids, known_paths, diagnostics, strict=strict, target="edges")
    validate_annotations(graph.get("nodeAnnotations", []), node_ids, diagnostics)
    validate_edges(graph.get("inferredEdges", []), node_ids, known_paths, diagnostics, strict=strict, target="inferredEdges", inferred=True)
    validate_view_suggestions(graph.get("viewSuggestions", []), diagnostics)
    return diagnostics


def validate_annotations(value: Any, node_ids: set[str], diagnostics: list[dict[str, Any]]) -> None:
    if value in (None, []):
        return
    if not isinstance(value, list):
        diagnostics.append(validation_diag("error", "nodeAnnotations must be a list.", target="nodeAnnotations"))
        return
    seen: set[str] = set()
    for annotation in value:
        if not isinstance(annotation, dict):
            diagnostics.append(validation_diag("error", "node annotation must be an object.", target="nodeAnnotations"))
            continue
        node_id = str(annotation.get("nodeId") or "")
        if node_id not in node_ids:
            diagnostics.append(validation_diag("error", f"node annotation target {node_id or '<missing>'} does not exist.", target=node_id or "nodeAnnotations"))
            continue
        if node_id in seen:
            diagnostics.append(validation_diag("warning", f"node annotation target {node_id} is duplicated.", target=node_id))
        seen.add(node_id)


def validate_edges(
    value: Any,
    node_ids: set[str],
    known_paths: set[str],
    diagnostics: list[dict[str, Any]],
    *,
    strict: bool,
    target: str,
    inferred: bool = False,
) -> None:
    if value in (None, []):
        return
    if not isinstance(value, list):
        diagnostics.append(validation_diag("error", f"{target} must be a list.", target=target))
        return
    seen: set[tuple[str, str, str, str]] = set()
    for edge in value:
        if not isinstance(edge, dict):
            diagnostics.append(validation_diag("error", f"{target} item must be an object.", target=target))
            continue
        source = str(edge.get("source") or "")
        edge_target = str(edge.get("target") or "")
        edge_type = normalize_relation_type(edge.get("type"))
        origin = str(edge.get("origin") or ("agent_inferred" if inferred else ""))
        if source not in node_ids or edge_target not in node_ids:
            diagnostics.append(validation_diag("error", f"edge {source or '<missing>'} -> {edge_target or '<missing>'} references an unknown node.", target=target))
        confidence = edge.get("confidence", "medium")
        label, _ = normalize_confidence(confidence)
        if isinstance(confidence, str) and confidence.strip().lower() not in CONFIDENCE_LABELS:
            diagnostics.append(validation_diag("warning", f"confidence {confidence!r} will be normalized to {label}.", target=target))
        key = (source, edge_target, edge_type, origin)
        if key in seen:
            diagnostics.append(validation_diag("warning", f"edge {source} -> {edge_target} duplicates source/target/type/origin.", target=target))
        seen.add(key)
        if strict and inferred and label == "high" and not str(edge.get("rationale") or "").strip():
            diagnostics.append(validation_diag("warning", "high-confidence inferred edge should include rationale.", target=target))
        evidence = edge.get("evidence", [])
        if evidence is None:
            continue
        if not isinstance(evidence, list):
            diagnostics.append(validation_diag("warning", "edge.evidence should be a list.", target=target))
            continue
        for item in evidence:
            if not isinstance(item, dict):
                diagnostics.append(validation_diag("warning", "edge evidence item should be an object.", target=target))
                continue
            path = item.get("path")
            if strict and path and known_paths and str(path) not in known_paths:
                diagnostics.append(validation_diag("warning", f"evidence path {path} is not a known skill path or directory.", target=target))


def validate_view_suggestions(value: Any, diagnostics: list[dict[str, Any]]) -> None:
    if value in (None, []):
        return
    if not isinstance(value, list):
        diagnostics.append(validation_diag("error", "viewSuggestions must be a list.", target="viewSuggestions"))
        return
    for suggestion in value:
        if not isinstance(suggestion, dict):
            diagnostics.append(validation_diag("error", "view suggestion must be an object.", target="viewSuggestions"))
            continue
        if not valid_view_suggestion_filter(suggestion.get("filter")):
            diagnostics.append(validation_diag("warning", "view suggestion filter contains unsupported keys.", target="viewSuggestions"))


def validation_report(graph: dict[str, Any], *, strict: bool = False, enrichment_only: bool = False) -> dict[str, Any]:
    diagnostics = validate_graph_document(graph, strict=strict, enrichment_only=enrichment_only)
    return {
        "valid": not any(item.get("severity") == "error" for item in diagnostics),
        "strict": strict,
        "diagnostics": diagnostics,
    }
