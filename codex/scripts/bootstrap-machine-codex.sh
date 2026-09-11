#!/usr/bin/env bash
set -euo pipefail

APPLY=0
GITHUB_ROOT="${HOME}/GitHub"
GLOBAL_CONFIG="${HOME}/.codex/config.toml"
GLOBAL_HOOKS="${HOME}/.codex/hooks.json"
GLOBAL_AGENTS="${HOME}/.codex/AGENTS.md"
REPO_FILTERS=()

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Apply the Codex-specific portion of machine bootstrap from the canonical
personal control plane in ~/GitHub/agents.

Default mode is dry-run. Use --apply to write changes.

Options:
  --apply                Apply changes
  --dry-run              Show actions only (default)
  --github-root <path>   Root used for workspace-write + repo trust scan
  --global-config <p>    Override ~/.codex/config.toml target
  --global-hooks <p>     Override ~/.codex/hooks.json target
  --global-agents <p>    Override ~/.codex/AGENTS.md target
  --repo <path>          Limit repo-local sync/check to an exact repo path
                         (repeatable)
  -h, --help             Show this help

Examples:
  ~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh
  ~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh --apply
  ~/GitHub/agents/codex/scripts/bootstrap-machine-codex.sh --apply --repo ~/GitHub/adi
USAGE
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --apply)
      APPLY=1
      shift
      ;;
    --dry-run)
      APPLY=0
      shift
      ;;
    --github-root)
      GITHUB_ROOT="${2:-}"
      shift 2
      ;;
    --global-config)
      GLOBAL_CONFIG="${2:-}"
      shift 2
      ;;
    --global-hooks)
      GLOBAL_HOOKS="${2:-}"
      shift 2
      ;;
    --global-agents)
      GLOBAL_AGENTS="${2:-}"
      shift 2
      ;;
    --repo)
      REPO_FILTERS+=("${2:-}")
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown option: $1"
      ;;
  esac
done

MODE_FLAG="--dry-run"
if (( APPLY == 1 )); then
  MODE_FLAG="--apply"
fi

REPO_ARGS=()
for repo in "${REPO_FILTERS[@]}"; do
  REPO_ARGS+=(--repo "$repo")
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYNC_CONFIG_SCRIPT="${SCRIPT_DIR}/sync-config.sh"
SYNC_GLOBAL_AGENTS_SCRIPT="${SCRIPT_DIR}/sync-global-agents-md.sh"
SYNC_TRUSTED_SCRIPT="${SCRIPT_DIR}/sync-trusted-projects.sh"
SYNC_REPO_CONFIGS_SCRIPT="${SCRIPT_DIR}/sync-repo-codex-configs.sh"
SYNC_HOOK_TRUST_SCRIPT="${SCRIPT_DIR}/sync-hook-trust-state.py"
PDF_DEPS_SCRIPT="${SCRIPT_DIR}/install-pdf-skill-deps.sh"
FINALIZE_STALE_THREADS_LAUNCHAGENT_SCRIPT="${SCRIPT_DIR}/install-finalize-stale-codex-threads-launchagent.sh"
ARCHIVE_CLAUDE_SESSIONS_LAUNCHAGENT_SCRIPT="${SCRIPT_DIR}/install-archive-stale-claude-sessions-launchagent.sh"
CHECK_CONTROL_PLANE_SCRIPT="${SCRIPT_DIR}/check-codex-control-plane.sh"

[[ -x "$SYNC_CONFIG_SCRIPT" ]] || die "Missing executable: $SYNC_CONFIG_SCRIPT"
[[ -x "$SYNC_GLOBAL_AGENTS_SCRIPT" ]] || die "Missing executable: $SYNC_GLOBAL_AGENTS_SCRIPT"
[[ -x "$SYNC_TRUSTED_SCRIPT" ]] || die "Missing executable: $SYNC_TRUSTED_SCRIPT"
[[ -x "$SYNC_REPO_CONFIGS_SCRIPT" ]] || die "Missing executable: $SYNC_REPO_CONFIGS_SCRIPT"
[[ -x "$SYNC_HOOK_TRUST_SCRIPT" ]] || die "Missing executable: $SYNC_HOOK_TRUST_SCRIPT"
[[ -x "$PDF_DEPS_SCRIPT" ]] || die "Missing executable: $PDF_DEPS_SCRIPT"
[[ -x "$FINALIZE_STALE_THREADS_LAUNCHAGENT_SCRIPT" ]] || die "Missing executable: $FINALIZE_STALE_THREADS_LAUNCHAGENT_SCRIPT"
[[ -x "$ARCHIVE_CLAUDE_SESSIONS_LAUNCHAGENT_SCRIPT" ]] || die "Missing executable: $ARCHIVE_CLAUDE_SESSIONS_LAUNCHAGENT_SCRIPT"
[[ -x "$CHECK_CONTROL_PLANE_SCRIPT" ]] || die "Missing executable: $CHECK_CONTROL_PLANE_SCRIPT"

sync_config_cmd=(
  "$SYNC_CONFIG_SCRIPT"
  "$MODE_FLAG"
  --github-root "$GITHUB_ROOT"
  --global-config "$GLOBAL_CONFIG"
  --global-hooks "$GLOBAL_HOOKS"
)
log "+ ${sync_config_cmd[*]}"
"${sync_config_cmd[@]}"

sync_global_agents_cmd=(
  "$SYNC_GLOBAL_AGENTS_SCRIPT"
  "$MODE_FLAG"
  --global-agents "$GLOBAL_AGENTS"
)
log "+ ${sync_global_agents_cmd[*]}"
"${sync_global_agents_cmd[@]}"

sync_trusted_cmd=(
  "$SYNC_TRUSTED_SCRIPT"
  "$MODE_FLAG"
  --root "$GITHUB_ROOT"
  --global-config "$GLOBAL_CONFIG"
)
log "+ ${sync_trusted_cmd[*]}"
"${sync_trusted_cmd[@]}"

sync_repo_configs_cmd=(
  "$SYNC_REPO_CONFIGS_SCRIPT"
  "$MODE_FLAG"
  "${REPO_ARGS[@]}"
)
log "+ ${sync_repo_configs_cmd[*]}"
"${sync_repo_configs_cmd[@]}"

sync_hook_trust_cmd=(
  "$SYNC_HOOK_TRUST_SCRIPT"
  "$MODE_FLAG"
  --global-config "$GLOBAL_CONFIG"
  --global-hooks "$GLOBAL_HOOKS"
  "${REPO_ARGS[@]}"
)
log "+ ${sync_hook_trust_cmd[*]}"
"${sync_hook_trust_cmd[@]}"

pdf_deps_cmd=(
  "$PDF_DEPS_SCRIPT"
  "$MODE_FLAG"
)
log "+ ${pdf_deps_cmd[*]}"
"${pdf_deps_cmd[@]}"

finalize_stale_threads_launchagent_cmd=(
  "$FINALIZE_STALE_THREADS_LAUNCHAGENT_SCRIPT"
  "$MODE_FLAG"
)
log "+ ${finalize_stale_threads_launchagent_cmd[*]}"
"${finalize_stale_threads_launchagent_cmd[@]}"

# Claude Desktop session archiver: only install where Claude Desktop is present.
if [[ -d "${HOME}/Library/Application Support/Claude/claude-code-sessions" ]]; then
  archive_claude_sessions_launchagent_cmd=(
    "$ARCHIVE_CLAUDE_SESSIONS_LAUNCHAGENT_SCRIPT"
    "$MODE_FLAG"
  )
  log "+ ${archive_claude_sessions_launchagent_cmd[*]}"
  "${archive_claude_sessions_launchagent_cmd[@]}"
else
  log "skip: Claude Desktop session dir not found; not installing claude-session-archiver LaunchAgent"
fi

check_cmd=(
  "$CHECK_CONTROL_PLANE_SCRIPT"
  --global-config "$GLOBAL_CONFIG"
  --global-hooks "$GLOBAL_HOOKS"
  "${REPO_ARGS[@]}"
)
log "+ ${check_cmd[*]}"
"${check_cmd[@]}"
