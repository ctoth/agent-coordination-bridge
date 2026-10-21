# Durable status replies

## Goal
Fix the observed late Claude status reply failure without a persistent sender,
new listener, transcript mutation or automatic command execution.

## State
Isolated branch `fix/durable-status-replies`; prepared for publication. Seven new
synthetic tests pass, including timeout and premature sender EOF before a late reply,
fresh-process collection, duplicate request refusal and durable idempotent
acknowledgment. Independent final review passed all 41 combined tests without blocking
findings. No new live inquiries have been sent; the new fallback prompt has not been
demonstrated live. Existing failed delivery was traced read-only to the Claude path.

## Decisions and why
The actual failed route was Claude SendMessage to a short-lived bridge messenger;
both name and address delivery failed after its inbox disappeared. The target's
normal transcript retained the intended reply. Persist request metadata before send
and collect that existing durable record into a local SQLite mailbox. Ask future
targets to leave a correlated text record as a fallback. Report recovered text
separately from confirmed live delivery. About 115 lines of storage/collection code,
small CLI integration, and regression tests; Codex transport remains unchanged.

## Surprises
Keeping stdin open does not make a bounded sender a durable return address. A longer
timeout would merely move the same failure. No real captures or session identities
are included in source or fixtures.

## Next step
Publish the reviewed repair as a pull request. Preserve the request ID and mailbox
after any uncertainty; collect later without resending. No automatic wake-up is built.
