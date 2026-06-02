import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def load_skillgraph_module():
    spec = importlib.util.spec_from_file_location(
        "skillgraph",
        REPO_ROOT / "scripts" / "skillgraph.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class SkillGraphViewerWorkflowTest(unittest.TestCase):
    maxDiff = None

    def test_collect_writes_graph_to_stdout_without_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/skillgraph.py",
                    "collect",
                    str(root),
                ],
                cwd=REPO_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(
                result.returncode,
                0,
                msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
            )
            self.assertFalse(
                (root / ".skillgraph").exists(),
                "collect must not create .skillgraph or other local graph files",
            )

            graph = json.loads(result.stdout)
            self._assert_graph(graph)
            self._assert_diagnostics(graph)

    def test_enriched_graph_annotations_and_viewer_html(self):
        skillgraph = load_skillgraph_module()
        graph = {
            "schemaVersion": "skillgraph-lite.v1",
            "generatedAt": "2026-05-28T00:00:00Z",
            "root": "/tmp/example",
            "nodes": [
                {"id": "skill.alpha", "kind": "skill", "label": "alpha", "path": "skills/alpha/SKILL.md"},
                {"id": "skill.beta", "kind": "skill", "label": "beta", "path": "skills/beta/SKILL.md"},
            ],
            "edges": [],
            "diagnostics": [],
            "nodeAnnotations": [
                {
                    "nodeId": "skill.alpha",
                    "label": "Alpha reviewer",
                    "summary": "Reviews the alpha flow.",
                    "suggestedCategory": "workflow",
                    "clusterId": "review",
                    "roleTags": ["entry"],
                    "triggerPhrases": ["review alpha"],
                },
                {"nodeId": "missing", "label": "Missing"},
            ],
            "inferredEdges": [
                {
                    "source": "skill.alpha",
                    "target": "skill.beta",
                    "type": "related_to",
                    "confidence": 0.74,
                    "rationale": "Both discuss review flow.",
                    "evidence": [{"path": "skills/alpha/SKILL.md", "text": "review"}],
                },
                {"source": "skill.alpha", "target": "missing", "type": "related_to"},
            ],
            "viewSuggestions": [],
        }

        enriched = skillgraph.enrich_graph(graph)
        alpha = self._node(enriched["nodes"], "skill.alpha")
        self.assertEqual(alpha["annotation"]["label"], "Alpha reviewer")
        self.assertEqual(len(enriched["nodeAnnotations"]), 1)
        self._assert_edge(
            enriched["edges"],
            "skill.alpha",
            "skill.beta",
            "depends_on",
            "agent_inferred",
        )
        diagnostic_types = {item["type"] for item in enriched["diagnostics"]}
        self.assertIn("invalid_agent_annotation", diagnostic_types)

        html = skillgraph.html_for_graph(enriched)
        self.assertIn("SkillGraph Viewer", html)
        self.assertNotIn("Inferred Cluster", html)
        self.assertNotIn("data-view-mode", html)
        self.assertNotIn('id="viewMode"', html)
        self.assertNotIn('class="segmented"', html)
        self.assertNotIn("syncViewModeButtons", html)
        self.assertNotIn('data-view-mode="artifacts"', html)
        self.assertIn("badge inferred", html)
        self.assertNotIn("showMentionsDefault", html)
        self.assertNotIn("Show mentions edges", html)
        self.assertIn("categoryFramesDefault = true", html)
        self.assertIn("Category frames", html)
        self.assertIn("category-frame", html)
        self.assertIn("category-frame-hit", html)
        self.assertIn("graphDisplayNodes = graph.nodes", html)
        self.assertNotIn('graph.nodes.filter(node => node.kind === "skill")', html)
        self.assertIn("edgeConnectsDisplayNodes", html)
        self.assertIn("categoryKeyForNode", html)
        self.assertIn("state.selectedCategory", html)
        self.assertIn("toggleCategorySelection", html)
        self.assertIn("clearCategorySelection", html)
        self.assertNotIn("nodePassesCategoryFilter", html)
        self.assertIn("groupBy(nodes, categoryKeyForNode)", html)
        self.assertIn("Clear category", html)
        self.assertIn("clearCategory.hidden = !state.selectedCategory", html)
        self.assertIn("panning && !panning.moved", html)
        self.assertIn('class", `category-frame${isSelected ? " selected" : ""}`', html)
        self.assertIn("annotation.suggestedCategory, annotation.clusterId, node.category", html)
        self.assertNotIn('nodes.filter(node => node.kind === "skill")', html)
        self.assertIn("layoutBoundsFor", html)
        self.assertIn("nodeAnchorMap", html)
        self.assertIn("pendingViewFit", html)
        self.assertIn("showNodeText", html)
        self.assertIn("edgeGeometry", html)
        self.assertNotIn("showEdgeText", html)
        self.assertNotIn("edge-label", html)
        self.assertNotIn("edgeType", html)
        self.assertNotIn("Edge type", html)
        self.assertNotIn("relationLabel", html)
        self.assertNotIn("Depends on", html)
        self.assertNotIn("badge edge-type", html)
        self.assertNotIn('id="edges"', html)
        self.assertNotIn('id="edgeCount"', html)
        self.assertNotIn("<h3>Relations</h3>", html)
        self.assertNotIn("Dependencies", html)
        self.assertNotIn("edgeItemHtml", html)
        self.assertNotIn("renderEdgeGroups", html)
        self.assertNotIn("appendEdgeGroup", html)
        self.assertIn("edgeDetailsJson", html)
        self.assertIn("const { id, type, ...payload } = edge", html)
        self.assertNotIn("node .node-type", html)
        self.assertNotIn('class", "node-type"', html)
        self.assertNotIn("nodeTypeLabel", html)
        self.assertNotIn("Uses reference", html)
        self.assertNotIn("Uses template", html)
        self.assertNotIn("Mentions", html)
        self.assertIn("compactCategoryLabel", html)
        self.assertIn("drawCategoryFrames", html)
        self.assertIn("drawCategoryFrameHits", html)
        self.assertIn("stroke-width: 1.15", html)
        self.assertIn("stroke-width: .8", html)
        self.assertIn('markerWidth="8"', html)
        self.assertIn("renderNodeDetails", html)
        self.assertIn("renderEdgeDetails", html)

    def _write_fixture(self, root):
        self._write(
            root / "skills" / "ddd_tactical" / "aggregate_design" / "SKILL.md",
            """\
            ---
            name: aggregate-design
            description: Use when designing DDD aggregate boundaries.
            ---
            # Aggregate Design

            Use this with clean-architecture-review when aggregate boundaries
            have architecture-level consequences.

            See [repository-design](../repository_design/SKILL.md).
            See [missing skill](../missing_skill/SKILL.md).
            Also read references/ddd/aggregate-rules.md and
            templates/ddd/aggregate-canvas.md.

            ## Related Skills

            - repository-design

            ## References

            - [Aggregate rules](../../../references/ddd/aggregate-rules.md)

            ## Templates

            - templates/ddd/aggregate-canvas.md
            """,
        )
        self._write(
            root / ".codex" / "skills" / "ddd_tactical" / "aggregate_design" / "SKILL.md",
            """\
            ---
            name: aggregate-design
            description: Codex copy of the same aggregate design skill.
            ---
            # Aggregate Design
            """,
        )
        self._write(
            root / "skills" / "ddd_tactical" / "aggregate_design" / "SKILL.en.md",
            """\
            ---
            name: aggregate-design
            description: English variant for aggregate design.
            ---
            # Aggregate Design
            """,
        )
        self._write(
            root / "skills" / "ddd_tactical" / "repository_design" / "SKILL.md",
            """\
            ---
            name: repository-design
            description: Use when designing repository interfaces.
            ---
            # Repository Design

            Repository guidance that supports aggregate-design.
            """,
        )
        self._write(
            root / "skills" / "architecture" / "clean_architecture_review" / "SKILL.md",
            """\
            ---
            name: clean-architecture-review
            description: Use when reviewing Clean Architecture boundaries.
            ---
            # Clean Architecture Review
            """,
        )
        self._write(
            root / "skills" / "lonely" / "unused_skill" / "SKILL.md",
            """\
            ---
            name: unused-skill
            description: Use when testing orphan diagnostics.
            ---
            # Unused Skill
            """,
        )
        self._write(root / "references" / "ddd" / "aggregate-rules.md", "# Aggregate rules\n")
        self._write(root / "templates" / "ddd" / "aggregate-canvas.md", "# Aggregate canvas\n")
        self._write(root / "scripts" / "helper.sh", "echo helper\n")
        self._write(root / "AGENTS.md", "# Agent instructions\n\nUse project rules.\n")
        self._write(
            root / ".github" / "copilot-instructions.md",
            "# Copilot instructions\n\nUse repo context.\n",
        )
        self._write(
            root / ".cursor" / "rules" / "backend.mdc",
            "---\ndescription: Backend rule.\n---\n# Backend\n",
        )

    def _assert_graph(self, graph):
        nodes = self._nodes(graph)
        self._node(nodes, "ddd-tactical.aggregate-design")
        self._node(nodes, "ddd-tactical.repository-design")
        self._node(nodes, "architecture.clean-architecture-review")

        aggregate = self._node(nodes, "ddd-tactical.aggregate-design")
        self.assertEqual(aggregate.get("path"), "skills/ddd_tactical/aggregate_design/SKILL.md")
        self.assertIn("SKILL.en.md", json.dumps(aggregate, ensure_ascii=False))
        self.assertIn(".codex/skills/ddd_tactical/aggregate_design/SKILL.md", aggregate.get("paths", []))

        kinds = {node.get("kind") for node in nodes}
        self.assertEqual(kinds, {"skill"})
        self.assertNotIn("references/ddd/aggregate-rules.md", {node.get("id") for node in nodes})
        self.assertNotIn("templates/ddd/aggregate-canvas.md", {node.get("id") for node in nodes})
        self.assertNotIn("scripts/helper.sh", {node.get("id") for node in nodes})

        edges = self._edges(graph)
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "ddd-tactical.repository-design", "depends_on", None)
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "architecture.clean-architecture-review", "depends_on", None)
        self._assert_edge(edges, "ddd-tactical.repository-design", "ddd-tactical.aggregate-design", "depends_on", None)
        self.assertFalse(any(edge.get("target", "").startswith(("references/", "templates/", "scripts/")) for edge in edges))
        self.assertFalse(any(edge.get("type") in {"mentions", "related_to", "uses_reference", "uses_template", "uses_script"} for edge in edges))

    def _assert_diagnostics(self, graph):
        diagnostic_types = {item.get("type") for item in self._diagnostics(graph)}
        self.assertIn("dangling_reference", diagnostic_types)
        self.assertIn("orphan_skill", diagnostic_types)
        self.assertNotIn("possible_relation", diagnostic_types)

    def _write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")

    def _nodes(self, payload):
        self.assertIsInstance(payload.get("nodes"), list)
        return payload["nodes"]

    def _edges(self, payload):
        self.assertIsInstance(payload.get("edges"), list)
        return payload["edges"]

    def _diagnostics(self, payload):
        self.assertIsInstance(payload.get("diagnostics"), list)
        return payload["diagnostics"]

    def _node(self, nodes, expected_id):
        for node in nodes:
            if node.get("id") == expected_id:
                return node
        self.fail(f"expected node id {expected_id!r}; got {[node.get('id') for node in nodes]}")

    def _assert_edge(self, edges, source, target, edge_type, origin):
        for edge in edges:
            if (
                edge.get("source") == source
                and edge.get("target") == target
                and edge.get("type") == edge_type
                and (origin is None or edge.get("origin") == origin)
            ):
                return
        self.fail(
            "expected edge "
            f"source={source!r} target={target!r} type={edge_type!r} origin={origin!r}; "
            f"got {edges!r}"
        )


if __name__ == "__main__":
    unittest.main()
