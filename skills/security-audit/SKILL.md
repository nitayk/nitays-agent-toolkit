---
name: security-audit
description: "Use for the deeper periodic security audit at release boundaries — runs language-specific scanners, secret detection, and OWASP-mapped review with concrete tool commands. Do NOT use for the per-PR security gate (use /security-review — lighter, faster, runs every PR). Triggers: 'security audit', 'audit before release', 'security review of release N'."
disable-model-invocation: true
last-reviewed: 2026-07-11
---

# Security Audit

Periodic deep security audit at release boundaries. **Heavier and slower than `/security-review`** — that's the per-PR gate; this is the release-boundary deep dive.

## When to use

**APPLY WHEN:**
- A release / milestone boundary (deep audit, not per-PR)
- User explicitly asks for "security audit", "audit before release", "deep security review"
- Periodic scheduled audit (quarterly, pre-launch, etc.)

**SKIP WHEN:**
- Per-PR security gate → use `/security-review` (faster, smaller scope)
- General code review → use `/code-review`
- The user just wants security documentation → write docs directly

`/security-review` ≠ `/security-audit`. The review is the per-PR scan; the audit is the release-boundary deep dive. Don't substitute one for the other — they have different cost and different depth.

## Anti-patterns (refuse these)

- **Fabricating findings from grep.** Do NOT report a vulnerability inferred from grep matches alone. A grep hit on `eval(` or `exec(` is not a finding — confirm the data path is *attacker-reachable* (taint flows from user input to the sink) before reporting. If you can't confirm reachability, mark the finding as "POTENTIAL — needs reachability check" and explicitly say so.
- **Hallucinating CVEs.** Do not invent CVE numbers. If you reference a CVE, the user must be able to find it on nvd.nist.gov. When unsure, describe the vulnerability class without a CVE ID.
- **Reporting without scanner.** If no scanner is installed for the language being audited, say so explicitly: "Auditing without `bandit` / `gosec` / `npm audit` — manual review only, scanner-detectable issues may be missed." Do not pretend you ran a scan you didn't run.
- **Severity inflation.** Tying a HIGH severity to a vulnerability that requires authenticated admin access + user interaction + a network position is wrong. Use the [CVSS](https://www.first.org/cvss/calculator/3.1) factors honestly.

## Workflow

### Step 1 — Inventory the surface

Identify what you're auditing:
- Language(s) and frameworks in scope
- Network-exposed surfaces (HTTP endpoints, gRPC services, message-queue consumers)
- Trust boundaries (where untrusted input enters)
- Authentication / authorization boundaries
- Secrets handling (env vars, secret managers, files)
- Third-party dependencies (lockfiles)

If the audit scope is large (whole repo), narrow first — pick the highest-risk subset (auth, payment, public APIs) and audit those first.

**Run a quick STRIDE pass over each trust boundary** to prioritize where Step 3 digs deepest — a lens, not a ceremony:

| Threat | Question to ask at the boundary |
|---|---|
| **S**poofing | Can a caller pretend to be someone else here (missing/weak authn, forgeable tokens)? |
| **T**ampering | Can data crossing this boundary be modified in transit or at rest (no integrity check, no TLS)? |
| **R**epudiation | If someone abuses this surface, would we know who did it (audit logging)? |
| **I**nformation disclosure | What leaks if this boundary is breached (secrets in errors, verbose responses, cross-tenant reads)? |
| **D**enial of service | Can this surface be exhausted cheaply (no rate limit, unbounded input, amplification)? |
| **E**levation of privilege | Can a low-privilege caller reach a high-privilege operation through this path (missing authz, confused deputy)? |

Boundaries with multiple live STRIDE rows get the deepest manual review. Count **LLM/agent integrations** as trust boundaries too — model output is untrusted input (see the AI/LLM review below).

### Step 2 — Run language-specific scanners

| Language / ecosystem | SAST | Secrets | Deps |
|---|---|---|---|
| Python | `bandit -r src/`, `semgrep --config=auto src/` | `trufflehog filesystem .` | `pip-audit`, `safety scan` |
| Go | `gosec ./...`, `staticcheck ./...` | `trufflehog filesystem .` | `govulncheck ./...`, `nancy sleuth` |
| Node / TypeScript | `semgrep --config=auto`, `eslint-plugin-security` | `trufflehog filesystem .` | `npm audit`, `pnpm audit`, `osv-scanner` |
| Java / Kotlin | `spotbugs` + `find-sec-bugs`, `semgrep --config=auto` | `trufflehog filesystem .` | `dependency-check`, `osv-scanner` |
| Scala | `wartremover` (limited), `semgrep --config=auto` | `trufflehog filesystem .` | `dependency-check`, `osv-scanner` |
| Rust | `cargo geiger`, `semgrep --config=auto` | `trufflehog filesystem .` | `cargo audit`, `cargo deny` |
| Containers | `trivy image <name>`, `grype <image>` | `trufflehog docker --image <name>` | `trivy image` (covers OS + app deps) |
| IaC / K8s | `kube-score`, `kubescape`, `tfsec`, `checkov` | — | — |

If a scanner isn't installed, do NOT silently skip — explicitly note it in the report ("dependency audit not run: `npm audit` not installed"). Don't fabricate findings to compensate.

### Step 3 — OWASP-mapped manual review

For each high-risk surface (auth, payment, file upload, deserialization, server-side requests):
- Walk the request path from entry → sink
- Check OWASP Top 10 categories applicable to the language/framework
- Verify input validation, output encoding, authn/authz checks, rate limiting

Reference: [OWASP Top 10](https://owasp.org/www-project-top-ten/), [OWASP ASVS](https://owasp.org/www-project-application-security-verification-standard/) for verification level.

#### SSRF deep dive (server-side request surfaces)

When the audited service fetches URLs influenced by user input (webhooks, URL previews, importers, callbacks, link unfurling), check each of these — a partial defense here is a finding:

- **Allowlist, not blocklist** — scheme + host allowlist for outbound destinations; string-matching blocklists are bypassable (decimal IPs, IPv6 mappings, redirects).
- **Resolve and validate ALL addresses** — resolve every A/AAAA record and reject anything non-unicast: loopback, RFC1918 private ranges, IPv6 unique-local, and link-local — `169.254.169.254` (cloud metadata) is the #1 SSRF target.
- **TOCTOU / DNS-rebinding gap** — most HTTP clients re-resolve DNS after your validation, so a short-TTL record can rebind to an internal IP between check and connect. For high-risk surfaces, resolve once and connect to the pinned IP (custom dialer/transport), or front outbound traffic with a filtering proxy.
- **Redirects re-open the hole** — either disable redirect following or re-validate the target on every hop.
- **Defense in depth at the network layer** — egress policy should independently block the metadata endpoint and internal ranges; app-level checks alone are one bug away from useless.

#### AI / LLM feature review (OWASP LLM Top 10)

If the audited service calls an LLM — chatbot, summarizer, agent, RAG — it has attack surface that classic OWASP review misses. Map it to the [OWASP Top 10 for LLM Applications](https://genai.owasp.org/llm-top-10/):

- **Treat all model output as untrusted input (LLM05).** Trace every path where LLM output flows into `eval`, SQL, a shell, `innerHTML`, or a file path — each unvalidated one is a finding, same severity logic as raw user input reaching that sink.
- **Assume prompts can be hijacked (LLM01).** Any untrusted text in the context window — user message, fetched web page, PDF, tool result — can carry instructions. The system prompt is not a security boundary; verify permissions are enforced in code, not in the prompt.
- **Secrets and cross-tenant data out of prompts (LLM02/LLM07).** Anything in the context can be echoed back to the caller. Flag API keys, other users' data, or full system prompts placed where the model can repeat them.
- **Excessive agency (LLM06).** For agent/tool-use integrations: tools scoped to minimum permissions, confirmation required for destructive or irreversible actions, every tool argument validated.
- **RAG tenant isolation.** Retrieval indexes must enforce the caller's tenancy — a shared index without a tenant filter is a cross-tenant read (Information disclosure in the STRIDE pass).

### Step 4 — Generate the report

Use this output template:

```markdown
# Security Audit — <scope> — <date>

## Methodology
- Scanners run: bandit 1.7.7, semgrep auto, trufflehog 3.x, pip-audit
- Manual review scope: src/auth/, src/payment/
- Scanners NOT run: <list any with reason>

## Findings

### HIGH

#### H-1: SQL injection in `src/users/api.py:84`
- **CWE:** [CWE-89](https://cwe.mitre.org/data/definitions/89.html)
- **CVSS 3.1:** 9.1 (Critical)
- **Reachability:** confirmed — `request.args['name']` flows untrusted into raw SQL
- **PoC:** `?name='; DROP TABLE users;--` returns 500 with SQL error
- **Fix:** use parameterized query (`cursor.execute("SELECT ... WHERE name = %s", (name,))`)

### MEDIUM
...

### LOW
...

### POTENTIAL (reachability not confirmed)
...

## Dependency advisories
- (table from scanner output)

## Recommendations
- (prioritized list)
```

Required fields per finding: file:line, CWE / CVE if known, CVSS or severity rationale, **reachability evidence** (or POTENTIAL flag), concrete fix.

### Step 5 — Verify high-severity fixes empirically

For HIGH findings: don't accept a fix as resolved without exercising it. See `/e2e` Phase 6 "Belt-and-suspenders happy-path check" — argument-mutating fixes can introduce new bugs (e.g. `git checkout -- main` shifts to pathspec mode and silently breaks branch checkout).

## Pair with

- `/security-review` — per-PR gate (lighter, faster, runs every PR — *not* a substitute for this skill at release boundaries)
- `/generate-changelog` — pair with security-audit at release boundaries to capture remediated CVEs in release notes

<!--
STRIDE lens, SSRF deep dive, and AI/LLM review adapted from addyosmani/agent-skills
`security-and-hardening` (https://github.com/addyosmani/agent-skills), MIT License,
© Addy Osmani. Copied once with attribution — not auto-synced. See SOURCES.md.
Cross-platform: see AGENTS.md in the repository root for Cursor, Claude Code, and Copilot paths.
-->
