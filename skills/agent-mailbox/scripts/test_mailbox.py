#!/usr/bin/env python3
"""Regression tests for mailbox.py retention + name-safety logic.

Run directly: python3 skills/agent-mailbox/scripts/test_mailbox.py
Pure stdlib; each test uses an isolated tmp ROOT.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mailbox  # noqa: E402

STALE_S = 7 * 3600  # older than the 6h default peer_stale_h


def make_mailbox(root: Path, name: str, hb_age_s: float = 0, inbox_msgs: int = 0) -> Path:
    d = root / name
    for box in mailbox.BOXES:
        (d / box).mkdir(parents=True)
    hb = d / "heartbeat.json"
    hb.write_text(json.dumps({"name": name, "last_seen": "x"}))
    for i in range(inbox_msgs):
        (d / "inbox" / f"m{i}.json").write_text(json.dumps({"id": f"m{i}", "body": "x"}))
    if hb_age_s:
        old = time.time() - hb_age_s
        os.utime(hb, (old, old))
    return d


class PruneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._orig_root = mailbox.ROOT
        mailbox.ROOT = self.root

    def tearDown(self):
        mailbox.ROOT = self._orig_root
        self._tmp.cleanup()

    def prune(self):
        return mailbox.prune_run(max_age_h=24, peer_stale_h=6)

    def test_foreign_dir_without_heartbeat_survives(self):
        """A directory that is not a mailbox must NEVER be deleted, however old."""
        foreign = self.root / "important-repo"
        (foreign / "src").mkdir(parents=True)
        (foreign / "src" / "main.go").write_text("package main")
        res = self.prune()
        self.assertTrue((foreign / "src" / "main.go").exists())
        self.assertEqual(res["removed_dead_peers"], [])

    def test_stale_peer_with_unread_mail_survives(self):
        """Undelivered messages must never vanish because someone registered."""
        d = make_mailbox(self.root, "long-runner", hb_age_s=STALE_S, inbox_msgs=1)
        res = self.prune()
        self.assertTrue((d / "inbox" / "m0.json").exists())
        self.assertEqual(res["removed_dead_peers"], [])

    def test_stale_empty_peer_is_removed(self):
        d = make_mailbox(self.root, "ghost", hb_age_s=STALE_S)
        res = self.prune()
        self.assertFalse(d.exists())
        self.assertEqual(res["removed_dead_peers"], ["ghost"])

    def test_fresh_peer_survives(self):
        d = make_mailbox(self.root, "alive")
        self.prune()
        self.assertTrue(d.exists())

    def test_protect_overrides_staleness(self):
        d = make_mailbox(self.root, "me", hb_age_s=STALE_S)
        mailbox.prune_run(max_age_h=24, peer_stale_h=6, protect="me")
        self.assertTrue(d.exists())

    def test_expired_messages_pruned_but_dir_kept_when_fresh(self):
        d = make_mailbox(self.root, "busy", inbox_msgs=1)
        msg = d / "inbox" / "m0.json"
        old = time.time() - 25 * 3600
        os.utime(msg, (old, old))
        res = self.prune()
        self.assertFalse(msg.exists())
        self.assertTrue(d.exists())
        self.assertEqual(res["expired_messages"], 1)


class NameValidationTests(unittest.TestCase):
    def test_valid_names_pass(self):
        for n in ("trace-harvest", "e2e-improver-2", "a", "x" * 64, "A1._-b"):
            self.assertEqual(mailbox.valid_name(n), n)

    def test_bad_names_exit(self):
        for n in ("../outside", "/abs/path", "a/b", "..", "ok\nInjected: line",
                  "-leading-dash", ".hidden", "", "x" * 65):
            with self.assertRaises(SystemExit, msg=repr(n)):
                mailbox.valid_name(n)

    def test_bad_message_id_exits(self):
        # '/' and glob metacharacters are the dangerous ones; bare dots are
        # harmless inside a single-level glob pattern.
        for raw in ("*", "a/b", "x*y", "m?", "[ab]", ""):
            with self.assertRaises(SystemExit, msg=repr(raw)):
                mailbox.msg_id_glob(raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
