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

    def test_cli_help_lists_only_supported_workflow_commands(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SKILLGRAPH_CLI),
                "--help",
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
        self.assertIn("{collect,view,enrichment-template,merge}", result.stdout)
        self.assertNotIn("export", result.stdout)
        self.assertNotIn("render", result.stdout)
        self.assertNotIn("summary", result.stdout)

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
        self.assertNotIn("confidence", edge)
        self.assertNotIn("confidenceScore", edge)
        evidence = edge["evidence"]
        self.assertGreaterEqual(len(evidence), 2)
        self.assertTrue(all("startLine" in item for item in evidence))
        self.assertIn("markdown_link", {item.get("matchKind") for item in evidence})
        self.assertIn("reference_link", {item.get("matchKind") for item in evidence})

    def test_collect_turns_backticked_skill_names_into_direct_references(self):
        skillgraph = load_skillgraph_module()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(
                root / "skills" / "alpha" / "SKILL.md",
                """\
                ---
                name: alpha
                description: Use when testing explicit skill name references.
                ---
                # Alpha

                Plain text mentions gamma-special but should not create an edge.

                ## Related Skills
                - `beta`
                - `gamma-special`
                - `missing-skill`
                """,
            )
            self._write(
                root / "skills" / "beta" / "SKILL.md",
                """\
                ---
                name: beta
                description: Use when testing explicit skill name references.
                ---
                # Beta
                """,
            )
            self._write(
                root / "skills" / "gamma_special" / "SKILL.md",
                """\
                ---
                name: gamma-special
                description: Use when testing explicit skill name references.
                ---
                # Gamma
                """,
            )

            graph = skillgraph.analyze_graph(root)

        edges = self._edges(graph)
        beta = self._assert_edge(edges, "alpha", "beta", "direct_reference", "inline_code_reference")
        gamma = self._assert_edge(edges, "alpha", "gamma-special", "direct_reference", "inline_code_reference")
        self.assertEqual(beta.get("legacyType"), "depends_on")
        self.assertEqual(gamma.get("evidence", [{}])[0].get("matchKind"), "inline_code_reference")
        self.assertFalse(any(edge.get("target") == "missing-skill" for edge in edges))

    def test_enrich_graph_dedupes_inferred_edges(self):
        skillgraph = load_skillgraph_module()
        graph = {
            "schemaVersion": "skillgraph-lite.v1.2",
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
        self.assertNotIn("confidence", matching[0])
        self.assertNotIn("confidenceScore", matching[0])
        self.assertFalse(any("confidence" in edge or "confidenceScore" in edge for edge in enriched["inferredEdges"]))
        self.assertFalse(enriched["diagnostics"])

    def test_enriched_graph_annotations_and_viewer_html(self):
        skillgraph = load_skillgraph_module()
        graph = {
            "schemaVersion": "skillgraph-lite.v1.2",
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
            "viewSuggestions": [
                {"name": "legacy", "filter": {"confidence": "high", "origin": "agent_inferred"}}
            ],
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
        self.assertNotIn("confidence", inferred)
        self.assertNotIn("confidenceScore", inferred)
        diagnostic_types = {item["type"] for item in enriched["diagnostics"]}
        self.assertIn("invalid_agent_annotation", diagnostic_types)
        self.assertNotIn("viewSuggestions", enriched)

        html = skillgraph.html_for_graph(enriched)
        self.assertIn("SkillGraph Cartographer", html)
        self.assertIn("スキルの関係を、わかりやすく確認するツール", html)
        self.assertIn("スキル名・目的・説明・ファイル名で検索...", html)
        self.assertIn("スキルのつながり", html)
        self.assertIn("全体のまとめ", html)
        self.assertIn("検索結果", html)
        self.assertIn("確認が必要な項目", html)
        self.assertIn("直接書かれている関係", html)
        self.assertIn("AI が読み取った関係", html)
        self.assertIn("スキルのまとまり", html)
        self.assertIn("cluster-region", html)
        self.assertIn("groupDisplayName", html)
        self.assertIn("参照先が見つかりません", html)
        self.assertIn("同じ呼び名のスキルがあります", html)
        self.assertIn("置き場所ごとにスキル内容が違います", html)
        self.assertIn("AI が読み取った情報に不整合があります", html)
        self.assertIn("何が起きているか", html)
        self.assertIn("なぜ困るか", html)
        self.assertIn("根拠", html)
        self.assertIn("次にすること", html)
        self.assertIn("選択中のスキル", html)
        self.assertIn("つながりの詳細", html)
        self.assertNotIn("全体を把握する", html)
        self.assertNotIn("つながりを確認する", html)
        self.assertNotIn("気になる点", html)
        self.assertNotIn("スキルを探す", html)
        self.assertNotIn("state.activePage", html)
        self.assertNotIn("showDirectRelations", html)
        self.assertNotIn("showAiRelations", html)
        self.assertNotIn("showGroupFrames", html)
        self.assertNotIn("pageNav", html)
        self.assertNotIn("displayControls", html)
        self.assertNotIn("viewSuggestions", html)
        self.assertNotIn("confidence", html.lower())
        self.assertNotIn("根拠の強さ", html)
        self.assertNotIn("Schema valid", html)
        self.assertNotIn("Export", html)
        self.assertNotIn("Mermaid", html)
        self.assertNotIn("DOT", html)
        self.assertNotIn("Matrix", html)
        self.assertNotIn("Evidence", html)
        self.assertNotIn("Inspector", html)
        self.assertIn("relation-row", html)
        self.assertIn('markerWidth="8"', html)

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
        self.assertNotIn("confidence", edge)
        self.assertNotIn("confidenceScore", edge)
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
