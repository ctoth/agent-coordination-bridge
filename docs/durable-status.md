# Collecting late Claude replies

The Claude messenger is temporary. A busy target can reply after it exits,
so a successful request send is not a successful roundtrip. Codex requests share
the same mailbox but are collected from the server; see [CODEX.md](../CODEX.md).

Before a Claude question is sent, the bridge commits its correlation, target session
identity, sender identity and transcript position to a local SQLite mailbox.
The default is `.status-mailbox/status.sqlite3`, excluded from Git. Use an explicit
`--mailbox` path for a stable location when moving the checkout. The mailbox contains
private status data and local paths; do not publish it. It uses the execution user's
existing filesystem permissions, not a new authentication boundary.

Choose one request ID before sending (the suffix is 32 lowercase hexadecimal digits):

```text
python agent_bridge.py ask --provider claude --target FULL_SESSION_UUID --request-id ask-11111111111111111111111111111111 --message "QUESTION"
python agent_bridge.py collect --request-id ask-11111111111111111111111111111111
python agent_bridge.py ack --request-id ask-11111111111111111111111111111111
```

Without `--request-id`, `ask` generates and prints an ID. After an uncertain send,
use `collect`, never another question with a fresh ID. Older `codex-status-` IDs
are still accepted. The same ID cannot be sent twice from the same mailbox,
including after a crash. Keep the mailbox: replacing it loses the duplicate-send history.

The request asks the target to leave an `AGENT-REPLY` text record in its own session
as well as attempting the live reply. If the sender is gone, it should leave only
that record. Collection reads the exact target's transcript without modifying it;
it can also recover a correlated failed SendMessage attempt. It does not need a
running sender, contact the target, resume sessions, or execute message content.

`reply_collected` means text was recovered, **not** that live delivery succeeded.
`pending` means no matching record was found. Acknowledgment records that the local
consumer handled the stored reply; it sends nothing to the agent. Collecting and
acknowledging repeatedly is idempotent. Once collected, the reply survives removal
of the source transcript. Before collection, recovery depends on that transcript
remaining available and unchanged. Existing requests from before this repair are
not automatically imported into the mailbox.

There is no daemon, watcher, push notification or automatic wake-up of Pip. Run
collection again when a pending reply may have arrived. Targets that never produce
a matching record cannot be recovered by this mechanism.
