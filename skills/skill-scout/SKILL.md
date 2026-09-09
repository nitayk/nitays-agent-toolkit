---
name: skill-scout
description: >-
  Rank every installed skill (local, all plugin caches, extra dirs) against a task
  phrase and return the best candidates — calibrated retrieval that subtracts the
  boilerplate all SKILL.md descriptions share, so the discriminative words decide the
  match. Invoke when the user asks "which skill should I use / is there a skill for X",
  when routing an unfamiliar task and the in-context skill list feels ambiguous, or
  before dispatching subagents whose briefs should name the right skill. Not a
  replacement for reading the chosen skill — it's a candidate finder, not an oracle.
last-reviewed: 2026-07-22
---

# skill-scout — find the right skill for a task phrase

`/using-superpowers` supplies the discipline ("if a skill applies you MUST use it");
this skill supplies the *finder*. One CLI call ranks every installed skill
description against the phrase, using description-calibrated retrieval
(clean-room implementation of arXiv:2607.18785 — see the maintainer note).

## Run it

```bash
uv run --python 3.12 <this skill's base dir>/scripts/skill_scout.py "<task phrase>" -k 8
```

- ~1s end-to-end (model2vec static embeddings — no torch; the corpus embedding
  cache under `~/.cache/skill-scout/` self-invalidates when any SKILL.md
  description changes).
- Surfaces scanned by default: `~/.claude/skills` + every installed plugin
  cache. Add repo checkouts via `SKILL_SCOUT_DIRS=/path/to/repo/skills:...`.
- `--reindex` forces a cache rebuild; `--eval <queries.json>` prints Recall@k
  against a labeled set (`[{"query": ..., "gold": [skill-names]}]`).
- Phrase the query as the TASK, not as a skill name ("watch the CI run and retry
  until green", not "loop skill").

## Protocol

1. Run the CLI with the user's task phrasing (or your restatement of it).
2. Treat the output as **candidates**: read the top hit's SKILL.md (path is
   printed) before invoking — descriptions can mislead, and ranking ≠ fit.
3. Invoke the chosen skill by its proper id (plugin skills need the
   `plugin:skill` namespace; a bare name may not resolve).
4. If nothing above rank ~5 fits, say so — "no skill covers this" is a valid
   answer and better than forcing a bad match.

## Why not just read the skills list in context?

Model-in-context triggering measurably misses **wrapper skills** whose
descriptions gate actions the model can already do (0% recall on that class in
our tests). Calibration fixes exactly that: on 30 real trigger phrases mined
from actual session transcripts against a 286-skill corpus, this tool ranks the
gold skill in the top-5 at **0.767 vs 0.700** for plain dense retrieval on the
same embeddings (top-10: **0.867 vs 0.700**), and returns the previously-missed
wrapper skills at #1.

<!-- Maintainer notes (2026-07-22):
     - Math reimplemented from the paper (arXiv:2607.18785); the authors' repo is
       UNLICENSED — do not vendor/copy its code. Two calibrations: (a) project out
       the background subspace spanned by low-IDF token doc-mean embeddings (Otsu
       split on df-weighted IDF, entropy-effective SVD rank); (b) lexical evidence
       w(t)=IDF(t)*(1-DF/N)^beta over non-generic query tokens; dispersion-weighted
       z-fusion (beat the paper's Eq.15 rectified variant on the eval, .767 vs .733 @5).
     - Config is frozen; the eval set is n=30 so ±1-2 query shifts are noise —
       resist re-tuning on it. -->

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
