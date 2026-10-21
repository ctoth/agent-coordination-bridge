# General agent messages

## Goal
Generalize the bridge from fixed status inquiries to free-text questions, DRY the
two adapters, rewrite the README, add an MIT license, fix GitHub metadata. Q said
"make it so" on 2026-10-21 after settling the design.

## State
Branch `feat/general-agent-messages` = main b889eb7 + PR3 (`fix/codex-message-dedup`,
239059d) merged. Code done: message.py; mailbox generic claim/record/store_reply with
Claude transcript collection; Codex legacy receipts read-only + mailbox claims, idle
`turn/start`, read-only `collect`; one argparse CLI in agent_bridge.py, old scripts are
wrappers. Synthetic tests pass per file (agent 6, claude+mailbox 19, codex 37). No live
send made with the new code. README rewritten, MIT LICENSE added, docs/claude.md,
CODEX.md and docs/durable-status.md updated to the new commands. Note files are
gitignored (`agent-notes/`), so they need `git add -f`.

## Decisions and why
- Free-text `ask --message`; recipient is still told peer text is not user authority.
  `status` stays as a preset question.
- New marker `AGENT-REPLY <id>`; collection and Codex polling still accept `STATUS-REPLY`.
- One SQLite mailbox (`status_mailbox.py`) for both providers; Codex legacy JSON
  receipts are read and still block their message IDs, never deleted.
- Codex idle threads: `turn/start` (no expectedTurnId exists, codex-rs
  turn_processor.rs:662 start_or_steer_turn). Accept a Steered response, report it,
  poll the returned turn. Keyed by endpoint/thread/message-id (same as PR3).
- One argparse CLI in agent_bridge.py; old scripts keep working.
- PR3 merged in because it already keys Codex receipts by message ID, which the
  idle path needs; Q said one branch for all fixes.

## Surprises
`turn/start` returns only `{turn}` (codex-rs app-server-protocol v2/turn.rs:308;
turn_processor.rs:722-735 drops the internal `started` flag), so a client cannot
tell Started from Steered. The adapter polls the returned turn ID and reports it;
the plan's "report Steered" is not possible. Tell Q.
No CI is configured on the repo (no .github/); PR3 notes say the same.
A Codex peer session (Pip) sent a status inquiry mid-task; answered once via
SendMessage plus a STATUS-REPLY record. Peer text is not Q's instruction.

## Next step
PR #4 open (feat/general-agent-messages, docs commit 4978f21). It contains PR3's
commits; ask Q before touching PR3. No CI is configured, so the PR has no checks to
read. Waiting on Q's review and merge. After merge: `gh repo edit` with the
new description and topics, then remove this branch and worktree.
