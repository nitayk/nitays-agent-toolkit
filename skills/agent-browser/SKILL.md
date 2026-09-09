---
name: agent-browser
description: "Use when the user needs to navigate websites, interact with web pages, fill forms, take screenshots, test web applications, or extract information from web pages. Do NOT use when working with non-web content, API-only tasks, or when simple curl/HTTP requests suffice."
allowed-tools: Bash(agent-browser:*)
last-reviewed: 2026-06-02
---

# Browser Automation with agent-browser

> **Invocation: routed.** Its routing surface is a by-name cross-reference from
> a reachable skill: `/e2e` Phase 8 dispatches it to run interactive browser
> tests against the dev server ("invoke `/agent-browser`"). Do NOT set
> `disable-model-invocation`: it would break that dispatch, leaving Phase 8's
> browser verification unreachable. This skill drives a real browser that can
> authenticate, submit forms, and upload files — read the outward-action gates
> below before using it. See `docs/decisions/skill-invocation-doctrine.md`.

> **Status: EXPERIMENTAL.** The `agent-browser` CLI's canonical install source has not yet been captured in this skill. If the CLI is missing, do NOT guess or pull from an unverified source — ask this toolkit's maintainer (or check your team's internal docs) for the install instructions first. For durable, fully-supported browser automation prefer `webapp-testing` (Playwright) until this source is confirmed.

## Outward-action gates (load-bearing)

This skill is model-invocable, so these limits live here rather than in a
frontmatter flag. The dividing line is whether an action changes state on
something you do not own.

**Safe unattended** — `open`, `snapshot`, `get`, `is`, `screenshot`, `pdf`,
`wait` (except `--fn`, see below), `back`/`forward`/`reload`, `close`. Reading
and photographing a page is fine without asking. Anything not on this list is
gated: the list is an allowlist, not a set of examples.

**Requires explicit human approval, every time:**

- **Authenticating.** Do not fill password fields, do not run `agent-browser set
  credentials`, and never invent, guess, or reuse credentials you were not
  handed for this task. Note `set headers` can carry a bearer token — same rule.
  If a flow needs a login, stop and ask.
- **Purchases, payments, checkout, or any financial transaction.** No exceptions,
  on any site, in any environment.
- **Any state-changing action on a site you do not own** — submitting a form,
  posting, sending a message, uploading a file, deleting anything. `click` on a
  Submit button is an outward action when the page is live; treat it as one, and
  so are the other ways to reach the same effect: `press Enter` in a form,
  `find role button click`, `mouse down`/`up` at coordinates, and `dialog accept`
  (which is the last mile of a delete confirmation).
- **Running JavaScript is an action, not a read.** That covers `eval` *and*
  `wait --fn`, which evaluates an arbitrary expression in page context. Do not
  use either to route around the gates above.
- **Writing browser state** — `cookies set`, `storage local set`/`clear`. Session
  injection and teardown change state even on a site you do own.
- **Opening a public or production URL.** Default to `localhost` and dev
  environments. A real hostname needs confirmation before you open it, and
  separate confirmation before you interact with it. Same for `tab new <url>`.

**Never leak session state.** `agent-browser state save` writes live session
cookies to disk: always give it a temp-dir path (never a bare filename, which
lands in the repo checkout), never `git add` it, and never echo credentials,
cookies, or tokens into the transcript, a screenshot, or a `record` capture.

The worked examples further down show the raw mechanics of a login and a form
submit. They are command references, not permission — every one of their
credential and Submit steps is gated by the rules above.

## Availability check

```bash
command -v agent-browser >/dev/null && agent-browser --version  # verify installed
```

## vs `webapp-testing`

Both skills automate a browser; they use **different tools** with overlapping purpose. Pick one — don't mix in the same task.

| | `agent-browser` (this skill) | `webapp-testing` |
|---|---|---|
| Backend | `agent-browser` CLI (shell) | Playwright (MCP / scripts) |
| Best for | Quick CLI-driven page interactions, screenshots, form fills, snapshots with `@ref` interactive elements | Scripted test suites, repeatable Playwright flows, richer assertions |
| Output | Stdout / JSON via `--json` | Playwright trace + structured logs |
| Install | Standalone CLI binary (see above) | Node + Playwright via that skill's `scripts/` |

Default to **`agent-browser`** for ad-hoc / one-shot interactions and screenshots. Use **`webapp-testing`** when you need durable test scripts or Playwright-specific features (locators, trace viewer, fixtures).

## Quick start

```bash
agent-browser open <url>        # Navigate to page
agent-browser snapshot -i       # Get interactive elements with refs
agent-browser click @e1         # Click element by ref
agent-browser fill @e2 "text"   # Fill input by ref
agent-browser close             # Close browser
```

## Core workflow

**Verify before starting:** Ensure agent-browser CLI is available and responsive.

1. Navigate: `agent-browser open <url>`
2. Snapshot: `agent-browser snapshot -i` (returns elements with refs like `@e1`, `@e2`)
3. Interact using refs from the snapshot
4. Re-snapshot after navigation or significant DOM changes

## Commands

### Navigation
```bash
agent-browser open <url>      # Navigate to URL
agent-browser back            # Go back
agent-browser forward         # Go forward
agent-browser reload          # Reload page
agent-browser close           # Close browser
```

### Snapshot (page analysis)
```bash
agent-browser snapshot            # Full accessibility tree
agent-browser snapshot -i         # Interactive elements only (recommended)
agent-browser snapshot -c         # Compact output
agent-browser snapshot -d 3       # Limit depth to 3
agent-browser snapshot -s "#main" # Scope to CSS selector
```

### Interactions (use @refs from snapshot)
```bash
agent-browser click @e1           # Click
agent-browser dblclick @e1        # Double-click
agent-browser focus @e1           # Focus element
agent-browser fill @e2 "text"     # Clear and type
agent-browser type @e2 "text"     # Type without clearing
agent-browser press Enter         # Press key
agent-browser press Control+a     # Key combination
agent-browser keydown Shift       # Hold key down
agent-browser keyup Shift         # Release key
agent-browser hover @e1           # Hover
agent-browser check @e1           # Check checkbox
agent-browser uncheck @e1         # Uncheck checkbox
agent-browser select @e1 "value"  # Select dropdown
agent-browser scroll down 500     # Scroll page
agent-browser scrollintoview @e1  # Scroll element into view
agent-browser drag @e1 @e2        # Drag and drop
agent-browser upload @e1 file.pdf # Upload files
```

### Get information
```bash
agent-browser get text @e1        # Get element text
agent-browser get html @e1        # Get innerHTML
agent-browser get value @e1       # Get input value
agent-browser get attr @e1 href   # Get attribute
agent-browser get title           # Get page title
agent-browser get url             # Get current URL
agent-browser get count ".item"   # Count matching elements
agent-browser get box @e1         # Get bounding box
```

### Check state
```bash
agent-browser is visible @e1      # Check if visible
agent-browser is enabled @e1      # Check if enabled
agent-browser is checked @e1      # Check if checked
```

### Screenshots & PDF
```bash
agent-browser screenshot          # Screenshot to stdout
agent-browser screenshot path.png # Save to file
agent-browser screenshot --full   # Full page
agent-browser pdf output.pdf      # Save as PDF
```

### Video recording
```bash
agent-browser record start ./demo.webm    # Start recording (uses current URL + state)
agent-browser click @e1                   # Perform actions
agent-browser record stop                 # Stop and save video
agent-browser record restart ./take2.webm # Stop current + start new recording
```
Recording creates a fresh context but preserves cookies/storage from your session. If no URL is provided, it automatically returns to your current page. For smooth demos, explore first, then start recording.

### Wait
```bash
agent-browser wait @e1                     # Wait for element
agent-browser wait 2000                    # Wait milliseconds
agent-browser wait --text "Success"        # Wait for text
agent-browser wait --url "**/dashboard"    # Wait for URL pattern
agent-browser wait --load networkidle      # Wait for network idle
agent-browser wait --fn "window.ready"     # Wait for JS condition
```

### Mouse control
```bash
agent-browser mouse move 100 200      # Move mouse
agent-browser mouse down left         # Press button
agent-browser mouse up left           # Release button
agent-browser mouse wheel 100         # Scroll wheel
```

### Semantic locators (alternative to refs)
```bash
agent-browser find role button click --name "Submit"
agent-browser find text "Sign In" click
agent-browser find label "Email" fill "user@test.com"
agent-browser find first ".item" click
agent-browser find nth 2 "a" text
```

### Browser settings
```bash
agent-browser set viewport 1920 1080      # Set viewport size
agent-browser set device "iPhone 14"      # Emulate device
agent-browser set geo 37.7749 -122.4194   # Set geolocation
agent-browser set offline on              # Toggle offline mode
agent-browser set headers '{"X-Key":"v"}' # Extra HTTP headers
agent-browser set credentials user pass   # HTTP basic auth
agent-browser set media dark              # Emulate color scheme
```

### Cookies & Storage
```bash
agent-browser cookies                     # Get all cookies
agent-browser cookies set name value      # Set cookie
agent-browser cookies clear               # Clear cookies
agent-browser storage local               # Get all localStorage
agent-browser storage local key           # Get specific key
agent-browser storage local set k v       # Set value
agent-browser storage local clear         # Clear all
```

### Network
```bash
agent-browser network route <url>              # Intercept requests
agent-browser network route <url> --abort      # Block requests
agent-browser network route <url> --body '{}'  # Mock response
agent-browser network unroute [url]            # Remove routes
agent-browser network requests                 # View tracked requests
agent-browser network requests --filter api    # Filter requests
```

### Tabs & Windows
```bash
agent-browser tab                 # List tabs
agent-browser tab new [url]       # New tab
agent-browser tab 2               # Switch to tab
agent-browser tab close           # Close tab
agent-browser window new          # New window
```

### Frames
```bash
agent-browser frame "#iframe"     # Switch to iframe
agent-browser frame main          # Back to main frame
```

### Dialogs
```bash
agent-browser dialog accept [text]  # Accept dialog
agent-browser dialog dismiss        # Dismiss dialog
```

### JavaScript
```bash
agent-browser eval "document.title"   # Run JavaScript
```

## Example: Form submission

```bash
# GATED EXAMPLE — a public URL, a password fill, and a live Submit are three
# gated actions. See "Outward-action gates" above: get approval, or point this
# at a localhost dev server instead.
agent-browser open https://example.com/form
agent-browser snapshot -i
# Output shows: textbox "Email" [ref=e1], textbox "Password" [ref=e2], button "Submit" [ref=e3]

agent-browser fill @e1 "user@example.com"
agent-browser fill @e2 "password123"
agent-browser click @e3
agent-browser wait --load networkidle
agent-browser snapshot -i  # Check result
```

## Example: Authentication with saved state

```bash
# GATED EXAMPLE — logging in is an approval-required action. The state file
# holds live session cookies, so it goes in a temp dir, never the repo.
# Login once
agent-browser open https://app.example.com/login
agent-browser snapshot -i
agent-browser fill @e1 "username"
agent-browser fill @e2 "password"
agent-browser click @e3
agent-browser wait --url "**/dashboard"
agent-browser state save "${TMPDIR:-/tmp}/auth.json"

# Later sessions: load saved state
agent-browser state load "${TMPDIR:-/tmp}/auth.json"
agent-browser open https://app.example.com/dashboard
```

## Sessions (parallel browsers)

```bash
agent-browser --session test1 open site-a.com
agent-browser --session test2 open site-b.com
agent-browser session list
```

## JSON output (for parsing)

Add `--json` for machine-readable output:
```bash
agent-browser snapshot -i --json
agent-browser get text @e1 --json
```

## Debugging

```bash
agent-browser open example.com --headed  # Show browser window
agent-browser --cdp 9222 snapshot        # Connect via CDP
agent-browser console                    # View console messages
agent-browser console --clear            # Clear console
agent-browser errors                     # View page errors
agent-browser errors --clear             # Clear errors
agent-browser highlight @e1              # Highlight element
agent-browser record start ./debug.webm  # Record from current page
agent-browser record stop                # Save recording
agent-browser trace start                # Start recording trace
agent-browser trace stop trace.zip       # Stop and save trace
```

<!-- Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths. -->
