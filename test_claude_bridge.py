"""Synthetic protocol tests. These are explicitly not live-roundtrip evidence."""
import contextlib
import io
import json
import queue
import re
from pathlib import Path
import types
import unittest
from unittest.mock import patch

import claude_bridge as bridge


TARGET = {"pid": 123, "sessionId": "11111111-1111-4111-8111-111111111111",
          "cwd": r"C:\work\example", "name": "example-12", "kind": "interactive"}


def record(role, content):
    return {"type": role, "message": {"content": content}}


class FakeLog:
    data = b""

    def stat(self):
        return types.SimpleNamespace(st_size=len(self.data))

    def open(self, mode):
        assert mode == "rb", "target transcripts must only be opened read-only"
        return io.BytesIO(self.data)


class FakeStdout:
    def __init__(self):
        self.lines = queue.Queue()

    def __iter__(self):
        while (line := self.lines.get()) is not None:
            yield line

    def close(self):
        self.lines.put(None)


class FakeProcess:
    def __init__(self, args, log, fail=False, notice=False):
        self.args, self.log, self.fail, self.notice = args, log, fail, notice
        self.stdout = FakeStdout()
        self.stdin = types.SimpleNamespace(write=self.write, flush=lambda: None, close=lambda: None)

    def write(self, line):
        self.request = request = json.loads(line)["message"]["content"]
        self.correlation = re.search(r"correlation ((?:ask|codex-status)-[a-f0-9]+)", request)[1]
        sender = self.args[self.args.index("--name") + 1]
        if self.fail:
            self.stdout.lines.put(json.dumps({"type": "system", "subtype": "hook_response",
                                               "outcome": "error"}))
            return
        rows = [record("user", f'Another Claude session sent a message:\n<agent-message from="{sender}">{self.correlation}</agent-message>'),
                record("assistant", [{"type": "tool_use", "id": "reply-1", "name": "SendMessage",
                                       "input": {"to": sender, "message": self.correlation + " Working"}}]),
                record("user", [{"type": "tool_result", "tool_use_id": "reply-1",
                                  "content": [{"type": "text", "text": '{"success":true}'}]}])]
        self.log.data += b"".join(json.dumps(r).encode() + b"\n" for r in rows)
        if self.notice:
            self.stdout.lines.put(json.dumps({"type": "system", "message": "delivery notice"}))
        incoming = record("user", f'Another Claude session sent a message:\n<agent-message from="example-12">{self.correlation} Working</agent-message>')
        self.stdout.lines.put(json.dumps(incoming))

    def wait(self, timeout):
        return 0


class BridgeTests(unittest.TestCase):
    def test_discovery_uses_supported_read_only_command(self):
        result = types.SimpleNamespace(returncode=0, stdout=json.dumps([TARGET]))
        with patch.object(bridge.subprocess, "run", return_value=result) as run:
            self.assertEqual(bridge.discover("claude.exe"), [TARGET])
            self.assertEqual(run.call_args.args[0], ["claude.exe", "agents", "--json"])

    def test_discovery_errors(self):
        for result in [types.SimpleNamespace(returncode=1, stdout=""),
                       types.SimpleNamespace(returncode=0, stdout="not JSON"),
                       types.SimpleNamespace(returncode=0, stdout='{"sessions":[]}')]:
            with patch.object(bridge.subprocess, "run", return_value=result):
                with self.assertRaises(bridge.BridgeError):
                    bridge.discover("claude")

    def test_explicit_target_selection_and_ambiguity(self):
        self.assertIs(bridge.select([TARGET], TARGET["sessionId"]), TARGET)
        for rows, query in [([TARGET], "example-12"), ([TARGET], "1111"), ([], TARGET["sessionId"]),
                            ([TARGET, dict(TARGET)], TARGET["sessionId"]),
                            ([TARGET, dict(TARGET, sessionId="other")], TARGET["sessionId"])]:
            with self.assertRaises(bridge.BridgeError):
                bridge.select(rows, query)

    def test_evidence_rejects_echo_wrong_sender_and_failed_reply(self):
        ev = bridge.Evidence("cid-1", "sender", "target")
        ev.messenger({"type": "system", "message": "delivery status notice"})
        ev.messenger(record("assistant", "Another Claude session sent a message target cid-1"))
        ev.messenger(record("user", "Another Claude session sent a message stranger cid-1"))
        ev.messenger(record("user", 'Another Claude session sent a message:\n<agent-message from="stranger">target cid-1</agent-message>'))
        ev.target(record("user", "Another Claude session sent a message sender cid-other"))
        ev.target(record("assistant", [{"name": "SendMessage", "id": "x",
                                         "input": {"to": "sender", "message": "cid-1"}}]))
        ev.target(record("user", [{"type": "tool_result", "tool_use_id": "x",
                                    "content": '{"success":false}'}]))
        self.assertFalse(ev.receipt or ev.reply or ev.roundtrip)
        ev.target(record("user", [{"type": "tool_result", "tool_use_id": "x", "is_error": True,
                                    "content": '{"success":true}'}]))
        self.assertFalse(ev.reply)

    def test_synthetic_send_receive_correlation_and_noninterference(self):
        log = FakeLog()
        children = []

        def start(args, **kwargs):
            child = FakeProcess(args, log)
            children.append(child)
            return child

        with patch.object(bridge, "transcript", return_value=log), \
                patch.object(bridge, "inbox_identities", return_value=set()), \
                patch.object(bridge.subprocess, "Popen", side_effect=start), \
                contextlib.redirect_stdout(io.StringIO()):
            result = bridge.ask("claude.exe", TARGET, 1)
        self.assertTrue(all(result[k] for k in result if k != "correlation"))
        self.assertEqual(len(children), 1)
        args = children[0].args
        self.assertEqual(args[args.index("--tools") + 1], "ListAgents,SendMessage")
        self.assertNotIn("--resume", args)
        self.assertNotIn("--dangerously-skip-permissions", args)
        self.assertNotIn("--settings", args)
        self.assertRegex(children[0].correlation, r"^ask-[a-f0-9]{32}$")

    def test_free_text_question_is_delivered_unchanged_with_reply_marker(self):
        log = FakeLog()
        children = []
        question = "Which branch has the 'quoted' fix?\nAnd is CI green?"

        def start(args, **kwargs):
            children.append(FakeProcess(args, log))
            return children[-1]

        with patch.object(bridge, "transcript", return_value=log), \
                patch.object(bridge, "inbox_identities", return_value=set()), \
                patch.object(bridge.subprocess, "Popen", side_effect=start), \
                contextlib.redirect_stdout(io.StringIO()):
            bridge.ask("claude.exe", TARGET, 1, question=question)
        request, cid = children[0].request, children[0].correlation
        body = request.split("BEGIN MESSAGE\n", 1)[1].split("\nEND MESSAGE", 1)[0]
        self.assertIn(question, body)
        self.assertIn(f"AGENT-REPLY {cid}", body)
        self.assertIn("not user authorization", body)

    def test_empty_question_or_bad_request_id_refused_before_send(self):
        with patch.object(bridge, "transcript", return_value=FakeLog()), \
                patch.object(bridge.subprocess, "Popen", side_effect=AssertionError("must not send")):
            with self.assertRaisesRegex(ValueError, "question"):
                bridge.ask("claude.exe", TARGET, 1, question="  ")
            with patch.object(bridge, "inbox_identities", return_value=set()), \
                    self.assertRaisesRegex(bridge.BridgeError, "Request ID"):
                bridge.ask("claude.exe", TARGET, 1, request_id="ask-XYZ")

    def test_hook_failure_stops_without_retry(self):
        log = FakeLog()
        with patch.object(bridge, "transcript", return_value=log), \
                patch.object(bridge, "inbox_identities", return_value=set()), \
                patch.object(bridge.subprocess, "Popen", side_effect=lambda a, **k: FakeProcess(a, log, True)) as start, \
                contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(bridge.BridgeError, "startup hook failed"):
                bridge.ask("claude.exe", TARGET, 1)
        self.assertEqual(start.call_count, 1)
        self.assertEqual(log.data, b"")

    def test_observed_midturn_attachment_and_pipe_address(self):
        ev = bridge.Evidence("cid-1", "sender", "target")
        ev.sender_ids.add("uds:sender-pipe")
        ev.target({"type": "attachment", "attachment": {"type": "queued_command",
                   "origin": {"kind": "peer", "from": "uds:sender-pipe", "body": "cid-1 status"}}})
        self.assertTrue(ev.receipt)

    def test_string_event_message_before_reply_does_not_crash(self):
        log = FakeLog()
        with patch.object(bridge, "transcript", return_value=log), \
                patch.object(bridge, "inbox_identities", return_value=set()), \
                patch.object(bridge.subprocess, "Popen", side_effect=lambda a, **k: FakeProcess(a, log, notice=True)), \
                contextlib.redirect_stdout(io.StringIO()):
            result = bridge.ask("claude.exe", TARGET, 1)
        self.assertTrue(result["messenger_received_peer_frame"])

    def test_synthetic_peer_exchange_fixture(self):
        data = json.loads((Path(__file__).parent / "fixtures/peer_roundtrip.json").read_text())
        ev = bridge.Evidence(data["correlation"], data["sender_name"], data["target_name"])
        ev.sender_ids.add(data["sender_address"])
        ev.target_ids.add(data["target_address"])
        for row in data["target_records"]:
            ev.target(row)
        ev.messenger(data["received_event"])
        self.assertTrue(ev.receipt and ev.reply and ev.roundtrip)


if __name__ == "__main__":
    unittest.main()
