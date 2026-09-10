# Codex Control Plane Script Flows

This page breaks the Codex control-plane scripts into a few small flows. Use
[Codex Control Plane](/Users/dobby/GitHub/agents/docs/architecture/codex-control-plane.md)
for the top-level system shape and
[Codex Control Plane Operations](/Users/dobby/GitHub/agents/docs/references/codex-control-plane-operations.md)
for exact commands.

## Apply Flow

```mermaid
flowchart TD
    P["plugins/registry.json"] --> Q["validate plugin registry"]
    P --> B["sync-config.sh"]
    A["bootstrap-machine-codex.sh"] --> B
    A --> C["sync-trusted-projects.sh"]
    A --> D["sync-repo-codex-configs.sh"]
    R["repo-bootstrap.json"] --> C
    R --> D
    M["mcp/config/presets.json"] --> B
    M --> D
    H["hooks/registry.json"] --> B
    H --> D
    B --> F["~/.codex/config.toml + ~/.codex/hooks.json"]
    C --> F
    D --> G["repo .codex/config.toml + .codex/hooks.json"]
```

Main scripts:

- `codex/scripts/bootstrap-machine-codex.sh`: Codex-specific bootstrap batch.
- `codex/scripts/sync-config.sh`: global Codex config, global hooks, MCPs, and global plugins.
- `codex/scripts/sync-repo-codex-configs.sh`: managed repo `.codex/config.toml` and `.codex/hooks.json`.
- `scripts/sync-plugins-registry.sh`: validates the plugin registry.
- `codex/config/repo-bootstrap.json`: managed repo set and repo-local Codex overrides.

## Post-Sync Reconcile

```mermaid
flowchart TD
    A["git-auto-sync.sh"] --> B["auto-apply-agent-control-planes.sh"]
    B --> C{"What changed in ~/GitHub/agents?"}
    C -->|"skills"| D["sync-skills-registry.sh"]
    C -->|"plugins"| E["sync-plugins-registry.sh"]
    C -->|"git hooks or repo registry"| F["sync-managed-git-hooks.sh"]
    C -->|"Codex/runtime inputs"| G["bootstrap-machine-codex.sh --apply"]
    D --> H["Update runtime skill links"]
    E --> I["Validate plugin registry"]
    F --> J["Update repo core.hooksPath"]
    G --> K["Update Codex runtime"]
    H --> L["Update reconcile stamp"]
    I --> L
    J --> L
    K --> L
```

`scripts/auto-apply-agent-control-planes.sh` keeps a machine-local stamp under
`~/.local/state/agents-control-plane/` so each machine can apply only changes it
has not already reconciled.

## Session And Prompt Dispatch

```mermaid
flowchart TD
    A["hooks/registry.json"] --> B["managed repo .codex/hooks.json"]
    B --> C["Codex SessionStart"]
    C --> D["hooks/scripts/session_start.py"]
    D --> E{"repo scripts/hooks/session_start.py exists?"}
    E -->|"yes"| F["run Python hook from repo root"]
    F --> G["forward stdout as additional context"]
    E -->|"no"| H["silent success"]

    B --> I["Codex UserPromptSubmit"]
    I --> J["hooks/scripts/user_prompt_submit.py"]
    J --> K{"repo scripts/hooks/user_prompt_submit.py exists?"}
    K -->|"yes"| L["run Python hook from repo root"]
    L --> M["forward stdout as additional context"]
    K -->|"no"| N["silent success"]
```

The shared dispatcher resolves the git root from the hook payload `cwd`, passes a
normalized JSON payload to the repo hook on stdin, and sets
`AGENT_HOOK_EVENT`, `AGENT_HOOK_RUNTIME`, `AGENT_REPO_ROOT`, and
`AGENT_HOOK_SCHEMA_VERSION`.

The control plane does not render a fake native-looking `SessionEnd` hook.
End-of-thread memory work is handled by explicit thread finalization.

## Explicit Thread Finalization

```mermaid
flowchart TD
    A["caller has Codex thread id"] --> B["finalize-codex-thread.py --thread-id"]
    B --> C["app-server thread/read"]
    C --> D["derive cwd + repo root"]
    D --> E{"repo scripts/hooks/finalize_codex_thread.py exists?"}
    E -->|"yes"| F["run repo hook for final-turn instruction"]
    E -->|"no"| G["archive-only finalization"]
    F --> H{"instruction emitted?"}
    H -->|"yes"| I["app-server turn/start in same source thread"]
    I --> J["wait for turn/completed"]
    H -->|"no"| K["skip final turn"]
    J --> L["app-server thread/archive"]
    K --> L
    G --> L
```

The thread id is the canonical input. Callers should not pass a separate repo
path or directory hint; the finalizer derives that from Codex App Server state.

## Post-Turn Automation

```mermaid
flowchart TD
    A["hooks/registry.json global Stop"] --> B["~/.codex/hooks.json"]
    B --> C["Codex turn reaches Stop"]
    C --> D["hooks/scripts/stop.py"]
    D --> E["read parent + subagent fileChange items"]
    E --> F["map exact paths to Git worktrees"]
    F --> G["lock all affected repositories in sorted order"]
    G --> H["consolidate all staged + working-tree changes"]
    H --> I["run every repo scripts/check-fast.sh in parallel"]
    I --> J{"new edits arrived during checks?"}
    J -->|"yes"| H
    J -->|"no"| K{"all checks passed?"}
    K -->|"no"| L["return aggregate feedback to the source task"]
    K -->|"yes"| M["commit consolidated repository state"]
    M --> N["persist committed phases"]
    N --> O["push every local commit"]
    O --> P{"remote ahead?"}
    P -->|"yes"| Q["git pull --rebase, then retry push"]
    P -->|"no"| R["best-effort main revision notification"]
    Q --> R
    R --> S["remove completed transaction"]
```

`scripts/check-fast.sh` is the repo-owned fast commit gate for agent-made
changes. Put slower repo-wide validation in `scripts/check-full.sh` or another
explicit command. Codex stores incomplete multi-repository transactions under
`~/.local/state/agents-control-plane/codex-stop-transactions/`, so a later Stop
can finish pushing repositories already committed before another repository
failed. Subagent Stop events defer to their parent; the parent Stop follows
`parentThreadId` recursively and aggregates the complete descendant turn tree.
The transaction also holds a discovery checkpoint while activity retrieval is
incomplete. A later retry replays the original boundary turn and subsequent
activity before finalizing; it cannot treat an empty retry turn as evidence that
only the primary repository changed. Discovery failures preserve state and
return bounded retry feedback without Git mutation.
Repository discovery combines structured file changes, explicit shell registrations,
and executed-command cwd/literal paths through `hooks/scripts/codex_shell_paths.py`.
It does not evaluate command text or inspect command output. Git state under the
existing repository locks remains the authority for what gets consolidated;
unreferenced sibling repos are not scanned.
Concurrent Codex tasks may edit the same
repository or file: the shared working tree is consolidated under the Git lock,
and edits that arrive during checks are restaged and rechecked up to a bounded
retry limit instead of being discarded.

The revision notification is deliberately after a successful push and contains
only the repo root plus final commit SHA. It wakes the shared Mac Mini production
reconciler when that repo is registered. It does not run checks or builds inside
the Stop hook, and a notifier failure cannot reverse an already successful Git
publication. If the Mac mini later pulls that revision, Git auto-sync emits the
same durable notification; the writer health sweep reports remaining revision
drift without deploying it.
