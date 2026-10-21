"""Opt-in live test: create ONLY a disposable thread, then use a second client.

This harness is separate from the adapter and is never run by unit tests.
It uses an existing server; it does not launch one or alter its configuration.
It creates a normal saved test thread because ephemeral threads reject polling.
The thread is retained as evidence, not automatically archived or interrupted.
"""
import argparse
import json
import os
from pathlib import Path
import time

import websocket

from codex_adapter import ProxySocket, Rpc, RpcError, inquire, snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--message-id", required=True)
    args = parser.parse_args()
    if not all(Path(p).is_absolute() for p in (args.codex, args.socket, args.cwd)):
        parser.error("binary, socket and cwd must be absolute")
    endpoint = os.path.normcase(os.path.normpath(args.socket))
    pipes = []

    def connect():
        pipe = ProxySocket(args.codex, endpoint, 20)
        pipes.append(pipe)
        stream = websocket.WebSocket()
        stream.connect("ws://localhost/", socket=pipe, timeout=20,
                       suppress_origin=True, redirect_limit=0)
        if stream.getstatus() != 101:
            raise RuntimeError("WebSocket upgrade not accepted")
        rpc = Rpc(stream, 20)
        rpc.initialize()
        return rpc

    try:
        owner = connect()
        observer = connect()
        thread = owner.call("thread/start", {
            "cwd": args.cwd,
            "developerInstructions": (
                "This is a disposable coordination protocol test. Do not read or write "
                "files, contact other agents, use network tools, or change settings. "
                "You may use the shell only for the requested 15-second wait. "
                "When a status inquiry arrives, reply with its requested literal marker "
                "followed by your task, working directory and blockers, then finish."
            ),
        })["thread"]
        turn = owner.call("turn/start", {
            "threadId": thread["id"],
            "input": [{"type": "text", "text": (
                "You are the disposable recipient of an authorized live coordination test. "
                "Use the shell once to run Start-Sleep -Seconds 15, with no other command. "
                "This keeps the test turn active while its initial history becomes readable. "
                "A separate client will send a benign status inquiry during the wait. "
                "After the wait, answer that inquiry and finish."
            ), "text_elements": []}],
        })["turn"]
        target = {k: thread[k] for k in ("id", "sessionId", "cwd", "model")}
        target["turnId"] = turn["id"]
        print(json.dumps({"disposableTarget": target, "endpoint": endpoint}), flush=True)
        # Only test-fixture readiness reads may retry. Never resend any user input.
        deadline = time.monotonic() + 10
        while True:
            try:
                snapshot(observer, target["id"])
                break
            except RpcError as error:
                if (time.monotonic() >= deadline or not any(fragment in str(error) for fragment in (
                        "is empty", "not materialized yet", "before first user message"))):
                    raise
                time.sleep(0.2)
        result = inquire(observer, endpoint, target, args.message_id,
                         Path(__file__).parent / ".coordination-receipts", 50)
        print(json.dumps(result), flush=True)
    finally:
        for pipe in reversed(pipes):
            pipe.close()


if __name__ == "__main__":
    main()
