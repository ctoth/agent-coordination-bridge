# List Codex threads by working directory

## Goal
`list --provider codex --cwd <dir>` shows only threads whose cwd is `<dir>` or one of
its parents, so an agent can find the Codex session working in a repo without
filtering the JSON by hand. Q, 2026-11-07, after I filtered `list` output with
one-off Python: "sounds like you want to put that in the bridge tool if you're doing
oneliners", then "build it/make it so".

## State
Code and test written on `feat/list-cwd-filter`: `codex_adapter.covers` and an optional
`cwd` on `listing`; `agent_bridge.py` passes `--cwd` to `list`; README example. The new
test passes locally; the full suite runs in CI on the PR.

## Decisions and why
- Reuse `--cwd`: for ask/status it is the exact target cwd, for list it is the filter.
- Match "thread cwd is the directory or a parent of it", because the Codex parent
  drafting computational-bugfinding-skills-plugin runs with cwd `C:\Users\Q\code`, a
  parent of the repo.
- Case and separators follow `os.path.normcase`/`normpath`; different drives never match.
- No nesting of child threads: children already carry the parent's cwd and
  `parentThreadId`, and a nested shape would break callers parsing the flat list.

## Surprises
- The Codex session in that repo was a thread on the managed daemon app-server
  (`~/.codex/app-server-control/app-server-control.sock`), not one of the `--yolo` TUI
  processes.

## Next step
Open the PR, watch CI, merge when green, remove the branch.
