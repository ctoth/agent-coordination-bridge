"""Ask a running Claude Code or Codex session a question and collect its correlated reply."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sys

import message
from status_mailbox import Mailbox

HERE = Path(__file__).parent


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("list", "ask", "status", "collect", "ack"),
                   help="status asks a fixed status question; collect and ack never send")
    p.add_argument("--provider", choices=("claude", "codex"), help="required for list, ask and status")
    p.add_argument("--message", help="the question for ask")
    p.add_argument("--request-id", "--message-id", dest="request_id",
                   help="Claude: ask-<32 hex>, generated if omitted; Codex: a new UUID. "
                        "Reuse only with collect/ack; a second send is refused")
    p.add_argument("--timeout", type=float,
                   help="seconds to wait for the reply: Claude up to 300 (default 90), Codex up to 60 (default 30)")
    p.add_argument("--mailbox", type=Path, default=HERE / ".status-mailbox" / "status.sqlite3")
    claude = p.add_argument_group("claude")
    claude.add_argument("--target", help="full live session UUID from list")
    codex = p.add_argument_group("codex")
    codex.add_argument("--codex", help="absolute path to a trusted codex binary")
    codex.add_argument("--socket", help="absolute path to an existing app-server socket")
    codex.add_argument("--thread")
    codex.add_argument("--session")
    codex.add_argument("--turn", help="active turn to steer; omit to start a turn on an idle thread")
    codex.add_argument("--cwd")
    codex.add_argument("--model")
    codex.add_argument("--receipts", type=Path, default=HERE / ".coordination-receipts",
                       help="receipt files from earlier versions; read, never written")
    return p


def run(args):
    if args.command in ("collect", "ack"):
        if not args.request_id:
            raise ValueError("collect and ack require --request-id")
        with closing(Mailbox(args.mailbox)) as mailbox:
            if args.command == "ack":
                return mailbox.acknowledge(args.request_id), 0
            request = mailbox.request(args.request_id)
            if request["provider"] == "codex" and args.codex:
                return codex_run(args, request["endpoint"], lambda codex, rpc, endpoint:
                                 codex.collect(rpc, mailbox, args.request_id))
            return mailbox.collect(args.request_id), 0
    if not args.provider:
        raise ValueError(f"{args.command} requires --provider")
    if args.command == "ask" and not args.message:
        raise ValueError("ask requires --message")
    question = message.STATUS_QUESTION if args.command == "status" else args.message
    if args.provider == "claude":
        import claude_bridge as claude
        cli = claude.executable()
        if args.command == "list":
            return claude.listing(cli), 0
        timeout = 90 if args.timeout is None else args.timeout
        if not args.target or not 0 < timeout <= 300:
            raise ValueError(f"{args.command} requires --target and a timeout between 0 and 300 seconds")
        target = claude.select(claude.discover(cli), args.target)
        with closing(Mailbox(args.mailbox)) as mailbox:
            claude.ask(cli, target, timeout, mailbox, args.request_id, question)  # prints its own evidence
        return None, 0
    if args.command == "list":
        return codex_run(args, None, lambda codex, rpc, endpoint: codex.listing(rpc, endpoint))
    if not all((args.thread, args.session, args.cwd, args.model, args.request_id)):
        raise ValueError(f"{args.command} requires --thread, --session, --cwd, --model and --request-id; "
                         "add --turn to steer an active turn")
    target = dict(id=args.thread, sessionId=args.session, turnId=args.turn, cwd=args.cwd, model=args.model)

    def ask(codex, rpc, endpoint):
        with closing(Mailbox(args.mailbox)) as mailbox:
            return codex.inquire(rpc, endpoint, target, args.request_id, mailbox, args.receipts,
                                 codex_timeout(args), question=question)
    return codex_run(args, None, ask)


def codex_timeout(args):
    # Only a missing timeout gets the default; 0 must reach connect() and be refused.
    return 30 if args.timeout is None else args.timeout


def codex_run(args, recorded, action):
    import codex_adapter as codex  # websocket-client is needed only here
    if not args.codex or not (args.socket or recorded):
        raise ValueError("Codex requires --codex and --socket")
    endpoint = codex.endpoint_of(args.socket) if args.socket else recorded
    if recorded and endpoint != recorded:
        raise ValueError("--socket differs from the endpoint this request was sent to")
    try:
        with codex.connect(args.codex, endpoint, codex_timeout(args)) as rpc:
            return action(codex, rpc, endpoint), 0
    except codex.Uncertain as error:
        return {"outcome": "uncertain", "error": str(error)}, 2


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["claude"], ["codex"]):  # earlier form: agent_bridge.py <provider> <command> ...
        argv = [*argv[1:2], "--provider", argv[0], *argv[2:]]
    args = parser().parse_args(argv)
    try:
        result, code = run(args)
    except Exception as error:
        result, code = {"outcome": "failed", "error": str(error)}, 1
    if result is not None:
        print(json.dumps(result, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
