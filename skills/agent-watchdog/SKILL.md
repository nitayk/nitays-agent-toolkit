---
name: agent-watchdog
description: "Audit ANOTHER agent's finished (or in-flight) work against the ORIGINAL request — not its own summary. Use when handed a peer agent's session id, transcript, PR, branch, CI run, or mailbox handoff and asked to watch/babysit/verify/audit/compare/'did it actually do what I asked'. Reconstructs the contract from the real request, inspects what actually changed vs was claimed, and classifies every gap (missing / bug / verification-miss / scope-drift). Modes: watch-only, audit, audit-and-fix (only when authorized), compare. NOT for checking your OWN work before finishing (use /verification-before-completion), reviewing a finished diff in your own task (use /requesting-code-review or /code-review), relaying to a still-running peer (use /agent-mailbox), or reporting YOUR OWN status. Triggers: 'audit that agent', 'did the other session actually finish X', 'watch this run until done', 'compare what these two agents did'."
---

# Agent Watchdog

Watch another agent's work like a reviewer with a pager: reconstruct what was actually asked, verify the evidence, and close the gap between what was requested and what actually happened. An agent's own "done ✅" is a claim, not proof — long sessions quietly turn "I intended to" into "I did", and a summary is written by the same context that may have drifted.

This is the *cross-agent* audit seam. It is distinct from checking your own work: you have no privileged view into a peer session's reasoning, so you judge it the way an outsider must — from the original request and the artifacts, never from its self-report.

## When to use (and when NOT to)

Use this when the subject is **another agent's** run and the question is "did it deliver what was asked?":

- A peer session says "done, PR up" and you need to confirm before merging or building on it.
- A background/parallel agent went quiet and you need its real terminal state.
- Two agents (or two models) did the same task and you need to reconcile the differences.

**When NOT to use — pick the right seam:**

- Verifying **your own** work before you finish → `/verification-before-completion`.
- Reviewing a **finished diff within your own task** → `/requesting-code-review` / `/code-review`.
- Relaying to / coordinating with a **still-running** peer agent → `/agent-mailbox`.
- Reporting **your own** current status to the user → a separate status-report workflow.
- Arbitrating **competing plans** (not finished work) → `/plan-arbiter`.

## Choose the mode

Infer from the ask; if authority is unclear, **default to audit-only** and say what you *would* fix.

- **Watch-only** — monitor a session / PR / branch / CI run until it reaches a terminal state (done, blocked, stale, waiting-on-human). Read-only. Never edit.
- **Audit** — read the request, transcript, diff, tests, CI, comments; return a gap report. Read-only.
- **Audit-and-fix** — audit first, then make *narrow* fixes for clear gaps, only when the user authorized repair. No broad rewrites, no branch moves, no speculative changes.
- **Compare** — given ≥2 sessions/agents, compare each against the *same* original request and reconcile the important differences.

## The audit checklist

Copy this when applying the skill:

```
Watchdog:
- [ ] Mode chosen (default audit-only if authority unclear)
- [ ] Target resolved to primary sources (not summaries)
- [ ] Contract reconstructed from the ORIGINAL request
- [ ] Evidence inspected (diff, tests, CI, screenshots) — not the agent's claims
- [ ] Each issue classified (gap / bug / verification-miss / scope-drift / no-issue)
- [ ] Fixes only if authorized + narrow; unrelated local changes preserved
- [ ] Report leads with the outcome
```

### 1. Resolve the target — prefer the most direct source

Identify every artifact the user gave: session id, transcript path, PR, branch, commit, CI run, issue, Slack/mailbox link, pasted summary. Then resolve it from the *primary* source, not a recap:

- Prior Claude Code sessions → `claude-history` (search + `read ref=…` the specific messages).
- A live/parallel peer → `/agent-mailbox` (`peers`, `check`).
- PR / branch / CI state → `gh pr view`, `gh run view`, `git diff`, never the PR description alone.
- Cross-service blast radius of the change → a code-graph MCP (`find_callers` / `get_affected_services`).

If the run is still going and the user asked to watch, poll at a *reasonable* interval until it's done, blocked, stale, or clearly waiting on a human — don't busy-poll.

### 2. Reconstruct the contract — from the request, not the summary

Before judging, build a compact contract from **what the user actually asked the watched agent**:

- The original request and any later scope changes.
- Explicit constraints: branch rules, no-edit requests, versions, validation expectations, security/privacy limits.
- Implied acceptance criteria: user-visible behavior, tests, CI green, docs, screenshots.
- The agent's own final claims and any "could not do" caveats — held as *claims to check*, not facts.

Treat the user's request as the source of truth. An agent that summarizes its own work grades generously.

### 3. Audit the evidence, not the vibes

- Read the changed files **and** the relevant unchanged files around them.
- `git status` / `git diff` without reverting unrelated work.
- Compare commands the agent *claimed* to run against actual output where it exists.
- Inspect failed/skipped tests, CI logs, browser screenshots, review comments, error traces.
- For PR work, check unresolved threads + CI state from the source system.
- For UI work, prefer a screenshot or a real browser check over a prose claim.

Classify each finding:

- **Gap** — requested behavior missing or incomplete.
- **Bug** — the implementation likely fails or regresses.
- **Verification-miss** — result may be right, but the evidence is weak/absent (the most common silent failure — a merged PR that no one proved works).
- **Scope-drift** — changed something unrelated, or skipped a stated constraint.
- **No-issue** — concern already handled, *with evidence*.

### 4. Fix narrowly — only when authorized

When repair is authorized:

1. Fix only gaps with clear evidence.
2. Preserve unrelated local changes; never move/rebase/force-push a branch unless explicitly asked (see the workspace git boundary — nested repos have live remotes).
3. Use existing repo patterns and targeted tests.
4. Re-run the smallest useful validation after each meaningful fix.
5. If a fix needs a product decision, credential, destructive action, or broad rewrite — **stop and report the decision**, don't guess. Never self-merge, never push to a protected branch.

### 5. Report — lead with the outcome

```md
Status      — done / blocked / stale / still-running.
Requested   — what the user asked the watched agent to do.
Observed    — what it actually changed, claimed, and verified.
Gaps        — missing behavior, bugs, verification-misses, scope drift.
Fixes made  — files changed + validation run. (Omit for audit-only.)
Remaining risk — anything still unverified or waiting on CI/review/human.
```

Name exact files, commands, PRs, or session/thread ids when they matter. Keep it scannable — the user is triaging many agents.

<!-- Adapted from BuilderIO/skills `agent-watchdog` (MIT). Original workflow credited in SOURCES.md; body rewritten to route to this toolkit's tools (claude-history, agent-mailbox, gh, a code-graph MCP) and a git-boundary / draft-not-act posture. -->

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
