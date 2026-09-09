#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
COMMAND = "finalize-codex-thread"
HOOK_EVENT = "FinalizeCodexThread"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_FINALIZATION_TIMEOUT_SECONDS = 900.0
MAX_OUTPUT_CHARS = 12_000
REPO_FINALIZER = Path("scripts/hooks/finalize_codex_thread.py")


class AppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class FinalizeResult:
    thread_id: str
    cwd: str | None
    repo_root: str | None
    finalizer_path: str | None
    finalizer_status: str
    finalization_turn_id: str | None
    finalization_turn_status: str | None
    archived: bool
    skipped_reason: str | None
    error: str | None


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def truncate_text(value: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = value.strip()
    if len(text) <= limit:
        return text
    suffix = "\n...[truncated]"
    return text[: max(0, limit - len(suffix))] + suffix


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def emit_plain(payload: dict[str, Any]) -> None:
    status = payload["status"]
    data = payload.get("data") or {}
    result = data.get("result") or {}
    if status == "ok":
        action = "archived" if result.get("archived") else "resolved"
        print(
            f"ok {action} id={result.get('thread_id')} "
            f"cwd={result.get('cwd') or 'unknown'} "
            f"repo_hook={result.get('finalizer_status')} "
            f"turn={result.get('finalization_turn_status') or 'none'}"
        )
        return

    error = payload.get("error") or {}
    print(f"error {error.get('code', 'error')}: {error.get('message', status)}", file=sys.stderr)
    if error.get("hint"):
        print(error["hint"], file=sys.stderr)


def finish(
    *,
    status: str,
    started_at: float,
    plain: bool,
    result: FinalizeResult | None = None,
    error: dict[str, Any] | None = None,
    exit_code: int,
) -> int:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND,
        "status": status,
        "data": {"result": asdict(result) if result else None},
        "error": error,
        "meta": {
            "timestamp_utc": utc_now(),
            "duration_ms": int((time.time() - started_at) * 1000),
        },
    }
    if plain:
        emit_plain(payload)
    else:
        emit_json(payload)
    return exit_code


class AppServerClient:
    """Read and archive through the daemon also used by Desktop SSH clients."""

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.connection: Any = None
        self.next_id = 1

    def __enter__(self) -> "AppServerClient":
        try:
            self.start()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        try:
            from websockets.sync.client import unix_connect
        except ImportError as exc:
            raise AppServerError(
                "Missing WebSocket dependency; run "
                "codex/scripts/install-thread-finalizer-deps.sh --apply"
            ) from exc

        codex_dir = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        socket_path = codex_dir / "app-server-control" / "app-server-control.sock"
        try:
            self.connection = unix_connect(
                str(socket_path),
                uri="ws://localhost/rpc",
                compression=None,
                user_agent_header=None,
                open_timeout=self.timeout_seconds,
                close_timeout=2,
            )
        except Exception as exc:
            raise AppServerError(
                f"Cannot connect to shared app-server at {socket_path}: {exc}; "
                "check `codex app-server daemon version`"
            ) from exc
        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "agents_finalize_codex_thread",
                    "title": "Agents Codex Thread Finalizer",
                    "version": SCHEMA_VERSION,
                }
            },
        )
        self.notify("initialized", {})

    def close(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None

    def _write(self, message: dict[str, Any]) -> None:
        if self.connection is None:
            raise AppServerError("shared app-server is not connected")
        self.connection.send(json.dumps(message, separators=(",", ":")))

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._write(message)
        deadline = time.monotonic() + (timeout_seconds or self.timeout_seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AppServerError(f"timed out waiting for {method} response id={request_id}")
            try:
                payload = json.loads(self.connection.recv(timeout=remaining))
            except Exception as exc:
                raise AppServerError(f"shared app-server {method} response failed: {exc}") from exc
            if not isinstance(payload, dict):
                raise AppServerError("shared app-server returned a non-object message")
            if "id" in payload and "method" in payload:
                # This connection only reads and archives; it never owns model work or approvals.
                self._write({"id": payload["id"], "error": {"code": -32601, "message": "Unsupported server request"}})
                continue
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                error = payload["error"]
                detail = error.get("message") if isinstance(error, dict) else str(error)
                raise AppServerError(f"{method} failed: {detail}")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise AppServerError(f"{method} returned a non-object result")
            return result


def thread_read(client: AppServerClient, thread_id: str) -> dict[str, Any]:
    result = client.request("thread/read", {"threadId": thread_id, "includeTurns": False})
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise AppServerError("thread/read returned malformed response")
    return thread


def archive_thread(client: AppServerClient, thread_id: str) -> None:
    client.request("thread/archive", {"threadId": thread_id})


def is_nonfatal_archive_error(exc: Exception) -> bool:
    return "no rollout found for thread id" in str(exc)


def repo_root_for_cwd(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return str(Path(cwd).expanduser().resolve())
    if result.returncode != 0 or not result.stdout.strip():
        return str(Path(cwd).expanduser().resolve())
    return str(Path(result.stdout.strip()).expanduser().resolve())


def run_repo_finalizer(
    *,
    finalizer_path: Path,
    repo_root: str,
    thread: dict[str, Any],
    reason: str,
    timeout_seconds: float,
    finalization_timeout_seconds: float,
) -> tuple[bool, str | None, str | None]:
    thread_id = str(thread.get("id") or "")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "hook_event_name": HOOK_EVENT,
        "thread_id": thread_id,
        "reason": reason,
    }
    env = os.environ.copy()
    try:
        result = subprocess.run(
            [sys.executable, str(finalizer_path)],
            cwd=repo_root,
            env=env,
            input=json.dumps(payload, sort_keys=True),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds + finalization_timeout_seconds + 30,
        )
    except subprocess.TimeoutExpired as exc:
        return False, None, f"repo finalizer timed out after {exc.timeout}s"
    except OSError as exc:
        return False, None, f"repo finalizer failed to start: {exc}"

    if result.returncode == 0:
        return True, result.stdout.strip() or None, None
    output = "\n".join(part for part in [result.stderr, result.stdout] if part.strip())
    message = output.strip() or f"repo finalizer exited {result.returncode}"
    return False, None, truncate_text(message)


def finalize_thread(
    *,
    thread_id: str,
    reason: str,
    dry_run: bool,
    timeout_seconds: float,
    finalization_timeout_seconds: float,
    client_factory: Any = AppServerClient,
) -> FinalizeResult:
    with client_factory(timeout_seconds) as client:
        thread = thread_read(client, thread_id)
    cwd = thread.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return FinalizeResult(
            thread_id=thread_id,
            cwd=None,
            repo_root=None,
            finalizer_path=None,
            finalizer_status="not_run",
            finalization_turn_id=None,
            finalization_turn_status=None,
            archived=False,
            skipped_reason="missing_thread_cwd",
            error="thread/read did not return a usable cwd",
        )

    repo_root = repo_root_for_cwd(cwd)
    finalizer_path = Path(repo_root) / REPO_FINALIZER
    finalizer_path_str = str(finalizer_path) if finalizer_path.is_file() else None

    if dry_run:
        return FinalizeResult(
            thread_id=thread_id,
            cwd=cwd,
            repo_root=repo_root,
            finalizer_path=finalizer_path_str,
            finalizer_status="would_run" if finalizer_path_str else "not_found",
            finalization_turn_id=None,
            finalization_turn_status=None,
            archived=False,
            skipped_reason="dry_run",
            error=None,
        )

    finalizer_status = "not_found"
    if finalizer_path_str:
        ok, output, error = run_repo_finalizer(
            finalizer_path=finalizer_path,
            repo_root=repo_root,
            thread=thread,
            reason=reason,
            timeout_seconds=timeout_seconds,
            finalization_timeout_seconds=finalization_timeout_seconds,
        )
        if not ok:
            return FinalizeResult(
                thread_id=thread_id,
                cwd=cwd,
                repo_root=repo_root,
                finalizer_path=finalizer_path_str,
                finalizer_status="failed",
                finalization_turn_id=None,
                finalization_turn_status=None,
                archived=False,
                skipped_reason="finalizer_failed",
                error=error,
            )
        finalizer_status = "completed"
        if output:
            print(
                f"[finalize-codex-thread] repo finalizer output: {truncate_text(output, 1000)}",
                file=sys.stderr,
            )

    with client_factory(timeout_seconds) as client:
        try:
            archive_thread(client, thread_id)
        except Exception as exc:
            if is_nonfatal_archive_error(exc):
                return FinalizeResult(
                    thread_id=thread_id,
                    cwd=cwd,
                    repo_root=repo_root,
                    finalizer_path=finalizer_path_str,
                    finalizer_status=finalizer_status,
                    finalization_turn_id=None,
                    finalization_turn_status=None,
                    archived=False,
                    skipped_reason="archive_unavailable",
                    error=None,
                )
            return FinalizeResult(
                thread_id=thread_id,
                cwd=cwd,
                repo_root=repo_root,
                finalizer_path=finalizer_path_str,
                finalizer_status=finalizer_status,
                finalization_turn_id=None,
                finalization_turn_status=None,
                archived=False,
                skipped_reason="archive_failed",
                error=str(exc),
            )

    return FinalizeResult(
        thread_id=thread_id,
        cwd=cwd,
        repo_root=repo_root,
        finalizer_path=finalizer_path_str,
        finalizer_status=finalizer_status,
        finalization_turn_id=None,
        finalization_turn_status=None,
        archived=True,
        skipped_reason=None,
        error=None,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize a Codex thread by deriving repo policy from thread/read, running an optional repo finalizer, then archiving the thread."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Run finalization and archive the thread.")
    mode.add_argument("--dry-run", action="store_true", help="Resolve repo policy without running finalization or archive (default).")
    parser.add_argument("--thread-id", required=True, help="Codex/App Server thread id to finalize.")
    parser.add_argument("--reason", default="manual", help="Reason label passed to repo finalizers.")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--finalization-timeout-seconds", type=float, default=DEFAULT_FINALIZATION_TIMEOUT_SECONDS)
    parser.add_argument("--no-input", action="store_true", help="Accepted for non-interactive callers; this command never prompts.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    parser.add_argument("--plain", action="store_true", help="Emit compact plain text for operator inspection.")
    return parser.parse_args()


def main() -> int:
    started_at = time.time()
    args = parse_args()
    plain = bool(args.plain)
    dry_run = not bool(args.apply)

    try:
        if args.timeout_seconds <= 0:
            raise ValueError("--timeout-seconds must be positive")
        if args.finalization_timeout_seconds <= 0:
            raise ValueError("--finalization-timeout-seconds must be positive")
        result = finalize_thread(
            thread_id=args.thread_id,
            reason=args.reason,
            dry_run=dry_run,
            timeout_seconds=args.timeout_seconds,
            finalization_timeout_seconds=args.finalization_timeout_seconds,
        )
        status = "ok" if result.error is None and (dry_run or result.archived) else "error"
        if result.error is None and result.skipped_reason == "archive_unavailable":
            status = "ok"
        exit_code = 0
        if status != "ok":
            exit_code = 5 if "timed out" in (result.error or "") else 4
        return finish(
            status=status,
            started_at=started_at,
            plain=plain,
            result=result,
            error=None
            if status == "ok"
            else {
                "code": "FinalizeFailed",
                "message": result.error or result.skipped_reason or "thread finalization failed",
                "hint": "Check the repo finalizer, Codex app-server availability, and thread/archive behavior.",
            },
            exit_code=exit_code,
        )
    except ValueError as exc:
        return finish(
            status="error",
            started_at=started_at,
            plain=plain,
            error={
                "code": "InvalidUsage",
                "message": str(exc),
                "retryable": False,
                "hint": "Fix the command arguments and retry.",
            },
            exit_code=2,
        )
    except Exception as exc:
        return finish(
            status="error",
            started_at=started_at,
            plain=plain,
            error={
                "code": exc.__class__.__name__,
                "message": str(exc),
                "retryable": True,
                "hint": "Run with --dry-run --json, then check Codex app-server availability.",
            },
            exit_code=4,
        )


if __name__ == "__main__":
    raise SystemExit(main())
