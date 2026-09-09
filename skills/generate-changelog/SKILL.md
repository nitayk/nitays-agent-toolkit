---
name: generate-changelog
description: "Use when generating changelog from git commits. Creates Keep a Changelog format from conventional commits. Make sure to use when user says: generate changelog, /changelog, create changelog, or needs release notes from commits."
disable-model-invocation: true
last-reviewed: 2026-06-08
---
# Generate Changelog

Generate a changelog from recent commits following Keep a Changelog format.

## When to Use This Skill

**APPLY WHEN:**
- User wants changelog for a release
- User says "generate changelog", "/changelog", "create changelog"
- Preparing release notes from commits

**SKIP WHEN:**
- No conventional commits (feat:, fix:, etc.)
- No git tags for version ranges
- Working in a repo whose release model has **no** CHANGELOG automation (e.g. bump-on-PR). This skill targets repos that maintain a `CHANGELOG.md`.

## Core Directive

**Fetch commits → Group by type (feat, fix, docs, etc.) → Format per Keep a Changelog → Append or create CHANGELOG.md.**

## Usage

```
/changelog [from-tag] [to-tag]
/changelog
```

## Process

1. **Resolve the version range.**
   - Explicit `from-tag to-tag`: use it directly.
   - No args given: newest tag → HEAD. Get the newest tag with
     `git describe --tags --abbrev=0`, then use `<latest-tag>..HEAD`.
   - **No tags exist at all:** fall back to full history (`git log`) and label
     the section `Unreleased` — do **not** error out.

2. **Fetch the commits in range:**
   ```bash
   git log <from>..<to> --no-merges --pretty=format:'%s%x1f%b%x1e'
   ```
   `%x1f` (unit sep) / `%x1e` (record sep) delimit subject/body/records so
   multi-line bodies carrying `BREAKING CHANGE:` footers parse cleanly.

3. **Group by conventional-commit type** (the prefix before the first `:`):
   - **Added** ← `feat:` · **Fixed** ← `fix:` · **Changed** ← `BREAKING CHANGE:`
     or a `!` bang (e.g. `feat!:`) · **Documentation** ← `docs:` ·
     **Refactoring** ← `refactor:` · **Performance** ← `perf:` ·
     **Tests** ← `test:` · **Chore** ← `chore:`
   - **Non-conventional commits** (no recognized prefix): collect under an
     **Other** heading — never drop them silently.

4. **Derive version + date.** Version = the `to-tag` with any leading `v`
   stripped (`v1.3.0` → `1.3.0`); if the range ends at HEAD with no tag, use
   `Unreleased`. Date = today in ISO form; header is `## [<version>] - YYYY-MM-DD`.

5. **Preview, then write.** Show the rendered changelog and confirm with the
   user before writing. Then prepend the new section to an existing
   `CHANGELOG.md` (preserving earlier entries) or create the file with a
   Keep a Changelog header if absent.

## Examples

```
/changelog
```

Generate changelog since last tag.

```
/changelog v1.2.0 v1.3.0
```

Generate changelog between specific versions.

## Format

Follows [Keep a Changelog](https://keepachangelog.com/) format:

```markdown
## [1.3.0] - 2026-01-25

### Added
- New feature X
- New feature Y

### Fixed
- Bug fix A
- Bug fix B

### Changed
- Breaking change description
```

## Requirements

- Uses conventional commit messages (feat:, fix:, etc.)
- Requires git tags for version ranges

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
