# PR Workflow — Reference

Demoted detail for `pr-workflow` SKILL. Load on demand when SKILL.md points here.

> **The hard gates in SKILL.md apply to everything here.** This file documents the
> commands; it does not re-authorize them. In particular: opening a PR and merging
> each need explicit human authorization per run; `--auto`, `gh pr update-branch`,
> and closing/reopening a PR need a human asking by name; force-pushing, writing to
> anything that isn't yours, resolving a thread you didn't address, and suppressing
> a security finding are off the table. Read SKILL.md § Hard gates before acting on
> a command from this file.

## `mergeable` vs `mergeStateStatus`

Two different fields, routinely confused — and they live on **two different API
surfaces with different names and different value casing**. Mixing the surfaces is
the most common way to write a command that cannot work.

| Surface | Conflict field | Merge-readiness field | Value casing |
|---------|---------------|----------------------|--------------|
| `gh` CLI / GraphQL | `mergeable` → `MERGEABLE` \| `CONFLICTING` \| `UNKNOWN` | `mergeStateStatus` | UPPERCASE |
| REST API (`/repos/{o}/{r}/pulls/{n}`) | `mergeable` → `true` \| `false` \| `null` | `mergeable_state` | lowercase |

`mergeableState` is **not a field on either surface**. `gh pr view --json mergeableState`
fails with `Unknown JSON field: "mergeableState"` and prints the valid field list.

`mergeable` alone never gates a merge — on the `gh` surface it is a string, so
`mergeable == true` is never satisfied, and even `MERGEABLE` only means "no git
conflicts": checks may be failing, reviews missing, or the branch stale.

### The state machine

| `mergeStateStatus` | Meaning | Safe to merge? | Move |
|--------------------|---------|----------------|------|
| `CLEAN` | Checks green, no conflicts, reviews satisfied | Yes | Merge |
| `BEHIND` | Branch behind base; base requires up-to-date branches | No | `gh pr update-branch` |
| `BLOCKED` | Required check / review / ruleset unsatisfied | No | Identify which; don't retry blindly |
| `DIRTY` | Merge conflicts | No | Resolve locally |
| `UNSTABLE` | Non-required checks failing | Project policy | Decide per repo |
| `HAS_HOOKS` | Passing, with pre-receive hooks | Usually | Repo-specific |
| `UNKNOWN` | Not yet computed | Unknown | Re-query |

Those seven are the complete `MergeStateStatus` enum (GraphQL introspection,
2026-08-21) — in particular there is no `DRAFT` state, so check `isDraft`
separately. The `mergeable` enum is likewise exactly `MERGEABLE` /
`CONFLICTING` / `UNKNOWN`.

**Rule:** only merge when `mergeStateStatus == "CLEAN"`.

```bash
gh pr view <pr-number> --json mergeable,mergeStateStatus,reviewDecision
```

### `UNKNOWN` is transient, not terminal

A query immediately after a push (or the first query against a PR in a while)
commonly returns `UNKNOWN` for **both** `mergeable` and `mergeStateStatus`, then a
real state seconds later — observed live on a PR that read `UNKNOWN` and then
`BEHIND` on the next query, with nothing changed in between. It is not an error and
not a permission problem: re-query. Treating the first `UNKNOWN` as terminal is how
a perfectly mergeable PR gets reported as broken.

### `BEHIND` after a peer merge is expected

Where the base branch requires branches to be up to date before merging, a merge
train lands **one PR at a time**. A sibling PR transitions to `BEHIND` when one
lands, and needs `gh pr update-branch` before it can merge in turn. With N PRs in
flight this cascades: one merges → the others go `BEHIND` → re-update → next merges
→ repeat. Budget for repeated re-updates rather than reading the transition as a
regression.

```bash
gh pr update-branch <pr>
gh pr view <pr> --json headRefOid,mergeStateStatus --jq '{head:.headRefOid[0:8],mergeStateStatus}'
```

Re-reading the state after the update is the point — `update-branch` returning
successfully does not mean the PR reached `CLEAN`.

## Ambient `GH_TOKEN` and cross-repo access

An exported `GH_TOKEN` is the account `gh` uses even when a keyring login is also
present — `gh auth status` lists both and marks which is active:

```
✓ Logged in to github.com account <login> (GH_TOKEN)
  - Active account: true
✓ Logged in to github.com account <login> (keyring)
  - Active account: false
```

When that token was minted for a single repo (a CI token, a fine-grained PAT),
every call against any *other* repo returns:

```
{"message":"Not Found","documentation_url":"...","status":"404"}
```

The 404 — rather than a 403 — makes the error indistinguishable from a typo'd repo
name, which is what makes this one expensive to diagnose. Drop the ambient token
for the call:

```bash
env -u GH_TOKEN gh pr view <pr> --repo <owner>/<repo> --json mergeable,mergeStateStatus
env -u GH_TOKEN gh pr list --repo <owner>/<repo> --author <login> --state open
```

Check which identity is in play with `gh auth status`. This prefix is the most-used
incantation in the [§ Provenance](#provenance) table below, at ~1.8x the next entry —
when a `gh` call 404s on a repo you know exists, try it before anything else.

## Monitoring loop (autonomous mode)

```bash
# 1. State
gh pr view <pr> --json mergeable,mergeStateStatus,reviewDecision,statusCheckRollup

# 2. Bot comments — both endpoints, paginated (see below)
for ep in issues pulls; do
  gh api --paginate "repos/<owner>/<repo>/$ep/<pr>/comments" \
    --jq '.[] | select(.user.type == "Bot")
          | "\(.user.login) \(.path // "(pr-level)"): \(.body[0:300])"'
done

# 3. CI status — blocks until checks settle
gh pr checks <pr> --watch

# 4. Re-verify AFTER --watch returns (it is not a merge signal)
gh pr view <pr> --json mergeable,mergeStateStatus
```

`gh pr checks --watch` exits when the checks stop *running*. That includes
stopping by failing — the existence of a separate `--fail-fast` flag ("Exit watch
mode on first check failure") is what tells you plain `--watch` waits out failures
rather than bailing on them. It is also blind to branch freshness and required
reviews. The re-read in step 4 is mandatory, not defensive.

### Enumerating bot comments: three silent traps

Every one of these returns exit 0 with empty or partial output. Nothing errors, so
nothing prompts a second look — and the consequence is merging with org-policy
findings unread.

**Trap 1 — the `gh` comments surface has no author type.** `gh pr view --json
comments` returns an `author` object with **only** a `login` key; the full per-comment
key set is `author, authorAssociation, body, createdAt, id, includesCreatedEdit,
isMinimized, minimizedReason, reactionGroups, url, viewerDidAuthor`. So:

```bash
gh pr view <pr> --json comments -q '.comments[] | select(.author.type == "Bot")'   # WRONG — matches nothing
```

That surface also **strips the `[bot]` suffix** — the same commenter is
`github-actions` there and `github-actions[bot]` on REST — so a login suffix match
does not rescue it. REST's `user.type` is the reliable discriminator. (On the
GraphQL surface the suffix is likewise stripped, and `author { __typename }` returns
`Bot`; that is the discriminator to use if you are already in GraphQL.)

**Trap 2 — PR-level and review comments are different endpoints.** `issues/<pr>/comments`
holds only PR-level (conversation-tab) comments. Inline review comments — where SAST,
lint and coverage bots usually post — live at `pulls/<pr>/comments`. Measured on a
live PR: `issues` returned **0**, `pulls` returned **48** findings from a security
bot. Querying only the issues endpoint on that PR reports "no bot comments" while 48
sit unread.

**Trap 3 — REST paginates at 30.** Without `--paginate`, the same PR returns 30 of
its 48 review comments — and page 1 is the *oldest* 30, so what gets dropped is the
newest findings. `?per_page=100` also works up to 100 items (REST caps the parameter
there); `--paginate` has no ceiling, so prefer it.

Covering all three:

```bash
for ep in issues pulls; do
  gh api --paginate "repos/<owner>/<repo>/$ep/<pr>/comments" \
    --jq '.[] | select(.user.type == "Bot")
          | "\(.user.login) \(.path // "(pr-level)"): \(.body[0:300])"'
done
```

`.path` is present on review comments and absent on PR-level ones, so the `//`
fallback labels which endpoint a line came from. Truncating the body matters more
than it looks: those 48 findings printed **94 KB** in full, against the 40 KB this
document already insists on bounding for CI logs — and a monitor loop re-runs this
every iteration.

A 404 from either endpoint fails loudly (`gh: Not Found (HTTP 404)`, non-zero exit),
so a wrong repo or PR number will not pass as "no bot comments".

Trap 1 is an instance of the surface-mixing trap in the table above — and it was
present in an earlier revision of this very document.

## CI failures

### Read before you rerun

```bash
gh run view <run-id> --log-failed
```

`--log-failed` narrows the run to its failed **jobs**, not to the failing steps
inside them — the output still contains every passing step of those jobs. On a run
with one failed job it measured 40 KB / 361 lines, identical to plain `--log`, and
four of that job's five steps had succeeded. Always bound it before it lands in
context:

```bash
gh run view <run-id> --repo <owner>/<repo> --log-failed 2>&1 | tail -40
```

Only after the log confirms flakiness:

```bash
gh run rerun <run-id> --failed
```

Rerunning first is how a real failure gets misclassified as a flake and costs two
CI cycles instead of one fix.

### Reruns and reusable workflows

A rerun does **not** pick up a merged fix to a reusable workflow. When a caller
workflow references a shared workflow by moving ref:

```yaml
uses: org/repo/.github/workflows/x.yml@main
```

…and you merge a fix to that shared workflow, `gh run rerun` of the failed check
re-executes the **pre-fix** version. The check fails again and it looks like the fix
didn't work.

Measured 2026-08-18: after a fix merged to a shared eval workflow, a rerun of the
same PR still reported the pre-fix model and score; a *fresh* run on the same
commit reported the post-fix model and score. Same PR, same commit — only the
trigger differed. (The observable is what was measured; the likely mechanism is
that the rerun reuses the workflow version resolved at the original run.)

Force a fresh resolution instead of rerunning, in this order:

1. **Push to the branch** (`synchronize`) — the safe default, on a branch that is
   yours. An empty commit is enough if there is nothing to change.
2. **`workflow_dispatch`** — if the caller exposes it, though it usually targets
   `main` / the full corpus rather than the PR.
3. **Close and reopen the PR** — **needs a human asking for it by name** (hard
   gate), and only if the caller's `on: pull_request: types:` list includes
   `reopened`. Read the warning below first.

> **Why close/reopen is last.** It is a two-step mutation, and anything that blocks
> the reopen half — a permission gate, or an agent-harness policy check on the write
> — leaves the PR **closed**, which is visible and disruptive. If you end up there,
> reopen via `gh api --method PATCH repos/<o>/<r>/pulls/<n> -f state=open`. Never
> pick this option on a PR that is not yours.

Then **verify which version actually ran** from the job log rather than assuming
the fresh trigger worked.

## Review threads: reply **and** resolve

A resolved thread with no reply loses the reason. Do both halves, for false
positives too — an explicit "not applicable because X" is the audit trail.

### List the threads (with their IDs)

```bash
gh api graphql -f query='
query($owner:String!,$repo:String!,$num:Int!){
  repository(owner:$owner,name:$repo){
    pullRequest(number:$num){
      reviewThreads(first:60){nodes{
        id isResolved isOutdated path line
        comments(first:10){nodes{author{login} body}}
      }}
    }
  }
}' -F owner=<owner> -F repo=<repo> -F num=<pr> \
 -q '.data.repository.pullRequest.reviewThreads.nodes[]
     | "THREAD \(.id)\n  resolved=\(.isResolved) outdated=\(.isOutdated) \(.path):\(.line)\n  "
       + ([.comments.nodes[] | "\(.author.login): \(.body[0:400])"] | join("\n  "))'
```

`id` is a `PRRT_…` node ID — that is what both mutations below take.

### Reply

```bash
gh api graphql -f query='mutation($t:ID!,$b:String!){
  addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{url}}
}' -F t="<PRRT_id>" -F b="Fixed in $(git rev-parse --short HEAD)"
```

### Resolve

```bash
gh api graphql -f query='mutation($t:ID!){
  resolveReviewThread(input:{threadId:$t}){thread{id isResolved}}
}' -F t="<PRRT_id>" -q '"\(.data.resolveReviewThread.thread.id) resolved=\(.data.resolveReviewThread.thread.isResolved)"'
```

Read back `isResolved` — the mutation echoes the new state, so there is no reason
to assume it worked.

Loop both mutations to sweep a PR, but sweep only the threads you actually acted
on — a bot's whole batch of findings usually qualifies once you've been through
them, a human reviewer's open question does not. Resolving is a claim that the
point was handled.

## Conflict inspection without touching your worktree

`git merge-tree` reports what a merge *would* do without creating a commit,
checking anything out, or dirtying the working tree — so it is safe to run against
someone else's branch mid-review:

```bash
git fetch origin <branch> -q
git merge-tree --write-tree --quiet origin/main origin/<branch>; echo "rc=$?"
```

**`rc=0` means the merge is clean; `rc=1` means it conflicts.** Read the exit status
rather than grepping the output. Drop `--quiet` to see which paths conflict and why:

```bash
git merge-tree --write-tree origin/main origin/<branch>
# ...
# CONFLICT (modify/delete): f.txt deleted in <branch> and modified in main.
```

### Don't use the three-argument form for this

`git merge-tree -h` names the two modes outright: the 3-argument
`git merge-tree <base-tree> <branch1> <branch2>` is `--trivial-merge`, while
`--write-tree` does "a real merge instead of a trivial merge". A trivial merge cannot
report conflicts it has no machinery to detect, so grepping its output silently
under-reports. Measured across four cases (git 2.50.1, scratch repos, each checked
against a real `git merge --no-commit --no-ff`):

| Case | Real outcome | 3-arg + `grep '^\+<<<<<<<'` | `--write-tree --quiet` rc |
|------|--------------|------------------------------|---------------------------|
| Non-overlapping edits, same file | clean | 0 matches ✓ | 0 ✓ |
| Same line edited both sides | **conflict** | 1 match ✓ | 1 ✓ |
| Modify on one side, delete on the other | **conflict** | **0 matches ✗** | 1 ✓ |

The modify/delete row is the trap: a real conflict that the 3-arg recipe reports as
clean. Rename and add/add conflicts fall in the same blind spot.

Two further reasons to prefer the exit status:

- **`2>/dev/null` on a `merge-tree` pipeline hides operational failures.** An unfetched
  or mistyped ref prints `not something we can merge` on stderr; suppress it and the
  pipeline emits nothing and exits 0 — indistinguishable from a clean merge.
- **`^changed in both` is not a conflict signal at all.** The 3-arg form emits it
  whenever both sides touched a file, conflict or not — measured firing on a merge
  that completed cleanly. It answers "which files did both sides touch?", which is a
  useful *review-scope* question and a useless *conflict* question. (Relatedly, that
  form prints markers inside a diff hunk as `+<<<<<<< .our`, so a bare `^<<<<<<<`
  anchor never matches either.)

The mined transcripts do use the 3-arg form — it is what the operator reached for —
but "observed" is not "correct", and this one under-reports. Prefer `--write-tree`.

For review scope rather than conflict status, ask for the file list directly:

```bash
git diff --name-only $(git merge-base origin/main origin/<branch>) origin/<branch>
```

## Bot feedback handling

| Bot signal | Typical cause | Action |
|------------|---------------|--------|
| Coverage below threshold | Untested new code | Add tests, push |
| Lint errors | Style violations | Run formatter, push |
| Security vuln (Dependabot/Snyk) | CVE in dep | Bump the dep. **Suppression is a human call** — write the justification, don't file it |
| Missing changelog | Release-please / similar | Add changelog entry |
| Doc build fails | Broken link / syntax | Fix referenced docs |
| Required check pending too long | CI runner backed up | Wait, do not rerun blindly |

**Rule:** account for every bot comment — fix it, or reply with why it doesn't
apply. False positives are common enough that dismissing one is routine; dismissing
it *silently* is what loses the org-policy audit trail.

## Pre-PR checks (multi-stack)

### Type check / compile
```bash
sbt compile          # Scala
npm run typecheck    # TS
mypy .               # Python
go build ./...       # Go
```

### Lint / format
```bash
sbt scalafmtCheck    # Scala
npm run lint         # TS
ruff check .         # Python
golangci-lint run    # Go
```

### Tests
```bash
sbt test
npm test
pytest
go test ./...
```

### Secrets scan
```bash
git secrets --scan
# or trufflehog / gitguardian / npm audit / safety check
```

## PR description template

```markdown
## What
<one-line summary>

## Why
<motivation, ticket link>

## How
<implementation notes>

## Testing
<how verified>

## Checklist
- [ ] Code compiles
- [ ] Tests pass
- [ ] No security issues
- [ ] Docs updated

Closes #<issue>
```

PR template lookup order: `.github/pull_request_template.md` → `docs/pull_request_template.md` → `PULL_REQUEST_TEMPLATE.md`.

## Merge strategies

| Strategy | When |
|----------|------|
| `--squash` | Default for feature branches; clean history |
| `--merge` | Preserve commit history (long-lived branches) |
| `--rebase` | Linear history, if repo policy allows |

```bash
gh pr merge <pr> --squash --delete-branch
```

> **This command needs explicit human authorization for this PR, on this run** —
> see SKILL.md § Hard gates. Note `--delete-branch` also deletes the remote branch,
> which is not reversible from here.

Check what the repo actually permits before assuming:

```bash
gh repo view --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge
```

### Confirm the merge landed

```bash
gh pr view <pr> --json state,mergedAt --jq '.state + " " + (.mergedAt // "NOT-MERGED")'
```

A merge command that printed no error is not evidence. `state == "MERGED"` with a
non-null `mergedAt` is. Sweep several at once when closing out a batch:

```bash
for n in <pr> <pr> <pr>; do
  echo "#$n: $(gh pr view $n --repo <owner>/<repo> --json state,mergedAt --jq '.state+" "+(.mergedAt//"")')"
done
```

### Auto-merge (arming, not merging)

`--auto` arms GitHub to merge the PR itself once requirements are met, instead of
holding a polling loop open:

```bash
gh pr merge <pr> --repo <owner>/<repo> --squash --auto
gh pr view <pr> --repo <owner>/<repo> --json mergeStateStatus,autoMergeRequest \
  --jq '"merge=\(.mergeStateStatus) auto=\(.autoMergeRequest != null)"'
```

Read `autoMergeRequest` back to confirm arming actually took.

`gh`'s own help scopes the merge-queue behavior to queue-enabled branches: *"When
targeting a branch that requires a merge queue, no merge strategy is required. If
required checks have not yet passed, auto-merge will be enabled. If required checks
have passed, the pull request will be added to the merge queue."* Read the whole
block — quoting only the last two sentences makes queue behavior look universal.
This repo's `main` has no merge queue (`mergeQueue(branch:"main")` → `null`), so the
queue sentence does not apply here; what `--auto` does on a non-queue branch whose
checks are *already* green is not stated by that help text, and is not established
below — arm it before the checks finish, or just merge.

Arming does **not** exempt the branch from the `BEHIND` cascade above — armed PRs
still need re-updating as siblings land, and an armed PR that goes `BEHIND` sits
there until updated.

> **Arming `--auto` is a merge with no human in the loop**, and it is one of this
> skill's hard gates: do not pass it unless a human asked for auto-merge by name.
> `/e2e` forbids it outright in its delivery phase. The command is documented here
> because reading `autoMergeRequest` back off a PR that someone *else* armed is a
> normal diagnostic — that is a read, not an arm.

### Repo-specific: plugin repos with a version-bump bot

- Bring a stale branch up to date with **`git merge origin/main`, never a rebase.**
  The version-bump bot pushes a `chore` commit onto the PR branch; a rebase
  rewrites that commit and loses the bump.
- On a `plugin.json` version conflict, keep the **higher** version.
- Never edit anything under a directory containing `.subtree-source` — those are
  vendored, and the `block-vendored-edits` check fails the PR. This includes adding
  new files there.

## Common pitfalls

Fails **silently** — wrong conclusion, no error, zero exit. These are the expensive ones:

| Symptom | Actual cause | Action |
|---------|--------------|--------|
| "No bot comments" on a PR that has them | `.author.type` doesn't exist on the `gh` surface | Filter on REST `user.type` |
| Bot comments found, but not the inline ones | Only `issues/<pr>/comments` queried | Query `pulls/<pr>/comments` too |
| Exactly 30 comments found | REST page-size default | Add `--paginate` (or `?per_page=100`) |
| A clean branch reported as conflicting | `merge-tree` grepped for `changed in both` | Use `merge-tree --write-tree --quiet`; read `$?` |
| A modify/delete conflict reported as clean | 3-arg `merge-tree` is trivial-merge mode | Same — `--write-tree`, exit status |
| An unfetched ref reported as clean | `2>/dev/null` swallowed `not something we can merge` | Drop the stderr suppression |
| `mergeable == true` never fires | `gh` returns a string enum, not a boolean | Compare `MERGEABLE`, or gate on `mergeStateStatus` |
| A state comparison never matches | Surfaces mixed: `CLEAN` (gh) vs `clean` (REST) | Pick one surface; match its casing |
| A PR reported broken right after a push | First `mergeStateStatus` read returned `UNKNOWN` | Re-query before concluding |
| Merged something whose checks had failed | `--watch` exit taken as a green light | Re-read `mergeable,mergeStateStatus` after it returns |
| `BEHIND` PR treated as `CLEAN` | `update-branch` exit taken as the new state | Re-read the state field after updating |
| "Merged" reported on an unmerged PR | Merge command's exit status taken as proof | Confirm `state` / `mergedAt` |

Fails **loudly**, but each costs a cycle:

| Symptom | Actual cause | Action |
|---------|--------------|--------|
| `Unknown JSON field: "mergeableState"` | Not a field on either surface | Use `mergeStateStatus` (gh) or `mergeable_state` (REST) |
| `404 Not Found` on a repo that exists | Ambient `GH_TOKEN` lacks scope for it | Retry under `env -u GH_TOKEN` |
| A fix that "didn't work" after a rerun | Rerun replayed the pre-fix reusable workflow | Force a fresh run; verify the version from the log |
| Two CI cycles spent on one failure | Reran before reading the log | `gh run view --log-failed \| tail -40` first |
| Sibling PRs keep going `BEHIND` | Base requires up-to-date branches; merge train | Expected — re-update stragglers until the last lands |
| A reviewer re-raises a resolved point | Thread resolved without a reply | Reply *and* resolve, false positives included |
| A bot's policy finding shipped unaddressed | Bot comment ignored rather than justified | Fix, or reply with the justification |
| Version-bump commit vanished from a bumped PR | Branch was rebased | `git merge origin/main` instead; keep the higher `plugin.json` version |

## Provenance

The operational content in this reference was mined from the operator's own Claude
Code transcripts of this ceremony rather than written from memory: 281 real-work
sessions (sandboxes, plugin caches and worktrees excluded), 19,746 shell
invocations scanned, re-measured 2026-08-21. Counts are occurrences of the
incantation in real commands:

| Incantation | Uses | Note |
|-------------|------|------|
| `env -u GH_TOKEN` | 1,175 | 153 of 419 failure/retry episodes carry the prefix |
| `mergeStateStatus` | 637 | |
| `mergedAt` | 504 | merge confirmation |
| `gh pr update-branch` | 97 | |
| `gh pr checks --watch` | 86 | |
| `gh run view --log-failed` | 62 | precedes a rerun in 19 of 21 sessions that reran |
| `gh api graphql` | 49 | |
| `gh pr merge --auto` | 47 | arming, with `autoMergeRequest` read back |
| `gh run rerun --failed` | 20 | |
| `reviewThreads` | 17 | `first:60`, fields `id isResolved isOutdated path line` |
| `git merge-tree` | 8 | three-arg form with `$(git merge-base …)` |
| `resolveReviewThread` | 6 | |
| `mergeableState` | 0 | never used — the field this doc previously named |

Verified independently of the transcripts, by running it:

- `mergeableState` rejected by `gh pr view --json` (gh 2.96.0), with the valid-field list.
- `mergeable` returning `MERGEABLE`/`UNKNOWN` rather than a boolean; the same PR on
  REST returning `mergeable: true` / `mergeable_state: "behind"` — the two-surface
  table checked against one live PR on both surfaces at once.
- `MergeStateStatus` = exactly the seven values listed, `MergeableState` = exactly
  three, no `DRAFT` in either — GraphQL introspection.
- `UNKNOWN` resolving to a real state on re-query — observed live on a PR that read
  `UNKNOWN` and then `BEHIND` seconds later.
- The `reviewThreads` query run verbatim against a live PR; both mutations validated
  against the schema (input/payload field names) without being executed on a real thread.
- `.author` carrying only `login` on the `gh` comments surface, the `[bot]`-suffix
  stripping (REST `cycode-security[bot]` vs GraphQL/`gh` `cycode-security`), and
  `user.type` / `author.__typename` being populated on REST / GraphQL — the
  silent-filter bug above.
- The endpoint split: one live PR returning 0 bot comments from
  `issues/<pr>/comments` and 48 from `pulls/<pr>/comments`.
- REST's 30-item default page size: the same PR returning 30 without `--paginate`
  and 48 with it.
- `--log-failed` output size (40 KB / 361 lines for one failed job, byte-identical
  to plain `--log`, with four of that job's five steps having passed) — measured.
- The `merge-tree` mode split — 3-arg = `--trivial-merge` per `git merge-tree -h`;
  measured on scratch repos against real `git merge` outcomes (git 2.50.1): the 3-arg
  form's marker grep misses a modify/delete conflict entirely, `changed in both`
  fires on a genuinely clean merge, `^<<<<<<<` never matches (markers arrive as
  `+<<<<<<<` inside a diff hunk), and `--write-tree --quiet` returned the correct
  exit status in every case.
- REST bot-comment output volume (94 KB for 48 findings, untruncated) and that a 404
  from either comments endpoint exits non-zero — measured.
- `mergeQueue(branch:"main")` returning `null` for this repo — GraphQL.
- `--auto` / merge-queue scoping, `--fail-fast` vs plain `--watch`, and
  `gh run rerun --failed` — `gh` CLI help.
- The reusable-workflow rerun behavior — a measured 2026-08-18 A/B (rerun vs fresh
  run on the same commit reported different shared-workflow versions).

**Deliberately not included**, for want of evidence:

- `gh pr merge --admin` force-merge for "zombie" blocked check suites — zero uses in
  the corpus. (`/e2e` documents its own zombie-suite handling; it is not restated here.)
- Any claim that `--auto` requires a specific repo-level setting to be enabled — not
  verified either way.
- What `--auto` does on a non-queue branch whose required checks have *already*
  passed. `gh`'s help covers only queue-enabled branches, and settling it would mean
  arming auto-merge on a live PR.
- A prefabricated `gh pr list --jq` triage one-liner — the observed variants differ
  per session, so the plain `--json` field list is given instead.
- Trigger phrases and policy wording — transcripts record the work, never the ask.

Asserted from GitHub's documented behavior but **not** demonstrated here, so treat
as the likely explanation rather than a checked fact: that a repo-scoped credential
is why the 404 appears (rather than a 403), and the internal reason a first
`mergeStateStatus` read comes back `UNKNOWN`. The observable behavior in both cases
is verified above; only the mechanism is inferred.

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
