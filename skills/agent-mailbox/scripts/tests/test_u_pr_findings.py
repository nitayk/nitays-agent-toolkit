#!/usr/bin/env python3
"""Regression tests for three agent-mailbox bugs a reviewer found.

1. `status` counted a reply from anyone who quoted the message id, not only the recipient.
2. The first prune after an upgrade deleted an idle mailbox before the registry of known
   names existed, so a later send to that name was refused as a typo.
3. `send` and the register take-over of a placeholder shared no lock, so a letter written
   between the take-over's check and its delete was lost while `send` reported success.

Each test gets a fresh AGENT_MAILBOX_ROOT; the live ~/.agent-mailbox is never touched.
"""
import json, os, subprocess, sys, tempfile, time
from pathlib import Path

try:
    import fcntl
except ImportError:                                        # Windows: the lock is a no-op there
    fcntl = None

MB = os.environ.get("MAILBOX_PY_UNDER_TEST") or str(
    Path(__file__).resolve().parents[1] / "mailbox.py"
)


def _env(root, sess):
    env = dict(os.environ, AGENT_MAILBOX_ROOT=str(root), AGENT_MAILBOX_SESSION=sess)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    return env


def run(root, sess, *args):
    p = subprocess.run([sys.executable, MB, *args], capture_output=True, text=True, env=_env(root, sess))
    return p.returncode, p.stdout, p.stderr


def reg(root, sess, name):
    rc, out, err = run(root, sess, "register", "--name", name)
    assert rc == 0, f"setup failed: register {name!r}: rc={rc} stderr={err!r}"


def send(root, sess, to, *extra):
    rc, out, err = run(root, sess, "send", "--to", to, "--subject", "s", "--body", "b", *extra)
    assert rc == 0, f"send to {to!r} failed: rc={rc} out={out!r} err={err!r}"
    return json.loads(out)


def fresh():
    return Path(tempfile.mkdtemp(prefix="mbureview-"))


# ---- 1. status attributes a reply to its recipient -----------------------------------

def test_status_ignores_a_reply_from_someone_who_was_not_asked():
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob"); reg(root, "sc", "carol")
    mid = send(root, "sa", "bob")["id"]
    send(root, "sc", "alice", "--reply-to", mid)           # carol quotes the id bob was given
    rc, out, err = run(root, "sa", "status", "--id", mid)
    assert rc == 0, err
    st = json.loads(out)
    assert st["answered"] == [], f"carol's letter was counted as bob's answer: {st}"
    assert st["waiting"] == ["bob"]


def test_status_still_counts_the_recipients_own_reply():
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    mid = send(root, "sa", "bob")["id"]
    send(root, "sb", "alice", "--reply-to", mid)
    st = json.loads(run(root, "sa", "status", "--id", mid)[1])
    assert st["answered"] == ["bob"], st


# ---- 2. the registry of known names is written before anything is pruned -------------

def test_first_prune_after_upgrade_does_not_forget_an_idle_name():
    root = fresh()
    reg(root, "sb", "bob"); reg(root, "sc", "carol")
    (root / ".known.json").unlink()                        # state before the registry existed
    hb = root / "bob" / "heartbeat.json"
    old = time.time() - 8 * 24 * 3600                      # idle past the 7-day peer window, no mail
    os.utime(hb, (old, old))
    rc, out, err = run(root, "sc", "prune")
    assert rc == 0, err
    assert not (root / "bob").exists(), "setup: bob should have been pruned as a dead peer"
    rc, out, err = run(root, "sc", "send", "--to", "bob", "--subject", "s", "--body", "b")
    assert rc == 0, f"bob was forgotten by the first prune, so the send was refused: {out} {err}"
    assert "queued" in json.loads(out)


def test_register_prune_does_not_forget_an_idle_name_either():
    root = fresh()
    reg(root, "sb", "bob")
    (root / ".known.json").unlink()
    old = time.time() - 8 * 24 * 3600
    os.utime(root / "bob" / "heartbeat.json", (old, old))
    reg(root, "sc", "carol")                               # register runs prune_run first
    assert not (root / "bob").exists(), "setup: bob should have been pruned as a dead peer"
    assert "queued" in send(root, "sc", "bob")


# ---- 3. send and placeholder take-over are serialized --------------------------------

LOCK = ".deliver.lock"


def _hold(root):
    fd = os.open(root / LOCK, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _placeholder(root, name, queued_id=None):
    d = root / name
    for box in ("inbox", "in-progress", "done", "sent"):
        (d / box).mkdir(parents=True, exist_ok=True)
    (d / "placeholder.json").write_text(json.dumps({"name": name, "created": "2026-01-01T00:00:00Z"}))
    if queued_id:
        (d / "inbox" / f"20260101T000000-{queued_id}.json").write_text(
            json.dumps({"id": queued_id, "from": "x", "to": name, "subject": "s", "body": "b"}))


def _blocks_while_locked(root, sess, *args):
    """True when the command waits for the lock and finishes once it is released."""
    fd = _hold(root)
    try:
        p = subprocess.Popen([sys.executable, MB, *args], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=_env(root, sess))
        time.sleep(1.5)
        waited = p.poll() is None
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN); os.close(fd)
    out, err = p.communicate(timeout=30)
    assert p.returncode == 0, f"{args[0]} failed after the lock was released: {err}"
    return waited


def test_send_waits_for_the_delivery_lock():
    if fcntl is None:
        return
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    assert _blocks_while_locked(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b"), \
        "send did not wait for a take-over holding the delivery lock"


def test_placeholder_takeover_waits_for_the_delivery_lock():
    if fcntl is None:
        return
    root = fresh()
    reg(root, "sd", "dave")
    _placeholder(root, "bob")
    assert _blocks_while_locked(root, "sd", "register", "--name", "bob"), \
        "register took over a placeholder without the delivery lock"


def test_takeover_keeps_the_queued_letter():
    root = fresh()
    reg(root, "sd", "dave")
    _placeholder(root, "bob", queued_id="abc12345")
    reg(root, "sd", "bob")
    assert list((root / "bob" / "inbox").glob("*abc12345.json")), "queued letter was lost in the take-over"


# ---- cross-model review round (gpt-6-astra) on the three fixes above -----------------

def test_status_survives_a_letter_with_an_odd_from_field():
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    mid = send(root, "sa", "bob")["id"]
    (root / "alice" / "inbox" / "20260101T000000-odd00001.json").write_text(
        json.dumps({"id": "odd00001", "reply_to": mid, "from": [], "subject": "s", "body": "b"}))
    rc, out, err = run(root, "sa", "status", "--id", mid)
    assert rc == 0, f"one malformed incoming letter broke status: {err}"
    assert json.loads(out)["answered"] == []


def test_prune_waits_for_the_delivery_lock_before_deleting_a_mailbox():
    if fcntl is None:
        return
    root = fresh()
    reg(root, "sb", "bob"); reg(root, "sc", "carol")
    old = time.time() - 8 * 24 * 3600
    os.utime(root / "bob" / "heartbeat.json", (old, old))
    assert _blocks_while_locked(root, "sc", "prune"), \
        "prune deleted an idle mailbox without the delivery lock, so a send could be lost"
    assert not (root / "bob").exists()


def test_takeover_moves_nothing_when_the_name_became_a_live_mailbox():
    """The collision loop saw a placeholder; by the time the lock is held the name is a
    real mailbox. Its queued letter must stay where it is."""
    root = fresh()
    reg(root, "sd", "dave"); reg(root, "sb", "bob")
    send(root, "sd", "bob")                                # a letter waiting in bob's inbox
    env = dict(_env(root, "sd"))
    code = f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("mb", {MB!r}); mb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mb)
real, calls = mb.is_placeholder, []
mb.is_placeholder = lambda d: (calls.append(1) or len(calls) == 1) or real(d)   # stale answer once
sys.argv = ["mailbox.py", "register", "--name", "bob"]
mb.main()
"""
    p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    assert p.returncode != 0, f"take-over of a live mailbox went through: {p.stdout}"
    assert list((root / "bob" / "inbox").glob("*.json")), "bob's letter was moved out of his inbox"
    assert not list((root / "dave" / "inbox").glob("*.json")), "bob's letter landed in dave's inbox"
