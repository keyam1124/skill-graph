---
name: skillgraph-cartographer
description: >
  Use when inspecting, classifying, or rendering relationships among SKILL.md
  files. Run the local read-only skillgraph viewer runtime, enrich the base
  graph with this agent's own labels, categories, clusters, and inferred
  dependency hints, then display the result without writing graph files or
  modifying the repository.
---
# SkillGraph Cartographer

Explore a repository's current SkillGraph as a read-only map. The local
`scripts/skillgraph.py` tool scans files and opens the viewer; this Skill
defines the analysis flow that Codex or Claude Code performs around that tool.

The tool must not call Codex, Claude Code, external LLM APIs, or write graph
artifacts. The agent using this Skill performs any inference itself and passes
temporary enriched JSON to the viewer.

## When To Use

Use this Skill when the user asks to visualize, inspect, classify, label, group,
or explain dependency relationships among `SKILL.md` files, duplicate aliases,
or orphan skills.

## Workflow

1. Confirm the repository root. Unless the user provides another root, use the
   current working directory.
2. Collect the deterministic base graph:

   ```bash
   python3 scripts/skillgraph.py collect .
   ```

   Capture stdout as JSON. Do not expect `.skillgraph/` or HTML files to be
   created.

3. Read the base graph:

   - `nodes`
   - `edges`
   - `diagnostics`
   - `languageVariants`
   - relation `origin`, `confidence`, and `evidence`

4. Enrich the graph in memory with agent-inferred annotations:

   - concise display labels
   - one-line summaries
   - semantic suggested categories derived after comparing all scanned
     `SKILL.md` contents
   - cluster IDs
   - role tags
   - trigger phrases
   - optional inferred dependency hints
   - optional view suggestions

5. Keep deterministic and inferred information separate. Use `nodeAnnotations`
   for inferred node metadata and `inferredEdges` for inferred relationship
   hints. Do not rewrite existing deterministic nodes or edges.

6. Display the enriched graph:

   ```bash
   python3 scripts/skillgraph.py view --stdin
   ```

   Pipe the enriched JSON to stdin. The command prints the local viewer URL.

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
- Treat inferred labels, categories, clusters, and edges as temporary viewer
  annotations, not source of truth.
- Derive categories from what the skills do.
- Nodes are SKILL nodes only. Do not create nodes for references, templates,
  scripts, or instruction files.
- Treat links and textual mentions as the same `depends_on` relationship.
- Prefer weak inferred dependency edges with rationale over overstating uncertain
  relationships.
- If an inferred edge has no evidence, include a rationale and keep confidence
  low.

## Output Guidance

When reporting to the user, distinguish deterministic graph facts from this
agent's inferred annotations. Keep the summary focused on what the viewer shows:
notable clusters, likely entry skills, dependencies, and diagnostics that affect
understanding the current graph.
