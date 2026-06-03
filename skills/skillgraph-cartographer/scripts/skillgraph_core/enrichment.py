"""Normalize optional host-agent annotations for viewer display."""

from __future__ import annotations

import json
import re
from typing import Any


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
