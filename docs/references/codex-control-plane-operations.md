# Codex Control Plane Operations

Use this page when you need the exact facts for changing or validating the personal Codex control plane.

Use [Codex Control Plane](/Users/dobby/GitHub/agents/docs/architecture/codex-control-plane.md) for the high-level system shape.
Use [Codex Control Plane Script Flows](/Users/dobby/GitHub/agents/docs/architecture/codex-control-plane-script-flows.md) for smaller diagrams of the main script groups.
Use [Codex Control Plane Ownership](/Users/dobby/GitHub/agents/docs/references/codex-control-plane-ownership.md) for the exact keep/move/generate split.

## What Lives Where

- `~/GitHub/agents`
  - canonical shared agent control-plane source
  - config templates in [`codex/config/`](/Users/dobby/GitHub/agents/codex/config)
  - bundled Codex skill allow/disable policy in [`codex/config/bundled-skills-policy.json`](/Users/dobby/GitHub/agents/codex/config/bundled-skills-policy.json)
  - Codex-specific scripts in [`codex/scripts/`](/Users/dobby/GitHub/agents/codex/scripts)
  - Codex shell fragment in [`codex/shell/codex-shell.zsh`](/Users/dobby/GitHub/agents/codex/shell/codex-shell.zsh)
- `~/GitHub/scripts`
  - generic machine bootstrap and shared shell glue
  - shared zshrc in [`setup/codex/zshrc.shared`](/Users/dobby/GitHub/scripts/setup/codex/zshrc.shared)
  - shared zprofile in [`setup/codex/zprofile.shared`](/Users/dobby/GitHub/scripts/setup/codex/zprofile.shared)
  - machine bootstrap entrypoint in [`setup/bootstrap-machine.sh`](/Users/dobby/GitHub/scripts/setup/bootstrap-machine.sh)
- `~/.codex`
  - live runtime home only
  - applied `config.toml`, global `hooks.json`, auth, sessions, logs, caches, sqlite, shell snapshots
  - global Codex hooks such as `Stop` live in `~/.codex/hooks.json`; repo-specific context hooks live in managed repo `.codex/hooks.json`
  - Codex-managed vendor imports in `vendor_imports/`, including the nested Git checkout at `vendor_imports/skills`
  - should not be a git repo
- `~/.local/state/codex-control-plane`
  - machine-local reconcile stamps and quarantine state

## Canonical Commands

- Apply the shared machine-facing agent bootstrap batch:
  - [`bootstrap-machine-agent-control-planes.sh`](/Users/dobby/GitHub/agents/scripts/bootstrap-machine-agent-control-planes.sh)
  - `~/GitHub/agents/scripts/bootstrap-machine-agent-control-planes.sh --apply`
  - this syncs managed skill links, native Codex plugin state, repo-local hook files, and the Codex runtime control plane from one stable root entrypoint
- Auto-apply the shared agent control plane after `~/GitHub/agents` sync when runtime-relevant files changed:
  - [`auto-apply-agent-control-planes.sh`](/Users/dobby/GitHub/agents/scripts/auto-apply-agent-control-planes.sh)
  - `~/GitHub/agents/scripts/auto-apply-agent-control-planes.sh --apply`
  - this is the machine-facing post-sync entrypoint that external bootstrap repos should call
- Enroll top-level GitHub repos into the managed repo bootstrap registry:
  - [`enroll-managed-repos.sh`](/Users/dobby/GitHub/agents/scripts/enroll-managed-repos.sh)
  - `~/GitHub/agents/scripts/enroll-managed-repos.sh --apply --github-root ~/GitHub`
  - this scans only direct child Git repos under the GitHub root and adds missing minimal entries to [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json)
- Validate shared skills, plugins, repo-local hook files, and Codex rendered runtime state:
  - [`check-agent-control-planes.sh`](/Users/dobby/GitHub/agents/scripts/check-agent-control-planes.sh)
  - `~/GitHub/agents/scripts/check-agent-control-planes.sh`
- Validate managed plugins:
  - [`sync-plugins-registry.sh`](/Users/dobby/GitHub/agents/scripts/sync-plugins-registry.sh)
  - `~/GitHub/agents/scripts/sync-plugins-registry.sh --apply`
  - global native Codex plugin enable/disable state is rendered by `sync-config.sh`
  - native Codex plugins are treated as global/user-level; use repo-local MCP presets for repo-specific tool availability
- Bootstrap one managed plugin into the canonical registry:
  - [`bootstrap-plugin.sh`](/Users/dobby/GitHub/agents/scripts/bootstrap-plugin.sh)
  - `~/GitHub/agents/scripts/bootstrap-plugin.sh browser --apply`
  - this writes the registry entry, validates it, and reapplies the shared control planes
- Apply the full Codex bootstrap batch:
  - [`bootstrap-machine-codex.sh`](/Users/dobby/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh)
  - `~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh --apply`
  - this applies the Codex control-plane outputs only, including the stale-thread finalization LaunchAgent; shared shell links still come from `~/GitHub/scripts/setup/codex/`
- Check stale Codex threads without finalizing:
  - [`finalize-stale-codex-threads.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-stale-codex-threads.py)
  - `~/GitHub/agents/codex/scripts/finalize-stale-codex-threads.py --dry-run --older-than-hours 24`
  - eligibility is based on Codex `thread.updatedAt`, not creation time
- Install/update the stale-thread finalization LaunchAgent:
  - [`install-finalize-stale-codex-threads-launchagent.sh`](/Users/dobby/GitHub/agents/codex/scripts/install-finalize-stale-codex-threads-launchagent.sh)
  - `~/GitHub/agents/codex/scripts/install-finalize-stale-codex-threads-launchagent.sh --apply`
  - default schedule is every hour, finalizing managed-repo threads whose last update is older than 24 hours
- Check stale Claude Desktop sidebar sessions without archiving:
  - [`archive-stale-claude-sessions.py`](/Users/dobby/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py)
  - `~/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py --plain --older-than-hours 24`
  - eligibility is based on the session metadata `lastActivityAt`; currently-running sessions and transcripts under `~/.claude/projects` are never touched
- Install/update the Claude session archiver LaunchAgent:
  - [`install-archive-stale-claude-sessions-launchagent.sh`](/Users/dobby/GitHub/agents/codex/scripts/install-archive-stale-claude-sessions-launchagent.sh)
  - `~/GitHub/agents/codex/scripts/install-archive-stale-claude-sessions-launchagent.sh --apply`
  - default schedule is every hour, flipping `isArchived` on Claude Desktop sessions idle longer than 24 hours; archived sessions leave the sidebar on the next Claude Desktop restart
- Auto-apply the Codex control plane after `~/GitHub/agents` sync when `codex/` changed:
  - [`auto-apply-codex-control-plane.sh`](/Users/dobby/GitHub/agents/codex/scripts/auto-apply-codex-control-plane.sh)
  - `~/GitHub/agents/codex/scripts/auto-apply-codex-control-plane.sh --apply`
  - use this for targeted Codex-only troubleshooting or component-scoped automation, not as the machine-facing shared reconcile entrypoint
- Apply only the managed Codex config:
  - [`sync-config.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-config.sh)
  - `~/GitHub/agents/codex/scripts/sync-config.sh --apply`
  - this syncs the managed global config, global `hooks.json`, removes orphaned managed profile files, and removes stale managed agent-role files from older control-plane versions
- Validate canonical and rendered Codex control-plane state:
  - [`check-codex-control-plane.sh`](/Users/dobby/GitHub/agents/codex/scripts/check-codex-control-plane.sh)
  - `~/GitHub/agents/codex/scripts/check-codex-control-plane.sh`
- Sync exact trusted repo roots into the global Codex config:
  - [`sync-trusted-projects.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-trusted-projects.sh)
  - `~/GitHub/agents/codex/scripts/sync-trusted-projects.sh --apply`
- Sync repo-local `.codex/config.toml` and `.codex/hooks.json` files from the canonical registries:
  - [`sync-repo-codex-configs.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-repo-codex-configs.sh)
  - `~/GitHub/agents/codex/scripts/sync-repo-codex-configs.sh --apply`
- Validate the repo bootstrap registry:
  - [`sync-repo-bootstrap-registry.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-repo-bootstrap-registry.sh)
  - `~/GitHub/agents/codex/scripts/sync-repo-bootstrap-registry.sh`
- Link the shared shell config:
  - [`link-shared-zshrc.sh`](/Users/dobby/GitHub/scripts/setup/codex/link-shared-zshrc.sh)
  - `~/GitHub/scripts/setup/codex/link-shared-zshrc.sh --apply`
  - [`link-shared-zprofile.sh`](/Users/dobby/GitHub/scripts/setup/codex/link-shared-zprofile.sh)
  - `~/GitHub/scripts/setup/codex/link-shared-zprofile.sh --apply`

## Healthy State Checklist

- `~/.codex` is runtime-only:
  - `git -C ~/.codex rev-parse --git-dir` should fail
- `~/.zshrc` points at the shared tracked shell file:
  - `readlink ~/.zshrc`
  - expected target: `~/GitHub/scripts/setup/codex/zshrc.shared`
- `~/.zprofile` points at the shared tracked login-shell file:
  - `readlink ~/.zprofile`
  - expected target: `~/GitHub/scripts/setup/codex/zprofile.shared`
- `~/.codex/config.toml` does not use Codex `notify`; global hook automation is rendered into `~/.codex/hooks.json`, and repo-assigned hooks are rendered into managed repo `.codex/hooks.json` from `hooks/registry.json`.
- The global `Stop` hook owns the managed-repo git conveyor:
  - reads Codex App Server `fileChange` items for the parent turn and recursively follows `parentThreadId` for descendant subagents
  - also discovers repositories from executed `commandExecution` working directories and literal absolute, `~/`, `./`, or `../` paths in command text, including commands that exited with an error after writing files; shell/Python edits no longer require a `fileChange` event when their repository is visible in command evidence
  - command use selects a repository for normal consolidation even when that individual command only reads it; clean repositories without local commits are skipped, and dirty siblings absent from the task's command/file evidence are not swept
  - parses command input as text only: no command evaluation, environment expansion, command-output inspection, or filesystem-wide scanning; dynamically computed paths not visible in any command cwd/literal still require the existing explicit registration helper
  - shell discovery limits return actionable Stop feedback rather than silently finalizing only the primary repository
  - also adopts exact repo skill-link paths registered by shell-based control-plane syncs through `CODEX_THREAD_ID`, so bootstrap changes that do not surface as App Server `fileChange` items still use the same checked multi-repo finalization path
  - merges descendant-thread registrations into the parent Stop transaction before finalization; subagent Stop events continue to defer to the parent
  - uses those exact absolute paths to identify affected Git worktrees, then consolidates all current staged and working-tree changes inside each affected repository
  - ignores subagent Stop events because the parent Stop owns the complete turn transaction
  - allows concurrent Codex tasks to edit the same repository or file and uses deterministic per-repository locks to serialize only stage/check/commit/push finalization
  - adopts pre-staged work into the consolidated commit instead of dropping or indefinitely orphaning another task's changes
  - preflights every affected repo's `scripts/check-fast.sh` concurrently and reruns checks when the staged Git tree changes during validation, including same-path content edits that do not change the path set
  - absorbs nonzero formatter/autofix passes into the same bounded restage/recheck loop when each failing repo's own tree changed; unchanged failures stop immediately and unresolved failures after three passes return feedback. A repaired tree must pass before commit or push
  - commits with mutable commit hooks disabled after the explicit fast-check and staged-tree stability gates
  - persists partial commit/push progress so a later continuation can finish all repositories without losing already-created commits
  - detects and pushes existing local commits even when the current turn has no new file changes in its primary repository
  - relies on managed repo local `core.hooksPath` pointing at `~/GitHub/agents/hooks/git`
  - returns aggregated hook feedback to the originating Codex task when any repository fails, so the same task can repair every affected repository
  - if task identity or activity discovery is unavailable, finalizes no repositories and preserves pending transaction state; the first failure returns retry feedback and a repeated hook continuation reports incomplete finalization without an infinite retry loop. It never silently downgrades to primary-repo-only publication. The normal primary-repo Git-status check still catches local work after successful discovery
  - records `discovery_started_at` in the existing transaction before reading activity; recovery replays the original boundary turn and subsequent turns, including their subagents, so a retry with no new file edits cannot forget the original sibling repos. Explicit path registration preserves this checkpoint, and complete discovery clears it only after saving the discovered repositories. Unavailable retained history reports incomplete discovery
  - tracked branches use an optimistic `commit -> push` path and only run `git pull --rebase` when push reports that the remote is ahead; after a successful rebase, the hook reruns `scripts/check-fast.sh` and requires a clean repository before retrying the push
  - brand-new branches without upstream tracking use an initial `git push -u <remote> HEAD`, so the hook can publish the branch before future tracked-branch pulls
  - after each successful `main` push, best-effort notifies `~/GitHub/scripts/sync/local-production-notify.sh` with the repo root and final commit SHA; the hook never builds an app, skips non-`main` branches and unregistered repos, and does not turn a successful Git publication into a failure when the local notifier is unavailable; Mac mini Git auto-sync emits the same event for a changed `main` revision it pulls, while the separate writer health sweep reports remaining revision drift without deploying it
  - logs phase timing to `~/.local/state/agents-control-plane/log/hooks-stop.log`
  - leaves Copilot, Claude, and Antigravity on the existing current-repository finalization path; multi-repository attribution is Codex-only
- Bootstrap and sync scripts remain renderers: they do not commit or push repositories directly. When `CODEX_THREAD_ID` is absent, such as unattended machine reconciliation, they apply runtime state without creating a Codex Stop transaction.
- `~/.codex/config.toml` contains exact trusted repo entries for local repos such as `focus`
- `~/.codex/config.toml` enables Codex hooks through `[features].hooks = true`
- `~/.codex/config.toml` contains `[hooks.state]` trust hashes for managed hooks rendered by this control plane, so global and repo-local lifecycle hooks do not need repeated `/hooks` review on every machine bootstrap.
- `~/.codex/config.toml` explicitly preserves enabled native Codex plugins such as `computer-use@openai-bundled`, points `openai-bundled` at the marketplace inside `ChatGPT.app`, and disables bundled Codex skills classified as `disabled` in [`bundled-skills-policy.json`](/Users/dobby/GitHub/agents/codex/config/bundled-skills-policy.json)
- `scripts/audit-agent-runtime-drift.py --repair-managed-plugin-drift` repairs missing managed native Codex plugin config/cache state by running [`sync-config.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-config.sh) once and then re-auditing. The scripts repo health check uses this path so app/runtime cache churn does not require a manual `computer-use` repair.
- `~/.codex/auth.json` uses `auth_mode = "chatgpt"`. In the normal global default, `~/.codex/config.toml` leaves `model_provider` unset, so Codex uses the default OpenAI ChatGPT account provider. [`global.config.toml`](/Users/dobby/GitHub/agents/codex/config/global.config.toml) also leaves `model`, reasoning effort, and `service_tier` unset so a client's new-thread selection wins and Codex starts on its normal Standard tier.
- `~/.codex/chatgpt.config.toml` and `~/.codex/autofix.config.toml` are managed profile overlays copied from [`codex/config/`](/Users/dobby/GitHub/agents/codex/config). Use `codex --profile chatgpt` for an explicit account-provider run; the health incident responder uses `codex --profile autofix`. Embedded `[profiles.<name>]` tables are legacy and are not used.
- The autonomous `autofix` profile selects `gpt-5.6-sol` at medium effort. When the account model catalog changes, update the canonical profile, run `codex/scripts/sync-config.sh --apply`, and verify a non-mutating `codex exec --profile autofix` request with hooks disabled before relying on unattended remediation. A profile that parses and renders successfully can still name a model rejected by the account provider.
- Keep Fast controls available, but do not set a top-level `service_tier`; Fast is an explicit client/thread choice rather than a managed default. Do not add separate desktop-only service-tier keys unless Codex has a verified compact-safe desktop service-tier path.
- `~/.codex/hooks.json` is rendered from `hooks/registry.json` for global Codex hooks. The managed `Stop` hook renders there; repo-specific lifecycle hooks such as `SessionStart` and `UserPromptSubmit` render into repo `.codex/hooks.json`.
- `com.<user>.codex-thread-finalizer` is loaded as a LaunchAgent and runs [`finalize-stale-codex-threads.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-stale-codex-threads.py) every hour against managed repo paths from [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json).
- `~/.codex/config.toml` contains no Git conflict markers
- `~/.codex/vendor_imports/skills` is a valid Git checkout:
  - `git -C ~/.codex/vendor_imports/skills rev-parse --show-toplevel`

## Main Scripts And Jobs

- [`sync-config.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-config.sh)
  - applies the canonical Codex config template into the live global config
  - copies canonical Codex profile files from `codex/config/*.config.toml` into `~/.codex/*.config.toml`, excluding `global.config.toml`
  - keeps `chatgpt.config.toml` available as the explicit account-provider profile
  - keeps Apps/connectors enabled through the managed `features.apps = true` baseline; app-backed plugins such as Google Drive require this layer in addition to plugin enablement. With Apps disabled, plugin skills can load while connector actions remain absent. Verify live tool exposure after runtime reload; config readback alone does not prove connector access.
  - renders global-scope native Codex plugin enable/disable state from [`plugins/registry.json`](/Users/dobby/GitHub/agents/plugins/registry.json)
  - points the `openai-bundled` marketplace at `ChatGPT.app` directly and seeds `~/.codex/plugins/cache` only for bundled plugins enabled by the registry
  - disables selected bundled Codex skills in `~/.codex/config.toml` from [`bundled-skills-policy.json`](/Users/dobby/GitHub/agents/codex/config/bundled-skills-policy.json) when the control plane should prefer managed skill copies or avoid duplicate runtime surfaces
  - rewrites machine-specific system-skill paths for the current `$HOME`
  - renders only global Codex lifecycle hooks from [`hooks/registry.json`](/Users/dobby/GitHub/agents/hooks/registry.json) into `~/.codex/hooks.json`
  - strips foreign-user project and system-skill entries before writing
  - prunes stale global `apps.*` and `plugins.*` sections that are no longer present in the canonical template or plugin registry, so old local connector/plugin state does not stick around
  - prunes stale global terminal `mcp_servers.*` sections that are no longer present in the canonical template
  - prunes stale managed agent declarations and runtime role files left by older control-plane versions
  - fails fast if the target config contains unresolved Git conflict markers
  - skips no-op rewrites
- [`sync-hook-trust-state.py`](/Users/dobby/GitHub/agents/codex/scripts/sync-hook-trust-state.py)
  - computes Codex's normalized hook trust hash for managed global and repo-local hooks
  - writes those hashes under `[hooks.state]` in `~/.codex/config.toml`
  - is intentionally scoped to hooks rendered from the shared control plane, not arbitrary repo hooks
- [`sync-trusted-projects.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-trusted-projects.sh)
  - scans repo roots from the canonical repo bootstrap registry (defaults to `~/GitHub`)
  - includes explicit extra managed repos such as `~/GitHub/agents`
  - writes exact `[projects."<path>"] trust_level = "trusted"` entries
  - removes managed `[projects."<path>"]` entries when a repo registry item sets `codex_trust: false`
  - skips no-op rewrites
- [`sync-repo-codex-configs.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-repo-codex-configs.sh)
  - renders managed repo-local Codex files from the shared repo inventory plus shared MCP and hook registries
  - supports `--check` to fail when rendered repo-local files differ from the current `.codex` files
  - writes `.codex/config.toml` for all managed repos
  - writes `.codex/hooks.json` for all managed repos with only the hooks assigned to that repo
  - prunes stale managed repo-local `.codex/agents/*.toml` files left by older control-plane versions
  - skips no-op rewrites instead of dirtying the git repos unnecessarily
  - keeps the repo list and repo-level behavior assignments in [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json); model, effort, and service tier remain client-owned
  - resolves Codex cells from the MCP target matrix in [`mcp/config/presets.json`](/Users/dobby/GitHub/agents/mcp/config/presets.json)
- [`sync-managed-git-hooks.sh`](/Users/dobby/GitHub/agents/scripts/sync-managed-git-hooks.sh)
  - applies local-only `core.hooksPath` for every managed repo in [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json)
  - points Git at [`hooks/git/pre-commit`](/Users/dobby/GitHub/agents/hooks/git/pre-commit)
  - does not edit repo worktree files and does not affect GitHub Actions
- [`check-codex-control-plane.sh`](/Users/dobby/GitHub/agents/codex/scripts/check-codex-control-plane.sh)
  - validates canonical `global.config.toml`, `repo-bootstrap.json`, and `mcp/config/presets.json`
  - validates [`bundled-skills-policy.json`](/Users/dobby/GitHub/agents/codex/config/bundled-skills-policy.json) and fails if a local OpenAI-bundled Codex skill exists under `~/.codex/skills/.system` or `~/.codex/skills/codex-primary-runtime` without being classified as `allowed` or `disabled`
  - validates that the live global Codex config disables each skill classified as `disabled`
  - validates [`hooks/registry.json`](/Users/dobby/GitHub/agents/hooks/registry.json), rendered global `~/.codex/hooks.json`, and rendered repo-local `.codex/hooks.json` files when hooks are enabled
  - fails if managed agent declarations reappear in canonical or generated Codex config
  - runs `sync-repo-codex-configs.sh --check`, so stale or hand-edited repo-local `.codex/config.toml`, `.codex/hooks.json`, and older managed `.codex/agents/*.toml` files fail validation
- [`sync-repo-bootstrap-registry.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-repo-bootstrap-registry.sh)
  - validates [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json)
  - validates MCP definitions and repository/client targets from [`mcp/config/presets.json`](/Users/dobby/GitHub/agents/mcp/config/presets.json)
  - rejects unknown repos or clients and target combinations that cannot be isolated by the available runtime surfaces
- [`bootstrap-machine-codex.sh`](/Users/dobby/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh)
  - runs config sync
  - runs trusted-project sync
  - runs repo-local Codex config sync
  - runs Ghostty config reconciliation
  - installs the stale-thread finalization LaunchAgent
  - runs control-plane validation at the end and fails if the rendered state is inconsistent
- [`finalize-stale-codex-threads.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-stale-codex-threads.py)
  - starts a short-lived Codex app-server JSONL client, uses `thread/list` for eligibility, then invokes [`finalize-codex-thread.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-codex-thread.py) for each stale thread
  - reads managed repo paths from [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json) unless `--repo` filters are supplied; it lists stale active threads globally, then keeps only threads whose `cwd` is inside a managed checkout, shares that checkout's Git common directory, or has the same normalized Git origin URL, so linked and already-removed Codex worktrees are included without widening cleanup to unrelated repositories
  - finalizes only non-archived threads whose `updatedAt` is older than the configured threshold; default is 24 hours
  - does not try to detect what the Desktop app currently has loaded; the safety boundary is the last-activity cutoff
  - isolates per-thread finalizer failures: a locked or broken thread is reported with `skipped_reason=finalizer_failed`, remaining candidates are still processed, and the command exits `4` with `error.code=PartialFinalizeFailure` so runtime health checks retain a failure signal
  - plain scheduler logs include the run timestamp and each failed/skipped task's reason and underlying error; child JSON error messages take precedence over incidental stderr output
  - executes the Python child finalizer with `sys.executable` so selection, finalization, and repo policy share the scheduled interpreter
  - defaults to dry-run; use `--apply` for actual finalization
  - uses a machine-local lock under `~/.local/state/codex-control-plane/` so overlapping launchd runs do not race
- [`finalize-codex-thread.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-codex-thread.py)
  - takes only `--thread-id` as canonical thread identity
  - connects using WebSocket over `$CODEX_HOME/app-server-control/app-server-control.sock` (default `~/.codex`) to the existing daemon, uses `thread/read` to derive the thread `cwd`, resolves the repo root, runs optional repo policy at `scripts/hooks/finalize_codex_thread.py`, then archives the source thread through `thread/archive`; repo policy owns any finalization model turn
  - uses `websockets==16.0`, installed by `codex/scripts/install-thread-finalizer-deps.sh --apply` for the shared preferred Python. The socket requires WebSocket framing with compression disabled; `codex app-server proxy` only relays bytes and cannot accept bare JSONL requests
  - requires the shared daemon even for a dry-run; check it with `codex app-server daemon version`. A missing daemon fails before repo policy runs, without falling back to a private server; the hourly scheduler retries eligible tasks on its next run
  - the daemon broadcasts `thread/archived` to connected clients, allowing a Desktop SSH connection using that daemon to remove the sidebar entry. The archive/sidebar step makes no model calls and does not edit Codex databases directly
  - checks shared-daemon activity before repo policy and again before archive, including loaded descendants because archive closes descendants too; an active task family is left for a later run with `skipped_reason=active_thread`. These checks reduce races but are not an atomic conditional archive. Connection or archive failure may leave the task unarchived, and a later retry may rerun its repo hook
  - future work: detect disconnected Desktop clients and reconcile missed archive notifications after reconnect. A running daemon does not prove the app is connected, notifications are not replayed, and local Desktop clients using their own stdio server do not receive the daemon's events
  - one-time stale-sidebar recovery: confirm a task is already archived, then call the app's `set_thread_archived` tool with its host/id and `archived: true`. The app can remove the stale catalog entry while preserving the archive; repeating `thread/archive` on the daemon alone cannot repair an already-archived entry. No unarchive/rearchive cycle or database edits are needed
  - an `already has an active writer` repo-policy error can mean an idle task is still owned by another Desktop server. For one-time recovery, run the workspace's normal final memory instruction through the owning app, verify that turn completed, then archive through the app. Do not bypass failed memory preservation or mistake an idle writer lock for a running model turn
  - for repos without `scripts/hooks/finalize_codex_thread.py`, finalization is archive-only
- [`install-finalize-stale-codex-threads-launchagent.sh`](/Users/dobby/GitHub/agents/codex/scripts/install-finalize-stale-codex-threads-launchagent.sh)
  - renders `~/Library/LaunchAgents/com.<user>.codex-thread-finalizer.plist`
  - resolves the machine's preferred Python through `~/GitHub/scripts/setup/codex/resolve-preferred-homebrew-python.sh` (currently Python 3.13), pins that interpreter explicitly in `ProgramArguments`, and installs/verifies its WebSocket dependency before loading the job; `--python` is an explicit override
  - never rely on the scheduler's bare `python3`: `/opt/homebrew/bin/python3` may point to a different major/minor version than the managed shell. User-installed Python packages belong to an interpreter version, so an interactive import check alone does not validate launchd
  - schedules [`finalize-stale-codex-threads.py`](/Users/dobby/GitHub/agents/codex/scripts/finalize-stale-codex-threads.py) every hour by default
  - removes the legacy `com.<user>.codex-session-archiver` LaunchAgent if present during apply
  - writes logs under `~/.local/state/codex-control-plane/log/`
  - supports dry-run output before writing or loading launchd state
- [`archive-stale-claude-sessions.py`](/Users/dobby/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py)
  - archives stale Claude Desktop sidebar sessions by flipping `isArchived` in the per-session metadata under `~/Library/Application Support/Claude/{claude-code-sessions,local-agent-mode-sessions}`
  - eligibility is the metadata `lastActivityAt` cutoff (default 24 hours); already-archived, `--keep-session`, and currently-running sessions are skipped, and transcripts under `~/.claude/projects` are never read or modified
  - running sessions are detected via the live handshake files under `~/.claude/sessions/` (matching each handshake `sessionId` to the metadata `cliSessionId`)
  - defaults to dry-run; `--apply` backs up each changed file under `~/.local/state/claude-control-plane/` before writing, behind a machine-local lock
  - does not quit or reopen Claude Desktop; archived sessions leave the active list on the next app restart
  - if Claude Desktop ever changes its session metadata shape the archiver fails fast with `E_SCHEMA` and writes nothing; the `claude_session_archiver` check in [`audit-agent-runtime-drift.py`](/Users/dobby/GitHub/agents/scripts/audit-agent-runtime-drift.py) surfaces that drift so the `~/GitHub/scripts` `ops/health-check.sh` run notifies Slack and the archiver can be updated
- [`install-archive-stale-claude-sessions-launchagent.sh`](/Users/dobby/GitHub/agents/codex/scripts/install-archive-stale-claude-sessions-launchagent.sh)
  - renders `~/Library/LaunchAgents/com.<user>.claude-session-archiver.plist`
  - schedules [`archive-stale-claude-sessions.py`](/Users/dobby/GitHub/agents/codex/scripts/archive-stale-claude-sessions.py) every hour by default with a 24-hour stale threshold
  - writes logs under `~/.local/state/codex-control-plane/log/`
  - supports dry-run output before writing or loading launchd state
- [`auto-apply-codex-control-plane.sh`](/Users/dobby/GitHub/agents/codex/scripts/auto-apply-codex-control-plane.sh)
  - checks whether `~/GitHub/agents/codex/` changed since the last successful reconcile on that machine
  - runs [`bootstrap-machine-codex.sh`](/Users/dobby/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh) only when a new Codex control-plane revision needs to be applied
  - stores a machine-local reconcile stamp under `~/.local/state/codex-control-plane/`
  - remains available when you intentionally want a Codex-only reconcile outside the shared machine-facing wrapper
- [`open-ghostty-codex-picker-current.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-codex-picker-current.sh)
  - inputs `codex_jump` into the focused Ghostty terminal
  - is the tracked helper used by the optional manual Keyboard Maestro `Cmd+Shift+G` macro
- [`codex-shell.zsh`](/Users/dobby/GitHub/agents/codex/shell/codex-shell.zsh)
  - `codex_jump` sets the Ghostty tab/surface title to the selected repo basename before launching Codex
  - `codex_jump` also reports the selected cwd back to Ghostty immediately so regular new tabs and splits inherit the active repo instead of falling back to `~`
  - `codex_jump` ranks picker rows by a decaying active working-set score: each selection adds `1`, scores halve every 6 hours by default, and `CODEX_JUMP_SCORE_HALFLIFE_HOURS` can tune the half-life
- [`open-ghostty-codex-tab.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-codex-tab.sh)
  - opens a new Ghostty tab with a custom surface configuration and immediately runs `codex`
  - is the tracked helper used by the optional manual Keyboard Maestro `Cmd+Opt+T` macro
- [`open-ghostty-codex-split.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-codex-split.sh)
  - opens a Ghostty split with a custom surface configuration and immediately runs `codex`
  - is the tracked helper used by the optional manual Keyboard Maestro `Cmd+Opt+D` macro
- [`open-ghostty-codex-picker-tab.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-codex-picker-tab.sh)
  - opens a new Ghostty tab with a custom surface configuration and immediately runs `codex_jump`
  - is the one tracked helper used by both the Stadia controller `Share` action and the optional manual Keyboard Maestro `Cmd+Shift+T` macro
- [`open-ghostty-codex-picker-split.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-codex-picker-split.sh)
  - opens a Ghostty split with a custom surface configuration and immediately runs `codex_jump` in the new split
  - is the tracked helper used by the Stadia controller `leftThumbstickButton` split-picker action
- [`open-ghostty-plain-shell-split.sh`](/Users/dobby/GitHub/agents/codex/scripts/open-ghostty-plain-shell-split.sh)
  - opens a Ghostty split with `CODEX_DISABLE_AUTOSTART=1` so the new pane stays a plain shell in the inherited cwd even if autostart is re-enabled for a session
  - remains available as a helper when you explicitly need a plain-shell split override beyond Ghostty's default `Cmd+D`

## Shared Registry Fields

- [`repo-bootstrap.json`](/Users/dobby/GitHub/agents/codex/config/repo-bootstrap.json) currently controls these per-repo fields:
  - `codex_trust`
  - `personality`
  - `model_instructions_file`
  - `developer_instructions`
  - `project_root_markers`
  - `features`
- Shared MCP definitions and all repo/client targets live separately in [`mcp/config/presets.json`](/Users/dobby/GitHub/agents/mcp/config/presets.json).
- Shared lifecycle hook definitions live separately in [`hooks/registry.json`](/Users/dobby/GitHub/agents/hooks/registry.json).
- Native Codex plugin scope and state lives separately in [`plugins/registry.json`](/Users/dobby/GitHub/agents/plugins/registry.json).
- The global defaults block supplies fallback values for allowed repo behavior. It must not contain model, effort, profile, Fast/service-tier, or related thread-selection keys.

## Automatic Cross-Machine Apply

- Launchd still lives in [`~/GitHub/scripts/sync/git-auto-sync.sh`](/Users/dobby/GitHub/scripts/sync/git-auto-sync.sh), because scheduler ownership is part of the generic machine-ops repo.
- Machine-facing multi-surface apply now lives in [`auto-apply-agent-control-planes.sh`](/Users/dobby/GitHub/agents/scripts/auto-apply-agent-control-planes.sh), which calls the Codex and shared repo-local entrypoints as needed after `~/GitHub/agents` sync.
- When shared skill inputs change, that wrapper also reruns the Codex bootstrap so machine-side dependencies for managed skills such as `pdf` stay converged.
- That same wrapper validates the plugin registry and reapplies Codex config when plugin state changes.
- When `hooks/registry.json` or `codex/config/repo-bootstrap.json` changes, that wrapper syncs global Codex hooks and repo-local Codex hooks in managed repos.
- Codex-specific post-sync apply logic still lives in [`auto-apply-codex-control-plane.sh`](/Users/dobby/GitHub/agents/codex/scripts/auto-apply-codex-control-plane.sh) as an optional lower-level Codex-only reconcile helper.
- Practical flow:
  1. one machine pushes a change in `~/GitHub/agents`
  2. the other machine pulls it on the next git auto-sync cycle
  3. `git-auto-sync.sh` calls `auto-apply-agent-control-planes.sh`
  4. that script runs the required shared skills, plugin, Git hook, and Codex apply steps based on what changed
- Result:
  - no daily manual Codex bootstrap is needed on healthy machines
  - offline machines catch up on the next successful sync after they come online

## Known Failure Modes

### Conflict Markers In `config.toml`

Symptom:
- Codex prints `key with no value, expected '='`
- lines in `config.toml` include `<<<<<<<`, `=======`, or `>>>>>>>`

Meaning:
- a prior sync/pull left unresolved Git conflict markers in the live config

Current protection:
- [`sync-config.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-config.sh) now refuses to run against a config containing conflict markers

Fix:
- remove the conflict block from the live config
- rerun `~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh --apply`

### Foreign Absolute Paths In Live Config

Symptom:
- a machine under `/Users/adi` contains `/Users/dobby/...` paths, or vice versa

Meaning:
- machine-specific config entries were preserved from another machine

Current protection:
- [`sync-config.sh`](/Users/dobby/GitHub/agents/codex/scripts/sync-config.sh) now rewrites local system-skill paths and strips foreign-user project entries before applying

Fix:
- rerun `~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh --apply`

### Repeated Trust Prompts For Nested Repos

Symptom:
- Codex keeps asking whether a repo like `~/GitHub/focus` is trusted

Meaning:
- the exact repo root is missing from `[projects.*]`, even if a parent path is trusted

Fix:
- rerun `~/GitHub/agents/codex/scripts/sync-trusted-projects.sh --apply`

### Stale Snapshot-Refresh LaunchAgent

Symptom:
- `launchctl print gui/$(id -u)/com.<user>.codex-app-server-snapshot-refresh` shows a scheduled job with `last exit code = 78`
- the job points at `~/GitHub/agents/scripts/refresh-codex-app-server-readme-reference.sh`
- that script path no longer exists

Meaning:
- this is leftover machine state from the older Codex App Server snapshot-refresh automation
- the automation was removed, so recreating the missing script is the wrong fix

Fix:
- unload and delete the stale LaunchAgent plist:
- `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/com.$USER.codex-app-server-snapshot-refresh.plist >/dev/null 2>&1 || true`
- `rm -f ~/Library/LaunchAgents/com.$USER.codex-app-server-snapshot-refresh.plist`

### Recommended Skills Fail To Load

Symptom:
- Codex App shows `Unable to load recommended skills`
- message says `Expected ~/.codex/vendor_imports/skills to be a git checkout but found an existing directory`

Meaning:
- the runtime-managed checkout under `~/.codex/vendor_imports/skills` was deleted or flattened into a plain directory

Fix:
- restore `~/.codex/vendor_imports/skills` as a real clone of `https://github.com/openai/skills.git`
- verify with `git -C ~/.codex/vendor_imports/skills rev-parse --show-toplevel`
- restart Codex App if it is already open

## Machine Notes

- Both current machines were aligned through this control-plane layout:
  - local machine under `/Users/dobby`
  - Adi MacBook via SSH alias `adithyans-macbook-pro` under `/Users/adi`
    - this alias is managed by `~/GitHub/scripts/setup/reconcile-ssh-machine-hosts.sh`
    - it reaches the MacBook through Tailscale with `ProxyCommand tailscale nc %h %p`
    - older references to `macbook-wan` are stale and should not be used as current setup guidance
- The control plane is designed to be home-relative at apply time, not by committing one machine's absolute paths into canonical templates.

