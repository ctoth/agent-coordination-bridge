# Codex message deduplication

## Goal
Allow distinct explicitly authorized requests in one active turn while retaining at-most-once local claiming for each request ID, including uncertain sends and historical receipts.

## State
Isolated branch from origin/main. Four new regression cases reproduced turn-level refusal before the implementation. Message-keyed receipts implemented; all32 adapter tests passed, including same-ID concurrent uncertainty, later-turn reuse, incomplete new receipt and historical receipt preservation. Exact scoped source/test/docs diff reviewed locally; draft PR publication next. No live clarification sent yet.

## Decisions and why
The demonstrated failure is the turn-level receipt key blocking a new clarification after a successful earlier inquiry. Smallest change: approximately20 production lines key new receipts by endpoint/thread/message, preserve turn metadata, read historical turn receipts to reject their already-used message IDs, and retain exclusive creation/fsync. No new service, transport, database or retry.

## Surprises
Synthetic server reused item IDs across sends; real server emits unique IDs. Adjust fake IDs so a second request tests actual new receipt/reply correlation.

## Next step
Publish feature branch/draft PR, complete the exact-PR two-pass review ledger/gate, then send one separately authorized clarification using the tested code. Private runtime identities/evidence stay outside Git. Keep the canonical checkout and all existing receipts unchanged.
