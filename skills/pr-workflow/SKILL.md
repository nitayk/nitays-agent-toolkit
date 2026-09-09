---
name: pr-workflow
description: "Use when driving the full PR lifecycle — pre-PR validation, status polling, bot-feedback handling, and merge gating on mergeStateStatus. Opening the PR and merging it each require explicit human authorization; the loop between them runs unattended. Do NOT use for single-file PRs (use /create-pr), responding to specific review comments (use /address-pr-feedback), or general gh-CLI operations like triage/releases (use /github-ops). Requires GitHub."
last-reviewed: 2026-08-21
---
# PR Workflow Management

> **Invocation: routed.** Wrapper/gate skills measure 0% description-trigger
> recall, so this skill's own description will not fire it. Its routing
> surface is a by-name cross-reference from a reachable skill: `/e2e`'s delivery
> phase dispatches it by name, and `/task-breakdown` emits `(use /pr-workflow)`
> into generated task checklists. (`/create-pr`, `/prd-generation` and
> `/setup-local-dev` only cross-reference it as a related skill.) A consumer
> CLAUDE.md rule or hook works too. Do NOT set `disable-model-invocation`: the
> routing rule works by model invocation, so the flag would break the only path
> that fires it. See `docs/decisions/skill-invocation-doctrine.md`.

End-to-end PR lifecycle: pre-PR checks, create, monitor, address bot feedback, merge only when `mergeStateStatus == "CLEAN"` **and** a human has authorized the merge.

## Hard gates — the human keeps these

This skill is model-invocable and every phase writes to a shared repo, so the
gates live here rather than in a frontmatter flag. They are not advisory.

**Definition of "yours".** A branch or PR is yours if *this run* created it, or a
human explicitly handed it to you. Everything else — including a teammate's PR and
a sibling PR in the same merge train — is someone else's. When in doubt it is not
yours.

**Two outward actions need explicit human authorization, per action, per run.**
Neither carries over from another PR, an earlier session, or a general "ship it"
earlier in the conversation:

- **Opening the PR.** `gh pr create` notifies reviewers, wakes every bot, and
  spends a full CI matrix. Ask before opening; closing one afterwards does not
  un-notify anyone. (Under `/e2e` this is already a mandatory prompt.)
- **Merging.** A `CLEAN` PR is *ready*, which is not *authorized*. Print the exact
  `gh pr merge` command and stop.

Ask the way `/e2e` does — an explicit question whose selection *is* the
authorization, single-use. Inferring consent from conversational history is not
authorization.

**Never, without a human asking for it by name:**

- **`--auto`.** It hands the merge to GitHub — the same gate, evaded by proxy.
  Under `/e2e` this is absolute: its delivery phase forbids `--auto` outright, and
  where the two rules differ the stricter one wins.
- **`gh pr update-branch`.** It spends a CI run per invocation. Print the command
  instead of running it — `/e2e` requires exactly this.
- **Closing or reopening a PR**, including to force a fresh workflow run. It is a
  two-step mutation, and a blocked second step leaves the PR closed.

**Never at all:**

- **Force-push** (`--force`, `--force-with-lease`) or rewrite published history on
  any branch carrying review activity or bot commits. To refresh a stale branch,
  merge the base in (Phase 5).
- **Push to, close, or reopen anything that is not yours** per the definition above.
- **Resolve a review thread you did not address.** Resolving asserts the point was
  handled. Reply first, resolve second, and only on threads you acted on.
- **Suppress a security finding.** Bumping a dependency is a fix; writing a
  suppression is a policy decision that belongs to a human.
- **Claim a PR merged without reading `mergedAt` back.** The merge command's exit
  status is not evidence.

**Bound the loop.** Phase 3 is a monitor loop that can commit, push, and spend CI
on every pass, so it needs a stop rule before it starts:

- At most **one rerun per failing check**. A second failure of the same check is a
  real failure — stop and report it.
- Cap total passes (10 is a sane default) and stop on the cap rather than
  continuing quietly.
- Stop immediately on anything needing authorization, and on any state the state
  machine has no move for.
- If you cannot state the stop rule, do not start the loop.

If a gate blocks you, say so and hand the command over. Do not route around it.

**CRITICAL:** `mergeable` and `mergeStateStatus` are different fields. `mergeable` answers "does it conflict?" only — and through `gh` it returns the strings `MERGEABLE` / `CONFLICTING` / `UNKNOWN`, never a boolean. Merge gating must use `mergeStateStatus`. See [references/pr-workflow.md](references/pr-workflow.md#mergeable-vs-mergestatestatus).

## When to Use

| Use this skill | Use instead |
|----------------|-------------|
| Multi-file PR with CI + bot loop | `/create-pr` for single-file trivial PRs |
| Autonomous monitor-and-fix until clean | `/address-pr-feedback` for one-shot reply to specific review comments |
| Full lifecycle (create through merge) | `/github-ops` for triage, releases, issue ops |
| GitHub | — (skip if not GitHub) |

**Do NOT use when:** PR already merged, you only need to push a commit, or the task is a single targeted comment reply.

## Core Directive

**Pre-PR checks → Create PR → Monitor → Address bot/CI feedback → Merge only when `mergeStateStatus: "CLEAN"` → Confirm `mergedAt` is non-null.**

## Phase 0 — Token Preflight

Run this before anything else, once per session:

```bash
gh auth status
```

`gh auth status` marks the active account and its source. When an ambient `GH_TOKEN` is exported, that is the account `gh` uses even if you are also logged in via keyring — and a token minted for one repo returns `404 Not Found` on every repo outside its scope, which reads as "repo doesn't exist" rather than "you lack access". Prefix `gh` with `env -u GH_TOKEN` to fall back to the stored login:

```bash
env -u GH_TOKEN gh pr view <pr> --repo <owner>/<repo> --json mergeable,mergeStateStatus
```

It is the most-used incantation in the [§ Evidence](#evidence) table below, at ~1.8x the next entry. When a `gh` call 404s on a repo you know exists, suspect the token before the URL.

## Phase 1 — Pre-PR Checks

Run locally before `gh pr create`. All must pass:

1. **Compile / typecheck** — `sbt compile` / `npm run typecheck` / `mypy .` / `go build ./...`
2. **Lint / format** — `sbt scalafmtCheck` / `npm run lint` / `ruff check .` / `golangci-lint run`
3. **Tests** — `sbt test` / `npm test` / `pytest` / `go test ./...`
4. **Secrets scan** — `git secrets --scan` (or trufflehog / `npm audit` / `safety check`)
5. **Branch sanity** — `git branch --show-current` is not `main`/`master`

Full command matrix and template lookup: [references/pr-workflow.md § Pre-PR checks](references/pr-workflow.md#pre-pr-checks-multi-stack).

## Phase 2 — Create PR

**Ask before you open it** (hard gate). Opening a PR notifies reviewers, wakes the
bots, and spends a CI matrix; closing it afterwards un-notifies nobody. Confirm the
base branch and the title in the same breath.

```bash
gh pr create \
  --title "feat: <short summary>" \
  --body "$(cat <<'EOF'
## What
<one-line>

## Why
<motivation, links>

## How
<implementation notes>

## Testing
<verification>

Closes #<issue>
EOF
)"
```

Use repo's `.github/pull_request_template.md` if present. Full template: [references/pr-workflow.md § PR description template](references/pr-workflow.md#pr-description-template).

### Draft → ready

Opening with `--draft` is the low-cost option when CI should run but reviewers
shouldn't be pulled in yet: it spends the CI matrix without requesting reviews.

```bash
gh pr create --draft --title "..." --body "..."   # CI runs, reviewers not notified
gh pr ready <pr>                                  # requests reviews — ask first
```

`gh pr ready` is the moment reviewers get pulled in, so it carries the same gate as
opening the PR: **ask before running it.** `mergeStateStatus` has no `DRAFT` value —
read `isDraft` to tell where a PR stands.

## Phase 3 — Monitor (Autonomous Loop)

```bash
# State — mergeStateStatus is the gate; the other two say why it is what it is
gh pr view <pr> --json mergeable,mergeStateStatus,reviewDecision

# CI checks (blocks until they settle; see the --watch caveat below)
gh pr checks <pr> --watch

# Re-read state AFTER --watch returns — its exit is not a merge signal
gh pr view <pr> --json mergeable,mergeStateStatus

# Bot comments — BOTH endpoints, paginated, bodies truncated (see the caveat below)
for ep in issues pulls; do
  gh api --paginate "repos/<owner>/<repo>/$ep/<pr>/comments" \
    --jq '.[] | select(.user.type == "Bot")
          | "\(.user.login) \(.path // "(pr-level)"): \(.body[0:300])"'
done
```

**Enumerating bot comments has three independent traps, and every one fails silently.**

1. **Wrong surface.** `gh pr view --json comments` returns an `author` object with only `login` — no `type` field — so `select(.author.type == "Bot")` matches nothing and reports zero bot comments on a PR that has them. The `gh` surface also strips the `[bot]` suffix (`github-actions`, not `github-actions[bot]`), so a login suffix match misses too. REST's `user.type` is the reliable discriminator.
2. **Wrong endpoint.** `issues/<pr>/comments` holds only PR-level comments; **inline review comments live at `pulls/<pr>/comments`**. Measured on a live PR: the issues endpoint returned 0 while the pulls endpoint returned 48 review findings from a SAST bot. Query both.
3. **Unpaginated.** REST defaults to 30 items per page. The same PR returns 30 without `--paginate` and 48 with it — and page 1 holds the *oldest* 30, so the truncation hides the newest findings.

All three produce a zero exit and empty-or-partial output. There is no error to notice.

Truncate the bodies (`.body[0:300]` above): unbounded, those 48 findings printed 94 KB. A monitor loop that re-runs this every iteration will bury its own context in bot output.

**`--watch` exiting does NOT mean mergeable.** It returns when the checks stop running — including when they stop by failing — and it says nothing about branch freshness or required reviews. Always re-read `mergeable,mergeStateStatus` after it returns; never treat its exit as a green light.

**`UNKNOWN` means "not computed yet", not an error.** A query right after a push routinely returns `UNKNOWN` for both fields and then a real state seconds later; re-query before concluding anything.

### The `mergeStateStatus` state machine

| State | Meaning | Move |
|-------|---------|------|
| `CLEAN` | Checks green, no conflicts, reviews satisfied | Merge (Phase 5) |
| `BEHIND` | Branch is behind base, and the base requires up-to-date branches | Print `gh pr update-branch <pr>` for the human (it spends a CI run); once run, re-read state |
| `BLOCKED` | A required check, review, or ruleset is unsatisfied | Read *which*: `reviewDecision` for reviews, `gh pr checks` for checks. Do not retry blindly |
| `DIRTY` | Merge conflicts with base | Inspect and resolve locally — [references § Conflict inspection](references/pr-workflow.md#conflict-inspection-without-touching-your-worktree) |
| `UNSTABLE` | Only non-required checks failing | Repo policy call — decide, don't auto-merge |
| `HAS_HOOKS` | Mergeable, with pre-receive hooks configured | Usually mergeable; repo-specific |
| `UNKNOWN` | Not yet computed | Re-query; not a failure |

Those seven are the whole enum — there is no `DRAFT` state, so check `isDraft` separately.

**`BEHIND` after a peer PR lands is normal, not an error.** Where the base branch requires up-to-date branches, a merge train lands one PR at a time: when one merges, its siblings go `BEHIND` and need re-updating before they can merge in turn. Don't treat the transition as a regression or start debugging it. Expect it to recur until the last PR lands — and note the stragglers in a shared train are usually **other people's PRs**, which are not yours to update.

For each iteration — bounded per the loop rule in the hard gates:
1. `mergeStateStatus == "CLEAN"` → go to Phase 5 (which needs authorization).
2. New bot comments → fix → commit → push (your own branch only).
3. CI failed → **read the failure before rerunning** (Phase 4). One rerun per check.
4. `BEHIND` → print `gh pr update-branch` for the human; re-read state after they run it.
5. Otherwise re-read the state fields and repeat — a push, a peer merge, or a review all change it.

Stop and report when: a gate needs authorization, a check fails twice, the pass cap is hit, or the state has no move in the table.

Field semantics and bot-signal taxonomy: [references/pr-workflow.md § mergeable vs mergeStateStatus](references/pr-workflow.md#mergeable-vs-mergestatestatus) and [§ Bot feedback handling](references/pr-workflow.md#bot-feedback-handling).

## Phase 4 — Address Feedback

| Source | Action |
|--------|--------|
| **Bot comment** | Fix the flagged issue; push; let CI re-validate. Address every one — they encode org policy. |
| **Review thread** | Reply **and** resolve — both halves. [references § Review threads](references/pr-workflow.md#review-threads-reply-and-resolve). |
| **CI failure** | `gh run view <run-id> --log-failed` **first** — read the actual error, then decide fix-vs-rerun. |
| **CI failure (flaky)** | `gh run rerun <run-id> --failed` — only after the log confirms flakiness. |

**Read the log before rerunning.** `--log-failed` narrows the run to its failed **jobs** — not to the failing steps inside them, so it still carries every passing step of those jobs (measured: 40 KB for one job). Always bound it: `| tail -40`. Rerunning without reading it is how a real failure gets mistaken for a flake and burns two CI cycles.

**One rerun per check.** A rerun is a spend, and "flaky" is your own judgment call. If the same check fails a second time, it is a real failure: stop rerunning and report it.

**A rerun does not pick up a fixed reusable workflow.** Measured 2026-08-18 on a shared `uses: org/repo/.github/workflows/x.yml@main` workflow: after the fix merged, a rerun of the failed check still ran the pre-fix version, while a fresh run on the same commit ran the fixed one — so a rerun makes a working fix look broken. Force a fresh run instead: [references § Reruns and reusable workflows](references/pr-workflow.md#reruns-and-reusable-workflows).

## Phase 5 — Merge

Two separate gates, and both must pass. The readiness gate is yours to evaluate;
the authorization gate is not.

**Readiness** — all of:

- `mergeStateStatus: "CLEAN"`
- All required checks green
- Zero unresolved review threads
- Required approvals obtained

**Authorization** — a human has said to merge *this* PR on *this* run. When
readiness passes and authorization has not been given, the correct action is to
report the state and print the command, not to run it:

```bash
gh pr merge <pr> --squash --delete-branch
```

Never add `--auto` to get around the wait, and never force-push to make a branch
mergeable — if it is `BEHIND`, `gh pr update-branch` or merge the base in; if it is
`DIRTY`, resolve the conflict with a merge commit. Rewriting published history to
tidy a PR loses other people's commits, including bot-pushed ones.

**Once a human authorizes and you merge, confirm it landed** — a merge command that printed no error is not proof:

```bash
gh pr view <pr> --json state,mergedAt --jq '.state + " " + (.mergedAt // "NOT-MERGED")'
```

`state == "MERGED"` with a non-null `mergedAt` is the only evidence. Never report a PR as merged off the merge command's exit status alone.

Strategy table, auto-merge arming, and repo-specific rules: [references/pr-workflow.md § Merge strategies](references/pr-workflow.md#merge-strategies).

> **Plugin repos with a version-bump bot:** bring a stale branch up to date with a **merge of `main`, never a rebase.** The version-bump bot pushes a `chore` commit onto the PR branch; a rebase rewrites it and loses the bump. On a `plugin.json` version conflict, keep the **higher** version.

## Workflow Diagram

```
Token preflight → Pre-PR checks → gh pr create → ┌─ Monitor ──────────────┐
                                                 │  mergeStateStatus?     │
                                                 │  bot comments?         │
                                                 │  CI checks?            │
                                                 └──┬──────────────┬──────┘
                                      not CLEAN     │              │ CLEAN
                                                    ▼              ▼
                                 Fix / update-branch / push    gh pr merge
                                                    │              │
                                                    └─→ loop       ▼
                                                             confirm mergedAt
```

## Integration

- `/git-workflow` — clean commit history feeding into the PR
- `/tdd-workflow` — tests written before code (Phase 1 will then pass)
- `/best-practices-enforcement` — language-rule gate before push
- `/create-pr` — single-file trivial PRs (use that, not this)
- `/address-pr-feedback` — focused review-comment reply (use that for that subtask)
- `/github-ops` — issue triage, releases, repo admin (different scope)

## Common Pitfalls

These fail **silently** — exit 0, no error, just a wrong conclusion. They are the expensive ones:

- **Enumerating bot comments from one endpoint, unpaginated, or off the `gh` surface** — any of the three reports fewer bot comments than exist, often zero. See Phase 3.
- **Treating `--watch`'s exit as mergeable** — it only means checks stopped running, failures included. Re-read the state fields.
- **Treating `UNKNOWN` as broken** — it means not-yet-computed. Re-query.
- **Treating `update-branch` success as `CLEAN`** — re-read the state field after updating.
- **Reporting "merged" off the merge command's exit status** — confirm `state` / `mergedAt`.
- **Gating on `mergeable`** — on the `gh` surface it is a string, so `== true` is never satisfied and the check silently never fires. Gate on `mergeStateStatus == "CLEAN"`.
- **Detecting conflicts with `git merge-tree`'s 3-argument form** — that is the trivial-merge mode; it misses modify/delete and rename conflicts entirely. Use `--write-tree --quiet` and read the exit status.
- **Comparing a state against the other surface's casing** — `mergeStateStatus == "clean"` (REST casing) never matches `CLEAN`, and never errors.

These announce themselves, but cost a cycle each:

- **Using `mergeableState` as a `gh` field** — not a valid `gh pr view --json` field; `gh` rejects it with `Unknown JSON field`. The `gh` field is `mergeStateStatus` (uppercase values); `mergeable_state` (lowercase values) is the *REST* field name.
- **A 404 on a repo you know exists** — ambient `GH_TOKEN` scope, not a bad URL. Retry under `env -u GH_TOKEN`.
- **Debugging a `BEHIND` caused by a peer merge** — expected where the base requires up-to-date branches. Re-update.
- **Rerunning a red check without reading `--log-failed`** — turns a real failure into two wasted CI cycles.
- **Rerunning to pick up a reusable-workflow fix** — the rerun replays the old workflow version. Force a fresh run.
- **Ignoring bot comments** — they encode org policy. Fix, or reply with the justification.
- **Resolving a review thread without replying** — the reviewer loses the audit trail of *why*.

Symptom-to-action tables covering these plus the repo-specific ones: [references/pr-workflow.md § Common pitfalls](references/pr-workflow.md#common-pitfalls).

## Success Criteria

- Phase 1 checks pass locally before `gh pr create`
- The human authorized opening the PR, and it opens with a complete description
- Between the two authorization gates the loop runs unattended — fixing, pushing and re-reading state without needing to be prodded — and drives `mergeStateStatus` to `CLEAN`
- Every bot comment addressed or justified; every review thread replied to **and** resolved
- The run ends at a `CLEAN` PR with the merge command printed — **not** at a merge, unless a human authorized this one
- If a human did authorize it, the merge is confirmed by a non-null `mergedAt`, not by the merge command's exit status
- No force-push, no `--auto`, no writes to a PR you don't own

## Evidence

The operational rules in Phases 0/3/4/5 and the pitfalls above were mined from real
Claude Code transcripts of this ceremony (281 real-work sessions; 19,746 shell
invocations scanned, re-measured 2026-08-21), keeping only incantations actually
observed in use:

| Rule | Observed uses |
|------|---------------|
| `env -u GH_TOKEN` prefix | 1,175 (153 of 419 failure/retry episodes) |
| `mergeStateStatus` as the gate field | 637 |
| `mergedAt` merge confirmation | 504 |
| `gh pr update-branch` | 97 |
| `gh pr checks --watch` | 86 |
| `gh run view --log-failed` | 62 (precedes a rerun in 19 of 21 sessions) |
| `reviewThreads` / `resolveReviewThread` | 17 / 6 |
| `mergeableState` (the field this doc used to name) | 0 |

Provenance detail, and the claims deliberately left out:
[references/pr-workflow.md § Provenance](references/pr-workflow.md#provenance).

## Remember

> `CLEAN` means ready, not authorized. The merge is the human's to give.

> `mergeStateStatus: "CLEAN"` is the only readiness gate. `mergeable` is not, and `mergeableState` is not a field.

> A 404 on a repo you know exists is a token-scope problem. `env -u GH_TOKEN`.

> Read the failing log before you rerun it.

> A merge is merged when `mergedAt` says so.

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
