"""Synthetic protocol tests. Nothing here connects to a real Codex server."""
import base64
import copy
import hashlib
import json
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import patch

import websocket

from codex_adapter import ProxySocket, Refused, Rpc, RpcError, Uncertain, inquire, loaded, snapshot


TARGET = {
    "id": "11111111-1111-4111-8111-111111111111",
    "sessionId": "22222222-2222-4222-8222-222222222222",
    "turnId": "33333333-3333-4333-8333-333333333333",
    "cwd": "C:\\test\\worktree", "model": "test-model",
}
MESSAGE = "44444444-4444-4444-8444-444444444444"
ENDPOINT = "C:\\test\\existing.sock"


class FakeServer:
    def __init__(self):
        self.thread = {**TARGET, "status": {"type": "active", "activeFlags": []},
                       "canAcceptDirectInput": True}
        self.turn = {"id": TARGET["turnId"], "status": "inProgress", "items": []}
        self.calls = []
        self.sent = 0
        self.ids = [TARGET["id"]]
        self.error = None
        self.after_send = lambda: None

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == "initialize":
            return {"userAgent": "synthetic-server"}
        if method == "thread/loaded/list":
            return {"data": self.ids, "nextCursor": None}
        if method == "thread/read":
            return {"thread": copy.deepcopy(self.thread)}
        if method == "thread/turns/list":
            return {"data": [copy.deepcopy(self.turn)], "nextCursor": None}
        if method != "turn/steer":
            raise AssertionError(f"unexpected mutation {method}")
        self.sent += 1
        if self.error:
            raise self.error
        if params["expectedTurnId"] != self.turn["id"]:
            raise RpcError("expected active turn mismatch")
        self.turn["items"].extend([
            {"id": "input", "type": "userMessage", "clientId": params["clientUserMessageId"],
             "content": params["input"]},
            {"id": "reply", "type": "agentMessage",
             "text": f"STATUS-REPLY {params['clientUserMessageId']} Synthetic status only."},
        ])
        self.after_send()
        return {"turnId": params["expectedTurnId"]}


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.receipts = Path(self.directory.name)
        self.server = FakeServer()

    def ask(self, server=None, message=MESSAGE):
        return inquire(server or self.server, ENDPOINT, TARGET, message,
                       self.receipts, timeout=0.01, interval=0.001)

    def test_correlated_reply_and_exact_guard(self):
        self.assertEqual(self.ask()["outcome"], "reply_observed")
        mutations = [(m, p) for m, p in self.server.calls if m == "turn/steer"]
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0][1]["expectedTurnId"], TARGET["turnId"])
        self.assertEqual(mutations[0][1]["clientUserMessageId"], MESSAGE)
        self.assertEqual(set(m for m, _ in self.server.calls),
                         {"thread/loaded/list", "thread/read", "thread/turns/list", "turn/steer"})

    def test_reply_marker_followed_by_newline(self):
        self.server.after_send = lambda: self.server.turn["items"][-1].update(
            text=f"STATUS-REPLY {MESSAGE}\nTask: Synthetic test.\nBlockers: None.")
        self.assertEqual(self.ask()["outcome"], "reply_observed")

    def test_reply_marker_must_end_at_id_boundary(self):
        self.server.after_send = lambda: self.server.turn["items"][-1].update(
            text=f"STATUS-REPLY {MESSAGE}extra Synthetic test.")
        with self.assertRaises(Uncertain):
            self.ask()

    def test_unloaded_target(self):
        self.server.ids = []
        with self.assertRaises(Refused):
            self.ask()
        self.assertEqual(self.server.sent, 0)

    def test_discovery_does_not_request_unmaterialized_idle_history(self):
        self.server.thread["status"] = {"type": "idle"}
        thread, turns = snapshot(self.server, TARGET["id"], active_only=True)
        self.assertEqual(thread["status"]["type"], "idle")
        self.assertEqual(turns, [])
        self.assertEqual([m for m, _ in self.server.calls], ["thread/read"])

    def test_ephemeral_thread_is_listable_but_not_pollable(self):
        self.server.thread["ephemeral"] = True
        self.assertEqual(snapshot(self.server, TARGET["id"], active_only=True)[1], [])
        with self.assertRaisesRegex(Refused, "ephemeral"):
            self.ask()
        self.assertEqual(self.server.sent, 0)
        self.assertNotIn("thread/turns/list", [m for m, _ in self.server.calls])

    def test_stale_or_unrelated_identity(self):
        for field in ("id", "sessionId", "cwd", "model"):
            with self.subTest(field=field):
                server = FakeServer()
                server.thread[field] = "another"
                with self.assertRaises(Refused):
                    self.ask(server)
                self.assertEqual(server.sent, 0)

    def test_stale_turn_and_unloaded_status(self):
        self.server.turn["id"] = "other-turn"
        with self.assertRaises(Refused):
            self.ask()
        self.server.turn["id"] = TARGET["turnId"]
        self.server.thread["status"] = {"type": "notLoaded"}
        with self.assertRaises(Refused):
            self.ask()
        self.assertEqual(self.server.sent, 0)

    def test_waiting_or_missing_direct_input_capability(self):
        for change in ({"status": {"type": "active", "activeFlags": ["waitingOnApproval"]}},
                       {"canAcceptDirectInput": None}):
            server = FakeServer()
            server.thread.update(change)
            with self.assertRaises(Refused):
                self.ask(server)
            self.assertEqual(server.sent, 0)

    def test_permission_rejection_never_retried(self):
        self.server.error = RpcError("permission denied")
        with self.assertRaisesRegex(Uncertain, "permission denied"):
            self.ask()
        with self.assertRaisesRegex(Refused, "already claimed"):
            self.ask()
        self.assertEqual(self.server.sent, 1)

    def test_turn_race_rejection_never_starts_another_turn(self):
        self.server.error = RpcError("expected active turn mismatch")
        with self.assertRaisesRegex(Uncertain, "turn mismatch"):
            self.ask()
        self.assertEqual(self.server.sent, 1)

    def test_uncertain_send_survives_new_invocation(self):
        self.server.error = TimeoutError("lost response after write")
        with self.assertRaises(Uncertain):
            self.ask()
        fresh = FakeServer()
        with self.assertRaisesRegex(Refused, "already claimed"):
            self.ask(fresh)
        self.assertEqual(fresh.sent, 0)

    def test_duplicate_even_with_new_message_id(self):
        self.ask()
        with self.assertRaisesRegex(Refused, "already claimed"):
            self.ask(message="55555555-5555-4555-8555-555555555555")
        self.assertEqual(self.server.sent, 1)

    def test_wrong_reply_or_receipt_is_not_success(self):
        for field, value in (("text", "unrelated reply"), ("clientId", "other-message")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                self.receipts = Path(directory)
                server = FakeServer()
                def corrupt():
                    item = server.turn["items"][1 if field == "text" else 0]
                    item[field] = value
                server.after_send = corrupt
                with self.assertRaises(Uncertain):
                    self.ask(server)

    def test_new_turn_after_send_is_uncertain(self):
        self.server.after_send = lambda: self.server.turn.update(id="new-turn")
        with self.assertRaises(Uncertain):
            self.ask()
        self.assertEqual(self.server.sent, 1)

    def test_duplicate_loaded_identity_rejected(self):
        self.server.ids *= 2
        with self.assertRaises(Refused):
            loaded(self.server)

    def test_preflight_permission_failure_does_not_send(self):
        with patch.object(self.server, "call", side_effect=PermissionError("read denied")):
            with self.assertRaises(PermissionError):
                self.ask()
        self.assertEqual(self.server.sent, 0)
        self.assertEqual(list(self.receipts.iterdir()), [])

    def test_historical_correlated_reply_is_not_live_proof(self):
        old = {"id": "old-reply", "type": "agentMessage", "text": f"STATUS-REPLY {MESSAGE} Old."}
        self.server.turn["items"] = [old]
        self.server.after_send = lambda: self.server.turn.update(items=[old])
        with self.assertRaises(Uncertain):
            self.ask()


class JsonStream:
    def __init__(self, responses):
        self.responses, self.sent = responses, []

    def send(self, value):
        self.sent.append(json.loads(value))

    def recv(self):
        return json.dumps(self.responses.pop(0))

    def settimeout(self, timeout):
        pass


class RpcTests(unittest.TestCase):
    def test_wrong_response_id_is_rejected(self):
        with self.assertRaisesRegex(Refused, "response ID"):
            Rpc(JsonStream([{"id": 99, "result": {}}]), 1).call("thread/read", {})

    def test_notifications_are_not_replies(self):
        stream = JsonStream([{"method": "item/completed", "params": {}},
                             {"id": 1, "result": {"ok": True}}])
        self.assertEqual(Rpc(stream, 1).call("thread/read", {}), {"ok": True})

    def test_approval_request_is_not_answered(self):
        stream = JsonStream([{"id": "approval", "method": "item/commandExecution/requestApproval"}])
        with self.assertRaises(Refused):
            Rpc(stream, 1).call("turn/steer", {})
        self.assertEqual(len(stream.sent), 1)


class MemoryChild:
    """A fake proxy child with an in-memory RFC6455 server; no network/socket."""
    def __init__(self, server):
        self.server, self.output = server, queue.Queue()
        self.stdin = self
        self.stdout = self
        self.handshake = False
        self.stopped = False
        self.pending = b""

    def write(self, data):
        data = bytes(data)
        if not self.handshake:
            key = next(line.split(b": ", 1)[1] for line in data.split(b"\r\n")
                       if line.lower().startswith(b"sec-websocket-key:"))
            accept = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
            self.output.put(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            self.handshake = True
        else:
            # websocket-client sends a full masked frame to this blocking pipe.
            self.pending += data
            frame = websocket.ABNF  # The library's frame parser checks the client wire encoding.
            from websocket._abnf import frame_buffer
            parsed = frame_buffer(self._frame_read, False).recv_frame()
            request = json.loads(parsed.data)
            if "id" in request:
                response = {"id": request["id"], "result": self.server.call(request["method"], request["params"])}
                outgoing = frame.create_frame(json.dumps(response), frame.OPCODE_TEXT)
                outgoing.mask_value = 0
                self.output.put(outgoing.format())
        return len(data)

    def _frame_read(self, size):
        value, self.pending = self.pending[:size], self.pending[size:]
        return value

    def read(self, size):
        return self.output.get()

    def close(self):
        self.output.put(b"")

    def poll(self):
        return 0 if self.stopped else None

    def terminate(self):
        self.stopped = True
        self.output.put(b"")

    def wait(self, timeout):
        return 0


class WireTests(unittest.TestCase):
    setUp = AdapterTests.setUp
    ask = AdapterTests.ask
    def test_proxy_websocket_jsonrpc_roundtrip_in_memory(self):
        child = MemoryChild(self.server)
        with patch("codex_adapter.subprocess.Popen", return_value=child) as spawn:
            pipe = ProxySocket("C:\\test\\codex.exe", ENDPOINT, 1)
            try:
                stream = websocket.WebSocket()
                stream.connect("ws://localhost/", socket=pipe, timeout=1,
                               suppress_origin=True, redirect_limit=0)
                rpc = Rpc(stream, 1)
                rpc.initialize()
                self.assertEqual(self.ask(rpc)["outcome"], "reply_observed")
                self.assertEqual(spawn.call_args.args[0],
                                 ["C:\\test\\codex.exe", "app-server", "proxy", "--sock", ENDPOINT])
            finally:
                pipe.close()
        self.assertTrue(child.stopped)

    def test_proxy_connection_denial_closes_only_owned_child_once(self):
        child = MemoryChild(self.server)
        child.write = lambda data: (child.output.put(b"") or len(data))
        with patch("codex_adapter.subprocess.Popen", return_value=child) as spawn:
            pipe = ProxySocket("C:\\test\\codex.exe", ENDPOINT, 1)
            stream = websocket.WebSocket()
            with self.assertRaises(websocket.WebSocketConnectionClosedException):
                stream.connect("ws://localhost/", socket=pipe, timeout=1, redirect_limit=0)
            pipe.close()  # Handshake failure already closed it; cleanup is idempotent.
        self.assertTrue(child.stopped)
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.server.calls, [])


if __name__ == "__main__":
    unittest.main()
