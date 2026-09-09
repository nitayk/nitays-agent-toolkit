#!/usr/bin/env python3
"""Behavioral tests for agent-mailbox leasing + the broadcast offline-peer fix.

Parameterized by the mailbox.py under test so the SAME suite can prove:
  known-good -> all pass (baseline)
  pre-fix    -> lease tests error, broadcast test fails (proves tests can detect absence)
  post-fix   -> all pass
Each test gets a fresh AGENT_MAILBOX_ROOT; the live ~/.agent-mailbox is never touched.

Defaults to THIS repo's own scripts/mailbox.py (resolved relative to this file) so
pytest can collect and run it with no arguments; override with MAILBOX_PY_UNDER_TEST
or by passing a path as argv[1] when run standalone (`python3 test_leasing.py <path>`).
Note: sys.argv is NOT consulted for this under pytest — sys.argv[1] there is pytest's
own CLI argument (e.g. the test path), not a mailbox.py override.
"""
import importlib.util, json, os, stat, subprocess, sys, tempfile, threading, time
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
    """Register, and fail loudly here (not 40 lines later on a missing file) if it didn't work."""
    rc, out, err = run(root, sess, "register", "--name", name)
    assert rc == 0, (
        f"setup failed: register --name {name!r} (session {sess!r}) under {root} "
        f"exited rc={rc}\nstdout={out!r}\nstderr={err!r}"
    )
    return rc, out, err


def fresh():
    return Path(tempfile.mkdtemp(prefix="mbtest-"))


def expire_lease(root):
    """Backdate the single lease record. Returns False when no lease exists (feature absent)."""
    files = list((root / ".leases").glob("*.json"))
    if not files:
        return False
    rec = json.loads(files[0].read_text()); rec["expires_epoch"] = time.time() - 10
    files[0].write_text(json.dumps(rec))
    return True


def run_leasing_suite():
    """Run every behavioral check and return [(name, passed, detail), ...]."""
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))

    # --- 1/2/3: acquire, renew, conflict + EXIT CODE contract ----------------
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    rc, _, _ = run(r, "sA", "lease", "--resource", "repo-x")
    check("acquire_free_exit0", rc == 0, f"rc={rc}")
    rc, _, _ = run(r, "sA", "lease", "--resource", "repo-x")
    check("renew_own_exit0", rc == 0, f"rc={rc}")
    rc, out, _ = run(r, "sB", "lease", "--resource", "repo-x")
    check("conflict_other_EXITS_1", rc == 1, f"rc={rc} (2=subcommand missing)")
    check("conflict_names_holder", '"holder": "agent-a"' in out, "")

    # --- 4/5/6/7: hierarchy overlap, both directions + lookalike -------------
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    run(r, "sA", "lease", "--resource", "repo-p")
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-p/pkg")
    check("parent_blocks_child", rc == 1, f"rc={rc} (2=subcommand missing)")
    run(r, "sA", "lease", "--resource", "repo-q/pkg")
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-q")
    check("child_blocks_parent", rc == 1, f"rc={rc} (2=subcommand missing)")
    run(r, "sA", "lease", "--resource", "repo-w")
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-w#74")
    check("hash_boundary_conflicts", rc == 1, f"rc={rc} (2=subcommand missing)")
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-p-other")
    check("lookalike_does_NOT_conflict", rc == 0, f"rc={rc}")

    # --- 8/9: expiry does not block; prune reports it -------------------------
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    run(r, "sA", "lease", "--resource", "repo-e")
    ok_setup = expire_lease(r)
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-e")
    check("expired_does_not_block", ok_setup and rc == 0, f"setup={ok_setup} rc={rc}")
    r2 = fresh(); reg(r2, "sA", "agent-a")
    run(r2, "sA", "lease", "--resource", "repo-e2")
    ok_setup2 = expire_lease(r2)
    _, out, _ = run(r2, "sA", "prune")
    check("prune_reports_expired_lease", ok_setup2 and "repo-e2" in out and "expired_leases" in out, f"setup={ok_setup2}")

    # --- 10/11: orphaned = TTL valid but holder dead --------------------------
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    run(r, "sA", "lease", "--resource", "repo-o")
    hb = r / "agent-a" / "heartbeat.json"
    old = time.time() - (7 * 3600)          # older than DEFAULT_PEER_STALE_H (6h)
    os.utime(hb, (old, old))
    _, out, _ = run(r, "sB", "leases")
    check("dead_holder_reads_orphaned", '"state": "orphaned"' in out, "")
    rc, _, _ = run(r, "sB", "lease", "--resource", "repo-o")
    check("orphaned_takeable_without_force", rc == 0, f"rc={rc}")

    # --- 12: BROADCAST reaches an OFFLINE peer (the non-leasing bug fix) -----
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    hb = r / "agent-b" / "heartbeat.json"
    old = time.time() - 600                 # >180s ONLINE_WINDOW_S => offline, but well within prune
    os.utime(hb, (old, old))
    run(r, "sA", "broadcast", "--subject", "s", "--body", "b")
    inbox = list((r / "agent-b" / "inbox").glob("*.json"))
    check("broadcast_reaches_OFFLINE_peer", len(inbox) == 1, f"inbox={len(inbox)}")

    # --- 13-16: a CORRUPT lease record must not wedge the whole store ---------
    # Records are written by peer processes; a partial write or schema skew leaves one
    # unreadable. If that crashes lease/leases/prune, the store cannot self-heal because
    # prune is exactly what would collect the bad file.
    r = fresh(); reg(r, "sA", "agent-a"); reg(r, "sB", "agent-b")
    run(r, "sA", "lease", "--resource", "good-one")
    leases_dir = r / ".leases"
    (leases_dir / "corrupt-expiry.json").write_text(
        json.dumps({"resource": "x1", "holder": "agent-a", "expires_epoch": "not-a-number"}))
    (leases_dir / "corrupt-shape.json").write_text(json.dumps(["not", "an", "object"]))
    (leases_dir / "corrupt-holder.json").write_text(
        json.dumps({"resource": "x2", "holder": "../../etc", "expires_epoch": 9e18}))
    rc, out, _ = run(r, "sB", "leases")
    check("corrupt_record_does_not_crash_leases", rc == 0, f"rc={rc}")
    check("corrupt_record_leaves_good_lease_visible", "good-one" in out, "")
    rc, _, _ = run(r, "sB", "lease", "--resource", "unrelated")
    check("corrupt_record_does_not_block_acquire", rc == 0, f"rc={rc}")
    rc, out, _ = run(r, "sA", "prune")
    check("prune_collects_corrupt_record", rc == 0 and not (leases_dir / "corrupt-expiry.json").exists(),
          f"rc={rc}")

    # --- 17: a renewed lease keeps the same restrictive mode as a fresh one ---
    r = fresh(); reg(r, "sA", "agent-a")
    run(r, "sA", "lease", "--resource", "mode-check")
    lf = leases_dir = next((r / ".leases").glob("*.json"))
    mode_new = stat.S_IMODE(os.stat(lf).st_mode)
    run(r, "sA", "lease", "--resource", "mode-check")          # renew -> atomic_write path
    mode_renewed = stat.S_IMODE(os.stat(lf).st_mode)
    check("renewed_lease_keeps_0600", mode_new == 0o600 and mode_renewed == 0o600,
          f"new={oct(mode_new)} renewed={oct(mode_renewed)}")

    # --- 18-19: a TRUNCATED lease file must not wedge that lane forever -------
    # The O_EXCL branch writes its content non-atomically, so a killed process can leave a
    # half-written record. That file is invisible to `leases` (parse error) yet still owns
    # the filename, so without these fixes the resource became permanently unleasable and
    # prune could not collect it either.
    r = fresh(); reg(r, "sA", "agent-a")
    run(r, "sA", "lease", "--resource", "repo-trunc")
    lf = r / ".leases" / "repo-trunc.json"
    lf.write_text('{"resource": "repo-trunc", "hol')          # interrupted mid-write
    rc, _, _ = run(r, "sA", "lease", "--resource", "repo-trunc")
    check("truncated_record_does_not_wedge_lane", rc == 0, f"rc={rc}")

    r = fresh(); reg(r, "sA", "agent-a")
    run(r, "sA", "lease", "--resource", "repo-trunc2")
    lf = r / ".leases" / "repo-trunc2.json"
    lf.write_text('{trunc')
    rc, _, _ = run(r, "sA", "prune")
    check("prune_collects_unreadable_record", rc == 0 and not lf.exists(), f"rc={rc} exists={lf.exists()}")

    return results


def test_all():
    results = run_leasing_suite()
    failed = [(name, detail) for name, ok, detail in results if not ok]
    lines = [f"  {'PASS' if ok else 'FAIL'}  {name}{('  [' + detail + ']') if detail and not ok else ''}"
             for name, ok, detail in results]
    summary = f"{len(results) - len(failed)}/{len(results)} passed"
    report = "\n".join(lines + [summary])
    assert not failed, f"leasing suite failures:\n{report}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        MB = sys.argv[1]
    results = run_leasing_suite()
    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  [' + detail + ']') if detail and not ok else ''}")
    print(f"\n{passed}/{len(results)} passed")
    sys.exit(0 if passed == len(results) else 1)


def _load_mailbox(root):
    """Import the mailbox under test in-process, pointed at an isolated root.

    Concurrency cannot be tested through subprocesses: ~50ms of interpreter startup
    serializes the agents, so the first one finishes before the second begins and the
    race window is never reached. That is exactly why this bug survived a suite that
    was otherwise green.
    """
    spec = importlib.util.spec_from_file_location("mb_under_test", MB)
    mb = importlib.util.module_from_spec(spec)
    os.environ["AGENT_MAILBOX_ROOT"] = str(root)
    spec.loader.exec_module(mb)
    from pathlib import Path as _P
    mb.ROOT = _P(root)
    mb.LEASES = mb.ROOT / ".leases"
    return mb


def test_overlapping_acquire_is_serialized():
    """Two agents racing OVERLAPPING resources must produce exactly one winner.

    `repo` and `repo/pkg` conflict but slug to two different filenames, so O_EXCL — which
    is atomic per filename — does not cover them. Without a lock spanning scan-then-write
    both agents win and hold overlapping lanes, which is the failure leasing exists to
    prevent and the case the docs lead with ("parents cover children").
    """
    root = tempfile.mkdtemp(prefix="mbrace-")
    mb = _load_mailbox(root)
    for n in ("agent-a", "agent-b"):
        mb.ensure_agent(n)
        mb.write_heartbeat(n, "", None)

    # Widen the naturally-existing gap: hold both threads at the point where each has
    # finished scanning for conflicts but neither has written its lease file yet.
    original = mb.active_leases
    barrier = threading.Barrier(2)

    def scan_then_sync(*a, **kw):
        out = original(*a, **kw)
        try:
            barrier.wait(timeout=5)
        except Exception:
            pass
        return out

    mb.active_leases = scan_then_sync
    results = {}

    def acquire(holder, resource):
        results[holder] = mb.lease_acquire(holder, resource, 8, "", False)

    threads = [threading.Thread(target=acquire, args=a)
               for a in (("agent-a", "raceparent"), ("agent-b", "raceparent/child"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert mb.conflicts("raceparent", "raceparent/child"), "precondition: these must conflict"
    winners = [h for h, r in results.items() if r.get("ok")]
    assert len(winners) == 1, (
        f"expected exactly one winner, got {len(winners)}: {winners}. "
        f"Lease files on disk: {sorted(os.listdir(mb.LEASES))}. "
        "Both agents hold overlapping lanes — the scan-then-write section is not serialized."
    )
