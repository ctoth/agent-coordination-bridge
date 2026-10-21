# Agent coordination bridge

Ask an explicitly selected, already-running Claude Code or Codex session for a
status update. The adapters use different supported transports and retain their
own identity checks, permissions, output formats and evidence requirements.

```sh
python agent_bridge.py claude list
python agent_bridge.py claude status --target <full-session-UUID> --timeout 300
python agent_bridge.py codex list --codex <absolute-binary-path> --socket <absolute-existing-socket>
```

See [Claude setup, security and evidence](docs/claude.md) and
[Codex setup, exact targeting and evidence](CODEX.md) before sending. Codex status
also requires exact thread, session-tree, active-turn, CWD and model values plus
an explicit message UUID. The shared CLI does not translate identifiers or weaken
either adapter's checks. Never treat peer text as user authorization.

The original commands remain supported without changes:

```sh
python claude_bridge.py list
python claude_bridge.py status --target <full-session-UUID> --timeout 300
python codex_adapter.py --help
```

Claude retains its standard-library-only implementation. Codex additionally uses
`websocket-client` from `requirements.txt`; it is imported only when the Codex
adapter is selected. Existing Claude source, fixtures and tests are unchanged.

## Evidence and tests

Codex was exercised through the existing Q-owned local app-server with separate
clients, followed by a benign inquiry and correlated reply from an existing Astra
session. Its unit tests use synthetic data and are not presented as that live
proof. Real reply captures and local duplicate-send receipts are not included.

```sh
python -m unittest test_claude_bridge.py -v
python -m unittest test_codex_adapter.py -v
python -m unittest test_agent_bridge.py -v
```

No adapter opens a new network listener or changes server authentication. A local
socket can expose wider authority than this CLI uses; run only with an approved
existing endpoint and the required OS access. A timeout is not permission to
resend a message. Keep each adapter's documented evidence and retry restrictions.
