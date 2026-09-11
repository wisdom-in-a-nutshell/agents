#!/usr/bin/env bash
set -euo pipefail

APPLY=0
PRINT_PYTHON=0
PYTHON_BIN=""
WEBSOCKETS_VERSION="16.0"

usage() {
  cat <<USAGE
Usage: $(basename "$0") [options]

Ensure the Python WebSocket dependency used by Codex thread finalization.

Default mode is dry-run. Use --apply to install the pinned version when needed.

Options:
  --apply            Install missing or mismatched dependencies
  --dry-run          Show actions only (default)
  --python <path>    Override the shared preferred Homebrew Python
  --print-python     Print the resolved interpreter only; do not check or install
  -h, --help         Show this help
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
    --python)
      [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "--python requires a path"
      PYTHON_BIN="$2"
      shift 2
      ;;
    --print-python)
      PRINT_PYTHON=1
      shift
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

if [[ -z "$PYTHON_BIN" ]]; then
  python_resolver="${HOME}/GitHub/scripts/setup/codex/resolve-preferred-homebrew-python.sh"
  [[ -x "$python_resolver" ]] || die "Preferred Python resolver missing: $python_resolver. Bootstrap ~/GitHub/scripts or pass --python explicitly."
  PYTHON_BIN="$("$python_resolver" --output python)" || die "Cannot resolve preferred Homebrew Python; check $python_resolver."
fi
command -v "$PYTHON_BIN" >/dev/null 2>&1 || die "Python not found: $PYTHON_BIN"
PYTHON_BIN="$("$PYTHON_BIN" -c 'import sys; print(sys.executable)')" || die "Cannot inspect the selected Python interpreter"
[[ "$PYTHON_BIN" == /* && -x "$PYTHON_BIN" ]] || die "Python returned an invalid executable: $PYTHON_BIN"
if (( PRINT_PYTHON == 1 )); then
  printf '%s\n' "$PYTHON_BIN"
  exit 0
fi
log "Python: $PYTHON_BIN"

check_dependency() {
  "$PYTHON_BIN" - "$WEBSOCKETS_VERSION" <<'PY'
import sys
from importlib.metadata import version

expected = sys.argv[1]
try:
    from websockets.sync.client import unix_connect

    installed = version("websockets")
except Exception as exc:
    print(f"websockets dependency unavailable: {exc}")
    sys.exit(1)
if installed != expected:
    print(f"websockets=={installed} installed; expected websockets=={expected}")
    sys.exit(1)
print(f"websockets=={installed} with synchronous Unix socket support")
PY
}

if dependency_status="$(check_dependency 2>&1)"; then
  log "OK: $dependency_status"
  exit 0
fi

log "$dependency_status"
if (( APPLY == 0 )); then
  log "WOULD INSTALL: websockets==$WEBSOCKETS_VERSION via $PYTHON_BIN -m pip install --user"
  exit 0
fi

pip_args=(install --user)
pip_install_help="$("$PYTHON_BIN" -m pip install --help 2>/dev/null || true)"
if [[ "$pip_install_help" == *"--break-system-packages"* ]]; then
  pip_args+=(--break-system-packages)
fi
pip_args+=("websockets==$WEBSOCKETS_VERSION")
log "+ $PYTHON_BIN -m pip ${pip_args[*]}"
"$PYTHON_BIN" -m pip "${pip_args[@]}"
dependency_status="$(check_dependency 2>&1)" || die "$dependency_status"
log "OK: $dependency_status"
