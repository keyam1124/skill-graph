# AGENTS.md

## Execution Rules

- When a final LLM-based evaluation is required, delegate it to a read-only SubAgent with reasoning effort `low`. Do not replace the final evaluation with the main agent or a high-reasoning agent.

## Document Generation

- When generating or reviewing Japanese documents, use the `polishing-documents` Skill and refine the text into natural Japanese suited to the expected readers and purpose.

## Git Operations

- Use branch names in the format `type/<task>-<slug>`. Examples: `feat/123-add-task-run-api`, `fix/task-456-claim-conflict`, `docs/git-operation-rules`.
- Align `type` with Conventional Commits. In general, use `feat`, `fix`, `docs`, `refactor`, `test`, or `chore`.
- Include `<task>` only when there is an agentflow Task, issue, or task number. Omit it otherwise.
- Make `<slug>` a short description using lowercase English letters, numbers, and hyphens.
- Use the Conventional Commits format `<type>(<scope>): <summary>` for commit messages. As a rule, keep the branch type and commit type aligned.
- Create pushes and PRs only after human confirmation.
- When AI creates a PR, check [.github/pull_request_template.md](.github/pull_request_template.md) and write the PR body according to its headings and checklist items. Do not leave non-applicable items blank; write `N/A` or a short reason instead.

## Safety

- Do not write secrets in Artifacts, logs, Task descriptions, or Decision rationales.
- Perform push, merge, and release-equivalent operations only after human confirmation.
- Avoid changes outside the requested scope. If such changes are necessary, state the reason and impact first.
