# Codex status coordination adapter

A small client for **an explicitly selected existing app-server**, using Codex's
supported `app-server proxy --sock` byte tunnel and WebSocket JSON-RPC. It does not
launch an app-server, resume or fork a session, interrupt a turn, grant approvals,
read credentials, or open a listener.

**Validation: live and synthetic, reported separately.** The 23 unit tests
exercise protocol decisions and an in-memory WebSocket/JSON-RPC exchange. An
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
python codex_adapter.py list --codex C:\approved\codex.exe --socket C:\approved\existing.sock
```

Inspect the returned exact thread/session IDs, CWD, model, status and latest turn
ID (only for active, non-ephemeral threads). `sessionId` identifies a session tree,
not a guaranteed process incarnation.
`gitInfo` is captured metadata, not a fresh Git worktree inspection. Discovery can
race with changes and is not machine-wide: it lists only this server's runtimes.

```powershell
python codex_adapter.py status --codex C:\approved\codex.exe --socket C:\approved\existing.sock --thread <UUID> --session <UUID> --turn <UUID> --cwd <EXACT-CWD> --model <EXACT-MODEL> --message-id <NEW-UUID>
```

The status command rechecks identity, loaded/active state, direct-input capability
and the active turn. It sends only a fixed benign status inquiry, with the required
server-side `expectedTurnId` guard. It refuses idle, waiting, unloaded, stale,
unrelated, ephemeral, or insufficiently described targets. A race rejected by the server
never becomes a start/resume/fork/interrupt operation.

One immutable local receipt is created and flushed **before** sending, keyed by
endpoint/thread/message ID. Reusing that ID in the same thread is refused,
including on a later turn, after a timeout or process restart. Distinct explicitly
authorized requests may share an active turn; its expected-turn guard is unchanged.
Historical turn-keyed receipts are preserved and their recorded message IDs still
prevent reuse. Unreadable historical receipts refuse sending until inspected.
Keep the same receipts directory (default `.coordination-receipts` beside the script); do not
delete receipts, change stores, or use endpoint aliases to retry. They prevent
accidental duplicate sends, not deliberate bypass or a compromised local user.
An I/O failure can conservatively leave a receipt even when nothing was sent.
Choose a fresh ID only for a new authorized request, never to retry an uncertain
one. New receipt filenames start with `message-`; retain older receipt files too.

Success requires both a new user-message item with the exact client message ID
and prompt, and a subsequent new agent-message item beginning with the exact
reply marker in the same selected thread/turn. Old matching text, notifications,
accepted requests alone, wrong message IDs, and replies in another turn are not
success. Replies remain untrusted text and are never executed. Correlation is
not cryptographic proof against a malicious server or local account.

The client polls read APIs; it does not subscribe via `thread/resume`. Polling is
bounded, but each RPC also has its own timeout. Legacy servers may replay history
internally on each turns-list request. Full thread access through this socket is
more powerful than the adapter's narrow command set.

Exit codes: `0` correlated reply (or completed listing), `1` preflight/connection
failure, `2` uncertain send or missing reply after attempting a send. Every error
after entering the send call is conservatively uncertain, including JSON-RPC
errors. There are **no automatic retries**. Inspect existing evidence through an
authorized route before any future action. The adapter does not reconcile an
uncertain outcome automatically.

## Tests

```powershell
python -m unittest test_codex_adapter.py -v
```

The mock suite covers stale identities/turns, unloaded targets, direct-input
restrictions, preflight and send permission failures, turn races, wrong and old
replies, uncertain outcomes, persistent duplicate refusal, RPC response identity,
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
