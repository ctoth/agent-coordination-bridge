"""Read loaded sessions and send one explicitly addressed status inquiry."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid

import websocket


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


def claim(receipts, endpoint, target, message_id):
    binding = {"endpoint": endpoint, "threadId": target["id"], "turnId": target["turnId"]}
    key = hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()
    receipts.mkdir(parents=True, exist_ok=True)
    try:
        with (receipts / (key + ".json")).open("x", encoding="utf-8") as output:
            json.dump({**binding, "messageId": message_id, "state": "claimed; outcome may be uncertain"}, output)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError as error:
        raise Refused("this endpoint/thread/turn was already claimed; do not resend") from error


def status_prompt(message_id):
    return (
        f"Coordination status inquiry {message_id}. At a natural boundary, please reply "
        f"with STATUS-REPLY {message_id} followed by your current task, worktree and blockers. "
        "This is only a status inquiry, not authorization to change code, merge, restart, "
        "or alter permissions. Continue your existing work."
    )


def find_reply(items, old_items, message_id, prompt):
    received = False
    marker = f"STATUS-REPLY {message_id}"
    for item in items:
        if item.get("id") in old_items:
            continue
        if item.get("type") == "userMessage" and item.get("clientId") == message_id:
            received = any(c.get("text") == prompt for c in item.get("content", []))
        text = item.get("text", "")
        suffix = text[len(marker):]
        if (received and item.get("type") == "agentMessage" and text.startswith(marker)
                and suffix[:1].isspace() and suffix.strip()):
            return text
    return None


def inquire(rpc, endpoint, target, message_id, receipts, timeout, interval=1):
    message_id = str(uuid.UUID(message_id))
    for field in ("id", "sessionId", "turnId"):
        if str(uuid.UUID(target[field])) != target[field]:
            raise Refused(f"{field} must be an exact canonical UUID")
    if target["id"] not in loaded(rpc):
        raise Refused("exact thread is not loaded on this server")
    thread, turns = snapshot(rpc, target["id"])
    verify(thread, turns, target, active=True)
    old_items = {item["id"] for item in turns[0]["items"]}
    prompt = status_prompt(message_id)
    claim(receipts, endpoint, target, message_id)
    # After claim, no error or timeout permits an automatic second send.
    try:
        response = rpc.call("turn/steer", {
            "threadId": target["id"], "expectedTurnId": target["turnId"],
            "clientUserMessageId": message_id,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
        })
    except Exception as error:
        raise Uncertain(f"send outcome uncertain; receipt retained: {error}") from error
    try:
        if response.get("turnId") != target["turnId"]:
            raise Refused("steer returned another turn ID")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            thread, turns = snapshot(rpc, target["id"])
            verify(thread, turns, target, active=False)
            reply = find_reply(turns[0]["items"], old_items, message_id, prompt)
            if reply is not None:
                return {"outcome": "reply_observed", "endpoint": endpoint,
                        "threadId": target["id"], "turnId": target["turnId"],
                        "messageId": message_id, "reply": reply}
            time.sleep(min(interval, max(0, deadline - time.monotonic())))
        raise TimeoutError("no correlated reply observed before deadline")
    except Exception as error:
        raise Uncertain(f"send/reply outcome uncertain; receipt retained: {error}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["list", "status"])
    parser.add_argument("--socket", required=True, help="Explicit existing local server socket")
    parser.add_argument("--codex", required=True, help="Absolute path to trusted codex.exe/binary")
    parser.add_argument("--thread")
    parser.add_argument("--session")
    parser.add_argument("--turn")
    parser.add_argument("--cwd")
    parser.add_argument("--model")
    parser.add_argument("--message-id", help="Explicit UUID; never generated during retry")
    parser.add_argument("--receipts", type=Path, default=Path(__file__).parent / ".coordination-receipts")
    parser.add_argument("--timeout", type=float, default=30)
    args = parser.parse_args()
    if not (math.isfinite(args.timeout) and 0 < args.timeout <= 60):
        parser.error("timeout must be finite and between 0 and 60 seconds")
    if not Path(args.socket).is_absolute() or not Path(args.codex).is_absolute():
        parser.error("socket and codex paths must be absolute")
    if args.action == "status" and not all((args.thread, args.session, args.turn, args.cwd, args.model, args.message_id)):
        parser.error("status requires thread, session, turn, cwd, model and message-id")
    endpoint = os.path.normcase(os.path.normpath(args.socket))
    pipe = None
    try:
        pipe = ProxySocket(args.codex, endpoint, args.timeout)
        stream = websocket.WebSocket()
        # The URL supplies HTTP handshake fields only. All bytes use the explicit pipe.
        stream.connect("ws://localhost/", socket=pipe, timeout=args.timeout,
                       suppress_origin=True, redirect_limit=0)
        if stream.getstatus() != 101:
            raise Refused("server did not accept WebSocket upgrade; redirects forbidden")
        rpc = Rpc(stream, args.timeout)
        rpc.initialize()
        if args.action == "list":
            result = []
            for thread_id in loaded(rpc):
                thread, turns = snapshot(rpc, thread_id, active_only=True)
                result.append({k: thread.get(k) for k in (
                    "id", "sessionId", "cwd", "model", "status", "parentThreadId", "forkedFromId", "gitInfo",
                    "ephemeral", "canAcceptDirectInput"
                )} | {"latestTurnId": turns[0]["id"] if turns else None})
            print(json.dumps({"endpoint": endpoint, "threads": result}))
        else:
            target = dict(id=args.thread, sessionId=args.session, turnId=args.turn, cwd=args.cwd, model=args.model)
            print(json.dumps(inquire(rpc, endpoint, target, args.message_id, args.receipts, args.timeout)))
        return 0
    except Exception as error:
        print(json.dumps({"outcome": "uncertain" if isinstance(error, Uncertain) else "failed",
                          "error": str(error)}))
        return 2 if isinstance(error, Uncertain) else 1
    finally:
        if pipe is not None:
            pipe.close()


if __name__ == "__main__":
    raise SystemExit(main())
