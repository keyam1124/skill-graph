---
name: skillgraph-cartographer
description: >
  Use when analyzing, updating, or rendering relationships among SKILL.md files.
  Run the local skillgraph scripts to build registry, graph, diagnostics, and HTML.
  Do not call external LLM APIs; use local deterministic outputs and propose
  SKILL.md or skillgraph.yaml updates for human review.
---
# SkillGraph Cartographer

Analyze local SKILL relationships with deterministic repository scripts, then
explain the generated graph and diagnostics to the user.

This Skill must not call external LLM APIs. Use only the current agent's normal
reasoning over local files and the outputs produced by `scripts/skillgraph.py`.

## When To Use

Use this Skill when the user asks to analyze, update, render, inspect, or explain
relationships among `SKILL.md` files, including related skills, path references,
mentions, duplicate aliases, orphan skills, dangling references, or description
conflicts.

## Workflow

1. Confirm the repository root. Unless the user provides another root, use the
   current working directory as the root.
2. Run the local SkillGraph pipeline:

   ```bash
   python scripts/skillgraph.py all --root .
   ```

3. Read the generated files:

   - `.skillgraph/registry.json`
   - `.skillgraph/graph.json`
   - `.skillgraph/diagnostics.json`

4. Summarize the results for the user:

   - registry size and notable Skill groups
   - graph nodes and edge types
   - diagnostics that require attention
   - generated viewer path, when `.skillgraph/skillgraph.html` exists

5. Explain relation quality and improvement candidates:

   - If a `mentions` edge is a false positive, propose a
     `skillgraph.yaml` `ignore_mentions` entry.
   - If an intentional relationship is missing, propose an explicit
     `skillgraph.yaml` relation.
   - If a relationship should be visible to humans, propose adding or updating
     the `Related Skills` section in the relevant `SKILL.md`.
   - If descriptions overlap or compete for the same trigger, propose clearer
     `description` boundaries.

6. Ask for human confirmation before editing `SKILL.md` or `skillgraph.yaml`.
   Do not edit implementation files or tests as part of this SkillGraph review
   workflow unless the user explicitly requests that separate work.

## Output Guidance

Keep the report concise and actionable. Distinguish deterministic script output
from the agent's interpretation. Do not present inferred relations as confirmed
facts unless they are backed by `graph.json`, `diagnostics.json`, `Related
Skills`, or `skillgraph.yaml`.
