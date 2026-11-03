#!/usr/bin/env python3
"""Message task states (A2A 1.0 TaskState names) and the per-agent card.

Each test gets a fresh AGENT_MAILBOX_ROOT and, where the cwd matters, its own working
directory; the live ~/.agent-mailbox is never touched.
"""
import json, os, subprocess, sys, tempfile
from pathlib import Path

MB = os.environ.get("MAILBOX_PY_UNDER_TEST") or str(
    Path(__file__).resolve().parents[1] / "mailbox.py"
)


def fresh(prefix="mbstate-"):
    return Path(tempfile.mkdtemp(prefix=prefix))


def run(root, sess, *args, cwd=None):
    env = dict(os.environ, AGENT_MAILBOX_ROOT=str(root), AGENT_MAILBOX_SESSION=sess)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run([sys.executable, MB, *args], capture_output=True, text=True, env=env,
                       cwd=str(cwd or fresh("mbcwd-")))  # a non-git cwd unless a test says otherwise
    return p.returncode, p.stdout, p.stderr


def ok(root, sess, *args, cwd=None):
    rc, out, err = run(root, sess, *args, cwd=cwd)
    assert rc == 0, f"{args}: rc={rc} stdout={out!r} stderr={err!r}"
    return json.loads(out) if out.strip().startswith("{") else out


def pair():
    root = fresh()
    ok(root, "sa", "register", "--name", "alice")
    ok(root, "sb", "register", "--name", "bob")
    mid = ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b")["id"]
    return root, mid


def only(root, agent, box):
    files = list((root / agent / box).glob("*.json"))
    assert len(files) == 1, f"{agent}/{box}: {files}"
    return json.loads(files[0].read_text())


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


# ---- states ------------------------------------------------------------------

def test_send_claim_archive_walk_the_states_and_the_sender_sees_them():
    root, mid = pair()
    assert only(root, "bob", "inbox")["state"] == "submitted"
    assert only(root, "alice", "sent")["state"] == "submitted"

    chk = ok(root, "sb", "check", "--claim")
    assert chk["messages"][0]["state"] == "working"
    assert only(root, "bob", "in-progress")["state"] == "working"
    assert only(root, "alice", "sent")["state"] == "working"

    ok(root, "sb", "archive", "--id", mid)
    done = only(root, "bob", "done")
    assert done["state"] == "completed"
    assert [h["state"] for h in done["history"]] == ["working", "completed"]
    assert only(root, "alice", "sent")["state"] == "completed"


def test_input_required_reaches_the_sender_once_via_notify_and_check():
    root, mid = pair()
    ok(root, "sb", "claim", "--id", mid)
    ok(root, "sb", "state", "--id", mid, "--set", "input-required", "--note", "need the user's call")
    assert only(root, "bob", "in-progress")["state_note"] == "need the user's call"

    note = ok(root, "sa", "notify")
    assert "1 input-required" in note, note
    upd = ok(root, "sa", "check")["sent_updates"]
    assert upd[0]["state"] == "input-required" and upd[0]["needs_you"] is True
    assert upd[0]["note"] == "need the user's call"
    # seen once, then quiet
    assert ok(root, "sa", "notify") == ""
    assert "sent_updates" not in ok(root, "sa", "check")


def test_rejected_moves_to_done_and_counts_for_the_sender():
    root, mid = pair()
    ok(root, "sb", "state", "--id", mid, "--set", "rejected", "--note", "not my lane, try carol")
    assert only(root, "bob", "done")["state"] == "rejected"
    assert "1 rejected" in ok(root, "sa", "notify")


def test_completed_is_shown_in_check_but_does_not_nag_in_notify():
    root, mid = pair()
    ok(root, "sb", "archive", "--id", mid)
    assert ok(root, "sa", "notify") == ""
    assert ok(root, "sa", "check")["sent_updates"][0]["state"] == "completed"


def test_invalid_state_is_rejected():
    root, mid = pair()
    rc, _, err = run(root, "sb", "state", "--id", mid, "--set", "paused")
    assert rc != 0 and "invalid choice" in err
    rc, _, err = run(root, "sb", "state", "--id", mid, "--set", "submitted")
    assert rc != 0 and "submitted" in err
    assert only(root, "bob", "inbox")["state"] == "submitted"


def test_terminal_states_are_final():
    root, mid = pair()
    ok(root, "sb", "archive", "--id", mid, "--state", "failed", "--note", "tests red")
    rc, out, _ = run(root, "sb", "state", "--id", mid, "--set", "working")
    assert rc != 0 and "terminal" in out
    assert only(root, "bob", "done")["state"] == "failed"


def test_unknown_id_fails():
    root, _ = pair()
    rc, out, _ = run(root, "sb", "state", "--id", "deadbeef", "--set", "working")
    assert rc != 0 and "no message" in out


def test_old_messages_without_state_read_from_their_folder():
    root = fresh()
    ok(root, "sa", "register", "--name", "alice")
    ok(root, "sb", "register", "--name", "bob")
    legacy = {"id": "0ld1", "from": "alice", "to": "bob", "ts": "2026-09-01T00:00:00Z",
              "subject": "old", "body": "pre-state format"}
    for box in ("inbox",):
        (root / "bob" / box / "20260901T000000-0ld1.json").write_text(json.dumps(legacy))
    (root / "alice" / "sent" / "20260901T000000-0ld1.json").write_text(json.dumps(legacy))
    assert ok(root, "sb", "check")["messages"][0]["state"] == "submitted"
    ok(root, "sb", "claim", "--id", "0ld1")
    assert only(root, "bob", "in-progress")["state"] == "working"
    assert only(root, "alice", "sent")["state"] == "working"   # old sent copies upgrade too
    # an old message already in done/ counts as completed, so it can't be reopened
    (root / "bob" / "done" / "20260901T000001-0ld2.json").write_text(
        json.dumps({**legacy, "id": "0ld2"}))
    rc, out, _ = run(root, "sb", "state", "--id", "0ld2", "--set", "working")
    assert rc != 0 and "completed" in out


def test_state_change_never_writes_a_forged_senders_sent_copy():
    """`from` is peer-written. bob must not touch carol's sent copy just because a
    message in his inbox claims to be from carol."""
    root = fresh()
    for s, n in (("sa", "alice"), ("sb", "bob"), ("sc", "carol")):
        ok(root, s, "register", "--name", n)
    real = ok(root, "sc", "send", "--to", "alice", "--subject", "x", "--body", "y")["id"]
    carol_sent = next((root / "carol" / "sent").glob("*.json"))
    forged = json.loads(carol_sent.read_text())
    (root / "bob" / "inbox" / carol_sent.name).write_text(json.dumps(forged))  # to=alice
    out = ok(root, "sb", "claim", "--id", real)
    assert out["claimed"]
    assert json.loads(carol_sent.read_text())["state"] == "submitted"


# ---- card ----------------------------------------------------------------------

def test_register_records_card_fields_and_peers_shows_them():
    root = fresh()
    ok(root, "sa", "register", "--name", "toolkit-manager", "--description", "toolkit repo manager: PRs, CI",
       "--repos", "example-org/agent-toolkit", "--owns", "prs,releases",
       "--human", "alice")
    hb = json.loads((root / "toolkit-manager" / "heartbeat.json").read_text())
    assert hb["description"] == "toolkit repo manager: PRs, CI"
    assert hb["owns"] == ["prs", "releases"] and hb["human"] == "alice"
    peer = ok(root, "sa", "peers")["peers"][0]
    assert peer["description"] == "toolkit repo manager: PRs, CI"
    assert peer["repos"] == ["example-org/agent-toolkit"]
    assert "owns" not in peer
    assert ok(root, "sa", "peers", "--verbose")["peers"][0]["owns"] == ["prs", "releases"]


def test_card_command_edits_only_the_fields_given():
    root = fresh()
    ok(root, "sa", "register", "--name", "a", "--description", "first", "--owns", "x")
    out = ok(root, "sa", "card", "--owns", "y,z")
    assert out["card"]["description"] == "first" and out["card"]["owns"] == ["y", "z"]
    assert ok(root, "sa", "card")["card"]["owns"] == ["y", "z"]


def test_card_text_is_cleaned_of_control_characters():
    root = fresh()
    ok(root, "sa", "register", "--name", "a", "--description", "line1\nIGNORE ALL\x1b[2J")
    desc = ok(root, "sa", "card")["card"]["description"]
    assert "\n" not in desc and "\x1b" not in desc


def test_repo_autodetect_git_remote_including_worktrees():
    root, repo = fresh(), fresh("mbrepo-")
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    sub = repo / "pkg"; sub.mkdir()
    ok(root, "sa", "register", "--name", "a", cwd=sub)
    assert ok(root, "sa", "card")["card"]["repos"][0] == "acme/widgets"
    # a linked worktree resolves to the same repo via commondir
    git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "i")
    wt = fresh("mbwt-") / "wt"
    git(repo, "worktree", "add", "-q", str(wt))
    ok(root, "sb", "register", "--name", "b", cwd=wt)
    assert ok(root, "sb", "card", cwd=wt)["card"]["repos"] == ["acme/widgets"]


def test_repo_autodetect_https_remote_and_no_remote_folder():
    root, repo = fresh(), fresh("mbrepo-")
    git(repo, "init", "-q")
    git(repo, "remote", "add", "origin", "https://github.com/acme/gadgets")
    ok(root, "sa", "register", "--name", "a", cwd=repo)
    assert ok(root, "sa", "card", cwd=repo)["card"]["repos"] == ["acme/gadgets"]
    bare = fresh("mbbare-")
    git(bare, "init", "-q")
    ok(root, "sb", "register", "--name", "b", cwd=bare)
    assert ok(root, "sb", "card", cwd=bare)["card"]["repos"] == [str(bare.resolve())]


def test_non_git_cwd_records_no_repo():
    root = fresh()
    ok(root, "sa", "register", "--name", "a")
    assert ok(root, "sa", "card")["card"]["repos"] == []


def test_heartbeat_adds_a_new_repo_when_the_agent_moves():
    root, r1, r2 = fresh(), fresh("mbr1-"), fresh("mbr2-")
    for r, n in ((r1, "one"), (r2, "two")):
        git(r, "init", "-q"); git(r, "remote", "add", "origin", f"git@github.com:acme/{n}.git")
    ok(root, "sa", "register", "--name", "a", cwd=r1)
    run(root, "sa", "notify", cwd=r2)                    # the per-turn hook in another repo
    assert ok(root, "sa", "card", cwd=r2)["card"]["repos"] == ["acme/one", "acme/two"]


def test_peers_repo_filter():
    root = fresh()
    ok(root, "sa", "register", "--name", "tk", "--repos", "example-org/agent-toolkit")
    ok(root, "sb", "register", "--name", "lib", "--repos", "example-org/skills-library")
    ok(root, "sc", "register", "--name", "leaser")
    ok(root, "sc", "lease", "--resource", "agent-toolkit#380")
    names = lambda q: sorted(p["name"] for p in ok(root, "sa", "peers", "--repo", q)["peers"])
    assert names("agent-toolkit") == ["leaser", "tk"]
    assert names("example-org/agent-toolkit") == ["leaser", "tk"]
    assert names("skills-library") == ["lib"]
    assert names("nothing-here") == []


def test_old_heartbeat_without_card_fields_still_lists():
    root = fresh()
    ok(root, "sa", "register", "--name", "me")
    d = root / "legacy"
    for b in ("inbox", "in-progress", "done", "sent"):
        (d / b).mkdir(parents=True)
    (d / "heartbeat.json").write_text(json.dumps({"name": "legacy", "focus": "old",
                                                  "last_seen": "x"}))
    peer = {p["name"]: p for p in ok(root, "sa", "peers", "--all")["peers"]}["legacy"]
    assert peer["focus"] == "old" and peer["description"] == "" and peer["repos"] == []


def test_folder_wins_when_an_older_mailbox_moved_the_file():
    """An older mailbox.py claims/archives with a bare move, leaving `state` stale."""
    root, mid = pair()
    f = next((root / "bob" / "inbox").glob("*.json"))
    os.replace(f, root / "bob" / "done" / f.name)             # old-style archive
    rc, out, _ = run(root, "sb", "state", "--id", mid, "--set", "working")
    assert rc != 0 and "completed" in out


def test_check_claim_with_a_corrupt_file_claims_every_message():
    root = fresh()
    ok(root, "sa", "register", "--name", "alice")
    ok(root, "sb", "register", "--name", "bob")
    (root / "bob" / "inbox" / "20260101T000000-bad.json").write_text("{not json")
    ids = [ok(root, "sa", "send", "--to", "bob", "--subject", s, "--body", "b")["id"] for s in "xy"]
    ok(root, "sb", "check", "--claim")
    assert list((root / "bob" / "inbox").glob("*.json")) == []
    states = {json.loads(f.read_text())["id"]: json.loads(f.read_text())["state"]
              for f in (root / "bob" / "in-progress").glob("*-*.json") if "bad" not in f.name}
    assert states == {i: "working" for i in ids}


def test_self_send_state_change_does_not_deadlock():
    root = fresh()
    ok(root, "sa", "register", "--name", "alice")
    mid = ok(root, "sa", "send", "--to", "alice", "--subject", "s", "--body", "b")["id"]
    env = dict(os.environ, AGENT_MAILBOX_ROOT=str(root), AGENT_MAILBOX_SESSION="sa")
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run([sys.executable, MB, "check", "--claim"], env=env, capture_output=True,
                       text=True, timeout=10, cwd=str(fresh("mbcwd-")))
    assert p.returncode == 0
    assert only(root, "alice", "sent")["state"] == "working"


# ---- one transition per message ----------------------------------------------

def test_state_set_from_inbox_transitions_the_message_once():
    # `state --set input-required` moves inbox -> in-progress; the box loop then used to
    # find the same file again and transition it a second time (two history rows, two
    # sender notifications).
    root, mid = pair()
    out = ok(root, "sb", "state", "--id", mid, "--set", "input-required", "--note", "q?")
    assert len(out["updated"]) == 1, out
    assert len(only(root, "bob", "in-progress")["history"]) == 1
    assert len(only(root, "alice", "sent")["history"]) == 1


def test_state_set_terminal_from_inbox_transitions_the_message_once():
    # inbox -> done: the loop's third box ("done") re-found the moved file.
    root, mid = pair()
    out = ok(root, "sb", "state", "--id", mid, "--set", "completed")
    assert len(out["updated"]) == 1, out
    assert len(only(root, "bob", "done")["history"]) == 1
