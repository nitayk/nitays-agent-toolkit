---
name: debugging-mcp-servers
description: "Debug an MCP server at the WIRE level when a tool silently doesn't fire, fires with the wrong arguments, hangs, or capabilities don't line up — by watching the real JSON-RPC frames between YOUR client and the server. Wraps the server with mcpsnoop (a transparent proxy) so you see the call the model actually made (or never made), not a guess from server logs. Use on 'why didn't that MCP tool get called', 'the tool ran with wrong args', 'the MCP call hangs', 'the client and server capabilities disagree'. NOT for authoring/scaffolding a new MCP server (use /mcp-builder), debugging an AGENT's own reasoning failures (use /agent-introspection-debugging), general code debugging (use /systematic-debugging), or interactive server exploration where the official MCP Inspector already suffices. Triggers: 'snoop the MCP traffic', 'wireshark for MCP', 'see the real MCP frames', 'mcpsnoop'."
---

# Debugging MCP servers at the wire level

When an MCP tool misbehaves, the hardest bugs are the *silent* ones: a tool that should fire but doesn't, one that fires with the wrong arguments, a call that hangs, or a client/server capability mismatch. Server logs and the official MCP Inspector can't show these — the **Inspector connects as its own client**, so it never sees what *your* client (Claude Code / Cursor / Codex) actually sent, and it can't show a call the model never made.

The fix is to sit in the **real data path** and watch every JSON-RPC frame. That is what [`mcpsnoop`](https://github.com/kerlenton/mcpsnoop) (MIT) does — a transparent proxy you wrap the server with. Empirically verified against a real stdio MCP and a live HTTP MCP: it proxies without breaking the client and records the exact frames (including a wrong-args call paired with the server's `isError`).

## When to reach for this (and when NOT to)

Use it for **wire-level** MCP symptoms:
- A tool that should have been called wasn't (see whether the client even sent it).
- A tool called with the wrong / missing arguments (see the exact `params`).
- A call that hangs (see which frame never got a response).
- Client and server capabilities disagree after `initialize`.

Pick a different tool when:
- You're **building** an MCP server → `/mcp-builder`.
- The problem is the **agent's own reasoning/recovery**, not the wire → `/agent-introspection-debugging`.
- It's ordinary code debugging → `/systematic-debugging`.
- You just want to **explore** a server's tools interactively and the official MCP Inspector is enough → use the Inspector; reach here only when you need to see your *real client's* traffic.

## Install (on demand — not a standing dependency)

```sh
go install github.com/kerlenton/mcpsnoop/cmd/mcpsnoop@latest   # needs Go >= 1.26
mcpsnoop demo                                                   # scripted session, zero setup — confirms it runs
```

It's a local, dev-time aid: install when you need it, don't wire it into CI or the fleet.

## Use it

**stdio server** — wrap the launch command in your client's MCP config (or drive it directly):

```sh
# in your client's mcp config: "command": "mcpsnoop", "args": ["--", "<real server cmd>", ...]
# or drive it directly, capturing a trace:
mcpsnoop --trace-file trace.jsonl --redact-secrets -- npx -y @scope/some-mcp-server
```

**streamable-HTTP server** — run mcpsnoop as a reverse proxy and point your client (or a curl probe) at the listen address:

```sh
mcpsnoop http --target https://the-real-server/mcp --listen 127.0.0.1:7099 --redact-secrets
# then POST JSON-RPC to http://127.0.0.1:7099/mcp — frames are captured transparently
```

`--redact-secrets` / `--redact-key token,authorization` scrub auth headers/keys in the saved trace — use them for any authenticated server.

## Read the frames

The trace is line-delimited JSON, one frame each, direction-tagged. The signal you're after:

```
[c2s] tools/call id=4 args={"WRONG_ARG":"..."}   ← what the client ACTUALLY sent
[s2c] response  id=4 isError=True                 ← how the server answered
```

`c2s` = client→server (the request the model made), `s2c` = server→client (the response). A missing `c2s` for a tool you expected = the model never called it. A `c2s` with the wrong `args` = a schema/prompt problem on the client side. A `c2s` with no matching `s2c` = where it hung. That pairing — the real call next to the real response — is the whole point: it turns "the tool didn't work" into a specific, located frame.

<!-- References the external tool mcpsnoop (kerlenton/mcpsnoop, MIT) — not vendored; credited in SOURCES.md. -->

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
