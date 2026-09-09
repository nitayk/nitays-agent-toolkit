---
name: multi-agent-branching
description: "Use when multiple agents work simultaneously, during autonomous agent workflows, or when preventing merge conflicts. Do NOT skip when committing - always verify branch first."
last-reviewed: 2026-05-20
---
# Multi-Agent Branching

> **Invocation: routed.** Wrapper/gate skills measure 0% description-trigger
> recall — wire a CLAUDE.md rule or hook instructing the model to
> invoke this skill before file edits/commits in multi-agent work.
> Do NOT set `disable-model-invocation`: the routing rule works by model
> invocation. See `docs/decisions/skill-invocation-doctrine.md`.

Prevent concurrent AI agents from overwriting each other's work. All agents must work in feature branches, never on main/master.

## When to Use This Skill

**APPLY WHEN:**
- Multiple agents work simultaneously
- Autonomous agent workflows
- Preventing merge conflicts

**DO NOT SKIP**: Always verify branch before first edit in each session.

## Core Directive

Check branch BEFORE any file edit. Create feature branch if on main/master. Never commit directly to main/master.

## Process

### Step 1: Check Current Branch

Before ANY file edit:

```bash
git branch --show-current
```

- Feature branch: Safe to proceed
- main/master: Must create branch first

### Step 2: Create Branch If Needed

If on main/master:

```bash
BRANCH_NAME="feat/agent-$(date +%Y%m%d-%H%M%S)"
git checkout -b "$BRANCH_NAME"
```

Use timestamps to prevent collisions between agents.

### Step 3: Work on Feature Branch

Make changes, commit to feature branch (NOT main):

```bash
git add .
git commit -m "feat: implement calculator"
git push -u origin HEAD
```

### Step 4: Create PR When Done

Merge via PR:

```bash
gh pr create --title "feat: add calculator" --body "..."
```

## Branch Verification Pattern

Every session, before first edit:

```bash
CURRENT_BRANCH=$(git branch --show-current)
if [[ "$CURRENT_BRANCH" == "main" ]] || [[ "$CURRENT_BRANCH" == "master" ]]; then
  BRANCH_NAME="feat/agent-$(date +%Y%m%d-%H%M%S)"
  git checkout -b "$BRANCH_NAME"
fi
```

## Key Rules

**MUST DO:**
- Check branch before first edit
- Create feature branch if on main/master
- Use unique branch names (timestamps)
- Commit to feature branch only
- Merge via PR

**NEVER DO:**
- Commit directly to main/master
- Assume you are the only agent
- Force push without explicit permission
- Skip branch check before editing

## Troubleshooting

**Already committed to main**: Create feature branch from current state, reset main to origin/main

**Branch name collision**: Add more precision to timestamp

**Update main**: On feature branch, `git fetch origin`, `git merge origin/main`

## Related Skills

- git-workflow - Clean commit history on feature branch
- pr-workflow - Merge feature branch via PR

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
