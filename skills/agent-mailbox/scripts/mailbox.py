#!/usr/bin/env python3
"""agent-mailbox: dependency-free, file-based message relay between Claude Code agents.

No MCP server, no daemon, no network, no Claude channels — just JSON files under a
shared directory, so it works even where Claude Code's dev channels are disabled.

Identity is bound to the real session (env CLAUDE_CODE_SESSION_ID), so commands
auto-resolve "who am I" — agents don't pass --as on every call. Humans only ever
see a friendly display name (derived from the agent's task), never the session id.

Layout (root defaults to ~/.agent-mailbox, override with $AGENT_MAILBOX_ROOT):
  <root>/<display-name>/inbox/        new messages addressed to this agent
  <root>/<display-name>/in-progress/  messages this agent has claimed
  <root>/<display-name>/done/         handled (archive; cleared by prune)
  <root>/<display-name>/sent/         copies of what this agent sent
  <root>/<display-name>/heartbeat.json  {name, session_id, focus, last_seen}

Message = JSON: {id, from, to, ts, subject, body, reply_to?}

Delivery is PULL: an agent sees messages when it runs check/notify. The
UserPromptSubmit hook runs `notify` each turn to surface a count automatically.
"""
from __future__ import annotations
import argparse, json, os, random, re, shutil, sys, time, uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

try:
    import fcntl                       # POSIX only; macOS + Linux are the supported surface
except ImportError:                    # pragma: no cover - non-POSIX falls back to no locking
    fcntl = None

ROOT = Path(os.environ.get("AGENT_MAILBOX_ROOT", str(Path.home() / ".agent-mailbox")))
BOXES = ("inbox", "in-progress", "done", "sent")
ONLINE_WINDOW_S = 180            # peer counts as "online" if seen within this
DEFAULT_MSG_MAX_AGE_H = 24       # inbox/in-progress messages older than this are pruned
DEFAULT_PEER_STALE_H = 6         # peer dirs whose heartbeat is older than this are removed
# Leases live in a dot-dir so all_agent_dirs() never mistakes one for an agent.
LEASES = ROOT / ".leases"
DEFAULT_LEASE_TTL_H = 8          # a lease expires on its own; nothing blocks forever
RESOURCE_SEPS = "/:#@"           # hierarchy boundaries: repo/path, repo:path, repo#PR

ADJ = ["brave", "calm", "swift", "clever", "quiet", "bright", "keen", "bold", "warm", "lucky"]
ANIMAL = ["fox", "owl", "otter", "wolf", "hawk", "lynx", "crane", "ibex", "raven", "seal"]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def session() -> str | None:
    """The stable identity key. Test override first, then the real Claude Code env."""
    return os.environ.get("AGENT_MAILBOX_SESSION") or os.environ.get("CLAUDE_CODE_SESSION_ID")


def agent_dir(name: str) -> Path:
    return ROOT / name


def ensure_agent(name: str) -> Path:
    d = agent_dir(name)
    for b in BOXES:
        (d / b).mkdir(parents=True, exist_ok=True)
    return d


def atomic_write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{uuid.uuid4().hex[:6]}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    os.replace(tmp, path)


def read_hb(name: str) -> dict:
    p = agent_dir(name) / "heartbeat.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def write_heartbeat(name: str, focus: str | None, session_id: str | None) -> dict:
    hb = read_hb(name)
    hb["name"] = name
    if session_id is not None:
        hb["session_id"] = session_id
    hb["last_seen"] = now_iso()
    if focus is not None:
        hb["focus"] = focus
    hb.setdefault("focus", "")
    atomic_write(agent_dir(name) / "heartbeat.json", hb)
    return hb


def friendly_name() -> str:
    for _ in range(50):
        n = f"{random.choice(ADJ)}-{random.choice(ANIMAL)}"
        if not agent_dir(n).exists():
            return n
    return f"agent-{uuid.uuid4().hex[:6]}"


def all_agent_dirs() -> list[Path]:
    if not ROOT.exists():
        return []
    return [d for d in ROOT.iterdir() if d.is_dir() and not d.name.startswith(".")]


def find_dir_by_session(sess: str | None) -> Path | None:
    if not sess:
        return None
    for d in all_agent_dirs():
        if read_hb(d.name).get("session_id") == sess:
            return d
    return None


def resolve_me(explicit: str | None) -> str | None:
    """Who am I? Explicit --as wins (override/testing); else resolve from session."""
    if explicit:
        return explicit
    d = find_dir_by_session(session())
    return d.name if d else None


NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def valid_name(n: str) -> str:
    """Agent names become directory names under ROOT and lines in hook output —
    reject anything that could escape ROOT or inject text (slashes, dots-only,
    newlines, absolute paths)."""
    if not NAME_RE.fullmatch(n):
        sys.exit(f"error: invalid agent name {n!r} — letters/digits/dot/dash/underscore, "
                 "max 64 chars, must start alphanumeric.")
    return n


def require_me(explicit: str | None) -> str:
    me = resolve_me(explicit)
    if not me:
        sys.exit("error: no mailbox identity for this session — run "
                 "`register --name <task-derived-name>` first (e.g. register --name trace-harvest).")
    return me


# ---- prune / retention -------------------------------------------------------

def prune_run(max_age_h: float, peer_stale_h: float, protect: str | None = None) -> dict:
    now = time.time()
    cleared_done = 0
    expired = 0
    dead: list[str] = []
    for d in all_agent_dirs():
        done = d / "done"
        if done.exists():
            for f in done.glob("*.json"):
                f.unlink(missing_ok=True); cleared_done += 1
        for box in ("inbox", "in-progress"):
            bx = d / box
            if not bx.exists():
                continue
            for f in bx.glob("*.json"):
                if now - f.stat().st_mtime > max_age_h * 3600:
                    f.unlink(missing_ok=True); expired += 1
        if d.name == protect:
            continue
        hb = d / "heartbeat.json"
        if not hb.exists():
            continue  # not a mailbox this tool created — NEVER delete foreign dirs
        age = now - hb.stat().st_mtime
        if age > peer_stale_h * 3600:
            # A stale peer is only removed once it holds no unhandled mail —
            # undelivered messages must never vanish because someone registered.
            has_mail = any(
                next((d / box).glob("*.json"), None) is not None
                for box in ("inbox", "in-progress")
            )
            if has_mail:
                continue
            shutil.rmtree(d, ignore_errors=True); dead.append(d.name)
    # Expired leases are garbage; an ORPHANED one is kept deliberately so the next agent
    # to touch that resource still sees who was on it and why before taking it over.
    expired_leases = []
    for rec in active_leases(include_dead=True):
        if rec["state"] == "expired":
            (LEASES / rec["_file"]).unlink(missing_ok=True)
            expired_leases.append(rec["resource"])
    # active_leases skips records it cannot parse, so they would otherwise survive every
    # prune forever while still occupying their filename. Collect them here instead.
    if LEASES.exists():
        for f in sorted(LEASES.glob("*.json")):
            try:
                rec = json.loads(f.read_text())
                if isinstance(rec, dict):
                    continue
            except Exception:
                pass
            f.unlink(missing_ok=True)
            expired_leases.append(f"<unreadable:{f.name}>")
    return {"cleared_done": cleared_done, "expired_messages": expired, "removed_dead_peers": dead,
"expired_leases": expired_leases}


# ---- peer listing ------------------------------------------------------------

def list_peers(include_offline: bool = False) -> list[dict]:
    now = time.time()
    leases = active_leases()
    peers = []
    for d in all_agent_dirs():
        hb_path = d / "heartbeat.json"
        hb = read_hb(d.name)
        online = hb_path.exists() and (now - hb_path.stat().st_mtime) <= ONLINE_WINDOW_S
        if not online and not include_offline:
            continue  # default roster = currently-live agents only; closed sessions drop off
        peers.append({
            "name": d.name,
            "focus": hb.get("focus", ""),
            "last_seen": hb.get("last_seen"),
            "online": online,
            "inbox": len(list((d / "inbox").glob("*.json"))) if (d / "inbox").exists() else 0,
            # what this peer is holding — so "who is on this repo?" is one command
            "holds": sorted(l["resource"] for l in leases if l["holder"] == d.name),
        })
    return sorted(peers, key=lambda p: p["name"])


def _render(paths) -> list[dict]:
    out = []
    for p in paths:
        try:
            m = json.loads(p.read_text()); m["_file"] = p.name; out.append(m)
        except Exception:
            continue
    return out


# ---- resource leases ---------------------------------------------------------
# A LEASE, not a lock: it expires, it never blocks a live agent forever, and a dead
# holder's lease can be taken. It exists so two agents don't quietly work the same
# repo/PR/path — the failure the message-level `claim` says nothing about.

def norm_resource(raw: str) -> str:
    r = " ".join(str(raw).split()).strip().strip("".join(RESOURCE_SEPS)).lower()
    if not r:
        raise SystemExit("lease: --resource must not be empty")
    return r


def _slug(resource: str) -> str:
    """Filename for a resource. The allowlist keeps every separator out, so a crafted
    resource can never escape the leases dir. Two resources differing only in separator
    (`repo/x` vs `repo_x`) do collide onto one file — the holder check still refuses the
    takeover, so the cost is a confusing refusal naming the other resource, not a stolen
    lane."""
    return re.sub(r"[^a-z0-9._-]+", "_", resource)[:120] or "_"


def conflicts(a: str, b: str) -> bool:
    """True when two resource ids overlap: identical, or one is an ancestor of the other
    at a separator boundary (`repo` covers `repo/pkg`, but not `repo-other`)."""
    if a == b:
        return True
    for x, y in ((a, b), (b, a)):
        if y.startswith(x) and len(y) > len(x) and y[len(x)] in RESOURCE_SEPS:
            return True
    return False


def _lease_state(rec: dict, now: float) -> str:
    """active | expired | orphaned — orphaned = TTL still valid but the holder is gone.

    Defensive on purpose: these records are written by peer processes, so a partial
    write, a truncated file, or a schema skew can leave one unreadable. A record whose
    expiry is missing or non-numeric counts as EXPIRED rather than raising — one corrupt
    file must not wedge every agent's lease commands, and `prune` has to be able to
    collect it. A holder carrying a path separator is refused the same way, so a bad
    record can never aim the heartbeat probe outside the mailbox root.
    """
    try:
        expires = float(rec.get("expires_epoch", 0))
    except (TypeError, ValueError):
        return "expired"
    if now >= expires:
        return "expired"
    holder = str(rec.get("holder", "") or "")
    if not holder or "/" in holder or "\\" in holder or holder in (".", ".."):
        return "orphaned"
    hb = agent_dir(holder) / "heartbeat.json"
    if not hb.exists() or (now - hb.stat().st_mtime) > DEFAULT_PEER_STALE_H * 3600:
        return "orphaned"
    return "active"


def active_leases(include_dead: bool = False) -> list[dict]:
    if not LEASES.exists():
        return []
    now = time.time()
    out = []
    for f in sorted(LEASES.glob("*.json")):
        try:
            rec = json.loads(f.read_text())
        except Exception:
            continue
        if not isinstance(rec, dict):
            continue                               # a parseable non-object is still garbage
        rec["state"] = _lease_state(rec, now)
        rec["_file"] = f.name
        if rec["state"] == "expired" and not include_dead:
            continue
        out.append(rec)
    return out


@contextmanager
def _leases_lock():
    """Serialize the whole scan-then-write critical section of an acquisition.

    O_EXCL makes a *single filename* atomic, which is enough when two agents race the
    identical resource — they collide on one file and exactly one wins. It is NOT enough
    for the hierarchy case the docs lead with: `repo` and `repo/pkg` conflict but slug to
    two different filenames, so both agents can scan, each see no conflict, and then write
    their own file. Both hold overlapping lanes. flock closes that gap by covering the scan
    and the write together, and the kernel drops it if the holder dies, so there is no stale
    lock to break.
    """
    if fcntl is None:                  # non-POSIX: behave exactly as before, no silent promise
        yield
        return
    LEASES.mkdir(parents=True, exist_ok=True)
    fd = os.open(LEASES / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _lease_acquire_locked(holder: str, resource: str, ttl_h: float, note: str, force: bool) -> dict:
    resource = norm_resource(resource)
    LEASES.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for held in active_leases():
        if not conflicts(resource, held["resource"]):
            continue
        if held["holder"] == holder:
            break                                  # renewing / narrowing your own hold
        if held["state"] == "active" and not force:
            return {"ok": False, "conflict": held,
                    "hint": f"held by '{held['holder']}' until {held.get('expires')} — "
                            f"message them, pick another lane, or --force if they are gone"}
        # expired/orphaned, or forced: the old record is stale, drop it
        (LEASES / held["_file"]).unlink(missing_ok=True)
    rec = {"resource": resource, "holder": holder, "note": note or "",
           "acquired": now_iso(), "ttl_h": ttl_h,
           "expires_epoch": now + ttl_h * 3600,
           "expires": datetime.fromtimestamp(now + ttl_h * 3600, timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = LEASES / f"{_slug(resource)}.json"
    try:
        # O_EXCL makes acquisition atomic against a peer racing us on the same resource.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        # An unreadable record is stale, not a claim: the O_EXCL branch writes content
        # non-atomically, so a killed process can leave a truncated file. Parsing it bare made
        # that resource permanently unleasable — prune could not collect it either, because it
        # was invisible to active_leases. Treat garbage as free and overwrite it.
        try:
            prev = json.loads(path.read_text()) if path.exists() else {}
            if not isinstance(prev, dict):
                prev = {}
        except Exception:
            prev = {}
        if prev.get("holder") not in (holder, None) and not force:
            prev["state"] = _lease_state(prev, now)
            if prev["state"] == "active":
                return {"ok": False, "conflict": prev, "hint": "lost the race to a peer"}
        atomic_write(path, rec)
        os.chmod(path, 0o600)                      # atomic_write uses the umask; match the O_EXCL path
        return {"ok": True, "lease": rec, "renewed": True}
    with os.fdopen(fd, "w") as fh:
        json.dump(rec, fh, indent=2, ensure_ascii=False)
    return {"ok": True, "lease": rec}


def lease_acquire(holder: str, resource: str, ttl_h: float, note: str, force: bool) -> dict:
    with _leases_lock():
        return _lease_acquire_locked(holder, resource, ttl_h, note, force)


def lease_release(holder: str, resource: str | None, force: bool) -> dict:
    dropped, skipped = [], []
    for held in active_leases(include_dead=True):
        if resource is not None and not conflicts(norm_resource(resource), held["resource"]):
            continue
        if held["holder"] != holder and not force:
            skipped.append(held["resource"]); continue
        (LEASES / held["_file"]).unlink(missing_ok=True)
        dropped.append(held["resource"])
    return {"released": dropped, "skipped_not_yours": skipped}


# ---- commands ----------------------------------------------------------------

def cmd_lease(a):
    me = require_me(a.sender)
    write_heartbeat(me, None, session())
    res = lease_acquire(me, a.resource, a.ttl_h, a.note, a.force)
    print(json.dumps({"agent": me, **res}, indent=2))
    return 0 if res.get("ok") else 1               # non-zero so a caller can branch on it


def cmd_release(a):
    me = require_me(a.sender)
    print(json.dumps({"agent": me, **lease_release(me, a.resource, a.force)}, indent=2))


def cmd_leases(a):
    print(json.dumps({"leases": active_leases(include_dead=a.all)}, indent=2))


def cmd_register(a):
    sess = session()
    if a.name:
        valid_name(a.name)
    # auto-prune so a fresh session starts clean; surface the result so any
    # removal is never invisible
    pruned = prune_run(a.max_age_h, a.peer_stale_h)
    me_dir = find_dir_by_session(sess)
    desired = a.name or (me_dir.name if me_dir else friendly_name())
    target, i = desired, 2
    while True:
        d = agent_dir(target)
        if not d.exists():
            break
        if sess and read_hb(target).get("session_id") == sess:
            break  # it's my own dir, reuse
        target = f"{desired}-{i}"; i += 1  # collision with a different session
    if me_dir and me_dir.name != target:
        os.replace(me_dir, agent_dir(target))  # honor a rename/override for my session
    ensure_agent(target)
    write_heartbeat(target, a.focus or "", sess)
    # Who is holding what, printed at the one moment every agent runs a command — so a new
    # session sees the occupied lanes before it starts working, not after it collides.
    held = [{"resource": l["resource"], "holder": l["holder"], "state": l["state"],
             "note": l.get("note", "")} for l in active_leases() if l["holder"] != target]
    print(json.dumps({"registered": target, "session_bound": bool(sess), "root": str(ROOT),
                      "pruned": pruned, "peer_leases": held,
                      "note": "Identity auto-resolves from your session now — no --as needed."
                              + (f" ⚠ {len(held)} resource(s) already leased by peers — see peer_leases"
                                 " and `lease` your own lane before you start." if held else ""),
                      }, indent=2))


def cmd_send(a):
    me = require_me(a.sender)
    valid_name(a.to)
    if not agent_dir(a.to).exists():
        sys.exit(f"error: peer '{a.to}' has no mailbox — run `peers` to see valid names.")
    ensure_agent(me)
    mid = uuid.uuid4().hex[:8]
    msg = {"id": mid, "from": me, "to": a.to, "ts": now_iso(), "subject": a.subject, "body": a.body}
    if a.reply_to:
        msg["reply_to"] = a.reply_to
    fname = f"{stamp()}-{mid}.json"
    atomic_write(agent_dir(a.to) / "inbox" / fname, msg)
    atomic_write(agent_dir(me) / "sent" / fname, msg)
    write_heartbeat(me, None, session())
    print(json.dumps({"id": mid, "from": me, "to": a.to}, indent=2))


def cmd_broadcast(a):
    me = require_me(a.sender)
    ensure_agent(me)
    sent, offline = [], []
    # Deliver to EVERY peer whose mailbox still exists, not just the currently-live ones.
    # Delivery is pull-based: an agent that is mid-turn (or parked) reads its inbox when it
    # next wakes, so filtering by the 180s online window silently dropped the whole point of
    # a broadcast -- with no live peers it reported success having written nothing. Retention
    # is `prune`'s job (peer dirs >6h, messages >24h), not the sender's.
    for p in list_peers(include_offline=True):
        if p["name"] == me:
            continue
        mid = uuid.uuid4().hex[:8]
        msg = {"id": mid, "from": me, "to": p["name"], "ts": now_iso(),
               "subject": a.subject, "body": a.body, "broadcast": True}
        fname = f"{stamp()}-{mid}.json"
        atomic_write(agent_dir(p["name"]) / "inbox" / fname, msg)
        atomic_write(agent_dir(me) / "sent" / fname, msg)
        sent.append(p["name"])
        if not p["online"]:
            offline.append(p["name"])
    write_heartbeat(me, None, session())
    # Split the roster so the sender knows who sees this now vs on their next wake-up.
    print(json.dumps({"from": me, "broadcast_to": sent,
                      "delivered_to_offline_peers": offline}, indent=2))


def cmd_check(a):
    me = require_me(a.sender)
    write_heartbeat(me, None, session())
    paths = sorted((agent_dir(me) / "inbox").glob("*.json")) if (agent_dir(me) / "inbox").exists() else []
    msgs = _render(paths)
    if a.claim:
        for p in paths:
            try:
                os.replace(p, agent_dir(me) / "in-progress" / p.name)
            except FileNotFoundError:
                continue  # raced with another mover/prune — already gone
    print(json.dumps({"agent": me, "new_messages": len(msgs),
                      "claimed": bool(a.claim and msgs), "messages": msgs}, indent=2))


def cmd_notify(a):
    """Used by the UserPromptSubmit hook. Silent unless THIS session has unread mail."""
    me = resolve_me(None)
    if not me:
        return  # session never registered — stay quiet
    write_heartbeat(me, None, session())  # keep this agent's heartbeat fresh each turn
    n = len(list((agent_dir(me) / "inbox").glob("*.json"))) if (agent_dir(me) / "inbox").exists() else 0
    if n:
        print(f"\U0001F4EC {n} new agent-mailbox message(s) waiting for '{me}' — "
              f"run the agent-mailbox `check` to read and reply.")


def msg_id_glob(raw: str) -> str:
    """Message ids are hex; reject glob/path metacharacters before interpolating."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        sys.exit(f"error: invalid message id {raw!r}.")
    return f"*{raw}*.json"


def cmd_claim(a):
    me = require_me(a.sender)
    moved = []
    for p in (agent_dir(me) / "inbox").glob(msg_id_glob(a.id)):
        try:
            os.replace(p, agent_dir(me) / "in-progress" / p.name); moved.append(p.name)
        except FileNotFoundError:
            continue
    print(json.dumps({"agent": me, "claimed": moved}, indent=2))


def cmd_archive(a):
    me = require_me(a.sender)
    moved = []
    for box in ("inbox", "in-progress"):
        for p in (agent_dir(me) / box).glob(msg_id_glob(a.id)):
            try:
                os.replace(p, agent_dir(me) / "done" / p.name); moved.append(p.name)
            except FileNotFoundError:
                continue
    print(json.dumps({"agent": me, "archived": moved}, indent=2))


def cmd_peers(a):
    shown = list_peers(include_offline=a.all)
    total = len(all_agent_dirs())
    out = {"peers": shown}
    if not a.all:
        hidden = total - len(shown)
        if hidden > 0:
            out["offline_hidden"] = hidden
            out["note"] = f"{hidden} offline/closed agent(s) hidden — use `peers --all` to see them."
    print(json.dumps(out, indent=2))


def cmd_heartbeat(a):
    me = require_me(a.sender)
    print(json.dumps(write_heartbeat(me, a.focus, session()), indent=2))


def cmd_prune(a):
    me = resolve_me(None)
    res = prune_run(a.max_age_h, a.peer_stale_h, protect=me)
    print(json.dumps(res, indent=2))


def _add_age_flags(p):
    p.add_argument("--max-age-h", dest="max_age_h", type=float, default=DEFAULT_MSG_MAX_AGE_H)
    p.add_argument("--peer-stale-h", dest="peer_stale_h", type=float, default=DEFAULT_PEER_STALE_H)


def main():
    p = argparse.ArgumentParser(description="File-based mailbox for Claude Code agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="bind this session to a display name + create mailbox")
    r.add_argument("--name", help="task-derived display name (auto-generated if omitted)")
    r.add_argument("--focus"); _add_age_flags(r); r.set_defaults(func=cmd_register)

    s = sub.add_parser("send", help="send a message to a peer's inbox")
    s.add_argument("--as", dest="sender", default=None, help="override identity (default: from session)")
    s.add_argument("--to", required=True); s.add_argument("--subject", required=True)
    s.add_argument("--body", required=True); s.add_argument("--reply-to", dest="reply_to")
    s.set_defaults(func=cmd_send)

    b = sub.add_parser("broadcast", help="send to every other live peer")
    b.add_argument("--as", dest="sender", default=None)
    b.add_argument("--subject", required=True); b.add_argument("--body", required=True)
    b.set_defaults(func=cmd_broadcast)

    c = sub.add_parser("check", help="list your inbox (use --claim to move to in-progress)")
    c.add_argument("--as", dest="sender", default=None); c.add_argument("--claim", action="store_true")
    c.set_defaults(func=cmd_check)

    n = sub.add_parser("notify", help="(hook) print a one-line count if this session has unread mail")
    n.set_defaults(func=cmd_notify)

    cl = sub.add_parser("claim", help="move a message inbox -> in-progress by id")
    cl.add_argument("--as", dest="sender", default=None); cl.add_argument("--id", required=True)
    cl.set_defaults(func=cmd_claim)

    ar = sub.add_parser("archive", help="move a message -> done by id")
    ar.add_argument("--as", dest="sender", default=None); ar.add_argument("--id", required=True)
    ar.set_defaults(func=cmd_archive)

    ls = sub.add_parser("lease", help="claim a RESOURCE (repo/PR/path) so peers don't work it too")
    ls.add_argument("--as", dest="sender", default=None)
    ls.add_argument("--resource", required=True,
                    help="what you're taking, e.g. 'my-repo', 'build#74', "
                         "'my-repo:services/payments' — parents cover children")
    ls.add_argument("--ttl-h", dest="ttl_h", type=float, default=DEFAULT_LEASE_TTL_H,
                    help=f"hours before it expires on its own (default {DEFAULT_LEASE_TTL_H})")
    ls.add_argument("--note", default="", help="what you're doing to it (shown to peers)")
    ls.add_argument("--force", action="store_true",
                    help="take it even if a live peer holds it — coordinate first")
    ls.set_defaults(func=cmd_lease)

    rl = sub.add_parser("release", help="drop your resource lease(s)")
    rl.add_argument("--as", dest="sender", default=None)
    rl.add_argument("--resource", default=None, help="omit to release everything you hold")
    rl.add_argument("--force", action="store_true", help="also release leases held by others")
    rl.set_defaults(func=cmd_release)

    lss = sub.add_parser("leases", help="who holds which resource right now")
    lss.add_argument("--all", action="store_true", help="include expired records")
    lss.set_defaults(func=cmd_leases)

    pe = sub.add_parser("peers", help="list LIVE agents (use --all to include offline/closed)")
    pe.add_argument("--all", action="store_true", help="include offline/closed agents")
    pe.set_defaults(func=cmd_peers)

    h = sub.add_parser("heartbeat", help="update your liveness + current focus")
    h.add_argument("--as", dest="sender", default=None); h.add_argument("--focus")
    h.set_defaults(func=cmd_heartbeat)

    pr = sub.add_parser("prune", help="clear done/, expire old messages, drop dead peers")
    _add_age_flags(pr); pr.set_defaults(func=cmd_prune)

    a = p.parse_args()
    # Propagate a command's exit code — `lease` returns non-zero on conflict so a caller
    # can branch on "someone else has it" instead of parsing the JSON.
    raise SystemExit(a.func(a) or 0)


if __name__ == "__main__":
    main()
