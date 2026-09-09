# Sources

This file tracks the upstream sources for community content in this repo.

Run `acr update` to refresh the community sources below.

## obra/superpowers

- **Repo**: https://github.com/obra/superpowers
- **License**: MIT
- **What we sync**:
  - `skills/` -- All 14 skills (brainstorming, test-driven-development, systematic-debugging, verification-before-completion, executing-plans, subagent-driven-development, writing-plans, dispatching-parallel-agents, requesting-code-review, receiving-code-review, using-git-worktrees, finishing-a-development-branch, using-superpowers, writing-skills)
  - `agents/` -- code-reviewer.md
  - `commands/` -- brainstorm.md, execute-plan.md, write-plan.md
  - `hooks/` -- hooks.json, session-start.sh, run-hook.cmd

## anthropics/skills

- **Repo**: https://github.com/anthropics/skills
- **License**: Apache 2.0 (example skills), source-available (document skills)
- **What we sync**:
  - `skills/docx`, `skills/pdf`, `skills/xlsx`, `skills/pptx` -- Document manipulation
  - `skills/mcp-builder` -- MCP server creation guide
  - `skills/skill-creator` -- Skill authoring guide
  - `skills/webapp-testing` -- Playwright web testing
  - `skills/frontend-design` -- Frontend design patterns
  - `skills/web-artifacts-builder` -- Web artifact creation
  - `spec/` -- Agent Skills specification
  - `template/` -- Skill template

## Private work toolkit (manual, not automated)

Some generic skills, agents, and commands were adapted from a private internal
work toolkit. Anything tied to that employer's internal systems (proprietary
tooling, infrastructure, internal repositories/hosts, product-specific stacks)
was intentionally **excluded or scrubbed** so this public pack stays generic.
This source is pulled by hand — it is **not** covered by `acr update`.

### 2026-09 sync

Refreshed the manual port after a long gap. Every ported file was scanned for
employer-identifying tokens (products, internal infra/MCP, internal hosts/orgs)
before inclusion; skills whose core purpose is an internal system were left out.

- **Added (generic, new to this pack):** `framework-uplift`, `doubt-driven-development`,
  `plan-arbiter`, `skill-scout`, `agent-watchdog`, `debugging-mcp-servers`,
  `agent-mailbox`, `flint-charting`, `loopcraft`, `deprecation-and-migration`.
- **Updated (upstream had matured):** `agent-browser`, `docker-patterns`,
  `generate-changelog`, `pr-workflow`, `security-audit`, `strategic-compact`,
  `fix-issue`, `github-ops`, `deep-research`, `multi-agent-branching`, `test-until-pass`.
- **Deliberately excluded** (core-bound to internal systems): the code-graph /
  topology family, internal deploy/telemetry onboarding, internal data/trace
  tooling, and internal knowledge/orchestration skills. A second pass will bring
  over skills that need per-file scrubbing (e.g. `e2e`, `create-pr`, `gh-search`).

## Custom (not synced from upstream)

These skills are original and maintained directly in this repo:

- `skills/session-memory` -- Persistent context across sessions
- `skills/git-workflow` -- Stable-state commit workflow
- `skills/pr-workflow` -- Full PR lifecycle management
- `skills/prd-generation` -- PRD generation via conversation
- `skills/task-breakdown` -- Break PRDs into actionable tasks
- `skills/multi-agent-branching` -- Feature-branch isolation
- `skills/setup-local-dev` -- Persistent dev server with pm2
- `skills/tdd-workflow` -- Red-Green-Refactor cycle
- `skills/code-cleanup` -- Three-phase cleanup
- `skills/best-practices-enforcement` -- Code quality validation
- `skills/debug-workflow` -- Log-Reproduce-Fix cycle

## Adding New Sources

`acr update` refreshes the community sources (obra/superpowers, anthropics/skills).
To add another community source, wire it into `acr update` and document it here.
