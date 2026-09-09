---
name: loopcraft
description: >-
  Pre-flight walkthrough that ARMS a safe agent loop before handing off to the
  built-in `/loop`. Invoke this whenever you're about to set up ANY unattended /
  repeating agent run — "run this in a loop", "loop until…", "keep doing X every
  N minutes", "schedule this", "watch for…", "run this on a cron", "make this run
  itself", "kick off a self-running loop", or before you call `/loop`,
  `ScheduleWakeup`, or `CronCreate` for real work. `/loop` is a reserved Claude
  Code built-in, so this WRAPS it: loopcraft makes you fill a 6-part loop contract
  (type · checker · stop-rule · budget · report-only · run-note) and refuses to
  launch until the load-bearing clauses are set, THEN hands off to the right
  primitive. Skip loopcraft only for a throwaway one-shot with a human watching.
---

# loopcraft — arm a loop before you launch it

> **Invocation: routed.** Wrapper/gate skills measure 0% description-trigger
> recall — wire a CLAUDE.md rule or hook instructing the model to
> invoke this skill before starting any unattended loop (/loop, ScheduleWakeup, CronCreate).
> Do NOT set `disable-model-invocation`: the routing rule works by model
> invocation. See `docs/decisions/skill-invocation-doctrine.md`.

`/loop` is a Claude Code **built-in** (dynamic pacing via `ScheduleWakeup`, cron
mode via `CronCreate`). This skill does **not** replace it — it is the **safety
harness you run first**. Guiding principle: *loops are the highest-leverage and
highest-risk shape an agent fleet runs; never run one unattended without the
6-part contract.*

> **Non-Claude harnesses:** the contract below is tool-agnostic. Map the
> primitives to your harness's equivalents — a self-rescheduling wake-up, a cron
> scheduler, an event hook. The clauses don't change; only the launch mechanism
> does.

Your job when this skill fires: **fill every clause of the contract — inferring as
much as you can from the calling context — then launch.** A clause is satisfied by
an *explicit* value, and an explicit value may be a permissive **default** ("no
budget cap", "no separate checker, I'll eyeball it", "write immediately, skip
report-only"). The gate blocks only on **omission** (a load-bearing clause nobody
decided), never on a conscious choice — but it **warns** on the risky permissive
defaults so the choice is informed, not accidental.

## Step 0 — Read the calling context FIRST, pre-fill, then ask only for the gaps

Before asking the human anything, mine the invocation for clause values — most of
the contract is usually implied by *how you were called*. Then present a filled
draft and ask only for what's genuinely undecided (and load-bearing).

| Signal in the request | Clauses it fills |
|---|---|
| The verb/goal ("fix failing CI", "watch logs", "every 10 min", "until X passes") | **type** (goal/heartbeat/cron/hook) + often the **stop rule** |
| A test/build/lint command in the task or repo (`pnpm test`, `go test`, a CI job) | **checker** (#2) + **stop rule** (#3: that command exits 0) |
| "watch / monitor / poll / keep checking" | type=**heartbeat**; the thing watched = the **signal**; ask for the escalation condition |
| "daily / hourly / at 09:00 / on a schedule" | type=**cron** + the cadence |
| "on PR / on push / when a file lands / on CI fail" | type=**hook** + the trigger event |
| "just run it / don't stop me / no limit / go" | permissive **defaults** for #4 budget + #5 report-only — accept, but warn (Step 3) |
| The repo / branch / worktree it's pointed at, and CLAUDE.md forbidden-action rules | the **forbidden-actions** + human-approval edge |
| A user-stated cap ("max 5 tries", "under $2", "≤50k tokens") | **#3 max-iterations** and/or **#4 budget** directly |

If the human **prefixed defaults** up front (e.g. "loop this, no budget limit, write
directly"), honor them: don't re-interrogate — fill those clauses from the prefix,
fill the rest from context, warn once on the permissive ones, and go.

## Step 1 — Name the loop type (pick ONE, deliberately)

| type | when | our primitive |
|---|---|---|
| **heartbeat** | short-interval monitor (watch logs / health / drift) | `ScheduleWakeup` short delay (60–270s, cache-warm) |
| **cron** | fixed schedule (daily 10:00, hourly) | `CronCreate` (`<<autonomous-loop>>`) |
| **hook** | event-triggered (PR push, CI fail, file lands) | Claude Code hook |
| **goal** | iterate until a success condition, then STOP | `/loop` dynamic (`ScheduleWakeup` + `<<autonomous-loop-dynamic>>`) |

Don't build a heartbeat when you want a goal loop. If it should stop when done,
it's a **goal** loop and it needs a stop rule (Step 3).

## Step 2 — The 6-part loop contract (each clause = an explicit value; a permissive default counts)

Each clause needs an **explicit** value. Where the human gave none, use the
**default** shown — a default is a legitimate conscious choice, but the ⚠ ones are
what Step 3 warns on.

1. **Type** — from Step 1. *(no default — must be chosen)*
2. **Maker ≠ checker** — the independent check that can say *no*: a validation
   command, a second/reviewer agent, or a human gate. **Default ⚠ "human eyeballs
   only"** (you, reviewing output) — allowed, but a real automated/second-agent
   checker is far safer.
3. **Stop rule + max iterations** — a **machine-verifiable** success condition (a
   command exits 0, an artifact exists), NOT "until it looks good". **Default:
   `max_iterations` = 5 (goal) / until-cancelled (heartbeat·cron).** A hard
   iteration cap is **always** set — ⚠ if you can't state a machine-checkable stop
   rule for a *goal* loop.
4. **Budget ceiling** — a per-run token/cost cap (+ daily cap for scheduled loops).
   Rough estimate = `iterations × typical-context-tokens`. **Default ⚠ "no cap /
   unlimited"** — allowed if the human says so, but flagged; our cost tooling
   (`/cost-audit`, `/agent-token-optimization`) is post-hoc, so this is the only
   pre-flight guard.
5. **Report-only first** — first runs **propose, don't act**; promote to write after
   clean passes. **Default:
   report-only ON for the first run.** ⚠ if overridden to write-immediately.
6. **Run note** — each pass appends a short note (signal seen, what changed, checks
   run, next action). **Default: ON** (cheap; it's the loop's memory).

**Also confirm the human-approval edge (no permissive default here):** the loop must
**never** merge, deploy, delete, purchase, or send external comms unattended — those
stay draft/PR/confirm (fleet canon: draft-means-draft, never-push, PR-not-merge).

**7. Spawned-agent environment (no permissive default here either).** If the loop
launches agent processes of its own, hand each child an explicit allowlist of
environment variables and point `HOME` at a scratch directory, rather than passing
your shell through. A child that inherits the whole environment carries every live
credential you hold, visible in `ps` to anything on the box, and loads every plugin
and MCP server you have configured (token cost you never asked for). Two hands-on
trials made this concrete: one town-of-agents tool inherited the full user
environment by default, while another jailed the child to `HOME=<scratch>` plus a
short list (proxy/TLS vars and one model credential) and the agent worked normally
under the restriction. Copy the second shape.

Same footing as the approval edge above: the destructive-action denials hold in
**every** posture, including the most permissive one you configure. A loop that can
be talked into `rm -rf` or a force-push is not made safe by a budget cap.

## Step 3 — Gate (block on omission, WARN on permissive default)

- **Block** only when a load-bearing clause was **neither inferred nor decided** —
  i.e. genuinely omitted. Name the missing clause, propose its default, and get a
  yes/no. Never block a clause the human explicitly set, even to a permissive value.
- **Warn, don't block, on permissive defaults** — surface a one-line ⚠ for any of:
  no independent checker (#2), unlimited budget (#4), write-immediately / report-only
  off (#5), or a goal loop with no machine-verifiable stop rule (#3). One clear
  heads-up, then honor the choice.
- The human-approval edge is **never** waivable by default — confirm it explicitly.

## Step 4 — Hand off to the built-in primitive

Once the contract is complete, launch the matching primitive **with the contract
baked in**:

- **goal / dynamic** → the built-in **`/loop`** (or `ScheduleWakeup` with
  `<<autonomous-loop-dynamic>>`). Put the stop rule + `max_iterations` + budget cap
  + "report-only for the first N passes" **into the loop prompt** so each wake-up
  re-reads them.
- **cron** → `CronCreate` (`<<autonomous-loop>>`), same clauses in the prompt; set
  the daily cap.
- **heartbeat** → `ScheduleWakeup` short delay; name the exact signal it watches and
  the escalation condition (don't just re-poll for nothing).
- **hook** → register the hook; the checker + report-only rule still apply.

Then state, in one line back to the human: *type · checker · stop rule · budget ·
report-only-until · where the run note lands.* That line is the proof the loop was
armed, not just started.

## The four failure modes this prevents (name them when you skip a clause)

- **Verification debt** — outputs pile up faster than anyone checks → clause #2.
- **Comprehension rot** — the loop changes things while your mental map lags → clause #6 + read diffs.
- **Cognitive surrender** — it sounds confident so you stop having opinions → keep judgment at the human gate.
- **Token blowout** — scheduled loops multiply cost → clauses #3 + #4.

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
