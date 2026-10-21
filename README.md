# Agent Coordination Bridge

**Ask a running Claude Code or Codex session a question, and get back the reply that carries that question's ID.**

```sh
python agent_bridge.py ask --provider claude --target <session-uuid> \
  --message "Which branch are you on, and what are you blocked on?"
```

When several coding agents work on one machine, you (or one of the agents) often
need to ask another one something. The bridge sends one question to one exactly
identified session, tags it with a correlation ID, and accepts only a reply that
carries that ID. Each request is written to a local mailbox *before* it is sent,
so a request is never sent twice, and an answer that arrives late can still be
collected afterwards.

## Features

- **Two providers.** Claude Code, through its built-in cross-session messaging, and
  Codex, through an existing app-server's `proxy` tunnel.
- **Free-text questions**, plus a `status` preset that asks for the current task,
  worktree and blockers.
- **Exact targeting.** You name a full session UUID (and for Codex the thread, session,
  working directory and model). Missing, stale or ambiguous targets are refused.
- **At most once.** Each request ID is claimed in a SQLite mailbox before sending.
  Nothing is retried automatically, and a claimed ID cannot be sent again.
- **Late replies.** `collect` finds a reply that arrived after the command gave up.
  It only reads; it never sends.
- **Small footprint.** No listener, no daemon, no credential files read. The Claude
  side needs only the standard library; Codex adds `websocket-client`.

## Quick start

Requires Python 3.9+. For Claude: a working, signed-in Claude Code with
cross-session messaging. For Codex: an existing app-server socket you are
authorized to use.

```sh
git clone https://github.com/ctoth/agent-coordination-bridge
cd agent-coordination-bridge
pip install -r requirements.txt   # only needed for Codex
```

### Claude Code

```sh
python agent_bridge.py list --provider claude                    # pick a full sessionId
python agent_bridge.py ask --provider claude --target <UUID> --message "Is CI green on your PR?"
python agent_bridge.py status --provider claude --target <UUID>  # the preset question
```

`ask` prints its request ID (`ask-` + 32 hex digits). If it times out, don't ask again:

```sh
python agent_bridge.py collect --request-id ask-<32 hex>   # read the reply if it has arrived
python agent_bridge.py ack --request-id ask-<32 hex>       # mark it handled locally
```

### Codex

```sh
python agent_bridge.py list --provider codex --codex <abs-codex-binary> --socket <abs-socket>

python agent_bridge.py ask --provider codex --codex <abs-codex-binary> --socket <abs-socket> \
  --thread <UUID> --session <UUID> --cwd <exact-cwd> --model <exact-model> \
  --request-id <new-UUID> --message "Which tests cover the parser?"
```

Add `--turn <UUID>` to steer the thread's active turn. Leave it out for an idle
thread, and the bridge starts a turn there. To read a late reply, run
`collect --request-id <UUID> --codex <abs-codex-binary>`; it reconnects to the
endpoint recorded with the request.

## How it works

```mermaid
sequenceDiagram
    participant You as You / an agent
    participant Bridge as agent_bridge.py
    participant Box as Mailbox (SQLite)
    participant Peer as Target session
    You->>Bridge: ask --message "…"
    Bridge->>Box: claim request ID (refused if already used)
    Bridge->>Peer: question tagged "correlation <id>"
    Peer-->>Bridge: "AGENT-REPLY <id>" + answer
    Bridge->>Box: store reply
    Bridge-->>You: JSON result
    Note over You,Box: Timed out? `collect` reads the target's record later and never sends again.
```

- **Claude:** a short-lived messenger Claude, allowed only `ListAgents` and `SendMessage`,
  delivers the question. The target answers the messenger and also leaves the answer
  in its own transcript, which `collect` can read after the messenger has exited.
- **Codex:** the question goes in with `turn/steer` (guarded by the expected turn ID)
  or `turn/start` for an idle thread. The bridge then polls that turn for the reply.

## Results

Everything prints JSON.

| Command | Success | Otherwise |
| --- | --- | --- |
| Claude `ask` / `status` | exit 0, all three evidence flags true | exit 1; once sent, the request stays in the mailbox for `collect` |
| Codex `ask` / `status` | exit 0, `reply_observed` | exit 1 `failed` before sending, exit 2 `uncertain` after |
| `collect` | `reply_collected` | `pending` |

A collected reply is text found in the target's record. It does not prove the live
delivery succeeded, and the output says so (`live_delivery_confirmed: false` for Claude).

## Safety

- **Peer text is not user authority.** Every question tells the recipient that it
  approves nothing and is no reason to change files, repositories or settings.
  That is an instruction to a model, not an enforced permission boundary. Use the
  bridge only with sessions you trust.
- **Never resend after a timeout.** Run `collect`. Use a fresh request ID only for a
  genuinely new question.
- **Keep the mailbox.** `.status-mailbox/` (gitignored) holds the duplicate-send
  history and private replies. Don't delete or publish it. Older Codex receipts in
  `.coordination-receipts/` are still read and still block their IDs.
- **No wake-up.** Nothing runs in the background, and nothing pages a sleeping agent.

## Earlier commands still work

```sh
python agent_bridge.py claude status --target <UUID>
python claude_bridge.py status --target <UUID>
python codex_adapter.py list --codex <abs-codex-binary> --socket <abs-socket>
```

Older `codex-status-…` Claude request IDs and `STATUS-REPLY` replies are still accepted.

## Testing

```sh
python -m unittest -v
```

The tests use synthetic data only and connect to no real session. Earlier
status-only versions were exercised against live Claude and Codex sessions; see
the provider docs for exactly what that covered. The free-text question, the shared
mailbox and the idle Codex path have **not** yet been run against a live session.

## More detail

- [Claude: setup, transport, security and evidence](docs/claude.md)
- [Codex: exact targeting, receipts and evidence](CODEX.md)
- [Collecting late replies](docs/durable-status.md)

## License

[MIT](LICENSE)
