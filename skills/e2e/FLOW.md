# /e2e Visual Flow — index

Visual companion to [SKILL.md](./SKILL.md). META reference: refresh
when the skill changes **structurally** (new phase, new gate, changed
skip-logic, new classification). Cosmetic edits to SKILL.md do not
require a refresh.

## Files

| File | Purpose |
|---|---|
| **[flow.html](./flow.html)** | **Interactive viewer — open this** |
| [flow.svg](./flow.svg) | Static SVG (no controls) — fallback / preview |
| [flow.d2](./flow.d2) | Source for the main top-down decision tree |
| [flow-legend.svg](./flow-legend.svg) | Style legend + 10 cross-phase rules (separate, smaller) |
| [flow-legend.d2](./flow-legend.d2) | Source for the legend |

## How to use the viewer

Open `flow.html` in any browser. It loads a self-contained pan/zoom
viewer (svg-pan-zoom inlined, no network needed). Controls:

- **Drag** to pan in any direction
- **Scroll wheel** to zoom in/out
- **`+`** / **`−`** / **`fit`** / **`50%`** toolbar buttons
- **Hover** any green box for the skill's one-line description (SVG title tooltip)
- The "legend ↗" link opens the style + cross-phase rules in a second tab

## How to refresh

After editing `flow.d2` or `flow-legend.d2`:

```sh
cd skills/e2e/
d2 flow.d2 flow.svg          # regenerate main SVG
d2 flow-legend.d2 flow-legend.svg
# Rebuild the self-contained flow.html (inlines flow.svg + vendored svg-pan-zoom):
python3 ../../scripts/build-flow-html.py
```

`build-flow-html.py` is stdlib-only and network-free — it inlines the
current `flow.svg` plus the vendored `svg-pan-zoom@3.6.1`
(`.flow-assets/`, MIT) into a single `flow.html`. Always re-run it after
regenerating `flow.svg`; never hand-edit `flow.html`.

## What's in the diagram

- **Top:** `INPUT` → Phase 0 INTAKE (expanded internal sequence with conditionals)
- **Middle:** classification fork (feature / bugfix / refactor / spike / hotfix / docs-only) → P1–P9
- **Critical fork:** Skip P6+P7 decision (per classification). Hotfix routes through the same P6+P7 path as feature/bugfix/refactor — gates run by default. The hotfix HIGH-escalation rule is an enforcement rule (in SKILL.md), not a visual branch.
- **Hard gates:** P6 (Quality) and P7 (Review) in red
- **Spike special:** P6 partial in yellow — `/code-cleanup` only (other gates skip; P7 skips, no PR expected)
- **Phase 8 special-modes (yellow):** empirical-not-static check (security-sensitive) and restore-semantics check (restore/lock/snapshot commands)
- **Bottom:** P10 LEARN → DONE

## Style legend

| Style | Meaning |
|---|---|
| green | active step |
| gray dashed | skipped per classification |
| red bold | HARD GATE (P6, P7) |
| yellow | special / partial mode (spike P6 cleanup-only, P8 empirical, P8 restore-semantics) |
| diamond | decision point (classification, conditional, executor pick) |
