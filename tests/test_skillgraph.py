import importlib.util
import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


sys.dont_write_bytecode = True

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLGRAPH_CLI = REPO_ROOT / "skills" / "skillgraph-cartographer" / "scripts" / "skillgraph.py"


def load_skillgraph_module():
    spec = importlib.util.spec_from_file_location(
        "skillgraph",
        SKILLGRAPH_CLI,
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
                    str(SKILLGRAPH_CLI),
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

    def test_collect_allows_free_form_skill_markdown_without_structure_diagnostics(self):
        skillgraph = load_skillgraph_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root / "skills" / "free_form" / "SKILL.md",
                """\
                # Free Form

                This skill intentionally has no frontmatter and no prescribed
                relationship section.
                """,
            )

            graph = skillgraph.analyze_graph(root)

        self._node(graph["nodes"], "free-form")
        diagnostic_types = {item.get("type") for item in self._diagnostics(graph)}
        self.assertNotIn("missing_frontmatter", diagnostic_types)

    def test_collect_ignores_skill_paths_inside_code_fences(self):
        skillgraph = load_skillgraph_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root / "skills" / "alpha" / "SKILL.md",
                """\
                ---
                name: alpha
                description: Use when testing fenced examples.
                ---
                # Alpha

                This skill only contains example JSON.

                ```json
                {
                  "path": "skills/beta/SKILL.md",
                  "link": "[beta](../beta/SKILL.md)"
                }
                ```
                """,
            )
            self._write(
                root / "skills" / "beta" / "SKILL.md",
                """\
                ---
                name: beta
                description: Use when testing fenced examples.
                ---
                # Beta
                """,
            )

            graph = skillgraph.analyze_graph(root)

        self.assertFalse(self._edges(graph))
        diagnostic_types = {item.get("type") for item in self._diagnostics(graph)}
        self.assertNotIn("dangling_reference", diagnostic_types)

    def test_collect_handles_markdown_link_edge_cases_with_line_evidence(self):
        skillgraph = load_skillgraph_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root / "skills" / "alpha" / "SKILL.md",
                """\
                ---
                name: alpha
                description: Use when testing Markdown link parsing.
                ---
                # Alpha

                See [beta](../beta/SKILL.md "title").
                See [beta reference][beta-skill].
                ![beta image](../beta/SKILL.md)
                Inline code should not count: `../beta/SKILL.md`.

                [beta-skill]: ../beta/SKILL.md
                """,
            )
            self._write(
                root / "skills" / "beta" / "SKILL.md",
                """\
                ---
                name: beta
                description: Use when testing Markdown link parsing.
                ---
                # Beta
                """,
            )

            graph = skillgraph.analyze_graph(root)

        edges = self._edges(graph)
        self.assertEqual(len(edges), 1)
        edge = edges[0]
        self.assertEqual(edge["type"], "direct_reference")
        self.assertEqual(edge["legacyType"], "depends_on")
        evidence = edge["evidence"]
        self.assertGreaterEqual(len(evidence), 2)
        self.assertTrue(all("startLine" in item for item in evidence))
        self.assertIn("markdown_link", {item.get("matchKind") for item in evidence})
        self.assertIn("reference_link", {item.get("matchKind") for item in evidence})

    def test_enrich_graph_dedupes_inferred_edges(self):
        skillgraph = load_skillgraph_module()
        graph = {
            "schemaVersion": "skillgraph-lite.v1.1",
            "generatedAt": "2026-05-28T00:00:00Z",
            "root": "/tmp/example",
            "nodes": [
                {"id": "skill.alpha", "kind": "skill", "label": "alpha", "path": "skills/alpha/SKILL.md"},
                {"id": "skill.beta", "kind": "skill", "label": "beta", "path": "skills/beta/SKILL.md"},
            ],
            "edges": [
                {
                    "id": "edge.inferred.skill.alpha.skill.beta.related_to.1",
                    "source": "skill.alpha",
                    "target": "skill.beta",
                    "type": "related_to",
                    "origin": "agent_inferred",
                    "confidence": "medium",
                    "rationale": "same",
                    "evidence": [],
                    "inferred": True,
                }
            ],
            "diagnostics": [],
            "inferredEdges": [
                {
                    "source": "skill.alpha",
                    "target": "skill.beta",
                    "type": "related_to",
                    "confidence": 0.7,
                    "rationale": "same",
                }
            ],
        }

        enriched = skillgraph.enrich_graph(graph)

        matching = [
            edge for edge in enriched["edges"]
            if edge.get("source") == "skill.alpha" and edge.get("target") == "skill.beta"
        ]
        self.assertEqual(len(matching), 1)
        self.assertFalse(enriched["diagnostics"])

    def test_enriched_graph_annotations_and_viewer_html(self):
        skillgraph = load_skillgraph_module()
        graph = {
            "schemaVersion": "skillgraph-lite.v1.1",
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
            "related_to",
            "agent_inferred",
        )
        inferred = self._assert_edge(enriched["edges"], "skill.alpha", "skill.beta", "related_to", "agent_inferred")
        self.assertEqual(inferred["confidence"], "medium")
        self.assertEqual(inferred["confidenceScore"], 0.74)
        diagnostic_types = {item["type"] for item in enriched["diagnostics"]}
        self.assertIn("invalid_agent_annotation", diagnostic_types)

        html = skillgraph.html_for_graph(enriched)
        self.assertIn("SkillGraph Viewer", html)
        self.assertIn("SkillGraph Cartographer", html)
        self.assertIn("Agent suggested views", html)
        self.assertIn("showDirectEdges", html)
        self.assertIn("showInferredEdges", html)
        self.assertIn("coverageBadge", html)
        self.assertIn("relationFocus", html)
        self.assertIn("Evidence ledger", html)
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
        self.assertIn("clearGraphSelection", html)
        self.assertIn("event.stopPropagation();\n          select(edge, \"edge\");", html)
        self.assertLess(
            html.index("drawCategoryFrameHits(layer, nodes"),
            html.index("const edgeOccurrences = new Map();"),
        )
        self.assertIn('class", `category-frame${isSelected ? " selected" : ""}`', html)
        self.assertIn("annotation.suggestedCategory, annotation.clusterId, node.category", html)
        self.assertNotIn('nodes.filter(node => node.kind === "skill")', html)
        self.assertIn("layoutBoundsFor", html)
        self.assertIn("layoutBoxFrame", html)
        self.assertIn("categoryFrameGroups(nodes, width, height)", html)
        self.assertIn("layoutGroupBoxes(nodes, width, height).values()", html)
        self.assertIn("sameGroup ? Math.max(120, edgeDistance * .78)", html)
        self.assertIn("anchorForce = .052", html)
        self.assertIn("nodeAnchorMap(visibleNodes(), bounds.width, bounds.height)", html)
        self.assertIn("nodeAnchorMap", html)
        self.assertIn("pendingViewFit", html)
        self.assertIn("showNodeText", html)
        self.assertIn("edgeGeometry", html)
        self.assertNotIn("showEdgeText", html)
        self.assertIn("edge-label", html)
        self.assertIn("drawEdgeLabel", html)
        self.assertNotIn("edgeType", html)
        self.assertNotIn("Edge type", html)
        self.assertNotIn("relationLabel", html)
        self.assertNotIn("Depends on", html)
        self.assertNotIn("badge edge-type", html)
        self.assertIn('id="edges"', html)
        self.assertIn('id="edgeCount"', html)
        self.assertIn("Agent reason", html)
        self.assertIn("Reference reason", html)
        self.assertIn("relationReason(edge)", html)
        self.assertNotIn("<h3>Relations</h3>", html)
        self.assertNotIn("Dependencies", html)
        self.assertIn("edgeItemHtml", html)
        self.assertIn("renderEdgeGroups", html)
        self.assertNotIn("appendEdgeGroup", html)
        self.assertIn("edgeDetailsJson", html)
        self.assertIn("const { id, type, ...payload } = edge", html)
        self.assertIn("edgeIndex", html)
        self.assertIn("relationTypeLabel", html)
        self.assertIn("renderRelationGroup", html)
        self.assertIn("relation-row", html)
        self.assertIn("Outgoing relations", html)
        self.assertIn("Incoming relations", html)
        self.assertIn("Source details", html)
        self.assertIn("inspectorPanel.hidden = false", html)
        self.assertIn("graph-workspace.inspector-hidden", html)
        self.assertIn("renderOverviewDetails", html)
        self.assertIn("<h2>Overview</h2>", html)
        self.assertNotIn("Trigger phrases", html)
        self.assertIn("interpreted", html)
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
            description: Use when testing unrelated skill handling.
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
        edge = self._assert_edge(edges, "ddd-tactical.aggregate-design", "ddd-tactical.repository-design", "direct_reference", None)
        self.assertEqual(edge.get("legacyType"), "depends_on")
        self.assertIn("startLine", edge.get("evidence", [{}])[0])
        self.assertIn("normalizedTarget", edge.get("evidence", [{}])[0])
        self.assertFalse(
            any(
                edge.get("source") == "ddd-tactical.aggregate-design"
                and edge.get("target") == "architecture.clean-architecture-review"
                for edge in edges
            ),
            "collect should not turn body text matches into deterministic edges",
        )
        self.assertFalse(
            any(
                edge.get("source") == "ddd-tactical.repository-design"
                and edge.get("target") == "ddd-tactical.aggregate-design"
                for edge in edges
            ),
            "collect should leave semantic skill relationships to host-agent inference",
        )
        self.assertFalse(any(edge.get("target", "").startswith(("references/", "templates/", "scripts/")) for edge in edges))
        self.assertFalse(any(edge.get("type") in {"mentions", "related_to", "uses_reference", "uses_template", "uses_script"} for edge in edges))

    def _assert_diagnostics(self, graph):
        diagnostic_types = {item.get("type") for item in self._diagnostics(graph)}
        self.assertIn("dangling_reference", diagnostic_types)
        self.assertNotIn("orphan_skill", diagnostic_types)
        self.assertNotIn("possible_relation", diagnostic_types)
        self.assertNotIn("missing_frontmatter", diagnostic_types)

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
                return edge
        self.fail(
            "expected edge "
            f"source={source!r} target={target!r} type={edge_type!r} origin={origin!r}; "
            f"got {edges!r}"
        )


if __name__ == "__main__":
    unittest.main()
