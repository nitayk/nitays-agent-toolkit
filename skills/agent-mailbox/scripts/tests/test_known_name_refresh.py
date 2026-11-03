#!/usr/bin/env python3
"""A standing agent that registered once, idles past the 7-day peer window and is pruned must
still be a known name. The registry used to age out 30 days after the last `register`, so
letters to a long-lived manager were refused as a typo.

Each test gets a fresh AGENT_MAILBOX_ROOT; the live ~/.agent-mailbox is never touched.
"""
import json, os, subprocess, sys, tempfile, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

MB = os.environ.get("MAILBOX_PY_UNDER_TEST") or str(
    Path(__file__).resolve().parents[1] / "mailbox.py"
)


def run(root, sess, *args):
    env = dict(os.environ, AGENT_MAILBOX_ROOT=str(root), AGENT_MAILBOX_SESSION=sess)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run([sys.executable, MB, *args], capture_output=True, text=True, env=env)
    return p.returncode, p.stdout, p.stderr


def reg(root, sess, name):
    rc, out, err = run(root, sess, "register", "--name", name)
    assert rc == 0, f"setup failed: register {name!r}: {err!r}"


def iso_days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def age(path, days):
    t = time.time() - days * 86400
    os.utime(path, (t, t))


def test_long_idle_manager_is_still_known_after_it_is_pruned():
    root = Path(tempfile.mkdtemp(prefix="mbknown-"))
    reg(root, "sb", "bob")
    (root / ".known.json").write_text(json.dumps({"bob": iso_days_ago(31)}))   # registered once, long ago
    age(root / "bob" / "heartbeat.json", 8)                                    # then idle past the peer window
    reg(root, "sc", "carol")                                                   # prune runs, then the registry is rewritten
    assert not (root / "bob").exists(), "setup: bob should have been pruned as a dead peer"
    rc, out, err = run(root, "sc", "send", "--to", "bob", "--subject", "s", "--body", "b")
    assert rc == 0, f"send to a pruned long-lived name was refused as a typo: {out} {err}"
    assert "queued" in json.loads(out)


def test_pruning_a_placeholder_does_not_keep_a_dead_name_alive():
    """Send -> placeholder -> ages out must not refresh the name, or it never expires."""
    root = Path(tempfile.mkdtemp(prefix="mbknown-"))
    reg(root, "sc", "carol")
    stamp = iso_days_ago(10)
    (root / ".known.json").write_text(json.dumps({"bob": stamp}))
    ph = root / "bob"
    for box in ("inbox", "in-progress", "done", "sent"):
        (ph / box).mkdir(parents=True)
    (ph / "placeholder.json").write_text(json.dumps({"name": "bob", "created": stamp}))
    age(ph / "placeholder.json", 8)
    rc, out, err = run(root, "sc", "prune")
    assert rc == 0, err
    assert not ph.exists(), "setup: the aged-out placeholder should have been removed"
    assert json.loads((root / ".known.json").read_text())["bob"] == stamp, \
        "pruning a placeholder refreshed the name's registry stamp"
