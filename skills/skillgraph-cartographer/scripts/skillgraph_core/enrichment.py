"""Normalize optional host-agent annotations for viewer display."""

from __future__ import annotations

import json
import re
from typing import Any

from .shared import graph_digest


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
    relation_type = str(value or "related_to").strip().lower()
    relation_type = re.sub(r"[^\w.-]+", "_", relation_type, flags=re.UNICODE).strip("_.-")
    return relation_type or "related_to"


def merge_enrichment(base_graph: dict[str, Any], annotations: dict[str, Any]) -> dict[str, Any]:
    graph = json.loads(json.dumps(base_graph))
    for key in ("annotationRun", "nodeAnnotations", "inferredEdges", "enrichmentCoverage"):
        if key in annotations:
            graph[key] = annotations[key]
    if "annotationRun" not in graph:
        graph["annotationRun"] = {"baseGraphDigest": graph_digest(base_graph)}
    return graph


def enrichment_template(base_graph: dict[str, Any], max_node_summary_chars: int = 800) -> dict[str, Any]:
    nodes = []
    for node in base_graph.get("nodes", []):
        if not isinstance(node, dict):
            continue
        nodes.append(
            {
                "nodeId": node.get("id"),
                "label": node.get("label"),
                "path": node.get("path"),
                "category": node.get("category"),
                "description": str(node.get("description") or "")[:max_node_summary_chars],
            }
        )
    return {
        "annotationRun": {
            "agent": "",
            "instructionVersion": "skillgraph-cartographer.v1",
            "baseGraphDigest": graph_digest(base_graph),
        },
        "nodes": nodes,
        "nodeAnnotations": [],
        "inferredEdges": [],
    }


def inferred_edge_key(edge: dict[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(edge.get("source") or ""),
        str(edge.get("target") or ""),
        str(edge.get("type") or ""),
        str(edge.get("origin") or ""),
        str(edge.get("rationale") or ""),
    )


def strip_edge_strength(edge: dict[str, Any]) -> dict[str, Any]:
    edge.pop("confidence", None)
    edge.pop("confidenceScore", None)
    return edge


def enrich_graph(graph: dict[str, Any]) -> dict[str, Any]:
    graph = json.loads(json.dumps(graph))
    nodes = graph.setdefault("nodes", [])
    edges = graph.setdefault("edges", [])
    diagnostics = graph.setdefault("diagnostics", [])
    for edge in edges:
        if isinstance(edge, dict):
            strip_edge_strength(edge)
    node_ids = {node.get("id") for node in nodes if node.get("id")}
    node_index = {node.get("id"): node for node in nodes if node.get("id")}

    valid_annotations: list[dict[str, Any]] = []
    seen_annotations: set[str] = set()
    for annotation in agent_list(graph, "nodeAnnotations", diagnostics):
        if not isinstance(annotation, dict):
            diagnostics.append(diagnostic_from_mapping({"value": annotation}, "Agent node annotation is not an object."))
            continue
        node_id = str(annotation.get("nodeId") or "")
        if node_id not in node_ids:
            diagnostics.append(diagnostic_from_mapping(annotation, f"Agent node annotation target {node_id or '<missing>'} does not exist."))
            continue
        if node_id in seen_annotations:
            diagnostics.append(diagnostic_from_mapping(annotation, f"Agent node annotation target {node_id} is duplicated."))
            continue
        seen_annotations.add(node_id)
        valid_annotations.append(annotation)
        node_index[node_id]["annotation"] = annotation
    graph["nodeAnnotations"] = valid_annotations

    valid_inferred_edges: list[dict[str, Any]] = []
    counters: dict[str, int] = {}
    existing_edges = {inferred_edge_key(edge) for edge in edges if isinstance(edge, dict)}
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
            "evidence": edge.get("evidence") if isinstance(edge.get("evidence"), list) else [],
            "rationale": edge.get("rationale") or "",
            "inferred": True,
        }
        valid_inferred_edges.append(normalized)
        key = inferred_edge_key(normalized)
        if key in existing_edges:
            continue
        existing_edges.add(key)
        edges.append(normalized)
    graph["inferredEdges"] = valid_inferred_edges

    graph.pop("viewSuggestions", None)
    return graph
