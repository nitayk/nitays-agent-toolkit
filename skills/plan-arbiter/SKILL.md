---
name: plan-arbiter
description: "Turn TWO OR MORE competing plans (from different agents or models) into one executable direction — pick a winner, merge a stronger hybrid, or send both back. Use when handed ≥2 rival plans, session ids, transcripts, plan docs, PR descriptions, or pasted strategies and asked to compare/cross-review/merge/judge/choose/arbitrate, or when you want one recommended plan AND a recommendation for which agent/model should execute it. Separates plan-quality from executor-fit (the best critique and the cheapest-good executor need not be the same model). NOT for stress-testing a SINGLE plan with one model's personas (use /council), reviewing a finished DIFF (use /code-review or /requesting-code-review), or authoring a plan from scratch (use /brainstorming then /writing-plans). Triggers: 'arbitrate these plans', 'Codex vs Claude planned this — which do we run', 'merge the best of both plans', 'pick a plan and who should build it'."
---

# Plan Arbiter

Turn competing plans into one executable direction: preserve the best ideas, reject weak assumptions, and produce a clear handoff instead of a blended mush. Two capable agents rarely converge — one has the better architecture, another the better migration or validation path. The job is to decide, with evidence, and hand off cleanly.

Planning here is **read-only** unless the user explicitly asks you to implement after the decision.

## When to use (and when NOT to)

Use this when you hold **two or more rival plans** and need one direction out:

- Two agents/models each proposed a plan for the same task.
- A plan doc + a competing PR description, or several pasted strategies.
- You want a recommended plan *and* a recommendation of who should build it.

**When NOT to use — pick the right seam:**

- Stress-testing **one** plan via one model's opposing personas → `/council` (Architect/Skeptic/Pragmatist/Critic). Arbiter judges *between* plans; council debates *within* one.
- Reviewing a **finished diff** → `/code-review` / `/requesting-code-review`.
- **Authoring** a plan → `/brainstorming` → `/writing-plans`.
- Auditing what an agent **already built** → `/agent-watchdog`.

## The arbitration workflow

Copy this when applying the skill:

```
Arbiter:
- [ ] Collected the source plans (resolved to originals where possible)
- [ ] Normalized each into comparable claims
- [ ] Cross-reviewed each against the real code/task context
- [ ] Decided: adopt / hybrid / revise-first (tie-break order applied)
- [ ] Handoff memo written (incl. Executor Recommendation)
```

### 1. Collect the source plans

Accept plans as pasted text, files, session ids, transcript paths, PRs, comments, `/handoff` briefs, or chat history. Resolve the *original* artifacts when you can (via `claude-history` for prior sessions, `/agent-mailbox` for peers, `gh` for PRs) — a final summary hides the assumptions and prompt changes that produced it. If a plan is still being written and the user asked you to wait, monitor until it's done or blocked. If one can't be resolved, continue with what you have and mark the missing source as a risk.

### 2. Normalize

For each plan, extract: objective + scope; key assumptions + open questions; proposed files/modules/APIs/data shapes/UI states; implementation sequence; validation strategy; rollback/migration concerns; cost, complexity, and expected executor fit. Do not reward verbosity — prefer plans that are concrete, grounded in real code, and honest about trade-offs.

### 3. Cross-review — as if a capable peer wrote each

- Does it satisfy the user's *actual* request?
- Verify claims against the repo, docs, tests, and — for cross-service work — a code-graph MCP (`find_callers`, `get_affected_services`), not the plan's own assertions.
- Find hidden dependencies, missing tests, risky sequencing, vague steps, unnecessary scope, hard-to-reverse decisions.
- Notice complementary strengths across plans.
- Separate **plan quality** from **executor preference**: a cheaper/faster model can be the right implementer even when another model produced the best critique.

Dispatch subagents for independent review when the plans are large or the codebase is wide (e.g. a technical pass and a product pass in parallel).

### 4. Decide

Choose one outcome — **Adopt** (one plan ~as written), **Hybrid** (combine specific pieces), or **Revise-first** (both miss a key constraint or hinge on an unresolved decision). Tie-break order:

1. Correctness and fit to the user's request.
2. Grounding in real files, APIs, tests, data, UI behavior.
3. Simpler first implementation that doesn't block the intended future.
4. Better validation and rollback story.
5. Lower token/time cost to execute, once quality is acceptable.

### 5. Handoff memo

```md
Decision — Adopt Plan A / Hybrid / Revise-first.
Why — the deciding evidence and trade-offs.
Execution Plan — ordered steps with files/surfaces to touch.
Borrowed From Other Plans — useful pieces kept from non-winners.
Rejected — ideas intentionally dropped, with reasons.
Verification — tests, browser checks, CI, review, deploy checks needed.
Executor Recommendation — which agent/model should implement, and why.
```

When the user already asked for execution and the winner is clear, proceed after reporting the decision briefly. Otherwise stop at the handoff and ask for approval.

<!-- Adapted from BuilderIO/skills `plan-arbiter` (MIT). Original workflow credited in SOURCES.md; body rewritten to route to this toolkit's tools (claude-history, agent-mailbox, a code-graph MCP, subagents) and to make the plan-quality-vs-executor-fit split explicit for a mixed-model fleet. Distinct from /council (single-plan persona debate). -->

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
