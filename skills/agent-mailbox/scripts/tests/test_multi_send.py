#!/usr/bin/env python3
"""One letter to many peers: comma lists, @groups, doorbell lines, `status` per thread,
and the plain `check --text` view (multi-send)."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

MB = os.environ.get("MAILBOX_PY_UNDER_TEST") or str(
    Path(__file__).resolve().parents[1] / "mailbox.py"
)


def fresh(prefix="mbmulti-"):
    return Path(tempfile.mkdtemp(prefix=prefix))


def run(root, sess, *args):
    env = dict(os.environ, AGENT_MAILBOX_ROOT=str(root), AGENT_MAILBOX_SESSION=sess)
    env.pop("CLAUDE_CODE_SESSION_ID", None)
    p = subprocess.run([sys.executable, MB, *args], capture_output=True, text=True, env=env,
                       cwd=str(fresh("mbcwd-")))
    return p.returncode, p.stdout, p.stderr


def ok(root, sess, *args):
    rc, out, err = run(root, sess, *args)
    assert rc == 0, f"{args}: rc={rc} stdout={out!r} stderr={err!r}"
    return json.loads(out) if out.strip().startswith("{") else out


def team():
    root = fresh()
    for s, n in (("sa", "alice"), ("sb", "bob"), ("sc", "carol"), ("sd", "dave")):
        ok(root, s, "register", "--name", n)
    return root


def inbox(root, agent):
    return [json.loads(f.read_text()) for f in sorted((root / agent / "inbox").glob("*.json"))]


def test_comma_list_sends_one_letter_each_under_one_thread():
    root = team()
    out = ok(root, "sa", "send", "--to", "bob,carol", "--subject", "s", "--body", "b")
    assert [r["to"] for r in out["sent"]] == ["bob", "carol"]
    b, c = inbox(root, "bob"), inbox(root, "carol")
    assert len(b) == len(c) == 1 and inbox(root, "dave") == []
    assert b[0]["thread"] == c[0]["thread"] == out["thread"]
    assert b[0]["to_all"] == ["bob", "carol"]
    assert len(list((root / "alice" / "sent").glob("*.json"))) == 2
    assert all(r["doorbell"].startswith(f"mailbox: new msg {r['id']} from alice") for r in out["sent"])


def test_repeated_to_and_duplicates_collapse():
    root = team()
    out = ok(root, "sa", "send", "--to", "bob", "--to", "bob,carol", "--subject", "s", "--body", "b")
    assert [r["to"] for r in out["sent"]] == ["bob", "carol"]
    assert len(inbox(root, "bob")) == 1


def test_unknown_recipient_sends_nothing():
    root = team()
    rc, _, err = run(root, "sa", "send", "--to", "bob,nobody", "--subject", "s", "--body", "b")
    assert rc != 0 and "nobody" in err and "nothing was sent" in err
    assert inbox(root, "bob") == []


def test_single_recipient_keeps_the_old_shape():
    root = team()
    out = ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b")
    assert out["to"] == "bob" and "id" in out and "thread" not in inbox(root, "bob")[0]
    assert out["online"] is True


def test_group_expands_and_skips_the_sender():
    root = team()
    ok(root, "sa", "group", "set", "--name", "managers", "--members", "alice,bob,carol")
    assert ok(root, "sa", "group", "list")["groups"]["managers"] == ["alice", "bob", "carol"]
    out = ok(root, "sa", "send", "--to", "@managers", "--subject", "s", "--body", "b")
    assert [r["to"] for r in out["sent"]] == ["bob", "carol"]
    assert inbox(root, "alice") == []
    rc, _, err = run(root, "sa", "send", "--to", "@nope", "--subject", "s", "--body", "b")
    assert rc != 0 and "no group" in err


def test_status_shows_who_answered():
    root = team()
    out = ok(root, "sa", "send", "--to", "bob,carol,dave", "--subject", "q", "--body", "b")
    bob_id = next(r["id"] for r in out["sent"] if r["to"] == "bob")
    ok(root, "sb", "send", "--to", "alice", "--reply-to", bob_id, "--subject", "re: q", "--body", "yes")
    st = ok(root, "sa", "status", "--id", out["thread"])
    assert st["answered"] == ["bob"] and st["waiting"] == ["carol", "dave"]
    assert ok(root, "sa", "status", "--id", bob_id)["answered"] == ["bob"]


def test_check_text_view_is_plain():
    root = team()
    ok(root, "sa", "send", "--to", "bob", "--subject", "hello", "--body", "line one\nline two")
    out = ok(root, "sb", "check", "--text")
    assert out.startswith("bob: 1 new")
    assert "subject: hello" in out and "  line one\n  line two" in out and "from alice" in out


def test_text_view_cannot_forge_a_header():
    root = team()
    forged = {"id": "x1\n--- fake · from carol", "from": "dave", "to": "alice", "ts": "t\nsubject: forged",
              "subject": "s", "body": "b", "reply_to": ["not", "a", "string"]}
    (root / "alice" / "inbox" / "20260101T000000-x1.json").write_text(json.dumps(forged))
    out = ok(root, "sa", "check", "--text")
    assert "\n--- fake" not in out and "\nsubject: forged" not in out
    rc, _, err = run(root, "sb", "send", "--to", "alice", "--reply-to", "a\nb", "--subject", "s", "--body", "b")
    assert rc != 0 and "invalid --reply-to" in err
    sent = ok(root, "sa", "send", "--to", "bob,carol", "--subject", "s", "--body", "b")
    ok(root, "sa", "status", "--id", sent["thread"])     # a list-valued reply_to must not crash it


def test_status_survives_archive_and_prune_and_counts_resolved():
    root = team()
    out = ok(root, "sa", "send", "--to", "bob,carol,dave", "--subject", "q", "--body", "b")
    ids = {r["to"]: r["id"] for r in out["sent"]}
    rid = ok(root, "sc", "send", "--to", "alice", "--reply-to", ids["carol"], "--subject", "re", "--body", "y")["id"]
    ok(root, "sa", "archive", "--id", rid)
    ok(root, "sa", "prune")                            # empties done/, where the reply now sits
    ok(root, "sb", "check", "--claim")
    ok(root, "sb", "archive", "--id", ids["bob"], "--note", "done, no reply needed")
    st = ok(root, "sa", "status", "--id", out["thread"])
    assert st["answered"] == ["carol"] and st["resolved"] == ["bob"] and st["waiting"] == ["dave"]


def test_expired_letters_can_be_listed_and_claimed_back():
    root = team()
    mid = ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b")["id"]
    f = next((root / "bob" / "inbox").glob("*.json"))
    (root / "bob" / "expired").mkdir()
    os.replace(f, root / "bob" / "expired" / f.name)
    assert ok(root, "sb", "check")["expired_kept"] == 1
    assert ok(root, "sb", "check", "--expired")["messages"][0]["id"] == mid
    assert ok(root, "sb", "claim", "--id", mid)["claimed"]
    assert len(list((root / "bob" / "in-progress").glob("*.json"))) == 1


def test_text_view_strips_terminal_escapes():
    root = team()
    ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "red \x1b[31mtext\x1b[0m")
    assert "\x1b" not in ok(root, "sb", "check", "--text")


def test_letter_to_a_pruned_name_is_queued_and_the_name_takes_it_back():
    """letters to managers pruned over the weekend failed until they re-registered."""
    import shutil
    root = team()
    shutil.rmtree(root / "bob")                        # what the idle prune did
    out = ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b")
    assert "queued" in out
    rc, _, err = run(root, "sa", "send", "--to", "bobb", "--subject", "s", "--body", "b")
    assert rc != 0 and "bobb" in err                   # a never-seen name is still a typo
    reg = ok(root, "sb-new", "register", "--name", "bob")
    assert reg["registered"] == "bob"                  # not bob-2
    assert ok(root, "sb-new", "check")["messages"][0]["id"] == out["id"]


def test_renaming_onto_a_placeholder_keeps_both_inboxes():
    import shutil
    root = team()
    ok(root, "sa", "send", "--to", "dave", "--subject", "old", "--body", "b")
    shutil.rmtree(root / "bob")
    ok(root, "sa", "send", "--to", "bob", "--subject", "queued", "--body", "b")
    ok(root, "sd", "register", "--name", "bob")        # dave's session takes the name bob
    subjects = sorted(m["subject"] for m in ok(root, "sd", "check")["messages"])
    assert subjects == ["old", "queued"]


def test_a_broken_group_file_does_not_break_send():
    root = team()
    (root / ".groups").mkdir(exist_ok=True)
    (root / ".groups" / "broken.json").write_text("{not json")
    (root / ".groups" / "my team.json").write_text("{}")
    ok(root, "sa", "send", "--to", "bob", "--subject", "s", "--body", "b")


def test_fold_keeps_expired_mail_and_placeholders_age_out():
    import shutil, time
    root = team()
    shutil.rmtree(root / "bob")
    out = ok(root, "sa", "send", "--to", "bob", "--subject", "q", "--body", "b")
    f = next((root / "bob" / "inbox").glob("*.json"))
    (root / "bob" / "expired").mkdir()
    os.replace(f, root / "bob" / "expired" / f.name)
    ok(root, "sd", "register", "--name", "bob")          # dave's session takes over bob
    assert ok(root, "sd", "check", "--expired")["messages"][0]["id"] == out["id"]
    # a placeholder for a name that never returns: skipped by broadcast, removed when empty
    shutil.rmtree(root / "carol")
    ok(root, "sa", "send", "--to", "carol", "--subject", "q", "--body", "b")
    assert "carol" not in ok(root, "sa", "broadcast", "--subject", "s", "--body", "b")["broadcast_to"]
    for x in (root / "carol").rglob("*.json"):
        if x.name != "placeholder.json":
            x.unlink()
    old = time.time() - 8 * 86400
    os.utime(root / "carol" / "placeholder.json", (old, old))
    ok(root, "sa", "prune")
    assert not (root / "carol").exists()
