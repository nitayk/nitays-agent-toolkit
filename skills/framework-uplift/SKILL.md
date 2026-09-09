---
name: framework-uplift
description: >-
  Same-stack version upgrade done safely — framework, toolchain, or runtime major bumps
  (vite 5→8, Spring Boot 2→3, .NET Framework→8, Go 1.x, Node majors, Scala compiler).
  Preserve the code; fix only the version deltas; prove equivalence by running the same
  checks on both sides. Invoke for "upgrade/bump X to version Y", "move off the EOL
  runtime", "the majors are piling up". Not for cross-language rewrites (see the
  maintainer note's kit pointer), moving code between repositories, or
  retiring a system (/deprecation-and-migration).
last-reviewed: 2026-07-22
---

# framework-uplift — same-stack version bumps, done in the right order

Generalized from Anthropic's `/modernize-uplift` doctrine (code-modernization
plugin) + our Bun-playbook reading, proven here on a real 5-major bump
(vite 5→8 + vitest 2→4 + plugin-svelte 4→7 + jsdom 25→29 + jest-dom 6→7:
zero source changes, green both sides). The order below is the value;
most failed uplifts did these steps in the wrong order.

## The seven steps — in this order

1. **Pin the exact version pair.** "Upgrade vite" is not a plan; "5.4.21 → 8.1.5"
   is. Every later step depends on the pair. If the user is vague, ask.
2. **Verify the target toolchain's floor.** Runtime version (Node/JDK/Go/.NET),
   OS constraints, peer majors. Fail fast here, not mid-upgrade.
3. **The test-framework question — before any planning.** Can the existing suite
   execute on the target as-is? Test runners are version-locked to the things
   they test (vitest↔vite, JUnit↔JDK, NUnit↔.NET). If not, the test-tooling
   migration is a PREREQUISITE, not a leaf — nothing you migrate can be
   validated until the validator itself runs on the target.
4. **Record the baseline BEFORE touching anything.** Run the full suite + build
   + typecheck on the CURRENT versions and write the numbers down (pass count,
   build exit, warnings). This is the equivalence target. A red baseline is a
   finding, not a blocker — but it must be known-red, with the failures listed.
5. **Delta catalog — the breaking changes *this* code hits.** Don't read the
   whole changelog; read YOUR config/usage surface against the known breaking
   changes, and run the ecosystem's own migration tool where one exists
   (`npx @next/codemod`, OpenRewrite, `dotnet upgrade-assistant`, scalafix).
   Deliberately exclude non-routine bumps hiding in the list (a "major" that is
   actually a rewrite-preview or a product pivot — e.g. TypeScript 7).
6. **Smallest diffs; pilot-first when there are multiple projects.** Version-locked
   majors move TOGETHER in one change (vitest with vite), independent ones move
   separately. For a multi-project repo: migrate ONE representative project
   end-to-end, write the lessons to a playbook, then fan out the rest in
   dependency order — stop the batch on the first novel failure (circuit breaker).
7. **Equivalence, both sides, honestly labeled.** Best case: same suite runs on
   old and new (record both). When the old side can't run anymore, say so —
   equivalence degrades to "suite green on target + baseline doc" and the
   review must know which proof it got. Include every referee the repo has:
   tests, build, typecheck, AND downstream consumers of build artifacts
   (e.g. a `go build` that embeds the JS bundle).

## Recurring gotchas (earned)

- **npm eresolve against the stale tree:** upgrading version-locked majors
  in-place trips peer resolution on the OLD installed packages. Pin the new
  majors in `package.json`, then fresh-install (remove lockfile + node_modules)
  instead of fighting `--legacy-peer-deps`.
- **Three repeats = a rule bug.** Fixing the same class of error a third time
  means the delta catalog is missing an entry: add it, then re-apply
  mechanically — don't keep hand-fixing instances.
- **Never weaken a test to shrink the queue.** Old-fails-too = inherited (run
  the failing test on the OLD version before classifying); slow-but-passing
  goes in a ledger, not a deletion.

<!-- Maintainer notes (2026-07-22):
     - Sources: Anthropic code-modernization plugin /modernize-uplift (official
       marketplace, maintained upstream — install it for the heavy legacy-discovery
       pipeline: assess/map/extract-rules/brief) and the Apache-2.0
       anthropics/code-migration-kit-with-claude-code (TOTAL language migrations:
       rulebook, disk queues, judge-before-translation — reference, unmaintained;
       clone on demand, don't vendor).
     - scala-upgrade-agent / scala-dependency-hell are narrow instances of this
       skill; keep them (deeper JVM specifics), route generic bumps here.
     - Proof run: a real 5-major toolchain uplift (vite 5→8 + vitest 2→4 +
       plugin-svelte 4→7 + jsdom + jest-dom), zero source changes, suite/build/
       typecheck green both sides (2026-07-22). -->

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
