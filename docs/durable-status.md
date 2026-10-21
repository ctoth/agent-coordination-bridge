# Collecting late Claude status replies

The Claude status messenger is temporary. A busy target can reply after it exits,
so a successful request send is not a successful roundtrip. The Codex adapter
uses a different return path and is unchanged by this repair.

Before a Claude inquiry is sent, the bridge commits its correlation, target session
identity, sender identity and transcript position to a local SQLite mailbox.
The default is `.status-mailbox/status.sqlite3`, excluded from Git. Use an explicit
`--mailbox` path for a stable location when moving the checkout. The mailbox contains
private status data and local paths; do not publish it. It uses the execution user's
existing filesystem permissions, not a new authentication boundary.

Choose one request ID before sending (the suffix is 32 lowercase hexadecimal digits):

```text
python agent_bridge.py claude status --target FULL_SESSION_UUID --request-id codex-status-11111111111111111111111111111111
python agent_bridge.py claude collect --request-id codex-status-11111111111111111111111111111111
python agent_bridge.py claude ack --request-id codex-status-11111111111111111111111111111111
```

Without `--request-id`, status generates and prints an ID for compatibility. After
an uncertain send, use `collect`, never another status inquiry with a fresh ID.
The same ID cannot be sent twice from the same mailbox, including after a crash.
Keep the mailbox: replacing it loses the duplicate-send history.

The request asks the target to leave a `STATUS-REPLY` text record in its own session
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
