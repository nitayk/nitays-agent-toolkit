#!/usr/bin/env python3
"""Regression tests for four agent-mailbox bugs found by the 2026-09-28 /repo-harden runs
(toolkit register MB-01..03 and a broadcast crash reported by another agent).

Each test gets a fresh AGENT_MAILBOX_ROOT; the live ~/.agent-mailbox is never touched.
"""
import importlib.util, json, os, subprocess, sys, tempfile
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
    assert rc == 0, f"setup failed: register {name!r}: rc={rc} stderr={err!r}"


def fresh():
    return Path(tempfile.mkdtemp(prefix="mbharden-"))


def test_own_overlapping_lease_does_not_bypass_a_peer():
    """MB-01: holding ANY overlapping lease used to `break` out of the conflict scan,
    so alice (holding repo/a) could lease `repo` over bob's live repo/b."""
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    assert run(root, "sa", "lease", "--resource", "repo/a")[0] == 0
    assert run(root, "sb", "lease", "--resource", "repo/b")[0] == 0
    rc, out, _ = run(root, "sa", "lease", "--resource", "repo")
    assert rc != 0, f"alice took `repo` over bob's live repo/b: {out}"
    assert "bob" in out


def test_renewing_your_own_lease_still_works():
    root = fresh()
    reg(root, "sa", "alice")
    assert run(root, "sa", "lease", "--resource", "repo")[0] == 0
    assert run(root, "sa", "lease", "--resource", "repo")[0] == 0
    assert run(root, "sa", "lease", "--resource", "repo/sub")[0] == 0   # narrowing your own hold


def test_explicit_as_is_name_validated():
    """MB-03: `--as ../escaped` used to create a mailbox OUTSIDE the root."""
    base = fresh()
    root = base / "root"
    root.mkdir()
    reg(root, "sb", "bob")
    rc, _, _ = run(root, "sx", "send", "--as", "../escaped", "--to", "bob",
                   "--subject", "s", "--body", "b")
    assert rc != 0
    assert not (base / "escaped").exists(), "--as escaped the mailbox root"


def test_broadcast_skips_a_dir_that_is_not_a_mailbox():
    """An earlier report: a stray dir under the root with no inbox/ made broadcast
    raise FileNotFoundError before any peer got the letter."""
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    (root / "reviews" / "rtk-astra").mkdir(parents=True)
    rc, out, err = run(root, "sa", "broadcast", "--subject", "s", "--body", "b")
    assert rc == 0, f"broadcast crashed: {err}"
    assert "bob" in json.loads(out)["broadcast_to"]
    assert "reviews" not in json.loads(out)["broadcast_to"]
    assert len(list((root / "bob" / "inbox").glob("*.json"))) == 1


def test_leases_follow_a_patched_root():
    """MB-02: LEASES was bound at import, so a test that patched ROOT still read and
    pruned the real ~/.agent-mailbox/.leases."""
    root, decoy = fresh(), fresh()
    spec = importlib.util.spec_from_file_location("mb_harden", MB)
    mb = importlib.util.module_from_spec(spec)
    # Import against a DECOY root so an unfixed module writes there, never to ~/.agent-mailbox.
    old = os.environ.get("AGENT_MAILBOX_ROOT")
    os.environ["AGENT_MAILBOX_ROOT"] = str(decoy)
    try:
        spec.loader.exec_module(mb)
    finally:
        if old is None:
            os.environ.pop("AGENT_MAILBOX_ROOT", None)
        else:
            os.environ["AGENT_MAILBOX_ROOT"] = old
    mb.ROOT = root
    res = mb._lease_acquire_locked("alice", "repo", 1.0, "", False)
    assert res["ok"], res
    assert list((root / ".leases").glob("*.json")), "lease was not written under the patched ROOT"


def test_prune_expires_old_sent_copies_only():
    """sent/ was never pruned, so copies piled up forever (130 on one machine by
    2026-10-05). Copies past the notify re-read window go; fresh ones stay."""
    root = fresh()
    reg(root, "sa", "alice"); reg(root, "sb", "bob")
    for i in range(2):
        assert run(root, "sa", "send", "--to", "bob", "--subject", f"s{i}", "--body", "b")[0] == 0
    old, new = sorted((root / "alice" / "sent").glob("*.json"))
    week_ago = os.path.getmtime(old) - 8 * 86400
    os.utime(old, (week_ago, week_ago))
    rc, out, err = run(root, "sa", "prune")
    assert rc == 0, err
    assert json.loads(out)["expired_sent"] == 1
    assert not old.exists() and new.exists()
