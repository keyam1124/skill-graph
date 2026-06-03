---
name: skillgraph-cartographer
description: >
  Use when inspecting, classifying, or rendering relationships among SKILL.md
  files in a target repository. Always use this skill when the user asks for a
  SkillGraph, skill map, skill dependency diagram, duplicate aliases, orphan
  skills, semantic clusters, or Codex/Claude Code-inferred skill relationships.
  Run the bundled read-only CLI, enrich the graph with host-agent reasoning,
  and display the result without modifying the target repository.
compatibility: Requires Python 3 and a local shell. No network access or external LLM API is required.
---
# SkillGraph Cartographer

Explore a target repository's current SkillGraph as a read-only map. This Agent
Skill includes its own CLI under `scripts/skillgraph.py`; always call the
bundled CLI, even when the target repository is somewhere else and does not
contain this tool.

The CLI is deterministic. It collects direct facts from files and serves the
local viewer. Semantic interpretation comes from the host agent that loaded this
Skill, such as Codex, Claude Code, Copilot, or another Agent Skills host. Do not
spawn nested Codex/Claude commands or call external LLM APIs for enrichment.

## When To Use

Use this Skill when the user asks to visualize, inspect, classify, label, group,
or explain relationships among `SKILL.md` files. Also use it for duplicate
aliases, orphan skills, semantic clusters, likely entry skills, dependency
diagrams, and inferred skill relationships.

## Workflow

1. Confirm the target repository root. Unless the user provides another root,
   use the current working directory.
2. Resolve `SKILL_DIR` to the directory that contains this `SKILL.md`. Do not
   assume the target repository has `scripts/skillgraph.py`.
3. Collect the deterministic base graph from the target repository:

   ```bash
   python3 "$SKILL_DIR/scripts/skillgraph.py" collect "$TARGET_REPO"
   ```

   Capture stdout as JSON. Do not expect `.skillgraph/` or HTML files to be
   created.

4. Read the base graph and preserve its facts:

   - `nodes`
   - `edges`
   - `diagnostics`
   - `languageVariants`
   - relation `origin`, `confidence`, and `evidence`

5. Run an agent inference pass in memory:

   - Read the `SKILL.md` files listed in `nodes[].path` when semantic grouping
     or relationship inference is requested.
   - Compare names, descriptions, triggers, workflow steps, referenced files,
     and shared concepts across all scanned skills.
   - Keep excerpts short and cite the relevant `SKILL.md` path in evidence.
   - Treat the host agent's reasoning as advisory annotation, not source truth.

6. Add agent-inferred annotations when useful:

   - concise display labels
   - one-line summaries
   - semantic suggested categories derived after comparing all scanned
     `SKILL.md` contents
   - cluster IDs
   - role tags
   - trigger phrases
   - optional inferred dependency hints
   - optional view suggestions

7. Keep deterministic and inferred information separate. Use `nodeAnnotations`
   for inferred node metadata and `inferredEdges` for inferred relationship
   hints. Do not rewrite existing deterministic nodes or edges.

8. Display the base or enriched graph:

   ```bash
   python3 "$SKILL_DIR/scripts/skillgraph.py" view --stdin
   ```

   Pipe the enriched JSON to stdin. The command prints the local viewer URL.

For a direct viewer run without custom enrichment, use the bundled wrapper:

```bash
SKILLGRAPH_REPO_ROOT="$TARGET_REPO" "$SKILL_DIR/scripts/view-skillgraph.sh" --no-open
```

## Enrichment JSON Shape

Add these top-level fields to the collected graph when useful:

```json
{
  "nodeAnnotations": [
    {
      "nodeId": "ddd-tactical.aggregate-design",
      "label": "Aggregate boundary design",
      "summary": "Helps decide aggregate boundaries and related tradeoffs.",
      "suggestedCategory": "ddd-tactical",
      "clusterId": "domain-modeling",
      "roleTags": ["specialist", "design-review"],
      "triggerPhrases": ["aggregate boundary", "DDD aggregate"]
    }
  ],
  "inferredEdges": [
    {
      "source": "ddd-tactical.aggregate-design",
      "target": "architecture.clean-architecture-review",
      "type": "depends_on",
      "confidence": 0.68,
      "rationale": "Aggregate boundary design depends on architecture boundary review context.",
      "evidence": [
        {
          "path": "skills/ddd_tactical/aggregate_design/SKILL.md",
          "text": "architecture-level consequences"
        }
      ]
    }
  ],
  "viewSuggestions": [
    {
      "name": "Domain modeling cluster",
      "description": "Focus on DDD tactical design skills.",
      "filter": {
        "clusterId": "domain-modeling"
      }
    }
  ]
}
```

## Rules

- Keep the workflow read-only.
- Do not create `.skillgraph/`.
- Do not write graph artifacts or per-skill configuration files.
- Do not edit source files as part of this visualization workflow.
- Do not propose write-back or approval workflows.
- Always call the bundled CLI from this Skill directory; do not rely on the
  target repository having a copy of the tool.
- Use host-agent reasoning for semantic labels, clusters, and inferred edges.
- Do not call nested agent CLIs or external LLM APIs.
- Treat inferred labels, categories, clusters, and edges as temporary viewer
  annotations, not source of truth.
- Derive categories from what the skills do.
- Nodes are SKILL nodes only. Do not create nodes for references, templates,
  scripts, or instruction files.
- Treat `SKILL.md` as free-form Markdown. Do not attach special meaning to
  fixed section names.
- Deterministic relations come from direct `SKILL.md` links, raw `SKILL.md` path
  references, and generic body mentions.
- Prefer weak inferred dependency edges with rationale over overstating uncertain
  relationships.
- If an inferred edge has no evidence, include a rationale and keep confidence
  low.

## Output Guidance

When reporting to the user, distinguish deterministic graph facts from this
agent's inferred annotations. Keep the summary focused on what the viewer shows:
notable clusters, likely entry skills, dependencies, and diagnostics that affect
understanding the current graph.

Report the local viewer URL when `view` starts. If the user only asked for JSON,
return the enriched graph JSON or a compact summary of the important nodes,
edges, clusters, and diagnostics.
