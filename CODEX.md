# Codex adapter

A small client for **an explicitly selected existing app-server**, using Codex's
supported `app-server proxy --sock` byte tunnel and WebSocket JSON-RPC. It does not
launch an app-server, resume or fork a session, interrupt a turn, grant approvals,
read credentials, or open a listener.

**Validation: live and synthetic, reported separately.** The unit tests
exercise protocol decisions and an in-memory WebSocket/JSON-RPC exchange. The live
runs below used the earlier status-only version; free-text questions, the shared
mailbox and idle-thread `turn/start` have been tested only synthetically. An
authorized non-elevated Q-user run reached the original existing server and
completed a single status inquiry/reply with the verified Qlatt Astra root;
the production CLI returned `reply_observed` with exit code 0. No work commands
or permission changes were sent.

A preceding disposable exchange used separate owner/observer clients. Its reply
was initially missed because the parser required a space after the correlation
marker; a read-only check found the matching receipt and newline-separated reply.
The parser was corrected and tested before the Astra exchange. No uncertain
inquiry was resent. Initial sandbox socket access had failed with error 10013;
the successful Q-user route was used only after explicit approval.

## Usage

Requires Python 3.9+ and `websocket-client==1.9.0` (already installed in the test
environment). Use a trusted absolute Codex binary path, and an existing local
socket you are authorized to access. There is no implicit endpoint selection,
server-start fallback, credential discovery, redirect, or reconnect.

```powershell
python agent_bridge.py list --provider codex --codex C:\approved\codex.exe --socket C:\approved\existing.sock
```

Inspect the returned exact thread/session IDs, CWD, model, status and latest turn
ID (only for active, non-ephemeral threads). `sessionId` identifies a session tree,
not a guaranteed process incarnation.
`gitInfo` is captured metadata, not a fresh Git worktree inspection. Discovery can
race with changes and is not machine-wide: it lists only this server's runtimes.

```powershell
python agent_bridge.py ask --provider codex --codex C:\approved\codex.exe --socket C:\approved\existing.sock --thread <UUID> --session <UUID> --cwd <EXACT-CWD> --model <EXACT-MODEL> --request-id <NEW-UUID> --message "<question>" [--turn <ACTIVE-TURN-UUID>]
```

`status` instead of `ask` sends the fixed status question. Every question tells the
recipient to answer starting with `AGENT-REPLY <id>`.

**With `--turn`** the command rechecks identity, loaded/active state, direct-input
capability and the active turn, then sends with `turn/steer` and the server-side
`expectedTurnId` guard. It refuses waiting, unloaded, stale, unrelated, ephemeral,
or insufficiently described targets. A race rejected by the server never becomes a
start/resume/fork/interrupt operation.

**Without `--turn`** the thread must be idle, non-ephemeral and accept direct input;
the command sends with `turn/start`. That method has no expected-turn guard: the
server calls `start_or_steer_turn` (codex-rs `app-server/src/request_processors/turn_processor.rs`),
so if a turn began after the check, the question is steered into it. The response
carries only the turn (`TurnStartResponse { turn }`), so the adapter cannot tell a
started turn from a steered one. It records and polls whichever turn ID the server returned.

Each request is committed to the shared SQLite mailbox (default
`.status-mailbox/status.sqlite3`) **before** sending, keyed by its request ID. A
claimed ID is refused again, including on another thread or turn, after a timeout or
a process restart. Receipt files from earlier versions in `.coordination-receipts`
are read, never written or deleted: their message IDs remain blocked, and an
unreadable one refuses sending until inspected. Do not delete the mailbox or the
receipts, change stores, or use endpoint aliases to retry. They prevent accidental
duplicate sends, not deliberate bypass or a compromised local user. Choose a fresh
ID only for a new authorized request, never to retry an uncertain one.

A late reply can be collected without sending anything:

```powershell
python agent_bridge.py collect --request-id <UUID> --codex C:\approved\codex.exe
```

It reconnects to the endpoint recorded with the request, finds the recorded turn
with `thread/turns/list`, and stores the first correlated reply. Without `--codex`
it only reports what the mailbox already holds.

Success requires both a new user-message item with the exact client message ID
and prompt, and a subsequent new agent-message item beginning with the exact
reply marker (`AGENT-REPLY`, or `STATUS-REPLY` from earlier versions) in the same
selected thread/turn. Old matching text, notifications,
accepted requests alone, wrong message IDs, and replies in another turn are not
success. Replies remain untrusted text and are never executed. Correlation is
not cryptographic proof against a malicious server or local account.

The client polls read APIs; it does not subscribe via `thread/resume`. Polling is
bounded, but each RPC also has its own timeout. Legacy servers may replay history
internally on each turns-list request. Full thread access through this socket is
more powerful than the adapter's narrow command set.

Exit codes: `0` correlated reply (or completed listing), `1` argument, preflight or
connection failure, `2` uncertain send or missing reply after attempting a send.
Every error after entering the send call is conservatively uncertain, including
JSON-RPC errors. There are **no automatic retries**. Use `collect` for a late reply;
it never sends.

## Tests

```powershell
python -m unittest test_codex_adapter.py -v
```

The mock suite covers stale identities/turns, unloaded targets, direct-input
restrictions, preflight and send permission failures, turn races, wrong and old
replies, uncertain outcomes, persistent duplicate refusal, earlier receipt files,
idle-thread `turn/start`, read-only late collection, RPC response identity,
unanswered approval requests, connection denial, and idempotent proxy cleanup.
Its in-memory wire server launches no subprocess or network listener. Only the
production transport would launch the supplied Codex binary's **proxy** child;
cleanup terminates that owned child, never the existing server.

## Source and access boundaries

Source anchors in the inspected Codex checkout include `codex-rs/cli/src/main.rs`
(AppServerSubcommand::Proxy), `app-server/src/request_processors/thread_processor.rs`
(loaded list and live read), and `turn_processor.rs` (guarded steer). Current source already permits guarded steering plus
reply polling on an accessible owning server; it does not require the proposed
subscription extension for this adapter.

The approved execution route was checked for Q's exact SID, a non-administrator
token and medium integrity before accessing the same socket. Read-only checks
found the control directory owned by Q with protected Q-only FullControl, and
the observed daemon process owned by Q. No ACL, ownership, authentication, token
or listener settings were changed. The sandbox route remains denied; this does
not authorize bypassing it in another environment.

The approved process has Q-user authority, including access to other thread
contents and mutation methods; the adapter's limited methods are not an
OS-enforced capability boundary. Use only an authorized execution context.

`live_disposable_probe.py` is a separate opt-in test harness, not part of the
status CLI. It creates a saved disposable thread on an explicit existing server
because ephemeral threads reject reply polling. It retains that test record and
does not resume, archive or interrupt other sessions. Do not run it as a retry
of an uncertain inquiry.
