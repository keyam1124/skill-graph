import json
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class SkillGraphLiteWorkflowTest(unittest.TestCase):
    maxDiff = None

    def test_all_builds_registry_graph_diagnostics_and_viewer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_fixture(root)

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/skillgraph.py",
                    "all",
                    "--root",
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
                msg=(
                    "skillgraph all should complete successfully.\n"
                    f"stdout:\n{result.stdout}\n"
                    f"stderr:\n{result.stderr}"
                ),
            )

            registry_path = root / ".skillgraph" / "registry.json"
            graph_path = root / ".skillgraph" / "graph.json"
            diagnostics_path = root / ".skillgraph" / "diagnostics.json"
            html_path = root / ".skillgraph" / "skillgraph.html"

            self.assertTrue(registry_path.exists(), "registry.json should be generated")
            self.assertTrue(graph_path.exists(), "graph.json should be generated")
            self.assertTrue(
                diagnostics_path.exists(), "diagnostics.json should be generated"
            )
            self.assertTrue(html_path.exists(), "skillgraph.html should be generated")

            registry = self._load_json(registry_path)
            graph = self._load_json(graph_path)
            diagnostics = self._load_json(diagnostics_path)
            html = html_path.read_text(encoding="utf-8")

            self._assert_registry(registry)
            self._assert_graph(graph)
            self._assert_diagnostics(graph, diagnostics)
            self._assert_html_viewer(html)

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
            root / "skills" / "ddd_tactical" / "aggregate_design" / "skillgraph.yaml",
            """\
            aliases:
              - Aggregate Design
              - Shared Alias
            category: ddd-tactical
            relations:
              invokes:
                - ddd-tactical.repository-design
              related_to:
                - architecture.clean-architecture-review
              uses_template:
                - templates/ddd/aggregate-canvas.md
              uses_reference:
                - references/ddd/aggregate-rules.md
              should_not_co_trigger:
                - ddd-tactical.missing-skill
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
            root / "skills" / "ddd_tactical" / "repository_design" / "skillgraph.yaml",
            """\
            aliases:
              - Shared Alias
            category: ddd-tactical
            """,
        )
        self._write(
            root
            / "skills"
            / "architecture"
            / "clean_architecture_review"
            / "SKILL.md",
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
        self._write(
            root / "references" / "ddd" / "aggregate-rules.md",
            "# Aggregate rules\n",
        )
        self._write(
            root / "templates" / "ddd" / "aggregate-canvas.md",
            "# Aggregate canvas\n",
        )

    def _assert_registry(self, registry):
        nodes = self._nodes(registry)
        aggregate = self._node(nodes, "ddd-tactical.aggregate-design")

        self.assertEqual(
            aggregate.get("path"),
            "skills/ddd_tactical/aggregate_design/SKILL.md",
        )
        self.assertEqual(aggregate.get("name") or aggregate.get("label"), "aggregate-design")
        self.assertEqual(
            aggregate.get("description"),
            "Use when designing DDD aggregate boundaries.",
        )
        self.assertEqual(aggregate.get("category"), "ddd-tactical")

        aliases = set(aggregate.get("aliases", []))
        self.assertIn("ddd-tactical.aggregate-design", aliases)
        self.assertIn("aggregate-design", aliases)
        self.assertIn("aggregate_design", aliases)
        self.assertIn("Aggregate Design", aliases)
        self.assertIn("Shared Alias", aliases)

        registry_text = json.dumps(registry, ensure_ascii=False)
        self.assertIn("SKILL.en.md", registry_text)
        self.assertIn("language", registry_text.lower())

    def _assert_graph(self, graph):
        nodes = self._nodes(graph)
        self._node(nodes, "ddd-tactical.aggregate-design")
        self._node(nodes, "ddd-tactical.repository-design")
        self._node(nodes, "architecture.clean-architecture-review")

        edges = self._edges(graph)
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "ddd-tactical.repository-design",
            "invokes",
            "sidecar",
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "architecture.clean-architecture-review",
            "related_to",
            "sidecar",
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "ddd-tactical.repository-design",
            "related_to",
            "related_section",
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "ddd-tactical.repository-design",
            "related_to",
            "markdown_link",
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "references/ddd/aggregate-rules.md",
            "uses_reference",
            None,
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "templates/ddd/aggregate-canvas.md",
            "uses_template",
            None,
        )
        self._assert_edge(
            edges,
            "ddd-tactical.aggregate-design",
            "architecture.clean-architecture-review",
            "mentions",
            None,
        )
        self.assertTrue(
            any(
                edge.get("type") == "language_variant"
                and edge.get("source") == "ddd-tactical.aggregate-design"
                for edge in edges
            ),
            f"expected a language_variant edge for aggregate-design; got {edges!r}",
        )

    def _assert_diagnostics(self, graph, diagnostics):
        all_diagnostics = self._diagnostics(graph) + self._diagnostics(diagnostics)
        diagnostic_types = {item.get("type") for item in all_diagnostics}

        self.assertIn("duplicate_alias", diagnostic_types)
        self.assertIn("dangling_reference", diagnostic_types)
        self.assertTrue(
            {"orphan_skill", "missing_sidecar"} & diagnostic_types,
            f"expected orphan_skill or missing_sidecar, got {diagnostic_types}",
        )

    def _assert_html_viewer(self, html):
        self.assertIn("mentions", html)
        self.assertRegex(
            html.lower(),
            r"(default|initial|checked|hidden|visible|display)[^\\n]{0,120}mentions",
        )
        self.assertRegex(
            html.lower(),
            r"mentions[^\\n]{0,120}(false|hidden|none|unchecked|off)",
        )
        self.assertIn(".node.selected", html)
        self.assertIn(".edge.selected", html)
        self.assertIn(".edge-hit", html)
        self.assertIn("Node Type", html)
        self.assertIn("All extensions", html)
        self.assertIn("nodeExtension", html)
        self.assertIn("height: 100vh", html)
        self.assertIn("order: -1", html)
        self.assertIn("clearSelection", html)
        self.assertIn("visibleDiagnostics", html)
        self.assertIn("Reset layout", html)
        self.assertIn("Reset view", html)
        self.assertIn('id="zoomIn"', html)
        self.assertIn('id="zoomOut"', html)
        self.assertIn('id="panUp"', html)
        self.assertIn('id="panDown"', html)
        self.assertIn('id="panLeft"', html)
        self.assertIn('id="panRight"', html)
        self.assertIn('id="viewState"', html)
        self.assertIn("runForceLayout", html)
        self.assertIn("edgePath", html)
        self.assertIn("beginNodeDrag", html)
        self.assertIn("beginGraphPan", html)
        self.assertIn("wheelZoomGraph", html)
        self.assertIn("viewTransform", html)
        self.assertIn("graphPoint", html)
        self.assertIn("nodeTypeLabel", html)
        self.assertIn("renderNodeDetails", html)
        self.assertIn("renderEdgeGroups", html)
        self.assertIn("renderDiagnostics", html)
        self.assertIn("Raw JSON", html)
        self.assertIn("arrow-default", html)
        self.assertIn("arrow-related", html)
        self.assertIn("arrow-selected", html)
        self.assertIn("pointermove", html)
        self.assertIn("touch-action: none", html)
        self.assertNotIn(
            'createElementNS("http://www.w3.org/2000/svg", "line")',
            html,
        )
        self.assertNotIn("marker-end: url(#arrow)", html)
        self._assert_contains_ordered(
            html,
            [
                "function nodeTypeLabel(node)",
                'if (node.kind === "skill") return "SKILL.md";',
                'return `Reference ${nodeExtension(node) || "file"}`;',
                "function relationLabel(type)",
                "uses_reference: \"Uses reference\"",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function resetGraphViewState()",
                "state.view = { x: 0, y: 0, scale: 1 };",
                "function zoomGraphAt(origin, nextScale)",
                "const scale = clampZoom(nextScale);",
                "state.view = {",
                "draw();",
                "function panGraphBy(dx, dy)",
                "state.view = { ...state.view, x: state.view.x + dx, y: state.view.y + dy };",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                'const layer = document.createElementNS("http://www.w3.org/2000/svg", "g");',
                'layer.setAttribute("class", "graph-layer");',
                'layer.setAttribute("transform", viewTransform());',
                "svg.append(layer);",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function resetGraphLayout()",
                "state.positions = {};",
                'state.layoutKey = "";',
                "resetGraphViewState();",
                "draw();",
                'resetLayout.addEventListener("click", resetGraphLayout);',
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                'const hitPath = document.createElementNS("http://www.w3.org/2000/svg", "path");',
                'hitPath.setAttribute("d", pathData);',
                'const path = document.createElementNS("http://www.w3.org/2000/svg", "path");',
                'path.setAttribute("d", pathData);',
                'path.setAttribute("marker-end", edgeMarker(isSelected, isRelated));',
                "layer.append(path);",
            ],
        )
        self.assertIn(
            "return `M ${start.x} ${start.y} Q ${mx} ${my} ${end.x} ${end.y}`;",
            html,
        )
        self._assert_contains_ordered(
            html,
            [
                "function edgePath(source, target, index = 0, sourceRadius = 18, targetRadius = 18)",
                "const sourceOffset = Math.min(sourceRadius + 8, available / 2);",
                "const targetOffset = Math.min(targetRadius + 16, available / 2);",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function graphPoint(svg, event)",
                "x: (point.x - state.view.x) / state.view.scale,",
                "y: (point.y - state.view.y) / state.view.scale,",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function beginGraphPan(event)",
                "event.target !== event.currentTarget",
                "state.panning = {",
                'window.addEventListener("pointermove", panGraph);',
                'window.addEventListener("pointerup", endGraphPan);',
                "function panGraph(event)",
                "state.panning.viewX + point.x - state.panning.startX",
                "state.panning.viewY + point.y - state.panning.startY",
                "draw();",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function wheelZoomGraph(event)",
                "event.preventDefault();",
                "const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12;",
                "zoomGraphAt(origin, state.view.scale * factor);",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                'group.addEventListener("pointerdown", event => beginNodeDrag(event, node));',
                "function beginNodeDrag(event, node)",
                "const point = graphPoint(svg, event);",
                "moved: false,",
                'window.addEventListener("pointermove", dragNode);',
                'window.addEventListener("pointerup", endNodeDrag);',
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function dragNode(event)",
                "const distance = Math.hypot",
                "if (!state.dragging.moved && distance <= 3) return;",
                "state.dragging.moved = true;",
                "state.positions[state.dragging.id] = clampPosition(",
                "draw();",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                'resetView.addEventListener("click", () => {',
                "resetGraphViewState();",
                "draw();",
                'zoomIn.addEventListener("click", () => zoomGraphBy(1.18));',
                'zoomOut.addEventListener("click", () => zoomGraphBy(1 / 1.18));',
                'panUp.addEventListener("click", () => panGraphBy(0, -72));',
                'panDown.addEventListener("click", () => panGraphBy(0, 72));',
                'panLeft.addEventListener("click", () => panGraphBy(-72, 0));',
                'panRight.addEventListener("click", () => panGraphBy(72, 0));',
                'document.getElementById("graph").addEventListener("pointerdown", beginGraphPan);',
                'document.getElementById("graph").addEventListener("wheel", wheelZoomGraph, { passive: false });',
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function appendGroup(container, title, items, renderItem)",
                "group-title",
                "function renderNodeGroups(nodeBox, nodes)",
                "const groups = groupBy(nodes, nodeTypeLabel);",
                "function renderEdgeGroups(edgeBox, edges)",
                "Outgoing from",
                "Incoming to",
                "function renderDiagnostics()",
                "groupBy(diagnostics",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function renderDetails(value, kind)",
                "if (kind === \"node\") return renderNodeDetails(value);",
                "function renderNodeDetails(node)",
                "<dt>Path</dt>",
                "<dt>Outgoing</dt>",
                "<dt>Incoming</dt>",
                "${rawJson(node)}",
                "function renderEdgeDetails(edge)",
                "<dt>From</dt>",
                "<dt>To</dt>",
                "${rawJson(edge)}",
                "function renderDiagnosticDetails(diag)",
                "${rawJson(diag)}",
            ],
        )
        self._assert_contains_ordered(
            html,
            [
                "function endNodeDrag()",
                "state.dragging = null;",
                "if (dragging && !dragging.moved) {",
                'select(dragging.node, "node");',
            ],
        )

    def _assert_contains_ordered(self, text, expected_parts):
        index = 0
        for part in expected_parts:
            next_index = text.find(part, index)
            self.assertNotEqual(
                next_index,
                -1,
                f"expected {part!r} after offset {index}",
            )
            index = next_index + len(part)

    def _write(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")

    def _load_json(self, path):
        with path.open(encoding="utf-8") as file:
            return json.load(file)

    def _nodes(self, payload):
        if isinstance(payload, dict):
            if isinstance(payload.get("nodes"), list):
                return payload["nodes"]
            if isinstance(payload.get("skills"), list):
                return payload["skills"]
            if isinstance(payload.get("registry"), list):
                return payload["registry"]
        self.fail(f"could not find node list in payload: {payload!r}")

    def _edges(self, payload):
        edges = payload.get("edges") if isinstance(payload, dict) else None
        self.assertIsInstance(edges, list, "graph.json should contain an edges list")
        return edges

    def _diagnostics(self, payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            value = payload.get("diagnostics")
            if isinstance(value, list):
                return value
            value = payload.get("items")
            if isinstance(value, list):
                return value
        return []

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
