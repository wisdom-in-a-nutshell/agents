#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Exact-source release gates run from a detached temporary worktree. Code checks use that frozen
# tree, while machine enrollment checks address the canonical managed checkout because temporary
# worktree paths are intentionally absent from the repo bootstrap registry.
MANAGED_REPO_CHECK_ROOT="${AGENTS_MANAGED_REPO_CHECK_ROOT:-$HOME/GitHub/agents}"

scripts/check-repo-hygiene.sh
bash -n hooks/git/pre-commit scripts/sync-managed-git-hooks.sh scripts/check-agent-control-planes.sh scripts/auto-apply-agent-control-planes.sh scripts/enroll-managed-repos.sh scripts/serve-control-plane-dashboard.sh scripts/install-control-plane-dashboard-launchagent.sh scripts/install-prune-stale-copilot-sessions-launchagent.sh scripts/deploy-control-plane-dashboard.sh scripts/local-production-source.sh scripts/switch-claude-provider.sh
scripts/check-skills-registry.sh --staged-ok
scripts/check-plugins-registry.sh --staged-ok
python3 -m unittest tests.control_plane.test_project_archive tests.control_plane.test_skills_sync
python3 -m unittest \
  tests.control_plane.test_hooks_control_plane.HooksControlPlaneTests.test_stop_publication_notifies_local_production_asynchronously \
  tests.control_plane.test_hooks_control_plane.HooksControlPlaneTests.test_stop_publication_skips_non_main_branch \
  tests.control_plane.test_hooks_control_plane.HooksControlPlaneTests.test_stop_publication_notify_failure_does_not_fail_git_finalization
bash tests/control_plane/test_local_production_source.sh
(
  cd "$MANAGED_REPO_CHECK_ROOT"
  "$PWD/scripts/sync-managed-git-hooks.sh" \
    --check \
    --hooks-path "$MANAGED_REPO_CHECK_ROOT/hooks/git" \
    --repo "$MANAGED_REPO_CHECK_ROOT"
  "$PWD/codex/scripts/check-codex-control-plane.sh" --repo "$MANAGED_REPO_CHECK_ROOT"
)

echo "[check-fast] passed"
