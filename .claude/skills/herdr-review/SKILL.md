---
name: herdr-review
description: Review the current branch's diff against main. Runs /code-review for bugs, checks this project's rules (single-owner modules, lint ratchet), then runs /update-docs to fix doc drift.
disable-model-invocation: true
argument-hint: "[base-branch | PR number | path]"
---

Spin up 2 herdr agents to review the PR (Target: `$ARGUMENTS`): one focused on correctness and edge cases, one on docs and type safety. Both must be read-only (no git checkout, use gh pr diff). Then merge their findings into one deduplicated table.