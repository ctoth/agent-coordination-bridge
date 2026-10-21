import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_bridge
import codex_adapter
from status_mailbox import Mailbox


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = agent_bridge.main(argv)
    return code, out.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.mailbox = Path(directory.name) / "mailbox.sqlite3"

    def test_earlier_provider_first_form_is_translated(self):
        seen = []
        def capture(args):
            seen.append(args)
            return None, 2
        with patch("agent_bridge.run", side_effect=capture):
            self.assertEqual(agent_bridge.main(["claude", "status", "--target", "exact-id"]), 2)
            agent_bridge.main(["codex", "status", "--message-id", "m"])
        self.assertEqual((seen[0].command, seen[0].provider, seen[0].target), ("status", "claude", "exact-id"))
        self.assertEqual((seen[1].provider, seen[1].request_id), ("codex", "m"))

    def test_help_exits_cleanly_in_both_forms(self):
        for argv in (["--help"], ["claude", "--help"]):
            with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as result:
                    agent_bridge.main(argv)
                self.assertEqual(result.exception.code, 0)

    def test_ask_needs_message_and_provider(self):
        for argv, error in ((["ask", "--provider", "claude"], "--message"), (["ask", "--message", "hi"], "--provider")):
            with self.subTest(argv=argv):
                code, out = run(argv)
                self.assertEqual(code, 1)
                self.assertIn(error, json.loads(out)["error"])

    def test_codex_collect_without_binary_reads_mailbox_only(self):
        with contextlib.closing(Mailbox(self.mailbox)) as mailbox:
            mailbox.claim("m-1", {"provider": "codex", "endpoint": "C:\\s.sock", "threadId": "t"})
        with patch.object(codex_adapter, "connect", side_effect=AssertionError("must not connect")):
            code, out = run(["collect", "--request-id", "m-1", "--mailbox", str(self.mailbox)])
        self.assertEqual((code, json.loads(out)["outcome"]), (0, "pending"))

    def test_codex_collect_refuses_a_different_socket(self):
        with contextlib.closing(Mailbox(self.mailbox)) as mailbox:
            mailbox.claim("m-1", {"provider": "codex", "endpoint": "c:\\recorded.sock", "threadId": "t"})
        with patch.object(codex_adapter, "connect", side_effect=AssertionError("must not connect")):
            code, out = run(["collect", "--request-id", "m-1", "--mailbox", str(self.mailbox),
                             "--codex", "C:\\codex.exe", "--socket", "C:\\other.sock"])
        self.assertEqual(code, 1)
        self.assertIn("differs", json.loads(out)["error"])

    def test_uncertain_codex_outcome_exits_2(self):
        @contextlib.contextmanager
        def lost(*args):
            raise codex_adapter.Uncertain("send outcome uncertain")
            yield
        with patch.object(codex_adapter, "connect", lost):
            code, out = run(["list", "--provider", "codex", "--codex", "C:\\codex.exe", "--socket", "C:\\s.sock"])
        self.assertEqual((code, json.loads(out)["outcome"]), (2, "uncertain"))


if __name__ == "__main__":
    unittest.main()
