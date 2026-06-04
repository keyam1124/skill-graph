"""Stdout exports for SkillGraph data."""

from __future__ import annotations

import json
import re
from typing import Any

from .enrichment import enrich_graph
from .viewer import html_for_graph


def node_label(node: dict[str, Any]) -> str:
    annotation = node.get("annotation") if isinstance(node.get("annotation"), dict) else {}
    return str(annotation.get("label") or node.get("label") or node.get("id") or "")


def node_display(index: dict[str, dict[str, Any]], node_id: str) -> str:
    node = index.get(node_id, {})
    return node_label(node) or node_id


def relation_label(edge: dict[str, Any]) -> str:
    value = str(edge.get("type") or "related_to").replace("_", " ")
    confidence = edge.get("confidence")
    return f"{value} / {confidence}" if confidence else value


def mermaid_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"n_{cleaned}"
    return cleaned


def export_mermaid(graph: dict[str, Any]) -> str:
    graph = enrich_graph(graph)
    lines = ["flowchart LR"]
    node_ids = {node.get("id"): mermaid_id(str(node.get("id"))) for node in graph.get("nodes", []) if node.get("id")}
    for node in graph.get("nodes", []):
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        lines.append(f'  {node_ids[node_id]}["{escape_mermaid(node_label(node) or node_id)}"]')
    for edge in graph.get("edges", []):
        source = node_ids.get(edge.get("source"))
        target = node_ids.get(edge.get("target"))
        if not source or not target:
            continue
        lines.append(f'  {source} -->|"{escape_mermaid(relation_label(edge))}"| {target}')
    return "\n".join(lines) + "\n"


def escape_mermaid(value: str) -> str:
    return value.replace('"', "'")


def export_dot(graph: dict[str, Any]) -> str:
    graph = enrich_graph(graph)
    lines = ["digraph SkillGraph {", "  graph [rankdir=LR];", "  node [shape=box, style=rounded];"]
    for node in graph.get("nodes", []):
        node_id = str(node.get("id") or "")
        if node_id:
            lines.append(f'  "{escape_dot(node_id)}" [label="{escape_dot(node_label(node) or node_id)}"];')
    for edge in graph.get("edges", []):
        source = edge.get("source")
        target = edge.get("target")
        if source and target:
            lines.append(f'  "{escape_dot(str(source))}" -> "{escape_dot(str(target))}" [label="{escape_dot(relation_label(edge))}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def escape_dot(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def markdown_summary(graph: dict[str, Any]) -> str:
    graph = enrich_graph(graph)
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    diagnostics = graph.get("diagnostics", [])
    inferred_edges = [edge for edge in edges if edge.get("inferred") or edge.get("origin") == "agent_inferred"]
    direct_edges = [edge for edge in edges if edge not in inferred_edges]
    categories: dict[str, int] = {}
    for node in nodes:
        annotation = node.get("annotation") if isinstance(node.get("annotation"), dict) else {}
        category = str(annotation.get("suggestedCategory") or annotation.get("clusterId") or node.get("category") or "Uncategorized")
        categories[category] = categories.get(category, 0) + 1
    lines = [
        "# SkillGraph Summary",
        "",
        f"- Schema: `{graph.get('schemaVersion', '')}`",
        f"- Root: `{graph.get('root', '')}`",
        f"- Nodes: {len(nodes)}",
        f"- Edges: {len(edges)} ({len(direct_edges)} direct, {len(inferred_edges)} inferred)",
        f"- Diagnostics: {len(diagnostics)}",
        "",
        "## Categories",
        "",
    ]
    if categories:
        lines.extend(f"- {name}: {count}" for name, count in sorted(categories.items()))
    else:
        lines.append("- N/A")
    lines.extend(["", "## Diagnostics", ""])
    if diagnostics:
        for item in diagnostics[:20]:
            lines.append(f"- {item.get('severity', 'info')}: {item.get('message', item.get('type', 'diagnostic'))}")
    else:
        lines.append("- None")
    lines.extend(["", "## Edges", ""])
    index = {node.get("id"): node for node in nodes if node.get("id")}
    if edges:
        for edge in edges[:40]:
            lines.append(f"- {node_display(index, str(edge.get('source')))} -> {node_display(index, str(edge.get('target')))}: {relation_label(edge)}")
    else:
        lines.append("- None")
    return "\n".join(lines) + "\n"


def render_html(graph: dict[str, Any]) -> str:
    return html_for_graph(enrich_graph(graph))


def export_json(graph: dict[str, Any]) -> str:
    return json.dumps(enrich_graph(graph), ensure_ascii=False, indent=2) + "\n"
