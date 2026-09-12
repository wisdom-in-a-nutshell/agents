# Agent Control-Plane Operations

Use this page for the machine-facing apply and validation entrypoints that live at the root of `~/GitHub/agents`.

This repo manages shared agent surfaces for Codex, Claude Code, GitHub Copilot, skills, plugins, MCP presets, lifecycle hooks, and the local dashboard. The temporary Antigravity spike script is tracked for manual experiments but is disabled in the shared machine bootstrap. `~/.agents` is no longer the source checkout; it is reserved for runtime surfaces such as Codex and Copilot user-scope skills at `~/.agents/skills`.

Only `skills/registry.json` is tracked in the top-level `skills/` folder; generated user-scope links are ignored. This keeps any retained historical `~/.agents` checkout from publishing runtime links into the source repo. Fast checks validate the current source tree, while machine-enrollment checks default to `~/GitHub/agents` (override with `AGENTS_MANAGED_REPO_CHECK_ROOT` for another canonical checkout).

Skill sync renders global links without staging them or registering them with Codex Stop; only repo-scoped links participate in checked publication, even if a user-scope runtime folder happens to sit inside a Git checkout.

Sparse machines are normal. A repo listed in the shared registries but not cloned on the current machine is skipped silently by sync/check commands. Existing non-git folders at managed repo paths still warn because they may be broken placeholders that should be deleted or replaced with a real checkout.

For repo authors adding `scripts/hooks/*.py`, start with [`repo-lifecycle-hook-adapter.md`](/Users/dobby/GitHub/agents/docs/references/repo-lifecycle-hook-adapter.md).

## Canonical Entry Points

- `scripts/bootstrap-machine-agent-control-planes.sh`
  - machine-facing full bootstrap batch
  - syncs managed skill links from `skills/registry.json`
  - syncs managed repo local Git `core.hooksPath` to `~/GitHub/agents/hooks/git`
  - skips the temporary Antigravity spike surface by default
  - applies the Claude Code control-plane surface
  - applies the GitHub Copilot CLI control-plane surface
  - applies the VS Code Chat/Agent extension defaults surface
  - applies the Codex runtime via `codex/scripts/bootstrap-machine-codex.sh`
- `scripts/auto-apply-agent-control-planes.sh`
  - machine-facing post-sync reconcile entrypoint
  - checks the current `~/GitHub/agents` commit against a machine-local stamp
  - runs the minimum apply steps needed for runtime-relevant changes
- `scripts/enroll-managed-repos.sh`
  - scans direct child Git repos under `~/GitHub`
  - appends missing repos to `codex/config/repo-bootstrap.json` as minimal entries
  - leaves visualization to the local control-plane dashboard
- `scripts/check-agent-control-planes.sh`
  - validates hygiene, skills, plugins, Copilot CLI state, managed Git hooks, Codex rendered state, and tests
- `scripts/audit-agent-runtime-drift.py`
  - read-only machine-health audit entrypoint
  - checks local Codex runtime drift such as unclassified OpenAI plugins and required plugin availability
- `scripts/sync-managed-git-hooks.sh`
  - sets repo-local `core.hooksPath` to `~/GitHub/agents/hooks/git`
  - supports `--check`
- `scripts/sync-claude.sh`
  - renders `~/.claude/CLAUDE.md` from `config/global.agents.md`
  - renders managed global skill links under `~/.claude/skills`
  - renders managed repo-scoped skill links under each target repo's `.claude/skills` only when that repo enables Claude through `enabled_clients`
  - when invoked inside a Codex thread, registers changed repo-scoped skill-link paths with that thread's existing Stop transaction instead of committing or pushing from the renderer
  - renders repo `.claude/CLAUDE.md`, settings, MCP, hooks, skills, and preview config only when that repo enables Claude through `enabled_clients`; disabling Claude prunes only recognized managed surfaces and leaves hand-written files alone with a warning. Enabled bridge files contain `@../AGENTS.md`, plus the identity import when `model_instructions_clients` includes Claude
  - renders user settings and the managed `Stop` hook under `~/.claude/settings.json`
  - renders managed Claude Desktop SSH entries from `config/claude-settings.json` into `~/.claude/settings.json` `sshConfigs`
  - renders selected Claude Desktop app preferences from `config/claude-settings.json` into `~/Library/Application Support/Claude/config.json`
  - pre-accepts Claude workspace trust in `~/.claude.json` for `~/GitHub`, every direct child folder under `~/GitHub`, discovered nested Git repos, and the agents control-plane repo
  - enables YOLO through Claude Code's native bypass mode
  - prunes retired Herdr lifecycle hook commands from `~/.claude/settings.json` while preserving ordinary custom hooks
  - renders a `~/bin/claude` wrapper that starts sessions with `--dangerously-skip-permissions`
  - the wrapper's real CLI defaults to `~/.local/bin/claude`, installed by Anthropic's official installer (`curl -fsSL https://claude.ai/install.sh | bash`), overridable per invocation with `CLAUDE_REAL_BIN`. Do not install the real CLI from the `claude-code` Homebrew cask: it lags the published version by enough to break newest-model access, and its `claude update` hangs indefinitely under a non-interactive shell. To upgrade, rerun the official installer.
  - VS Code Remote SSH sessions do **not** use this wrapper. The VS Code agent host downloads its own Claude CLI into `~/.vscode-server/data/agent-host/sdk-cache/claude/<version>/`, pinned by the *client* VS Code version, so a stale client on the connecting Mac produces `Claude Code <version> does not support this model` on the remote host no matter what `~/.local/bin/claude` reports. Fix by updating VS Code on the connecting machine and reconnecting.
  - renders per-repo agent preview configs from `dev-servers/registry.json`, opt-in per repo:
    - Claude Code: `.claude/launch.json`
    - Codex: `.codex/environments/environment.toml`
  - wraps preview commands with `scripts/run-agent-preview-server.py`, which reuses an existing listener on the fixed preview port instead of starting a second server
- `scripts/sync-copilot.sh`
  - renders managed GitHub Copilot CLI settings from `config/copilot-settings.json` into `~/.copilot/settings.json`
  - renders managed trusted folders into `~/.copilot/config.json`, where Copilot CLI 1.0.67 stores `trustedFolders`, while preserving Copilot-managed login/session keys
  - renders `~/.copilot/hooks/agents-control-plane.json` from `hooks/registry.json`
  - renders `~/bin/copilot`, a terminal wrapper that defaults sessions to `--yolo --no-ask-user --model claude-sonnet-5 --effort high --mode autopilot --max-autopilot-continues 100 --disable-builtin-mcps --disable-mcp-server ide`
  - renders `.github/github-app.yml` for repos listed in `dev-servers/registry.json` only when that repo enables Copilot through `enabled_clients`, giving the GitHub Copilot app the same Run/browser-ready preview surface without adding app-specific instructions or skills
  - leaves `.github/skills` and `~/.copilot/skills` empty by design; Copilot reuses `.agents/skills` and `~/.agents/skills`
  - allowlists the macOS app-bundled skill names observed under `~/Library/Application Support/com.github.githubapp/app-skills`
  - tool surface scope: `--available-tools` (allowlist) / `--excluded-tools` (denylist) / `--disable-mcp-server` / `--disable-builtin-mcps` are real, tested CLI flags (not persisted `settings.json` keys or env vars) — add them to `launcher.defaultArgs` to trim the **terminal** `copilot` CLI's tool surface. They only affect processes launched through the managed `~/bin/copilot` wrapper. The **GitHub Copilot desktop app** spawns its own session process and injects an additional, larger tool set (canvas/widgets, session/project management, PR review helpers, workflows, cross-session messaging) that is not routed through `~/bin/copilot` and has no known file-based or env-var config surface today — there is currently no way to trim the desktop app's tool set from this control plane.
- `scripts/sync-vscode-agent-defaults.sh`
  - a distinct surface from `sync-copilot.sh`: that script manages the standalone terminal `copilot` CLI (`~/.copilot/*`); this one manages **VS Code's own Chat/Agent extension** defaults, from `config/vscode-agent-defaults.json`
  - renders managed keys into `~/.vscode-server/data/User/globalStorage/agent-host-config.json` (the Copilot-CLI-in-VS-Code "agent host" runtime config), best-effort skipped when `~/.vscode-server` doesn't exist on the machine
  - renders `chat.permissions.default` and `chat.defaultConfiguration` into the real, machine-local VS Code user `settings.json` (`~/Library/Application Support/Code/User/settings.json`), best-effort skipped when VS Code has never run locally on the machine; this is what makes a brand-new session in the VS Code Agents window / Chat view start on **Bypass Approvals + Autopilot** instead of silently falling back to VS Code's built-in "Default Approvals" + interactive mode
  - `agent-host-config.json` is app-owned: the running VS Code extension host rewrites it during normal operation and can silently reset `globalAutoApproveEnabled` back to `false`. This is why the reconcile also runs unconditionally every 15 minutes from `~/GitHub/scripts/sync/git-auto-sync.sh` (`apply_vscode_agent_defaults_reconcile`, alongside the Codex/Copilot trust reconciles) rather than relying on a one-time apply — treat drift here as expected app behavior that self-heals, not a bug
  - deliberately not part of `scripts/check-agent-control-planes.sh`'s strict validation gate, matching the existing precedent for the Codex/Copilot trust reconciles (which are also periodic-apply-only, not check-gated), since this file's drift is driven by the app itself rather than by a bug in this repo
- `scripts/sync-antigravity-spike.sh`
  - temporary manual-only Antigravity experiment
  - not called by `scripts/bootstrap-machine-agent-control-planes.sh`
  - keep out of shared bootstrap automation until a proper opt-in gate exists
- `scripts/sync-skills-registry.sh`
  - renders global Codex skill symlinks into `~/.agents/skills`
  - renders repo-scoped Codex skill symlinks into repo `.agents/skills`
  - stages exact tracked repo skill-link changes and, inside a Codex thread, registers the changed paths with the existing Stop transaction for checked multi-repo finalization
- `scripts/switch-claude-provider.sh`
  - switches the machine-local Claude Code credential profile used by the wrapper between
    Claude subscription OAuth and Amazon Bedrock; Marketplace-backed provider modes are absent
- `scripts/test-control-plane.sh`
  - hermetic regression test entrypoint

## Agent Preview Ports

`dev-servers/registry.json` is the source of truth for short-lived local agent
previews only. It intentionally does not own public Cloudflare/LaunchAgent
services such as `adithyan.io` or `adi.adithyan.io`; those stay documented in
`~/GitHub/scripts`.

Each listed repo gets exactly one preview server. The renderer rejects
`autoPort: true` so Claude Code, Codex, and the GitHub Copilot app cannot drift to different ports. Each
client runs the same generated command through
`scripts/run-agent-preview-server.py`: if `127.0.0.1:<port>` is already
listening, the runner prints that the preview is already running and exits
successfully without spawning another server.

Use `{repo_root}` when the preview should follow the active repo checkout. For
Claude Code and Codex this expands to the canonical local checkout under
`~/GitHub`; for the GitHub Copilot app it expands to
`${COPILOT_WORKSPACE_PATH:-~/GitHub/<repo>}` inside `.github/github-app.yml`,
so worktree sessions preview their own checkout.

## Desktop SSH Connections

Claude Desktop reads preconfigured SSH targets from `~/.claude/settings.json`
`sshConfigs`. The managed `macmini` entry is declared in
`config/claude-settings.json` and rendered by `scripts/sync-claude.sh`; manual
Claude SSH targets with other ids are preserved. The entry uses `sshHost:
macmini`, so the actual address, user, key, and Tailscale transport stay owned
by the scripts repo's `setup/reconcile-ssh-machine-hosts.sh` output in
`~/.ssh/config`.

Codex Desktop does not use this Claude `sshConfigs` list. Codex remote
connections are enabled by `codex/config/global.config.toml`
`features.remote_connections = true` and discovered from the same managed
OpenSSH host aliases in `~/.ssh/config`.

## Desktop App Preferences

Claude Desktop app preferences that need to be reproducible across machines are
declared under `desktopPreferences` in `config/claude-settings.json` and merged
into `~/Library/Application Support/Claude/config.json` by
`scripts/sync-claude.sh`. Existing app config keys, including sign-in state and
unmanaged preferences, are preserved.

The managed `chromeExtensionEnabled: false` preference keeps the Claude in
Chrome connector off by default. The connector does not spend tokens merely
because the native messaging host exists, but keeping the toggle off avoids
accidental browser-context use from Desktop.

## Runtime-Relevant Change Model

`scripts/auto-apply-agent-control-planes.sh` watches:

- `config/`
- `skills/`
- `skills-source/`
- `plugins/`
- `mcp/`
- `codex/`
- `hooks/`
- `scripts/`
- `dev-servers/`

Current apply rules:

- `skills/` or `skills-source/` changes:
  - run `scripts/sync-skills-registry.sh`
  - run `codex/scripts/bootstrap-machine-codex.sh`
- `plugins/` changes:
  - run `scripts/sync-plugins-registry.sh`
  - run `codex/scripts/bootstrap-machine-codex.sh`
- `config/`, `mcp/`, `hooks/`, `codex/`, or `dev-servers/` changes:
  - run the relevant client sync or fall back to the full shared bootstrap batch
- `hooks/git/`, `scripts/sync-managed-git-hooks.sh`, or `codex/config/repo-bootstrap.json` changes:
  - run `scripts/sync-managed-git-hooks.sh`
- first reconcile or missing prior stamp:
  - fall back to the full shared bootstrap batch

## Commands

```bash
cd ~/GitHub/agents
./scripts/bootstrap-machine-agent-control-planes.sh
./scripts/bootstrap-machine-agent-control-planes.sh --apply
./scripts/enroll-managed-repos.sh --dry-run
./scripts/enroll-managed-repos.sh --apply
./scripts/auto-apply-agent-control-planes.sh --dry-run
./scripts/auto-apply-agent-control-planes.sh --apply
./scripts/audit-agent-runtime-drift.py --plain
./scripts/audit-agent-runtime-drift.py --plain --repair-managed-plugin-drift
./scripts/check-agent-control-planes.sh
./scripts/test-control-plane.sh
./scripts/sync-managed-git-hooks.sh --apply
./scripts/sync-managed-git-hooks.sh --check
./scripts/sync-claude.sh --apply
./scripts/sync-copilot.sh --apply
./scripts/sync-copilot.sh --check
./scripts/switch-claude-provider.sh status
./scripts/switch-claude-provider.sh subscription --apply
./scripts/switch-claude-provider.sh bedrock --apply
```

Scoped validation/bootstrap:

```bash
./scripts/bootstrap-machine-agent-control-planes.sh --apply --repo ~/GitHub/agents
./scripts/check-agent-control-planes.sh --repo ~/GitHub/agents
```

## Lifecycle Hook Contract

- Lifecycle hooks are defined in [`hooks/registry.json`](/Users/dobby/GitHub/agents/hooks/registry.json).
- Codex global hooks render into `~/.codex/hooks.json`.
- Codex repo-local hooks render into managed repo `.codex/hooks.json`.
- Claude Code global hooks render into `~/.claude/settings.json`.
- `Stop` is global so the git conveyor does not depend on repo-local hook loading.
- `SessionStart` and `UserPromptSubmit` are repo-scoped to selected repos in `hooks/registry.json`; per-repo `enabled_clients` filters their rendered client targets.
- Explicit Codex thread finalization is not a native hook. The global `codex/scripts/finalize-codex-thread.py` command derives repo policy from `thread/read` and runs optional repo-local `scripts/hooks/finalize_codex_thread.py` before archive.
- Event entrypoints live in [`hooks/scripts/`](/Users/dobby/GitHub/agents/hooks/scripts).
- Repo-specific lifecycle behavior belongs in optional Python scripts under `scripts/hooks/`.
- Missing repo scripts are successful no-ops.

## Commit Gate Contract

- The `Stop` hook runs [`hooks/scripts/stop.py`](/Users/dobby/GitHub/agents/hooks/scripts/stop.py).
- It stages changes, runs `git commit`, and lets Git invoke the shared local hook from [`hooks/git/pre-commit`](/Users/dobby/GitHub/agents/hooks/git/pre-commit).
- The shared Git hook delegates to repo-owned `scripts/check-fast.sh` when present.
- For tracked branches, Stop optimistically pushes first and only runs `git pull --rebase` when the push shows the remote is ahead.
- Brand-new branches without upstream tracking use `git push -u <remote> HEAD`.
- After a successful `main` push, Stop emits a bounded best-effort revision notification to the shared Mac Mini local-production control plane. Builds remain outside the hook, non-`main` branches never notify production, and notifier failure does not invalidate the successful Git push because periodic reconciliation is the recovery path.
- Stop hook timing is logged to `~/.local/state/agents-control-plane/log/hooks-stop.log`.
