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
  <root>/<display-name>/expired/      aged-out mail (unread >1 week, claimed >--max-age-h); kept a week
  <root>/<display-name>/sent/         copies of what this agent sent
  <root>/<display-name>/heartbeat.json  {name, session_id, focus, last_seen,
                                         description?, repos?, owns?, human?}  (the card)

Message = JSON: {id, from, to, ts, subject, body, reply_to?, state?, state_note?, history?}
`state` uses A2A 1.0 TaskState names; a message without it reads its state from its folder.

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
DEFAULT_MSG_MAX_AGE_H = 24       # claimed messages older than this move to expired/
DEFAULT_PEER_STALE_H = 7 * 24    # peer dirs whose heartbeat is older than this are removed:
                                 # a week, so a manager idle over a weekend keeps its mailbox
LEASE_ORPHAN_H = 6               # a lease whose holder has been silent this long is takeable
# Leases live in a dot-dir so all_agent_dirs() never mistakes one for an agent. Resolved
# from ROOT at call time (not bound at import), so anything that re-points ROOT — tests
# included — can never read or prune the live ~/.agent-mailbox/.leases by accident.
def leases_dir() -> Path:
    return ROOT / ".leases"
def known_file() -> Path:
    return ROOT / ".known.json"
def groups_dir() -> Path:
    return ROOT / ".groups"
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


def write_heartbeat(name: str, focus: str | None, session_id: str | None,
                    card: dict | None = None) -> dict:
    """Refresh liveness. Also records the repo the cwd is in, so the card's `repos`
    follows the agent into new repos without anyone re-registering. `card` replaces the
    given card fields (description/owns/human; repos are added, not replaced)."""
    hb = read_hb(name)
    hb["name"] = name
    if session_id is not None:
        hb["session_id"] = session_id
    hb["last_seen"] = now_iso()
    if focus is not None:
        hb["focus"] = focus
    hb.setdefault("focus", "")
    repos = hb.get("repos") if isinstance(hb.get("repos"), list) else []
    here = detect_repo()
    for k, v in (card or {}).items():
        if k == "repos":
            repos = _merge(repos, v)
        elif k == "repos_replace":
            repos = list(v)[-CARD_LIST_MAX:]
        else:
            hb[k] = v
    if here:
        repos = _merge(repos, [here])
    if repos or "repos" in hb:
        hb["repos"] = repos
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
        return valid_name(explicit)                # --as becomes a dir name under ROOT
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


@contextmanager
def _agent_lock(name: str):
    """Serialize read-modify-write of one agent's files. The recipient updating a sent
    copy and the sender clearing its unseen flag are two processes touching one file."""
    if fcntl is None:
        yield
        return
    fd = os.open(agent_dir(name) / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---- message state (A2A 1.0 TaskState names) ----------------------------------
# Vocabulary only, not the A2A transport. A message without `state` (written before
# this field existed) takes its state from the folder it sits in.

STATES = ("submitted", "working", "input-required", "auth-required",
          "completed", "failed", "canceled", "rejected")
TERMINAL = ("completed", "failed", "canceled", "rejected")
NEEDS_SENDER = ("input-required", "auth-required", "rejected", "failed")
FOLDER_STATE = {"inbox": "submitted", "in-progress": "working", "done": "completed"}
SENT_SCAN_MAX_AGE_S = 7 * 86400   # notify only re-reads sent copies touched this week


def state_box(state: str) -> str:
    if state == "submitted":
        return "inbox"
    return "done" if state in TERMINAL else "in-progress"


def effective_state(msg: dict, box: str) -> str:
    """The folder wins when it disagrees: an older mailbox.py moves files without
    touching `state`, so a `submitted` message in in-progress/ is really working."""
    s = msg.get("state")
    if s in STATES and (box not in FOLDER_STATE or state_box(s) == box):
        return s
    return FOLDER_STATE.get(box, "submitted")


def clean_text(raw, limit: int) -> str:
    """Peer-written display text: one line, no control characters, bounded length."""
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", str(raw or "")).strip()
    return s[:limit]


def _mirror_to_sender(me: str, fname: str, msg: dict) -> bool:
    """Copy the new state onto the sender's sent/ copy so the sender sees it. The copy is
    only touched when it really is this message to me; `from` is peer-written data."""
    sender = str(msg.get("from", ""))
    if not NAME_RE.fullmatch(sender) or not re.fullmatch(r"[A-Za-z0-9._-]+\.json", fname):
        return False
    sent = agent_dir(sender) / "sent" / fname
    if not sent.is_file():
        return False                               # old sender, or its mailbox was pruned
    with _agent_lock(sender):
        try:
            copy = json.loads(sent.read_text())
        except Exception:
            return False
        if not isinstance(copy, dict) or copy.get("id") != msg.get("id") or copy.get("to") != me:
            return False
        for k in ("state", "state_note", "state_ts", "state_by", "history"):
            if k in msg:
                copy[k] = msg[k]
        copy["state_unseen"] = True
        atomic_write(sent, copy)
    return True


def set_state(me: str, path: Path, new: str, note: str = "") -> dict:
    """Move one of my messages to `new`, file it in the matching folder, and mirror the
    change to the sender. Leaving a terminal state is refused, as in A2A."""
    # My lock covers read/move/write so two parallel commands can't file one message in
    # two folders. The mirror runs after it is released: on a self-send the sender's lock
    # is this same file, and flock on a second fd would block on ourselves.
    with _agent_lock(me):
        if not path.exists():
            raise FileNotFoundError(path)          # moved by a parallel command meanwhile
        box = path.parent.name
        msg = json.loads(path.read_text())
        old = effective_state(msg, box)
        if old in TERMINAL and new != old:
            raise ValueError(f"message {msg.get('id')} is already {old}; terminal states are final")
        dest = agent_dir(me) / state_box(new) / path.name
        if dest != path:
            os.replace(path, dest)                 # move first: the folder alone stays truthful
        msg["state"] = new
        msg["state_ts"] = now_iso()
        msg["state_by"] = me
        msg["state_note"] = clean_text(note, 500)
        hist = msg.get("history") if isinstance(msg.get("history"), list) else []
        hist.append({"state": new, "ts": msg["state_ts"], "by": me, "note": msg["state_note"]})
        msg["history"] = hist[-20:]
        atomic_write(dest, msg)
    try:
        notified = _mirror_to_sender(me, path.name, msg)
    except OSError:
        notified = False                           # the local change stands either way
    return {"id": msg.get("id"), "from": old, "to": new, "box": dest.parent.name,
            "sender_notified": notified}


def sent_updates(me: str, needs_only: bool = False) -> list[tuple[Path, dict]]:
    out = []
    d = agent_dir(me) / "sent"
    if not d.is_dir():
        return out
    cutoff = time.time() - SENT_SCAN_MAX_AGE_S
    for p in sorted(d.glob("*.json")):
        try:
            if p.stat().st_mtime < cutoff:
                continue
            raw = p.read_text()
            if '"state_unseen": true' not in raw:      # cheap skip: notify runs every turn
                continue
            m = json.loads(raw)
        except Exception:
            continue
        if not isinstance(m, dict) or not m.get("state_unseen"):
            continue
        if needs_only and m.get("state") not in NEEDS_SENDER:
            continue
        out.append((p, m))
    return out


# ---- agent card (A2A Agent Card analogue) -------------------------------------
# Self-declared by each agent, so peers treat every field as display-only data.

CARD_LIST_MAX = 20


def split_list(raw) -> list[str]:
    items = []
    for chunk in (raw if isinstance(raw, list) else [raw or ""]):
        items += [clean_text(x, 120) for x in str(chunk).split(",")]
    return [x for x in items if x]


def _merge(old: list, new: list) -> list:
    out = [x for x in old if isinstance(x, str)]
    for x in new:
        if x not in out:
            out.append(x)
    return out[-CARD_LIST_MAX:]


def _repo_from_url(url: str) -> str | None:
    """`git@host:owner/name.git` or `https://host/owner/name` -> `owner/name`."""
    tail = re.split(r"[:/]", url.strip().rstrip("/"))
    tail = [t for t in tail if t]
    if len(tail) < 2:
        return None
    name = tail[-1][:-4] if tail[-1].endswith(".git") else tail[-1]
    return f"{tail[-2]}/{name}" if name else None


def detect_repo(start: Path | None = None) -> str | None:
    """The git repo the cwd is in: origin's owner/name, else the repo's folder path, else
    None outside git. Reads .git files directly so the per-turn hook spawns no process."""
    try:
        cur = (start or Path.cwd()).resolve()
    except OSError:
        return None
    for top in (cur, *cur.parents):
        dot = top / ".git"
        if not dot.exists():
            continue
        try:
            gitdir = dot
            if dot.is_file():                      # worktree or submodule: `gitdir: <path>`
                ref = dot.read_text().strip()
                if not ref.startswith("gitdir:"):
                    return str(top)
                gitdir = (top / ref[7:].strip()).resolve()
                common = gitdir / "commondir"
                if common.is_file():
                    gitdir = (gitdir / common.read_text().strip()).resolve()
            urls, remote = {}, None
            for line in (gitdir / "config").read_text().splitlines():
                line = line.strip()
                m = re.fullmatch(r'\[remote\s+"([^"]+)"\]', line)
                if m:
                    remote = m.group(1); continue
                if line.startswith("["):
                    remote = None; continue
                if remote and re.match(r"url\s*=", line):
                    urls.setdefault(remote, line.split("=", 1)[1].strip())
            url = urls.get("origin") or next(iter(urls.values()), None)
            return (url and _repo_from_url(url)) or str(top)
        except OSError:
            return str(top)
    return None


def card_of(hb: dict) -> dict:
    return {"description": clean_text(hb.get("description"), 200),
            "repos": [clean_text(x, 200) for x in hb.get("repos", []) if isinstance(x, str)]
                     if isinstance(hb.get("repos"), list) else [],
            "owns": [clean_text(x, 120) for x in hb.get("owns", []) if isinstance(x, str)]
                    if isinstance(hb.get("owns"), list) else [],
            "human": clean_text(hb.get("human"), 80)}


# ---- prune / retention -------------------------------------------------------

def _rmtree_if_still_empty(d: Path) -> bool:
    """Delete an idle mailbox unless a letter landed since the caller looked. Takes the
    delivery lock so a send cannot write between the re-check and the delete."""
    with _deliver_lock():
        if any(next((d / box).glob("*.json"), None) is not None
               for box in ("inbox", "in-progress", "expired")):
            return False
        shutil.rmtree(d, ignore_errors=True)
        return True


def prune_run(max_age_h: float, peer_stale_h: float, protect: str | None = None) -> dict:
    seed_known()
    now = time.time()
    cleared_done = 0
    expired = 0
    expired_sent = 0
    dead: list[str] = []
    for d in all_agent_dirs():
        done = d / "done"
        if done.exists():
            for f in done.glob("*.json"):
                f.unlink(missing_ok=True); cleared_done += 1
        # Nothing is deleted straight out of inbox/ or in-progress/: register auto-prunes,
        # so a restart after a crash used to delete every letter that waited it out at 24h.
        # Old mail moves to expired/ instead (unread after a week, claimed after
        # --max-age-h), and only expired/ copies older than another week are deleted.
        for box, limit_s in (("inbox", max(max_age_h * 3600, SENT_SCAN_MAX_AGE_S)),
                             ("in-progress", max_age_h * 3600)):
            bx = d / box
            if not bx.exists():
                continue
            for f in bx.glob("*.json"):
                try:                               # parallel registers / claims race here
                    if now - f.stat().st_mtime > limit_s:
                        (d / "expired").mkdir(exist_ok=True)
                        os.replace(f, d / "expired" / f.name)
                        os.utime(d / "expired" / f.name)   # its week in expired/ starts now
                        expired += 1
                except FileNotFoundError:
                    continue
        exp = d / "expired"
        if exp.exists():
            for f in exp.glob("*.json"):
                if now - f.stat().st_mtime > SENT_SCAN_MAX_AGE_S:
                    f.unlink(missing_ok=True)
        # sent/ copies only matter while notify still re-reads them (SENT_SCAN_MAX_AGE_S),
        # so anything older is dead weight; keep at least that long so a slow reply's
        # status update still lands on the sender's copy.
        sent = d / "sent"
        if sent.exists():
            sent_age_s = max(max_age_h * 3600, SENT_SCAN_MAX_AGE_S)
            for f in sent.glob("*.json"):
                if now - f.stat().st_mtime > sent_age_s:
                    f.unlink(missing_ok=True); expired_sent += 1
        if d.name == protect:
            continue
        hb = d / "heartbeat.json"
        if not hb.exists():
            # A placeholder whose queued mail is gone (aged out) and whose name never came
            # back is removed like a dead peer; anything else without a heartbeat is not a
            # mailbox this tool created — NEVER delete foreign dirs.
            ph = d / PLACEHOLDER
            if ph.exists() and now - ph.stat().st_mtime > peer_stale_h * 3600 and not any(
                    next((d / box).glob("*.json"), None) is not None
                    for box in ("inbox", "in-progress", "expired")):
                if _rmtree_if_still_empty(d):
                    dead.append(d.name)
            continue
        age = now - hb.stat().st_mtime
        if age > peer_stale_h * 3600:
            # A stale peer is only removed once it holds no unhandled mail —
            # undelivered messages must never vanish because someone registered.
            has_mail = any(
                next((d / box).glob("*.json"), None) is not None
                for box in ("inbox", "in-progress", "expired")   # expired/ is kept its week too
            )
            if has_mail:
                continue
            # A name leaves the registry 30 days after its last register; a manager that
            # registered once and then idled must stay known once its folder is pruned.
            # A pruned placeholder is not refreshed, so a name nobody uses still ages out.
            remember_name(d.name)
            if _rmtree_if_still_empty(d):
                dead.append(d.name)
    # Expired leases are garbage; an ORPHANED one is kept deliberately so the next agent
    # to touch that resource still sees who was on it and why before taking it over.
    expired_leases = []
    for rec in active_leases(include_dead=True):
        if rec["state"] == "expired":
            (leases_dir() / rec["_file"]).unlink(missing_ok=True)
            expired_leases.append(rec["resource"])
    # active_leases skips records it cannot parse, so they would otherwise survive every
    # prune forever while still occupying their filename. Collect them here instead.
    if leases_dir().exists():
        for f in sorted(leases_dir().glob("*.json")):
            try:
                rec = json.loads(f.read_text())
                if isinstance(rec, dict):
                    continue
            except Exception:
                pass
            f.unlink(missing_ok=True)
            expired_leases.append(f"<unreadable:{f.name}>")
    return {"cleared_done": cleared_done, "expired_messages": expired, "expired_sent": expired_sent,
            "removed_dead_peers": dead,
"expired_leases": expired_leases}


# ---- peer listing ------------------------------------------------------------

def works_in(peer: dict, repo: str) -> bool:
    """`--repo` match: a card repo equal to it (or with the same final name), or a lease
    on it. `agent-toolkit` finds `example-org/agent-toolkit`."""
    want = repo.strip().strip("/").lower()
    if not want:
        return False
    short = want.rsplit("/", 1)[-1]
    for r in peer.get("repos", []):
        r = r.lower().rstrip("/")
        if r == want or r.rsplit("/", 1)[-1] == short:
            return True
    return any(conflicts(short, h) or conflicts(want, h) for h in peer.get("holds", []))


def list_peers(include_offline: bool = False, verbose: bool = False) -> list[dict]:
    now = time.time()
    leases = active_leases()
    peers = []
    for d in all_agent_dirs():
        hb_path = d / "heartbeat.json"
        hb = read_hb(d.name)
        online = hb_path.exists() and (now - hb_path.stat().st_mtime) <= ONLINE_WINDOW_S
        if not online and not include_offline:
            continue  # default roster = currently-live agents only; closed sessions drop off
        card = card_of(hb)
        peer = {
            "name": d.name,
            "focus": hb.get("focus", ""),
            "description": card["description"],
            "repos": card["repos"],
            "last_seen": hb.get("last_seen"),
            "online": online,
            "inbox": len(list((d / "inbox").glob("*.json"))) if (d / "inbox").exists() else 0,
            # what this peer is holding — so "who is on this repo?" is one command
            "holds": sorted(l["resource"] for l in leases if l["holder"] == d.name),
        }
        if is_placeholder(d):
            peer["queued_only"] = True             # pruned name holding queued letters
        if verbose or include_offline:
            peer["owns"] = card["owns"]
            peer["human"] = card["human"]
        peers.append(peer)
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
    if not hb.exists() or (now - hb.stat().st_mtime) > LEASE_ORPHAN_H * 3600:
        return "orphaned"
    return "active"


def active_leases(include_dead: bool = False) -> list[dict]:
    if not leases_dir().exists():
        return []
    now = time.time()
    out = []
    for f in sorted(leases_dir().glob("*.json")):
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
    leases_dir().mkdir(parents=True, exist_ok=True)
    fd = os.open(leases_dir() / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
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
    leases_dir().mkdir(parents=True, exist_ok=True)
    now = time.time()
    for held in active_leases():
        if not conflicts(resource, held["resource"]):
            continue
        if held["holder"] == holder:
            continue                               # renewing / narrowing your own hold: not a
                                                   # conflict, but keep checking the OTHER leases
        if held["state"] == "active" and not force:
            return {"ok": False, "conflict": held,
                    "hint": f"held by '{held['holder']}' until {held.get('expires')} — "
                            f"message them, pick another lane, or --force if they are gone"}
        # expired/orphaned, or forced: the old record is stale, drop it
        (leases_dir() / held["_file"]).unlink(missing_ok=True)
    rec = {"resource": resource, "holder": holder, "note": note or "",
           "acquired": now_iso(), "ttl_h": ttl_h,
           "expires_epoch": now + ttl_h * 3600,
           "expires": datetime.fromtimestamp(now + ttl_h * 3600, timezone.utc)
                              .strftime("%Y-%m-%dT%H:%M:%SZ")}
    path = leases_dir() / f"{_slug(resource)}.json"
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
        (leases_dir() / held["_file"]).unlink(missing_ok=True)
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


def card_args(a, replace_repos: bool = False) -> dict:
    """Card fields given on the command line. Only flags actually passed are returned,
    so `card --owns x` leaves the description alone."""
    card = {}
    if getattr(a, "description", None) is not None:
        card["description"] = clean_text(a.description, 200)
    if getattr(a, "owns", None) is not None:
        card["owns"] = split_list(a.owns)[:CARD_LIST_MAX]
    if getattr(a, "human", None) is not None:
        card["human"] = clean_text(a.human, 80)
    if getattr(a, "repos", None) is not None:
        card["repos_replace" if replace_repos else "repos"] = split_list(a.repos)
    return card


def cmd_card(a):
    me = require_me(a.sender)
    hb = write_heartbeat(me, None, session(), card=card_args(a, replace_repos=True))
    print(json.dumps({"agent": me, "card": card_of(hb)}, indent=2))


KNOWN_MAX_AGE_S = 30 * 86400    # a name silent this long is forgotten (typo guard stays tight)


def known_names() -> dict:
    """Names that have registered here before (name -> last register time). A letter to
    one of them is queued even when its mailbox was pruned; an unknown name is a typo.
    Seeded from the live mailboxes the first time, so an upgrade forgets nobody."""
    try:
        rec = json.loads(known_file().read_text())
        return rec if isinstance(rec, dict) else {}
    except FileNotFoundError:
        return {d.name: now_iso() for d in all_agent_dirs() if (d / "heartbeat.json").exists()}
    except Exception:
        return {}


@contextmanager
def _known_lock():
    if fcntl is None:
        yield
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    fd = os.open(ROOT / ".known.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def seed_known() -> None:
    """Write the registry from the live mailboxes when it does not exist yet. Prune runs
    this first: it deletes idle mailboxes, and a name it deletes before the registry exists
    would never be seeded, so a later letter to it would be refused as a typo."""
    with _known_lock():
        if not known_file().exists():
            atomic_write(known_file(), {d.name: now_iso() for d in all_agent_dirs()
                                        if (d / "heartbeat.json").exists()})


@contextmanager
def _deliver_lock():
    """One lock for delivering a letter and for a rename taking over a placeholder
    mailbox. It lives in ROOT, outside every mailbox directory a rename can replace."""
    if fcntl is None:
        yield
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    fd = os.open(ROOT / ".deliver.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def remember_name(name: str) -> None:
    with _known_lock():
        rec = known_names()
        rec[name] = now_iso()
        cutoff = time.time() - KNOWN_MAX_AGE_S
        def fresh(ts):
            try:
                return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp() > cutoff
            except (TypeError, ValueError):
                return False
        atomic_write(known_file(), {k: v for k, v in rec.items() if fresh(v)})


PLACEHOLDER = "placeholder.json"


def is_placeholder(d: Path) -> bool:
    """A mailbox `send` made to queue letters for a pruned name: no heartbeat, a marker."""
    return (d / PLACEHOLDER).exists() and not (d / "heartbeat.json").exists()


def group_members_quiet() -> set[str]:
    """Every member of every readable group; a broken group file is skipped, not fatal."""
    out: set[str] = set()
    for f in groups_dir().glob("*.json") if groups_dir().is_dir() else []:
        try:
            rec = json.loads(f.read_text())
            members = rec.get("members", []) if isinstance(rec, dict) else []
            out.update(m for m in members if isinstance(m, str) and NAME_RE.fullmatch(m))
        except Exception:
            continue
    return out


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
        if is_placeholder(d):
            break  # letters queued for this name while it was pruned: take them
        target = f"{desired}-{i}"; i += 1  # collision with a different session
    if me_dir and me_dir.name != target:
        with _deliver_lock():                      # a send cannot land between the check and the delete
            dest = agent_dir(target)
            if dest.exists() and not is_placeholder(dest):
                sys.exit(f"error: {target} became a live mailbox while renaming; "
                         "nothing was moved. Run register again.")
            if dest.exists():                          # a placeholder: fold its queued mail into
                for box in (*BOXES, "expired"):        # my dir, then move my dir onto the name
                    for f in (dest / box).glob("*.json") if (dest / box).is_dir() else []:
                        (me_dir / box).mkdir(parents=True, exist_ok=True)
                        os.replace(f, me_dir / box / f.name)
                leftover = [f for f in dest.rglob("*.json") if f.name != PLACEHOLDER]
                if leftover:                           # never delete mail we failed to move
                    sys.exit(f"error: could not take over {target}'s queued mail; nothing deleted.")
                shutil.rmtree(dest, ignore_errors=True)
            os.replace(me_dir, dest)                   # honor a rename/override for my session
    ensure_agent(target)
    (agent_dir(target) / PLACEHOLDER).unlink(missing_ok=True)
    remember_name(target)
    hb = write_heartbeat(target, a.focus or "", sess, card=card_args(a))
    # Who is holding what, printed at the one moment every agent runs a command — so a new
    # session sees the occupied lanes before it starts working, not after it collides.
    held = [{"resource": l["resource"], "holder": l["holder"], "state": l["state"],
             "note": l.get("note", "")} for l in active_leases() if l["holder"] != target]
    print(json.dumps({"registered": target, "session_bound": bool(sess), "root": str(ROOT),
                      "card": card_of(hb), "pruned": pruned, "peer_leases": held,
                      "note": "Identity auto-resolves from your session now — no --as needed."
                              + (f" ⚠ {len(held)} resource(s) already leased by peers — see peer_leases"
                                 " and `lease` your own lane before you start." if held else ""),
                      }, indent=2))


def read_group(name: str) -> list[str]:
    f = groups_dir() / f"{valid_name(name)}.json"
    try:
        rec = json.loads(f.read_text())
    except FileNotFoundError:
        sys.exit(f"error: no group '{name}' — `group list` shows the groups, "
                 f"`group set --name {name} --members a,b` makes one.")
    except Exception:
        sys.exit(f"error: group '{name}' is unreadable; set it again with `group set`.")
    members = rec.get("members") if isinstance(rec, dict) else None
    return [m for m in members if isinstance(m, str) and NAME_RE.fullmatch(m)] if isinstance(members, list) else []


def resolve_recipients(raw: list[str], me: str) -> list[str]:
    """`--to a,b,@managers` -> unique mailbox names, in order. `@x` expands group x.
    The sender is dropped from a group (sending yourself the team letter is noise), but
    kept when named directly."""
    out: list[str] = []
    for item in split_list(raw):
        names = [m for m in read_group(item[1:]) if m != me] if item.startswith("@") else [valid_name(item)]
        for n in names:
            if n not in out:
                out.append(n)
    return out


def _mark_replied(me: str, peer: str, orig_id: str, reply_id: str) -> None:
    """Note on `peer`'s sent/ copy of `orig_id` that I answered it. `status` reads this,
    so an answer still counts after the reply is archived and done/ is pruned."""
    d = agent_dir(peer) / "sent"
    if not d.is_dir():
        return
    with _agent_lock(peer):
        for f in d.glob(msg_id_glob(orig_id)):
            try:
                copy = json.loads(f.read_text())
            except Exception:
                continue
            if not isinstance(copy, dict) or copy.get("id") != orig_id or copy.get("to") != me:
                continue
            ids = copy.get("reply_ids") if isinstance(copy.get("reply_ids"), list) else []
            copy["reply_ids"] = (ids + [reply_id])[-20:]
            atomic_write(f, copy)


def doorbell(mid: str, me: str, subject: str) -> str:
    return f"mailbox: new msg {mid} from {me} ({clean_text(subject, 60)}) — run check --claim"


def cmd_send(a):
    me = require_me(a.sender)
    to = resolve_recipients(a.to, me)
    if a.reply_to and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", a.reply_to):
        sys.exit(f"error: invalid --reply-to {a.reply_to!r}; pass the message id you are answering.")
    if not to:
        sys.exit("error: --to named nobody (an empty group?).")
    with _deliver_lock():                          # held until every copy is written
        # Check every recipient before writing anything: a partial send is harder to fix
        # than a refused one, and the sender can just drop the bad name and rerun.
        missing = [n for n in to if not (agent_dir(n) / "inbox").is_dir()]
        # A name that registered here before (or sits in a group) only lost its folder to
        # prune: queue the letter for it. A name never seen is a typo: refuse, send nothing.
        known = (set(known_names()) | group_members_quiet()) if missing else set()
        unknown = [n for n in missing if n not in known]
        if unknown:
            sys.exit(f"error: no mailbox for {', '.join(unknown)} — nothing was sent. "
                     "Run `peers --all` to see valid names.")
        for n in missing:
            ensure_agent(n)                            # placeholder; register takes it over
            atomic_write(agent_dir(n) / PLACEHOLDER, {"name": n, "created": now_iso(), "by": me})
        ensure_agent(me)
        thread = uuid.uuid4().hex[:8] if len(to) > 1 else None
        now = time.time()
        sent = []
        for n in to:
            mid = uuid.uuid4().hex[:8]
            msg = {"id": mid, "from": me, "to": n, "ts": now_iso(), "subject": a.subject, "body": a.body,
                   "state": "submitted"}
            if thread:
                msg["thread"] = thread
                msg["to_all"] = to
            if a.reply_to:
                msg["reply_to"] = a.reply_to
            fname = f"{stamp()}-{mid}.json"
            atomic_write(agent_dir(n) / "inbox" / fname, msg)
            atomic_write(agent_dir(me) / "sent" / fname, msg)
            if a.reply_to:
                try:
                    _mark_replied(me, n, a.reply_to, mid)
                except OSError:
                    pass                               # the letter itself is delivered either way
            hb = agent_dir(n) / "heartbeat.json"
            online = hb.exists() and (now - hb.stat().st_mtime) <= ONLINE_WINDOW_S
            row = {"to": n, "id": mid, "online": online, "doorbell": doorbell(mid, me, a.subject)}
            if n in missing:
                row["queued"] = "not registered right now; the letter waits for it to register again"
            sent.append(row)
    write_heartbeat(me, None, session())
    if len(sent) == 1:                             # unchanged shape for the one-recipient case
        out = {"id": sent[0]["id"], "from": me, "to": to[0], "online": sent[0]["online"],
               "doorbell": sent[0]["doorbell"]}
        if "queued" in sent[0]:
            out["queued"] = sent[0]["queued"]
    else:
        out = {"thread": thread, "from": me, "sent": sent,
               "note": f"`status --id {thread}` shows who has answered."}
    print(json.dumps(out, indent=2))


def cmd_broadcast(a):
    me = require_me(a.sender)
    ensure_agent(me)
    sent, offline = [], []
    # Deliver to EVERY peer whose mailbox still exists, not just the currently-live ones.
    # Delivery is pull-based: an agent that is mid-turn (or parked) reads its inbox when it
    # next wakes, so filtering by the 180s online window silently dropped the whole point of
    # a broadcast -- with no live peers it reported success having written nothing. Retention
    # is `prune`'s job (peer dirs >7 days idle, old mail into expired/), not the sender's.
    skipped = []
    for p in list_peers(include_offline=True):
        if p["name"] == me:
            continue
        if not (agent_dir(p["name"]) / "inbox").is_dir() or is_placeholder(agent_dir(p["name"])):
            skipped.append(p["name"])              # a stray dir under ROOT, not a mailbox:
            continue                               # skip it rather than abort the whole send
        mid = uuid.uuid4().hex[:8]
        msg = {"id": mid, "from": me, "to": p["name"], "ts": now_iso(),
               "subject": a.subject, "body": a.body, "broadcast": True, "state": "submitted"}
        fname = f"{stamp()}-{mid}.json"
        atomic_write(agent_dir(p["name"]) / "inbox" / fname, msg)
        atomic_write(agent_dir(me) / "sent" / fname, msg)
        sent.append(p["name"])
        if not p["online"]:
            offline.append(p["name"])
    write_heartbeat(me, None, session())
    # Split the roster so the sender knows who sees this now vs on their next wake-up.
    out = {"from": me, "broadcast_to": sent, "delivered_to_offline_peers": offline}
    if skipped:
        out["skipped_not_a_mailbox"] = skipped
    print(json.dumps(out, indent=2))


def cmd_check(a):
    me = require_me(a.sender)
    write_heartbeat(me, None, session())
    box = "expired" if a.expired else "inbox"
    paths = sorted((agent_dir(me) / box).glob("*.json")) if (agent_dir(me) / box).exists() else []
    msgs = _render(paths)
    for m in msgs:
        m["state"] = effective_state(m, box)
    if a.claim:
        by_file = {m["_file"]: m for m in msgs}
        for p in paths:                            # every path, parseable or not
            try:
                set_state(me, p, "working")
                if p.name in by_file:
                    by_file[p.name]["state"] = "working"
            except FileNotFoundError:
                continue  # raced with another mover/prune — already gone
            except (ValueError, json.JSONDecodeError):
                try:                               # unreadable: still claim it, as before
                    os.replace(p, agent_dir(me) / "in-progress" / p.name)
                except FileNotFoundError:
                    pass
    out = {"agent": me, "new_messages": len(msgs),
           "claimed": bool(a.claim and msgs), "messages": msgs}
    # State changes peers made to mail I sent. Shown once, then marked seen.
    updates = []
    with _agent_lock(me):
        for p, m in sent_updates(me):
            updates.append({"id": m.get("id"), "to": m.get("to"), "subject": m.get("subject"),
                            "state": m.get("state"), "note": m.get("state_note", ""),
                            "ts": m.get("state_ts"), "needs_you": m.get("state") in NEEDS_SENDER})
            m["state_unseen"] = False
            atomic_write(p, m)
    if updates:
        out["sent_updates"] = updates
    exp = agent_dir(me) / "expired"
    n_exp = len(list(exp.glob("*.json"))) if exp.is_dir() else 0
    if n_exp:
        out["expired_kept"] = n_exp
    if a.text:
        print(render_text(out))
    else:
        print(json.dumps(out, indent=2))


def render_text(out: dict) -> str:
    """Plain view of `check`, so reading mail needs no JSON post-processing. Peer text
    is printed as data under each header, never as a header of its own."""
    lines = [f"{out['agent']}: {out['new_messages']} new" + (" (claimed)" if out.get("claimed") else "")]
    for m in out["messages"]:
        extra = f" · thread {clean_text(m['thread'], 64)}" if m.get("thread") else ""
        extra += f" · reply to {clean_text(m['reply_to'], 64)}" if m.get("reply_to") else ""
        lines.append(f"\n--- {clean_text(m.get('id'), 64)} · from {clean_text(m.get('from'), 64)}"
                     f" · {clean_text(m.get('ts'), 64)}{extra}")
        lines.append(f"subject: {clean_text(m.get('subject'), 300)}")
        body = [re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", ln) for ln in str(m.get("body", "")).splitlines()]
        lines += ["  " + ln for ln in body] or ["  (empty)"]
    for u in out.get("sent_updates", []):
        flag = "  ← needs you" if u["needs_you"] else ""
        note = f": {u['note']}" if u.get("note") else ""
        lines.append(f"\nupdate · {u['id']} to {u['to']} is {u['state']}{note}{flag}")
    if out.get("expired_kept"):
        lines.append(f"\n{out['expired_kept']} old letter(s) in expired/ (kept a week): "
                     "`check --expired` lists them, `claim --id` takes one back.")
    return "\n".join(lines)


def cmd_status(a):
    """Who answered a letter I sent. `--id` is a thread id (multi-recipient send) or one
    message id. A reply counts when a letter in my mailbox is from that recipient and carries
    reply_to = the id they got; the same id quoted by anyone else answers nothing."""
    me = require_me(a.sender)
    pattern_ok = re.fullmatch(r"[A-Za-z0-9._-]+", a.id)
    if not pattern_ok:
        sys.exit(f"error: invalid id {a.id!r}.")
    copies = [m for m in _render(sorted((agent_dir(me) / "sent").glob("*.json")))
              if a.id in (m.get("id"), m.get("thread"))]
    if not copies:
        sys.exit(f"error: nothing sent with id or thread {a.id!r} (sent/ copies last a week).")
    order = copies[0].get("to_all") if isinstance(copies[0].get("to_all"), list) else []
    copies.sort(key=lambda c: order.index(c.get("to")) if c.get("to") in order else len(order))
    replies: dict[str, list] = {}
    for box in ("inbox", "in-progress", "done", "expired"):
        for m in _render(sorted((agent_dir(me) / box).glob("*.json"))):
            if isinstance(m.get("reply_to"), str) and m["reply_to"]:
                sender = m["from"] if isinstance(m.get("from"), str) else ""
                replies.setdefault((m["reply_to"], sender), []).append(
                    {"id": m.get("id"), "subject": clean_text(m.get("subject"), 120), "box": box})
    rows = []
    for c in copies:
        got = replies.get((c.get("id"), c.get("to")), [])
        seen = {r["id"] for r in got}
        for rid in c.get("reply_ids") if isinstance(c.get("reply_ids"), list) else []:
            if isinstance(rid, str) and rid not in seen:   # reply archived and pruned since
                got.append({"id": rid, "subject": "", "box": "gone"}); seen.add(rid)
        state = c.get("state", "submitted")
        rows.append({"to": c.get("to"), "id": c.get("id"), "state": state,
                     "note": clean_text(c.get("state_note"), 200), "replied": bool(got), "replies": got})
    # answered = sent a reply letter; resolved = closed it with archive/state but no reply
    # letter (archive --note is the documented way to answer briefly); waiting = neither.
    print(json.dumps({"agent": me, "id": a.id, "subject": copies[0].get("subject"),
                      "answered": [r["to"] for r in rows if r["replied"]],
                      "resolved": [r["to"] for r in rows if not r["replied"] and r["state"] in TERMINAL],
                      "waiting": [r["to"] for r in rows if not r["replied"] and r["state"] not in TERMINAL],
                      "recipients": rows}, indent=2))


def cmd_group(a):
    groups_dir().mkdir(parents=True, exist_ok=True)
    if a.action == "list":
        out = {}
        for f in sorted(groups_dir().glob("*.json")):
            try:
                rec = json.loads(f.read_text())
                out[f.stem] = rec.get("members", []) if isinstance(rec, dict) else []
            except Exception:
                out[f.stem] = "<unreadable>"
        print(json.dumps({"groups": out}, indent=2))
        return 0
    if not a.name:
        sys.exit(f"error: `group {a.action}` needs --name.")
    f = groups_dir() / f"{valid_name(a.name)}.json"
    if a.action == "rm":
        f.unlink(missing_ok=True)
        print(json.dumps({"removed": a.name}, indent=2))
        return 0
    members = [valid_name(m) for m in split_list(a.members or "")]
    if not members:
        sys.exit("error: `group set` needs --members a,b,c.")
    missing = [m for m in members if not agent_dir(m).is_dir()]
    atomic_write(f, {"name": a.name, "members": members, "updated": now_iso()})
    out = {"group": a.name, "members": members}
    if missing:
        out["warning"] = f"no mailbox yet for {', '.join(missing)}; `send` to the group fails until they register"
    print(json.dumps(out, indent=2))
    return 0


def cmd_notify(a):
    """Used by the UserPromptSubmit hook. Silent unless THIS session has unread mail."""
    me = resolve_me(None)
    if not me:
        return  # session never registered — stay quiet
    write_heartbeat(me, None, session())  # keep this agent's heartbeat fresh each turn
    n = len(list((agent_dir(me) / "inbox").glob("*.json"))) if (agent_dir(me) / "inbox").exists() else 0
    needs: dict[str, int] = {}
    for _, m in sent_updates(me, needs_only=True):
        needs[m["state"]] = needs.get(m["state"], 0) + 1
    parts = []
    if n:
        parts.append(f"{n} new agent-mailbox message(s) waiting for '{me}'")
    if needs:
        parts.append("peers updated mail you sent: "
                     + ", ".join(f"{c} {s}" for s, c in sorted(needs.items())))
    if parts:
        print("\U0001F4EC " + " · ".join(parts) + " — run the agent-mailbox `check` to read and reply.")


def msg_id_glob(raw: str) -> str:
    """Message ids are hex; reject glob/path metacharacters before interpolating."""
    if not re.fullmatch(r"[A-Za-z0-9._-]+", raw):
        sys.exit(f"error: invalid message id {raw!r}.")
    return f"*{raw}*.json"


def _transition(me: str, raw_id: str, new: str, note: str, boxes) -> tuple[list, list]:
    done, errors = [], []
    # Collect every match before moving anything: set_state files the message into the
    # next folder, and a lazy glob over the later boxes would find it again and
    # transition it a second time (double history row, double sender notification).
    pattern = msg_id_glob(raw_id)
    found = [p for box in boxes for p in (agent_dir(me) / box).glob(pattern)]
    for p in found:
        try:
            done.append({"file": p.name, **set_state(me, p, new, note)})
        except FileNotFoundError:
            continue                               # raced with another mover/prune
        except (ValueError, json.JSONDecodeError) as e:
            errors.append(f"{p.name}: {e}")
    return done, errors


def cmd_claim(a):
    me = require_me(a.sender)
    done, _ = _transition(me, a.id, "working", "", ("inbox", "expired"))
    print(json.dumps({"agent": me, "claimed": [d["file"] for d in done]}, indent=2))


def cmd_archive(a):
    me = require_me(a.sender)
    done, errors = _transition(me, a.id, a.state, a.note, ("inbox", "in-progress"))
    out = {"agent": me, "archived": [d["file"] for d in done], "state": a.state}
    if errors:
        out["errors"] = errors
    print(json.dumps(out, indent=2))
    return 1 if errors and not done else 0


def cmd_state(a):
    me = require_me(a.sender)
    if a.set == "submitted":
        sys.exit("error: `submitted` is the state `send` sets; it can't be set again.")
    done, errors = _transition(me, a.id, a.set, a.note, ("inbox", "in-progress", "done"))
    out = {"agent": me, "updated": done}
    if errors:
        out["errors"] = errors
    if not done and not errors:
        out["errors"] = [f"no message with id {a.id!r} in your mailbox"]
    print(json.dumps(out, indent=2))
    return 0 if done else 1


def cmd_peers(a):
    shown = list_peers(include_offline=a.all, verbose=a.verbose)
    total = len(all_agent_dirs())
    if a.repo:
        shown = [p for p in shown if works_in(p, a.repo)]
    out = {"peers": shown}
    if a.repo:
        out["repo"] = a.repo
    elif not a.all:
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


def _add_card_flags(p):
    p.add_argument("--description", help="one line on what this agent is about")
    p.add_argument("--repos", help="comma list of repos (the cwd repo is detected for you)")
    p.add_argument("--owns", help="comma list of what this agent is responsible for")
    p.add_argument("--human", help="who this agent works for")


def _add_age_flags(p):
    p.add_argument("--max-age-h", dest="max_age_h", type=float, default=DEFAULT_MSG_MAX_AGE_H)
    p.add_argument("--peer-stale-h", dest="peer_stale_h", type=float, default=DEFAULT_PEER_STALE_H)


def main():
    p = argparse.ArgumentParser(description="File-based mailbox for Claude Code agents.")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("register", help="bind this session to a display name + create mailbox")
    r.add_argument("--name", help="task-derived display name (auto-generated if omitted)")
    r.add_argument("--focus"); _add_card_flags(r); _add_age_flags(r); r.set_defaults(func=cmd_register)

    s = sub.add_parser("send", help="send one letter to one or more peers (or a @group)")
    s.add_argument("--as", dest="sender", default=None, help="override identity (default: from session)")
    s.add_argument("--to", required=True, action="append",
                   help="recipient(s): a name, a comma list, or @group; repeatable")
    s.add_argument("--subject", required=True)
    s.add_argument("--body", required=True); s.add_argument("--reply-to", dest="reply_to")
    s.set_defaults(func=cmd_send)

    b = sub.add_parser("broadcast", help="send to every other live peer")
    b.add_argument("--as", dest="sender", default=None)
    b.add_argument("--subject", required=True); b.add_argument("--body", required=True)
    b.set_defaults(func=cmd_broadcast)

    c = sub.add_parser("check", help="list your inbox (use --claim to move to in-progress)")
    c.add_argument("--as", dest="sender", default=None); c.add_argument("--claim", action="store_true")
    c.add_argument("--text", action="store_true", help="plain id/from/subject/body view, not JSON")
    c.add_argument("--expired", action="store_true", help="list expired/ instead of inbox/")
    c.set_defaults(func=cmd_check)

    so = sub.add_parser("status", help="who has answered a letter you sent (thread or message id)")
    so.add_argument("--as", dest="sender", default=None); so.add_argument("--id", required=True)
    so.set_defaults(func=cmd_status)

    g = sub.add_parser("group", help="named recipient lists for `send --to @name`")
    g.add_argument("action", choices=("list", "set", "rm"))
    g.add_argument("--name"); g.add_argument("--members", help="comma list of mailbox names")
    g.set_defaults(func=cmd_group)

    n = sub.add_parser("notify", help="(hook) print a one-line count if this session has unread mail")
    n.set_defaults(func=cmd_notify)

    cl = sub.add_parser("claim", help="move a message inbox (or expired) -> in-progress by id")
    cl.add_argument("--as", dest="sender", default=None); cl.add_argument("--id", required=True)
    cl.set_defaults(func=cmd_claim)

    ar = sub.add_parser("archive", help="move a message -> done by id")
    ar.add_argument("--as", dest="sender", default=None); ar.add_argument("--id", required=True)
    ar.add_argument("--state", choices=TERMINAL, default="completed",
                    help="how it ended (default completed)")
    ar.add_argument("--note", default="", help="one line the sender sees")
    ar.set_defaults(func=cmd_archive)

    st = sub.add_parser("state", help="set a message's task state; the sender sees it")
    st.add_argument("--as", dest="sender", default=None); st.add_argument("--id", required=True)
    st.add_argument("--set", required=True, choices=STATES)
    st.add_argument("--note", default="", help='e.g. "need the user\'s call on X"')
    st.set_defaults(func=cmd_state)

    cd = sub.add_parser("card", help="show or edit your agent card (description/repos/owns)")
    cd.add_argument("--as", dest="sender", default=None); _add_card_flags(cd)
    cd.set_defaults(func=cmd_card)

    ls = sub.add_parser("lease", help="claim a RESOURCE (repo/PR/path) so peers don't work it too")
    ls.add_argument("--as", dest="sender", default=None)
    ls.add_argument("--resource", required=True,
                    help="what you're taking, e.g. 'skills-library', 'agent-skill-eval#74', "
                         "'agent-toolkit:skills/e2e' — parents cover children")
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
    pe.add_argument("--verbose", "-v", action="store_true", help="also show owns/human")
    pe.add_argument("--repo", default=None, help="only agents working in this repo")
    pe.set_defaults(func=cmd_peers)

    h = sub.add_parser("heartbeat", help="update your liveness + current focus")
    h.add_argument("--as", dest="sender", default=None); h.add_argument("--focus")
    h.set_defaults(func=cmd_heartbeat)

    pr = sub.add_parser("prune", help="clear done/, age old claimed mail into expired/, drop dead peers")
    _add_age_flags(pr); pr.set_defaults(func=cmd_prune)

    a = p.parse_args()
    # Propagate a command's exit code — `lease` returns non-zero on conflict so a caller
    # can branch on "someone else has it" instead of parsing the JSON.
    raise SystemExit(a.func(a) or 0)


if __name__ == "__main__":
    main()
