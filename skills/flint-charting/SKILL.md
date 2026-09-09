---
name: flint-charting
description: "Wire agent/LLM-driven chart generation into a product using Microsoft Flint (flint-chart) instead of hand-writing Vega-Lite/ECharts/Chart.js specs. Use when ADDING or IMPLEMENTING programmatic chart generation in an app, dashboard, or service — 'add charts to', 'agent should generate a chart', 'render analytics in the app', 'LLM-generated visualization', 'charting layer'. NOT for one-off charts in chat/artifacts or terminal plots (use terminal-chart) — this is for building chart generation into a product."
---

# flint-charting — agent-driven charts go through Flint

**Rule:** when a product needs an agent/LLM to emit charts, the agent authors a compact **Flint spec** (`ChartAssemblyInput`: `semantic_types` + `chart_spec`) and the **flint-chart compiler** derives the fragile parts (scales, axes, formats, layout) and produces the backend spec (Vega-Lite / ECharts / Chart.js). Never let the LLM hand-write raw backend JSON in product code paths.

**Why (measured, 2026-07-12):** blind same-model head-to-head on real Prometheus metrics, 6 chart tasks: hand-written Vega-Lite crashed 2/6 renders (temporal-format-on-ordinal-axis class of error — the canonical LLM chart bug); Flint rendered 6/6 with ~3× smaller specs (368 B vs 1,096 B avg) and equal-or-better visual quality. flint-chart is MIT (microsoft/flint-chart) — vendorable into proprietary products.

## How to wire it

Pick ONE of two integration shapes:

1. **In-process (preferred for product code):**
   ```bash
   npm install flint-chart
   ```
   ```ts
   import { assembleVegaLite } from 'flint-chart';   // also assembleECharts / assembleChartjs
   const vlSpec = assembleVegaLite({ data: { values: rows }, semantic_types, chart_spec });
   ```
   The LLM's job is ONLY `{semantic_types, chart_spec}` — the host binds `data`. There is also a Python package (`flint-py` in the same repo).

2. **MCP server (agent workflows / prototyping):**
   ```bash
   npx flint-chart-mcp   # stdio; tools: render_chart, compile_chart, validate_chart, list_chart_types, create_chart_view
   ```
   Add per-project, NOT to an always-on global config — it costs context in every session.

**Authoring reference for the generating agent's prompt:** [references/flint-chart-author-upstream.md](references/flint-chart-author-upstream.md) (upstream authoring guide, snapshot @ 0.2.0). 34 chart types across Points / Bars / Distributions / Lines & Areas / Circular / Tables & Maps; donut = Pie Chart + `chartProperties.innerRadius`.

## Known caveats (verified on 0.2.0 — re-verify on the version you adopt)

- **`stackMode` on Area Chart is broken:** it compiles to Vega-Lite `stack:"stacked"` (an invalid enum value), so the chart silently renders UNSTACKED with occluded series. Omit the property — Vega-Lite's default already stacks area+color correctly.
- Temporal axes can emit sub-day ticks ("12 PM") on daily-grain data — cosmetic.
- Flint output has no chart title unless you set one — set it explicitly for dashboards.

## Validation gate (always)

Compile and render every generated spec server-side (vl-convert, or the MCP's `validate_chart`) before showing it to a user; on failure, feed the compiler error back to the LLM and retry. Flint removes most chart failure modes, not all.

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
