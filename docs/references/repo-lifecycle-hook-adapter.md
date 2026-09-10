# Repo Lifecycle Hook Adapter

Use this page when adding repo-specific behavior to Codex lifecycle hooks or
explicit thread finalization.

The shared `~/GitHub/agents` control plane owns Codex integration and dispatch. Each
repository owns what it wants to do when a supported lifecycle event or explicit
thread finalization arrives.

## Shape

```text
Native Codex runtime event
  -> shared ~/GitHub/agents hook script
  -> hooks/scripts/hook_runtime.py
  -> normalized JSON adapter payload
  -> repo script when it exists

Explicit thread finalization
  -> ~/GitHub/agents/codex/scripts/finalize-codex-thread.py --thread-id <id> --apply
  -> app-server thread/read derives cwd + repo root
  -> repo scripts/hooks/finalize_codex_thread.py when it exists
  -> one same-thread finalization turn
  -> app-server thread/archive after success
```

## Rule Of Thumb

Put repo policy in the repo. Keep the shared control plane boring.

- Shared `~/GitHub/agents` layer:
  - receives supported Codex hook payloads or explicit finalizer invocations
  - runs event-specific entrypoints such as `session_start.py`
  - keeps common dispatch plumbing in `hooks/scripts/hook_runtime.py`
  - resolves the Git repo root when a runtime hook provides `cwd`
  - normalizes the payload shape
  - runs a repo hook if it exists
  - enforces short, non-interactive execution for native runtime hooks
- Repo layer:
  - decides whether the event matters
  - reads repo files or local state
  - emits context, a finalization instruction, or writes follow-up records
  - keeps expensive work out of blocking runtime hooks

## Repo Hook Locations

Lifecycle events are not enabled just because a script exists. The shared
[`hooks/registry.json`](/Users/dobby/GitHub/agents/hooks/registry.json) decides which
managed repos receive which native Codex events, and the repo script only runs
when the event is assigned to that repo.

Create these files only when a repo needs them:

```text
scripts/hooks/session_start.py
scripts/hooks/user_prompt_submit.py
scripts/hooks/finalize_codex_thread.py
```

All repo lifecycle hooks are Python. Do not add shell compatibility shims.

## Events

`SessionStart`

- Native Codex hook event.
- Runs when Codex starts or resumes a session.
- Stdout can become startup context.
- Good for loading compact repo-local orientation.

`UserPromptSubmit`

- Native Codex hook event.
- Runs before a user prompt is processed.
- Stdout can become additional prompt context.
- Good for very small, current-time or current-state context.

`FinalizeCodexThread`

- Not a native Codex runtime hook. It is the repo-policy extension point used by
  the global `codex/scripts/finalize-codex-thread.py` command.
- Optional repo script path: `scripts/hooks/finalize_codex_thread.py`.
- Runs before the global finalizer archives a known Codex thread.
- The repo script prints the instruction for a final turn in the same source
  thread. A non-zero exit blocks archive.
- Good for repo-specific absorption of useful thread context: Dobby memory,
  project trackers, docs, or other agent-native repo state.
- May run longer than native runtime hooks because it is an explicit
  finalization command, but it must remain non-interactive and bounded by
  timeout.

`Stop`

- Native Codex hook event, rendered as the shared global turn-end commit gate.
- For Codex, it reads exact `fileChange` paths from the parent and recursively
  discovered descendant subagent turns, finalizes every affected repository as one persisted
  transaction, and routes aggregate failures back to the source task.
- Executed shell commands also contribute their cwd and literal filesystem paths
  as repository candidates. This covers inline Python, generators, formatters,
  and commands that partially write before failing, without per-repo adapters.
  Candidates are repository-use evidence, not attribution of every file write:
  even a read command can select a repo with pending changes. Unreferenced dirty
  siblings are untouched; clean candidates are skipped. Computed paths absent
  from command text/cwd still need explicit transaction registration.
- Missing task identity or failed activity discovery stops finalization before
  any Git mutation. Pending transactions remain available for a complete retry;
  the hook returns actionable incomplete-finalization feedback instead of
  publishing only the primary repo. A repeated continuation emits a warning
  rather than starting an unbounded retry loop.
- It uses attributed paths to identify repositories, then consolidates all
  staged and working-tree changes under deterministic repository locks.
  Concurrent tasks may overlap; staged-tree fingerprints detect same-path as
  well as new-path edits arriving during checks, and changed trees are restaged
  and rechecked. Existing local commits are pushed even when no new commit is
  needed. Explicit fast checks and staged-tree stability replace mutable
  commit-hook execution before rebase/push. A successful pull-rebase reruns the
  repo fast check and must leave a clean repository before the push is retried.
- Subagent Stop events do not finalize independently; their parent Stop owns the
  full turn tree. Copilot, Claude, and Antigravity keep the current-repository
  adapter behavior.
- Do not use `scripts/check-fast.sh` as a general after-turn hook.

Codex now exposes a native `SessionEnd` event, but it is synchronous and has a
short execution window. Dobby therefore continues using explicit
`FinalizeCodexThread` for memory work; a future native hook should be piloted
only as a tiny signal/queue trigger, never as the finalization implementation.

## Runtime Payload Contract

Native runtime repo hooks receive one JSON object on stdin. Runtime-specific
details are preserved under `raw_payload`.

```json
{
  "schema_version": "1.0",
  "hook_event_name": "SessionStart",
  "runtime": "codex",
  "cwd": "/Users/dobby/GitHub/example/services/api",
  "repo_root": "/Users/dobby/GitHub/example",
  "session_id": "optional",
  "turn_id": "optional",
  "model": "optional",
  "timestamp": 1760000000000,
  "source": "optional",
  "prompt": "optional",
  "initial_prompt": "optional",
  "final_message": "optional",
  "error": null,
  "transcript_path": "optional",
  "transcript_format": "optional",
  "raw_payload": {}
}
```

Important fields:

- `schema_version`: current adapter contract version. Today this is `1.0`.
- `hook_event_name`: `SessionStart` or `UserPromptSubmit` for native runtime
  hooks.
- `runtime`: `codex`.
- `cwd`: where the Codex session was running.
- `repo_root`: resolved Git top-level directory.
- `session_id`: present when Codex provides one.
- `transcript_path`: present when Codex exposes a transcript file.
- `transcript_format`: format label for `transcript_path`.
- `raw_payload`: original runtime-specific input.

Prefer top-level normalized fields in repo hooks. Read `raw_payload` only when a
runtime-specific detail is genuinely needed.

## Finalization Payload Contract

`finalize_codex_thread.py` receives one JSON object on stdin from the global
finalizer:

```json
{
  "schema_version": "1.0",
  "hook_event_name": "FinalizeCodexThread",
  "command": "finalize-codex-thread",
  "thread_id": "019e...",
  "source_thread_id": "019e...",
  "reason": "stale-cleanup",
  "cwd": "/Users/dobby/GitHub/adi",
  "repo_root": "/Users/dobby/GitHub/adi",
  "archive_requested": true,
  "finalization_mode": "same_thread_turn",
  "thread": {}
}
```

The script should print either:

- a concise instruction for the same-thread finalization turn; or
- nothing, if the repo has no memory/state work to do before archive.

A non-zero exit means finalization failed and the global command must not
archive the source thread.

## Environment

Repo hooks also receive:

```text
AGENT_HOOK_EVENT=SessionStart | UserPromptSubmit | FinalizeCodexThread
AGENT_HOOK_RUNTIME=codex        # native runtime hooks only
AGENT_REPO_ROOT=/absolute/repo/root
AGENT_HOOK_SCHEMA_VERSION=1.0
```

Use `AGENT_REPO_ROOT` or payload `repo_root` for stable repo files. Use payload
`cwd` only when behavior should depend on the subdirectory where the session was
started.

## Minimal Python Helper

This helper is enough for most repo hooks:

```python
#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def read_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def repo_root(payload: dict[str, Any]) -> Path:
    return Path(
        payload.get("repo_root")
        or os.environ.get("AGENT_REPO_ROOT")
        or "."
    ).resolve()


def main() -> int:
    payload = read_payload()
    if payload.get("schema_version") != "1.0":
        return 0

    root = repo_root(payload)
    event = payload.get("hook_event_name")

    # Add repo-specific behavior here.
    print(f"repo={root}")
    print(f"event={event}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

For `session_start.py` and `user_prompt_submit.py`, stdout may become model
context for Codex. Print only concise context that should be shown to the agent.

For `finalize_codex_thread.py`, stdout is consumed by
`finalize-codex-thread.py` as the final-turn instruction. Print no debug text to
stdout.

## Local Smoke Tests

Run a repo hook directly:

```bash
cd /path/to/repo
printf '{"schema_version":"1.0","hook_event_name":"SessionStart","runtime":"codex","cwd":"%s","repo_root":"%s","raw_payload":{}}' "$PWD" "$PWD" \
  | python3 scripts/hooks/session_start.py
```

Run through the shared dispatcher:

```bash
cd /path/to/repo
printf '{"hook_event_name":"SessionStart","cwd":"%s","session_id":"test-session","source":"startup"}' "$PWD" \
  | python3 ~/GitHub/agents/hooks/scripts/session_start.py --runtime codex
```

Run explicit finalization:

```bash
~/GitHub/agents/codex/scripts/finalize-codex-thread.py \
  --thread-id <codex-thread-id> \
  --reason manual \
  --apply \
  --json \
  --no-input
```

Expected behavior:

- Missing repo hook: exits `0`, no output.
- `SessionStart` / `UserPromptSubmit`: stdout may be wrapped and forwarded for Codex.
- `FinalizeCodexThread`: stdout/stderr are consumed by
  `finalize-codex-thread.py`; non-zero exit blocks archive.

## Change Checklist

When adding or changing repo lifecycle hooks:

- Keep hooks non-interactive.
- Keep native runtime hooks fast and deterministic.
- Prefer normalized payload fields over `raw_payload`.
- Use Python only.
- Avoid secrets in stdout, stderr, payload files, and logs.
- If the hook emits context, make it concise and directly useful.
- If the hook needs slow memory work, move it to explicit finalization.

When changing shared hook dispatchers in `~/GitHub/agents`, run:

```bash
cd ~/GitHub/agents
python3 -m py_compile \
  hooks/scripts/hook_adapter.py \
  hooks/scripts/hook_runtime.py \
  hooks/scripts/session_start.py \
  hooks/scripts/user_prompt_submit.py \
  hooks/scripts/stop.py \
  codex/scripts/finalize-codex-thread.py
python3 -m unittest tests.control_plane.test_hooks_control_plane
./scripts/test-control-plane.sh
./scripts/bootstrap-machine-agent-control-planes.sh --apply
./scripts/check-agent-control-planes.sh
```

## Hand-Off Prompt

Use this when asking another agent to add repo-specific hook behavior:

```text
Use ~/GitHub/agents/docs/references/repo-lifecycle-hook-adapter.md.

Add repo-specific lifecycle behavior only under scripts/hooks/*.py.
Do not edit rendered .codex/hooks.json.
Keep native runtime hooks fast and non-interactive.
Run the control-plane tests before handing back.
```
