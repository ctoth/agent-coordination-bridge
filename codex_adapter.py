"""Read loaded Codex threads and send one explicitly addressed question."""
import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

import websocket

import message


class Refused(RuntimeError):
    pass


class Uncertain(RuntimeError):
    pass


class RpcError(Refused):
    pass


class ProxySocket:
    """Socket-like byte stream for websocket-client; only the proxy is spawned."""

    def __init__(self, executable, endpoint, timeout):
        self.timeout = timeout
        self.data = queue.Queue()
        self.buffer = b""
        self.closed = False
        self.process = subprocess.Popen(
            [executable, "app-server", "proxy", "--sock", endpoint],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            # Preserve the proxy's error verbatim without reading credential files.
            stderr=None, bufsize=0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while block := self.process.stdout.read(4096):
                self.data.put(block)
        finally:
            self.data.put(b"")

    def gettimeout(self):
        return self.timeout

    def settimeout(self, timeout):
        self.timeout = timeout

    def recv(self, size):
        if not self.buffer:
            try:
                self.buffer = self.data.get(timeout=self.timeout)
            except queue.Empty as error:
                raise TimeoutError("proxy read timed out") from error
        result, self.buffer = self.buffer[:size], self.buffer[size:]
        return result

    def send(self, data):
        return self.process.stdin.write(data)

    def close(self):
        # Never terminate the server. This Popen owns only our byte-relay child.
        if self.closed:
            return
        self.closed = True
        self.process.stdin.close()
        if self.process.poll() is None:
            self.process.terminate()
        self.process.wait(timeout=5)
        self.reader.join(timeout=5)
        self.process.stdout.close()


class Rpc:
    def __init__(self, stream, timeout):
        self.stream, self.timeout, self.sequence = stream, timeout, 0

    def initialize(self):
        self.call("initialize", {
            "clientInfo": {"name": "codex_coordination", "version": "0.1"},
            "capabilities": {"experimentalApi": True},
        })
        self.stream.send(json.dumps({"method": "initialized"}))

    def call(self, method, params):
        self.sequence += 1
        request_id = self.sequence
        self.stream.send(json.dumps({"id": request_id, "method": method, "params": params}))
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("RPC response timed out")
            self.stream.settimeout(remaining)
            message = json.loads(self.stream.recv())
            if "method" in message:
                if "id" in message:
                    raise Refused("server requested an action; adapter cannot grant approvals")
                continue  # Unsubscribed notifications are not reply evidence.
            if message.get("id") != request_id:
                raise Refused("unexpected RPC response ID")
            if "error" in message:
                raise RpcError(json.dumps(message["error"]))
            return message["result"]


def loaded(rpc):
    result, cursor, cursors = [], None, set()
    while True:
        page = rpc.call("thread/loaded/list", {"cursor": cursor, "limit": 100})
        for thread_id in page["data"]:
            if thread_id in result:
                raise Refused("duplicate loaded thread ID")
            result.append(thread_id)
        cursor = page.get("nextCursor")
        if cursor is None:
            return result
        if cursor in cursors:
            raise Refused("repeated loaded-list cursor")
        cursors.add(cursor)


def snapshot(rpc, thread_id, active_only=False):
    thread = rpc.call("thread/read", {"threadId": thread_id, "includeTurns": False})["thread"]
    if thread["id"] != thread_id:
        raise Refused("thread identity mismatch")
    if thread.get("ephemeral"):
        if active_only:
            return thread, []
        raise Refused("ephemeral threads do not support reply polling")
    if active_only and thread.get("status", {}).get("type") != "active":
        return thread, []
    turns = rpc.call("thread/turns/list", {
        "threadId": thread_id, "limit": 1, "sortDirection": "desc", "itemsView": "full",
    })["data"]
    return thread, turns


def verify(thread, turns, target, active):
    for field in ("id", "sessionId", "cwd", "model"):
        if thread.get(field) != target[field]:
            raise Refused(f"{field} changed or mismatched")
    status = thread.get("status", {})
    if status.get("type") not in ({"active"} if active else {"active", "idle"}):
        raise Refused("target is not a loaded usable runtime")
    if active and (status.get("activeFlags") or thread.get("canAcceptDirectInput") is not True):
        raise Refused("target is waiting or direct input is unavailable")
    if len(turns) != 1 or turns[0].get("id") != target["turnId"]:
        raise Refused("active/latest turn identity mismatch")
    if active and turns[0].get("status") != "inProgress":
        raise Refused("expected turn is no longer in progress")


def verify_idle(thread, target):
    for field in ("id", "sessionId", "cwd", "model"):
        if thread.get(field) != target[field]:
            raise Refused(f"{field} changed or mismatched")
    if thread.get("ephemeral"):
        raise Refused("ephemeral threads do not support reply polling")
    if thread.get("status", {}).get("type") != "idle":
        raise Refused("target is not idle; pass its active turn ID to steer it instead")
    if thread.get("canAcceptDirectInput") is not True:
        raise Refused("direct input is unavailable")


def claim(mailbox, receipts, request):
    binding = {field: request[field] for field in ("endpoint", "threadId", "messageId")}
    key = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    # Receipt files from earlier versions still block their message IDs. They are
    # read, never written or deleted; the mailbox now arbitrates new claims.
    if (receipts / ("message-" + key + ".json")).exists():
        raise Refused("this endpoint/thread/message was already claimed; do not resend")
    for legacy in receipts.glob("*.json") if receipts.is_dir() else ():
        if legacy.name.startswith("message-"):
            continue
        try:
            previous = json.loads(legacy.read_text(encoding="utf-8"))
            if not isinstance(previous, dict) or not all(
                previous.get(field) for field in ("endpoint", "threadId", "messageId")
            ):
                raise ValueError("missing receipt identity")
        except (OSError, ValueError) as error:
            raise Refused("legacy receipt unreadable; preserve it and inspect before sending") from error
        if all(previous.get(field) == value for field, value in binding.items()):
            raise Refused("this endpoint/thread/message was already claimed; do not resend")
    try:
        mailbox.claim(request["messageId"], {"provider": "codex", **request})
    except ValueError as error:
        raise Refused("this request ID was already claimed; do not resend") from error


def find_reply(items, old_items, message_id, prompt):
    received = False
    for item in items:
        if item.get("id") in old_items:
            continue
        if item.get("type") == "userMessage" and item.get("clientId") == message_id:
            received = any(c.get("text") == prompt for c in item.get("content", []))
        if received and item.get("type") == "agentMessage" and message.reply(item.get("text", ""), message_id):
            return item["text"]
    return None


def find_turn(rpc, thread_id, matches):
    cursor, cursors = None, set()
    while True:
        page = rpc.call("thread/turns/list", {
            "threadId": thread_id, "cursor": cursor, "limit": 20, "sortDirection": "desc", "itemsView": "full",
        })
        for turn in page["data"]:
            if matches(turn):
                return turn
        cursor = page.get("nextCursor")
        if cursor is None:
            return None
        if cursor in cursors:
            raise Refused("repeated turns-list cursor")
        cursors.add(cursor)


def inquire(rpc, endpoint, target, message_id, mailbox, receipts, timeout, interval=1,
            question=message.STATUS_QUESTION):
    """Steer the given active turn, or start a turn when target has no turnId."""
    message_id = str(uuid.UUID(message_id))
    idle = target.get("turnId") is None
    for field in ("id", "sessionId") if idle else ("id", "sessionId", "turnId"):
        if str(uuid.UUID(target[field])) != target[field]:
            raise Refused(f"{field} must be an exact canonical UUID")
    if target["id"] not in loaded(rpc):
        raise Refused("exact thread is not loaded on this server")
    if idle:
        verify_idle(snapshot(rpc, target["id"], active_only=True)[0], target)
        old_items = set()
    else:
        thread, turns = snapshot(rpc, target["id"])
        verify(thread, turns, target, active=True)
        old_items = {item["id"] for item in turns[0]["items"]}
    prompt = message.compose(message_id, question, "Reply in this thread; no other delivery is needed.")
    claim(mailbox, receipts, {"endpoint": endpoint, "threadId": target["id"], "messageId": message_id,
                              "turnId": target.get("turnId"), "prompt": prompt, "oldItems": sorted(old_items)})
    # After claim, no error or timeout permits an automatic second send.
    send = {"threadId": target["id"], "clientUserMessageId": message_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}]}
    try:
        if idle:
            # turn/start has no expected-turn guard; if a turn began after our check,
            # the server steers into it. Either way it returns the turn to poll.
            turn_id = rpc.call("turn/start", send)["turn"]["id"]
        else:
            turn_id = rpc.call("turn/steer", {**send, "expectedTurnId": target["turnId"]}).get("turnId")
    except Exception as error:
        raise Uncertain(f"send outcome uncertain; request retained: {error}") from error
    try:
        if not idle and turn_id != target["turnId"]:
            raise Refused("steer returned another turn ID")
        mailbox.record(message_id, turnId=turn_id)
        target = {**target, "turnId": turn_id}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread, turns = snapshot(rpc, target["id"])
            verify(thread, turns, target, active=False)
            reply = find_reply(turns[0]["items"], old_items, message_id, prompt)
            if reply is not None:
                mailbox.store_reply(message_id, {"text": reply, "source": "codex_agent_message",
                                                 "threadId": target["id"], "turnId": turn_id})
                return {"outcome": "reply_observed", "endpoint": endpoint,
                        "threadId": target["id"], "turnId": turn_id,
                        "send": "turn/start" if idle else "turn/steer",
                        "messageId": message_id, "reply": reply}
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise TimeoutError("no correlated reply observed before deadline; run collect later")
    except Exception as error:
        raise Uncertain(f"send/reply outcome uncertain; request retained: {error}") from error


def collect(rpc, mailbox, message_id):
    """Read-only: look for a late reply in the recorded turn; never sends."""
    request = mailbox.request(message_id)
    if request["provider"] != "codex":
        raise Refused("not a Codex request")
    if mailbox.collect(message_id)["outcome"] == "pending":
        turn_id = request.get("turnId")
        if turn_id:
            turn = find_turn(rpc, request["threadId"], lambda turn: turn.get("id") == turn_id)
            if turn is None:
                raise Refused("recorded turn is no longer listed")
        else:
            # A lost send response leaves no turn ID; the turn holding our client message ID is the one.
            turn = find_turn(rpc, request["threadId"], lambda turn: any(
                item.get("type") == "userMessage" and item.get("clientId") == message_id
                for item in turn.get("items", [])))
        reply = turn and find_reply(turn.get("items", []), set(request["oldItems"]), message_id, request["prompt"])
        if reply:
            mailbox.store_reply(message_id, {"text": reply, "source": "codex_agent_message",
                                             "threadId": request["threadId"], "turnId": turn["id"]})
    return mailbox.collect(message_id)


def endpoint_of(socket):
    return os.path.normcase(os.path.normpath(socket))


@contextlib.contextmanager
def connect(codex, endpoint, timeout):
    if not (math.isfinite(timeout) and 0 < timeout <= 60):
        raise Refused("timeout must be finite and between 0 and 60 seconds")
    if not Path(endpoint).is_absolute() or not Path(codex).is_absolute():
        raise Refused("socket and codex paths must be absolute")
    pipe = ProxySocket(codex, endpoint, timeout)
    try:
        stream = websocket.WebSocket()
        # The URL supplies HTTP handshake fields only. All bytes use the explicit pipe.
        stream.connect("ws://localhost/", socket=pipe, timeout=timeout,
                       suppress_origin=True, redirect_limit=0)
        if stream.getstatus() != 101:
            raise Refused("server did not accept WebSocket upgrade; redirects forbidden")
        rpc = Rpc(stream, timeout)
        rpc.initialize()
        yield rpc
    finally:
        pipe.close()


def listing(rpc, endpoint):
    result = []
    for thread_id in loaded(rpc):
        thread, turns = snapshot(rpc, thread_id, active_only=True)
        result.append({k: thread.get(k) for k in (
            "id", "sessionId", "cwd", "model", "status", "parentThreadId", "forkedFromId", "gitInfo",
            "ephemeral", "canAcceptDirectInput"
        )} | {"latestTurnId": turns[0]["id"] if turns else None})
    return {"endpoint": endpoint, "threads": result}


def main():
    import agent_bridge
    return agent_bridge.main(["codex", *sys.argv[1:]])


if __name__ == "__main__":
    raise SystemExit(main())
