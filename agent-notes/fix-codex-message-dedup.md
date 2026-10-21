# Codex message deduplication

## Goal
Allow distinct explicitly authorized requests in one active turn while retaining at-most-once local claiming for each request ID, including uncertain sends and historical receipts.

## State
Published as draft PR3 on fix/codex-message-dedup; implementation commit4990ea9. Four new regression cases reproduced turn-level refusal before the implementation. Message-keyed receipts implemented; all32 adapter tests passed, including same-ID concurrent uncertainty, later-turn reuse, incomplete new receipt and historical receipt preservation. Exact PR two-pass review and required review gate passed at4990ea9. No hosted checks are configured.

The tested implementation then completed one newly authorized clarification to an existing active owner in the same turn as an earlier inquiry. It returned reply_observed/exit0, and a separate read-only client recovered the acknowledgment after the sender exited. The original historical receipt hash remained unchanged. Private runtime identities, message content and captures remain outside Git. This is live evidence, separate from synthetic tests.

## Decisions and why
The demonstrated failure is the turn-level receipt key blocking a new clarification after a successful earlier inquiry. Smallest change: approximately20 production lines key new receipts by endpoint/thread/message, preserve turn metadata, read historical turn receipts to reject their already-used message IDs, and retain exclusive creation/fsync. No new service, transport, database or retry.

## Surprises
Synthetic server reused item IDs across sends; real server emits unique IDs. Adjust fake IDs so a second request tests actual new receipt/reply correlation.

## Next step
Draft PR awaits maintainer review; no merge or default-branch push is authorized. Keep the canonical checkout and all existing receipts unchanged. Use new IDs for distinct authorized messages only; inspect saved evidence rather than relabeling an uncertain retry.
