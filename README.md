# Claude Code status bridge

A small Python CLI that asks an **already-running Claude Code session** for a
status update and checks the reply. No Python dependencies beyond the standard library.

It uses `claude agents --json` to discover sessions, then launches a separate
messenger Claude with only `ListAgents` and `SendMessage`. The selected existing
session remains the recipient; this does not resume or replace it.

## Use

Requirements: Python 3.8+, a working Claude Code installation and authentication,
and cross-session messaging available in both sessions. Developed against native
Windows Claude Code 2.1.304. Other versions/platforms are not validated here.

```sh
python claude_bridge.py list
python claude_bridge.py status --target <full-live-session-UUID> --timeout 300
```

Choose the full UUID from the listing and check its name/workspace first. The
bridge refuses missing targets, duplicate session IDs and duplicate names. Each
status command requests one benign status message with a new correlation ID.
It asks the recipient to reply at a natural tool boundary and continue its work.
It does not automatically resend. The timeout is bounded to 300 seconds.

The CLI emits JSON progress and three independent evidence flags:

- `target_received`: a correlated peer receipt in the selected target's transcript.
- `target_reply_send_succeeded`: a matching successful reply-tool result.
- `messenger_received_peer_frame`: the reply actually reached the messenger.

Exit 0 requires all three. An attempted send, queued message, assistant claim,
wrong sender/correlation or failed tool result is insufficient. An active-turn
receipt can be a structured `queued_command` attachment rather than a user message.

The messenger keeps stdin open to receive replies. Its standard Claude transcript
is retained, and its session UUID/PID is printed for diagnosis. Target transcripts
are opened read-only. Cleanup closes or terminates only the messenger child.

On timeout or error, delivery can be uncertain. Check the existing messenger and
target records before deciding to repeat a request. Held/refused messages remain
subject to Claude's existing inbound controls; this tool never approves them or
changes settings.

## Transport and security limits

The Python bridge opens **no HTTP/TCP listener**, binds no socket and reads no
messaging token or credential file. It delegates message delivery to Claude Code's
built-in peer tools. Native Windows Claude uses named pipes; macOS/Linux/WSL use
Unix domain sockets. Windows and WSL sessions cannot directly peer with each other.
The Claude model still uses its normal provider connection, and existing hooks or
Remote Control configuration can have their own behavior.

[Anthropic's cross-session documentation](https://code.claude.com/docs/en/cross-session-messaging)
describes Windows authentication, platform requirements, message delivery and
per-session permission controls. This project does not independently verify the
named pipe's effective ACL, native runtime flags or remote-client rejection.
Do not infer that a Windows pipe is remotely unreachable merely from its name.

Peer messages are **untrusted text**, never user approval. The messenger prompt
forbids obeying peer instructions, following up, bypassing denials or changing
settings. Its tool list excludes file editing and shell execution. These are
model/Claude permission constraints, not a deterministic RPC authorization layer.
The recipient retains its own tools and permissions; this bridge cannot guarantee
that a recipient will ignore malicious text. Use it only with sessions you trust.

The random correlation ID and registered pipe/name checks associate an exchange;
they are not cryptographic authentication. The evidence checker trusts local
session-registry and transcript files. A same-user process able to alter those
files can spoof evidence. The bridge does not protect against a compromised user
account, malicious hooks/plugins, or a compromised Claude process.

Stdout and Claude's local transcripts may contain sensitive status replies.
Do not publish them. This repository contains only implementation, tests and a
clearly marked synthetic fixture; no real session captures are included.

## Tests

```sh
python -m unittest test_claude_bridge.py -v
```

Tests cover discovery errors, explicit target selection, correlation/sender
checks, failed replies, read-only target access, hook failures, string-valued
system events and active-turn attachment receipts. `fixtures/peer_roundtrip.json`
is synthetic structural regression data, not proof of a live exchange.

## Implementation constraints

Session-registry locations and transcript encoding follow Claude's default local
configuration. Custom `CLAUDE_CONFIG_DIR` and non-Windows installations have not
been validated. Discovery can include stale/background entries; status selection
requires live PID/workspace/name metadata and verifies the registry's session ID.
The messenger is model-mediated and consumes normal Claude usage. It can refuse
or make an incorrect tool choice; its fixed prompt is not a hard recipient ACL.
Unknown event formats remain unconfirmed rather than being counted as success.
