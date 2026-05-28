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
            "related_to",
            "agent_inferred",
        )
        diagnostic_types = {item["type"] for item in enriched["diagnostics"]}
        self.assertIn("invalid_agent_annotation", diagnostic_types)

        html = skillgraph.html_for_graph(enriched)
        self.assertIn("SkillGraph Viewer", html)
        self.assertIn("Inferred Cluster", html)
        self.assertIn("badge inferred", html)
        self.assertIn("showMentionsDefault = false", html)
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
        self.assertIn("Shared Alias", aggregate.get("aliases", []))

        kinds = {node.get("kind") for node in nodes}
        self.assertTrue({"instruction", "rule", "reference", "template", "script"} <= kinds)
        self._node(nodes, "instruction.root.agents")
        self._node(nodes, "instruction.github.copilot-instructions")
        self._node(nodes, "rule.cursor.backend")
        self._node(nodes, "references/ddd/aggregate-rules.md")
        self._node(nodes, "templates/ddd/aggregate-canvas.md")
        self._node(nodes, "scripts/helper.sh")

        edges = self._edges(graph)
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "ddd-tactical.repository-design", "invokes", "sidecar")
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "architecture.clean-architecture-review", "related_to", "sidecar")
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "ddd-tactical.repository-design", "related_to", "related_section")
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "ddd-tactical.repository-design", "related_to", "markdown_link")
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "references/ddd/aggregate-rules.md", "uses_reference", None)
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "templates/ddd/aggregate-canvas.md", "uses_template", None)
        self._assert_edge(edges, "ddd-tactical.aggregate-design", "architecture.clean-architecture-review", "mentions", None)

    def _assert_diagnostics(self, graph):
        diagnostic_types = {item.get("type") for item in self._diagnostics(graph)}
        self.assertIn("duplicate_alias", diagnostic_types)
        self.assertIn("dangling_reference", diagnostic_types)
        self.assertIn("orphan_skill", diagnostic_types)

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
