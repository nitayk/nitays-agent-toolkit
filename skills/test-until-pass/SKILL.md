---
name: test-until-pass
description: "Use when failing tests need a bounded fix-and-retry loop with explicit guardrails (flake detection, no-progress halt, anti-weakening rule). Targets the specific test(s) that failed, not the whole suite. Triggers: 'fix the tests', 'test until pass', '/test-until-pass'. Do NOT use to run tests once (use the test runner directly), to fix tests known to be flaky (fix flakiness first), or for tests where the failure is genuinely a spec change (re-spec first)."
last-reviewed: 2026-05-20
---

# Test Until Pass

> **Invocation: routed.** Its routing surface is a by-name cross-reference from
> a reachable skill: `/e2e` Phase 8 dispatches it ("For flaky suites or long
> retry cycles, invoke `/test-until-pass`"). Do NOT set
> `disable-model-invocation`: it would break that dispatch, leaving Phase 8's
> retry loop with no bounded implementation. The guardrails are in the body
> (anti-patterns, stop conditions, iteration cap), not the frontmatter.
> See `docs/decisions/skill-invocation-doctrine.md`.

Bounded run → analyze → fix → re-run loop with explicit stop conditions. **Loops are dangerous** without guardrails — this skill is the disciplined version.

## When to use

**APPLY WHEN:**
- One or more tests are failing and the user wants them fixed
- The failure is reproducible (same input → same fail) — flakes are out of scope
- The expected behavior is unambiguous (the test asserts the correct thing)

**SKIP WHEN:**
- User wants to run tests once with no fix loop → use `pytest` / `go test` / `vitest` directly
- The test is *flaky* (intermittent fail) — fix the flake first; this skill assumes deterministic failure
- The failure is actually a spec disagreement — re-spec first; don't loop "fixing" a test that asserts the wrong thing
- The fix is obvious (one-line typo) — just fix it

## Anti-patterns (refuse these — load-bearing)

- **Weakening assertions to make tests pass.** Do NOT change `assert x == 5` to `assert x is not None` to make a failing test green. Do NOT add `if False:` to skip a check. Do NOT add `pytest.skip` / `it.skip` / `t.Skip` without an explicit user request and a recorded reason. If the *test* is wrong, fix the test deliberately and document why; don't quietly weaken it inside the loop.
- **Same edit twice.** If iteration N's edit doesn't change the failure output, iteration N+1 with the same edit shape is wasted. Stop, escalate, ask. The pattern is: same diff → same failure → repeat. Detect it.
- **Whole-suite re-runs after a 1-file change.** If only `src/auth.py` changed, run `pytest tests/auth/` (or the smallest unit that exercises the change), not the whole suite. Whole-suite runs every iteration burn time and tokens.
- **Catching exceptions to suppress failures.** Wrapping the failing call in a `try` that swallows the exception is the worst-shape fix. Refuse.
- **Hallucinating fixture state.** When a test fails because a fixture is missing, do not invent fixture data — read the actual fixture, understand what it should contain, fix the source.
- **Committing, pushing, or opening anything from inside the loop.** The loop edits and re-runs; it does not `git commit`, `git push`, open a PR, or merge. When the cap is hit or a stop condition fires, stop and report to the human — never "finish the job" by landing the change.

## Stop conditions (the loop ENDS when ANY of these trigger)

| Condition | Action |
|---|---|
| All target tests pass | Done — report the diff that fixed it |
| Max iterations reached (default 5) | Stop, summarize what was tried, escalate to user |
| Same test, different failure shape, **no edit between runs** | Flake detector — stop, report flake; do NOT keep "fixing" |
| Same test, different failure shape, edit between runs | Progress (a cascading bug surfaced) — continue iterating |
| Same test, same failure shape after an edit | No-progress halt — stop, the current approach isn't working |
| User pre-approved cap (e.g. `--max=3`) reached | Stop |

## Workflow

### Step 1 — Scope to the failure

Run only the failing test(s):
- `pytest tests/path/to/test_failing.py::TestClass::test_name -x -vv`
- `go test -run TestSpecific ./path/to/pkg`
- `vitest run path/to/file.test.ts -t "test name"`
- `sbt "testOnly com.example.MySpec -- -z 'specific behavior'"`

If the user said "fix the tests" without naming which, run the smallest module that includes the changed code, not the whole suite. Identify the failing tests, then narrow.

### Step 2 — Analyze the failure

Read the actual error output. Don't infer from test name. Distinguish:
- **Assertion failure** — code produced wrong value
- **Exception** — code crashed before assertion (bug or fixture issue)
- **Setup failure** — fixture / mock / DB state wrong
- **Timeout** — async, deadlock, or genuinely slow code
- **Snapshot mismatch** — output changed; was the change intentional?

If the failure is a *snapshot mismatch* and the change is intentional, regenerate the snapshot deliberately (don't loop). If the change is unintentional, fix the source.

### Step 3 — Form a hypothesis, make ONE change

Don't shotgun changes. Pick the highest-confidence fix, apply it, re-run.

### Step 4 — Re-run the SAME failing test (not the whole suite)

`pytest -x` or equivalent — stop on first failure. Re-running the whole suite when one test failed wastes time.

### Step 5 — Detect flakes and no-progress

Track per iteration:
- Test names that failed
- Failure shape (assertion message, stack frame, error class)

If iteration N+1 has the *same test* with a *different* failure shape **and you made no edit between the runs** → flake. Stop, report. If you did edit between runs, a different failure shape is progress, not flake: a cascading bug just surfaced.
If iteration N+1 has the *same test* with the *same* failure shape after an edit → no-progress. Stop.

### Step 6 — Cap iterations

Default 5. Configurable. When cap is reached:
- Summarize: which tests still fail, what was tried, what was tried-and-rolled-back
- Surface for user decision — don't silently exceed
- Never raise the cap on your own authority — a higher cap comes from an explicit user request, not from the loop's own sense that it is close.

## Example output (cap reached)

```
test-until-pass: stopping at iteration 5/5

Still failing:
- tests/auth/test_login.py::test_expired_token_returns_401 (assertion: expected 401, got 200)

Tried:
1. Fixed token expiry comparison (src/auth/jwt.py:84) → still failing, same message
2. Added token validation to middleware (src/auth/middleware.py:22) → still failing, same message
3. Reverted #2, fixed expiry math (src/auth/jwt.py:91) → still failing, same message
4. Reverted #3, examined fixture token (tests/auth/fixtures.py) → fixture token has no `exp` claim
5. Added `exp` claim to fixture (tests/auth/fixtures.py:34) → still failing, same message

Hypothesis: middleware path doesn't actually validate tokens — production path does. Test is hitting a different route.

Recommend: human investigation. Likely test vs. middleware mismatch, not a JWT bug.
```

Honest "stopped" reports are infinitely more useful than a forced "passed" via assertion-weakening.

## Pair with

- `/debug-workflow` — for the Log → Reproduce → Fix discipline when the failure is gnarly
- `/systematic-debugging` — for root-cause investigation when the failure cause is unclear
- `/tdd-workflow` — when working in red→green→refactor mode and a test stays red

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
