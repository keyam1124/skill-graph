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
        self.assertIn("topPadding", html)

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
