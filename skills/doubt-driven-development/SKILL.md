---
name: doubt-driven-development
description: "In-flight adversarial verification of non-trivial decisions BEFORE they stand — materialize a fresh-context reviewer biased to DISPROVE, fed the artifact and its contract but never your conclusion. Use when about to commit non-trivial logic, assert a property the compiler can't verify (thread safety, idempotence, ordering, invariants), make an architectural call under uncertainty, or act irreversibly (production deploy, data migration, public API change). NOT the post-hoc gate: /requesting-code-review reviews a finished diff after a task; this cross-examines individual decisions while course-correction is still cheap. Triggers: 'doubt this', 'cross-examine this', 'adversarial review of my reasoning', 'stress this assumption before we proceed'."
last-reviewed: 2026-07-11
---

# Doubt-Driven Development

> **Invocation: routed.** Wrapper/gate skills measure 0% description-trigger
> recall — wire a CLAUDE.md rule or hook instructing the model to
> invoke this skill before letting a non-trivial decision stand.
> Do NOT set `disable-model-invocation`: the routing rule works by model
> invocation. See `docs/decisions/skill-invocation-doctrine.md`.

A confident answer is not a correct one. Long sessions accumulate context that quietly turns assumptions into "facts". Doubt-driven development is the discipline of materializing a fresh-context reviewer — biased to **disprove**, not approve — before any non-trivial output stands.

This is not a review of a finished artifact (`/requesting-code-review`, `/code-review`). It is an in-flight posture: non-trivial decisions get cross-examined while course-correction is still cheap.

## When to Use

A decision is **non-trivial** when at least one of these is true:

- It introduces or modifies branching logic
- It crosses a module or service boundary
- It asserts a property the type system or compiler cannot verify (thread safety, idempotence, ordering, invariants)
- Its correctness depends on context the future reader cannot see
- Its blast radius is irreversible (production deploy, data migration, public API change)

**When NOT to use:**

- Mechanical operations (renames, formatting, file moves), one-line changes with obvious correctness
- Following a clear, unambiguous user instruction
- The user has explicitly asked for speed over verification
- Post-task diff review — that's `/requesting-code-review`; both can apply to the same work at different moments

## The Process

Copy this checklist when applying the skill:

```
Doubt cycle:
- [ ] Step 1: CLAIM — wrote the claim + why-it-matters
- [ ] Step 2: EXTRACT — isolated artifact + contract, stripped reasoning
- [ ] Step 3: DOUBT — invoked fresh-context reviewer with adversarial prompt
- [ ] Step 4: RECONCILE — classified every finding against the artifact text
- [ ] Step 5: STOP — met stop condition (trivial findings, 3 cycles, or user override)
```

### Step 1: CLAIM — Surface what stands

Name the decision in two or three lines:

```
CLAIM: "The new caching layer is thread-safe under the
        read-heavy workload described in the spec."
WHY THIS MATTERS: a race here corrupts user data and is
                  hard to detect in QA.
```

If you can't write the claim that compactly, you have a vibe, not a decision. Surface it before scrutinizing it.

### Step 2: EXTRACT — Smallest reviewable unit

A fresh-context reviewer needs the **artifact** and the **contract**, not the journey.

- Code: the diff or the function — not the whole file
- Decision: the proposal in 3–5 sentences plus the constraints it has to satisfy
- Assertion: the claim plus the evidence that supposedly supports it

Strip your reasoning. If you hand over conclusions, you'll get back validation of your conclusions. The unit must be small enough that a reviewer can hold it in mind in one read — if it's a 500-line PR, decompose first.

### Step 3: DOUBT — Invoke the fresh-context reviewer

Dispatch a subagent (in Claude Code, subagents start with isolated context by design). The reviewer's prompt **must be adversarial** — framing decides the answer:

```
Adversarial review. Find what is wrong with this artifact.
Assume the author is overconfident. Look for:
- Unstated assumptions
- Edge cases not handled
- Hidden coupling or shared state
- Ways the contract could be violated
- Existing conventions this might break
- Failure modes under unexpected input

Do NOT validate. Do NOT summarize. Find issues, or state
explicitly that you cannot find any after thorough examination.

ARTIFACT: <paste artifact>
CONTRACT: <paste contract>
```

**Pass ARTIFACT + CONTRACT only. Do NOT pass the CLAIM.** Handing the reviewer your conclusion biases it toward agreement. The reviewer must independently determine whether the artifact satisfies the contract.

If you use a reviewer persona that produces balanced strengths-and-weaknesses verdicts, paste the adversarial prompt verbatim so it overrides the persona's default response shape — doubt-driven needs issues-only output.

**Cross-model second opinion:** a different-vendor model can catch blind spots a single model shares with itself, but measured experience says adopt it narrowly: reports-only (a human or the orchestrating model adjudicates — never auto-act), gated to high-blast-radius artifacts (auth, crypto, cross-service contracts), and fed the full changed files, not just the diff — cross-model reviewers judging isolated hunks produce confident false positives, and two vendors agreeing is NOT a validity signal. Never invoke an external CLI without explicit user authorization, and pipe the prompt via stdin from a file (never interpolate an artifact into a shell-quoted argument).

### Step 4: RECONCILE — Fold findings back

The reviewer's output is data, not verdict. **You are still the orchestrator.** Re-read the artifact text against each finding before classifying — rubber-stamping the reviewer is the same failure mode as ignoring it.

For each finding, classify in this **precedence order** (first matching class wins):

1. **Contract misread** — the reviewer flagged something because the CONTRACT you provided was unclear or incomplete. Fix the contract first, re-classify on the next cycle.
2. **Valid + actionable** — real issue requiring a change to the artifact. Change it, re-loop.
3. **Valid trade-off** — issue is real but cost of fixing exceeds cost of accepting. Document the trade-off explicitly so the user sees it.
4. **Noise** — the reviewer flagged something that's actually correct under context it didn't have. Note it, move on, and ask: would adding that context to the contract have prevented the false flag?

A fresh reviewer can be wrong because it lacks context. Don't defer just because it's "fresh."

### Step 5: STOP — Bounded loop, not recursion

Stop when:

- The next iteration returns only trivial or already-considered findings, **or**
- 3 cycles completed (escalate to the user, don't grind a fourth alone), **or**
- The user explicitly says "ship it"

If after 3 cycles the reviewer still surfaces substantive issues, the artifact may not be ready — surface this to the user; three unresolved cycles is information about the artifact, not a reason to keep looping. If 3 cycles feels insufficient because the artifact is large, the artifact is too big — return to Step 2 and decompose. Do not lift the bound.

## Common Rationalizations

| Rationalization | Reality |
|---|---|
| "I'm confident, skip the doubt step" | Confidence correlates poorly with correctness on novel problems. Moments of certainty are exactly when blind spots hide. |
| "Spawning a reviewer is expensive" | Debugging a wrong commit in production is more expensive. The check is bounded; the bug isn't. |
| "The reviewer will just nitpick" | Only if unscoped. Constrain the prompt to "issues that would make this fail under the contract." |
| "I'll do doubt at the end with the PR review" | The PR review is a final gate. Doubt-driven catches wrong directions early when course-correction is cheap. By PR time it's too late. |
| "If I doubt every step I'll never ship" | The skill applies to non-trivial decisions, not every keystroke. Re-read "When NOT to Use." |
| "The reviewer disagreed so I was wrong" | The reviewer lacks your context — disagreement is information, not verdict. Re-read the artifact, classify, then decide. |

## Red Flags

- Spawning a fresh-context reviewer for a one-line rename or formatting change
- Treating reviewer output as authoritative without re-reading the artifact text
- Prompting the reviewer with "is this good?" instead of "find issues"
- Passing the CLAIM or your reasoning to the reviewer (biases toward agreement)
- Re-spawning fresh-context on an unchanged artifact (you'll get the same findings; you're stalling)
- Looping past 3 cycles without escalating to the user
- **Doubt theater (checkable signal):** across 2+ cycles where the reviewer surfaced substantive findings, zero findings were classified as actionable. You are validating, not doubting. Stop and escalate.
- Doubting only after committing — that's the post-hoc review, not doubt-driven development

## Pair with

- `/requesting-code-review` — complementary, different moment: that is the post-task fresh-context review of a finished diff; this is the per-decision in-flight cross-examination. Use both on the same work.
- `/verification-before-completion` — verification proves what the code *does*; doubt-driven stress-tests what you *decided*. Run doubt before building, verification before claiming done.
- `/tdd-workflow` — TDD's RED step is doubt made concrete: a failing test is a disproof attempt, and it satisfies the doubt step for behavioral claims.
- `/council` — for decision *trade-offs* with multiple valid paths, convene the council; for verifying a single decision is not wrong, run a doubt cycle.
- `/systematic-debugging` — when the reviewer surfaces a real failure mode, drop into debugging to localize and fix.

<!--
Adapted from addyosmani/agent-skills `doubt-driven-development` (https://github.com/addyosmani/agent-skills),
MIT License, © Addy Osmani. Copied once with attribution — not auto-synced; distilled and the cross-model
section replaced with this repo's measured cross-model review policy. See SOURCES.md.
Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths.
-->
