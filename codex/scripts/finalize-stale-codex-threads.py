#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


SCHEMA_VERSION = "1.0"
COMMAND = "finalize-stale-codex-threads"
DEFAULT_OLDER_THAN_HOURS = 24.0
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_FINALIZATION_TIMEOUT_SECONDS = 900.0
DEFAULT_PAGE_LIMIT = 100
DEFAULT_MAX_REPORT = 80
MAX_ERROR_CHARS = 12_000
ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT_DIR / "codex" / "config" / "repo-bootstrap.json"
DEFAULT_LOCK = Path.home() / ".local" / "state" / "codex-control-plane" / "finalize-stale-codex-threads.lock"
DEFAULT_FINALIZER_COMMAND = ROOT_DIR / "codex" / "scripts" / "finalize-codex-thread.py"

SOURCE_KINDS = [
    "cli",
    "vscode",
    "exec",
    "appServer",
    "subAgent",
    "subAgentReview",
    "subAgentCompact",
    "subAgentThreadSpawn",
    "subAgentOther",
    "unknown",
]


class AppServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Candidate:
    thread_id: str
    name: str
    cwd: str
    updated_at: int
    source: Any
    status: Any
    path: str | None

    @property
    def updated_at_utc(self) -> str:
        return datetime.fromtimestamp(self.updated_at, UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ManagedRepoScope:
    roots: tuple[Path, ...]
    common_git_dirs: frozenset[Path]
    origin_urls: frozenset[str]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def expand_path(raw: str) -> str:
    return str(Path(raw).expanduser().absolute())


def load_registry_repos(registry: Path) -> list[str]:
    data = json.loads(registry.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        entries = data.get("repos")
    else:
        entries = data
    if not isinstance(entries, list):
        raise ValueError(f"expected repo registry with a repos list: {registry}")

    repos: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_path = entry.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        path = expand_path(raw_path.strip())
        if path not in seen:
            seen.add(path)
            repos.append(path)
    return repos


def parse_timestamp(value: str) -> float:
    raw = value.strip()
    if not raw:
        raise argparse.ArgumentTypeError("timestamp cannot be empty")
    try:
        return float(raw)
    except ValueError:
        pass

    normalized = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "timestamp must be an epoch value or ISO timestamp"
        ) from exc


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def one_line(value: str, *, max_chars: int = 120) -> str:
    text = " ".join(value.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "..."


def truncate_text(value: str, *, max_chars: int = MAX_ERROR_CHARS) -> str:
    text = value.strip()
    if len(text) <= max_chars:
        return text
    suffix = "\n...[truncated]"
    return text[: max(0, max_chars - len(suffix))] + suffix


def emit_plain(payload: dict[str, Any]) -> None:
    status = payload["status"]
    data = payload.get("data", {})
    if "candidate_count" in data:
        mode = "apply" if data.get("applied") else "dry-run"
        outcome = "ok" if status == "ok" else "partial"
        print(
            f"{outcome} timestamp={payload.get('meta', {}).get('timestamp_utc', 'unknown')} "
            f"mode={mode} candidates={data.get('candidate_count', 0)} "
            f"finalized={data.get('finalized_count', 0)} "
            f"skipped={data.get('skipped_count', 0)} "
            f"failed={data.get('failed_count', 0)}"
        )
        candidates = data.get("candidates", [])
        max_report = int(data.get("max_report", DEFAULT_MAX_REPORT))
        report_items = candidates if max_report == 0 else candidates[:max_report]
        for item in report_items:
            action = "finalized" if item.get("finalized") else "would_finalize"
            if item.get("skipped_reason"):
                action = "skipped"
            if item.get("finalizer_error"):
                action = "failed"
            detail = ""
            if item.get("skipped_reason"):
                detail += f" reason={item['skipped_reason']}"
            if item.get("finalizer_error"):
                detail += f" error={one_line(item['finalizer_error'], max_chars=500)}"
            print(
                f"{action} updated_at={item['updated_at_utc']} "
                f"cwd={item['cwd']} id={item['thread_id']} name={one_line(item['name'])}{detail}"
            )
        omitted = len(candidates) - len(report_items)
        if omitted > 0:
            print(f"... omitted {omitted} more candidate detail lines")
        if status == "ok":
            return

    error = payload["error"]
    print(f"error {error['code']}: {error['message']}", file=sys.stderr)
    if error.get("hint"):
        print(error["hint"], file=sys.stderr)


def finish(
    *,
    status: str,
    started_at: float,
    plain: bool,
    data: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    exit_code: int,
) -> int:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "command": COMMAND,
        "status": status,
        "data": data or {},
        "error": error,
        "meta": {
            "request_id": str(uuid.uuid4()),
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
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.proc: subprocess.Popen[str] | None = None
        self.stdout_queue: queue.Queue[str] = queue.Queue()
        self.stderr_lines: list[str] = []
        self.next_id = 1

    def __enter__(self) -> "AppServerClient":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def start(self) -> None:
        self.proc = subprocess.Popen(
            ["codex", "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        threading.Thread(target=self._read_stdout, args=(self.proc.stdout,), daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(self.proc.stderr,), daemon=True).start()

        self.request(
            "initialize",
            {
                "clientInfo": {
                    "name": "agents_finalize_stale_codex_threads",
                    "title": "Agents Stale Codex Thread Finalizer",
                    "version": SCHEMA_VERSION,
                }
            },
        )
        self.notify("initialized", {})

    def close(self) -> None:
        if self.proc is None:
            return
        proc = self.proc
        self.proc = None
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)

    def _read_stdout(self, stream: Any) -> None:
        for line in stream:
            self.stdout_queue.put(line)

    def _read_stderr(self, stream: Any) -> None:
        for line in stream:
            text = line.rstrip("\n")
            if text:
                self.stderr_lines.append(text)
                del self.stderr_lines[:-80]

    def _write(self, message: dict[str, Any]) -> None:
        if self.proc is None or self.proc.stdin is None:
            raise AppServerError("app-server is not running")
        if self.proc.poll() is not None:
            raise AppServerError(f"app-server exited with code {self.proc.returncode}")
        self.proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"method": method, "params": params})

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._write(message)
        return self._read_response(request_id)

    def _read_response(self, request_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                stderr_tail = "\n".join(self.stderr_lines[-20:])
                raise AppServerError(
                    f"timed out waiting for app-server response id={request_id}"
                    + (f"\nstderr:\n{stderr_tail}" if stderr_tail else "")
                )
            try:
                line = self.stdout_queue.get(timeout=remaining)
            except queue.Empty as exc:
                raise AppServerError(f"timed out waiting for app-server response id={request_id}") from exc
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if payload.get("id") != request_id:
                continue
            if "error" in payload:
                error = payload["error"]
                message = error.get("message") if isinstance(error, dict) else str(error)
                raise AppServerError(f"{method_for_error(payload)} failed: {message}")
            result = payload.get("result")
            if not isinstance(result, dict):
                return {}
            return result


def method_for_error(payload: dict[str, Any]) -> str:
    return f"request id={payload.get('id')}"


def normalized_path(raw: str | Path) -> Path:
    return Path(raw).expanduser().resolve(strict=False)


def git_common_dir(cwd: Path) -> Path | None:
    if not cwd.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = completed.stdout.strip()
    if completed.returncode != 0 or not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = cwd / path
    return path.resolve(strict=False)


def normalize_git_remote_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if "://" in value:
        scheme, remainder = value.split("://", 1)
        if scheme in {"http", "https", "ssh"}:
            authority, separator, path = remainder.partition("/")
            host = authority.rsplit("@", 1)[-1].lower()
            return f"{host}/{path}" if separator else host
    if ":" in value and "@" in value.split(":", 1)[0]:
        authority, path = value.split(":", 1)
        host = authority.rsplit("@", 1)[-1].lower()
        return f"{host}/{path.lstrip('/')}"
    return value


def git_origin_url(cwd: Path) -> str | None:
    if not cwd.is_dir():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(cwd), "remote", "get-url", "origin"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    raw = completed.stdout.strip()
    if completed.returncode != 0 or not raw:
        return None
    return normalize_git_remote_url(raw)


def build_managed_repo_scope(repos: list[str]) -> ManagedRepoScope:
    roots = tuple(dict.fromkeys(normalized_path(repo) for repo in repos))
    common_git_dirs = frozenset(
        common_dir
        for root in roots
        if (common_dir := git_common_dir(root)) is not None
    )
    origin_urls = frozenset(
        origin_url
        for root in roots
        if (origin_url := git_origin_url(root)) is not None
    )
    return ManagedRepoScope(
        roots=roots,
        common_git_dirs=common_git_dirs,
        origin_urls=origin_urls,
    )


def thread_belongs_to_managed_repo(thread: dict[str, Any], scope: ManagedRepoScope) -> bool:
    cwd = thread.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    path = normalized_path(cwd)
    if any(path == root or root in path.parents for root in scope.roots):
        return True
    common_dir = git_common_dir(path)
    if common_dir is not None and common_dir in scope.common_git_dirs:
        return True
    git_info = thread.get("gitInfo")
    origin_url = git_info.get("originUrl") if isinstance(git_info, dict) else None
    return (
        isinstance(origin_url, str)
        and normalize_git_remote_url(origin_url) in scope.origin_urls
    )


def list_candidates(
    client: AppServerClient,
    *,
    repos: list[str],
    cutoff_epoch: float,
    page_limit: int,
    source_kinds: list[str] | None,
    use_state_db_only: bool,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    scope = build_managed_repo_scope(repos)
    cursor: str | None = None
    while True:
        params: dict[str, Any] = {
            "cursor": cursor,
            "limit": page_limit,
            "sortKey": "updated_at",
            "sortDirection": "asc",
            "archived": False,
            "useStateDbOnly": use_state_db_only,
        }
        if source_kinds is not None:
            params["sourceKinds"] = source_kinds
        result = client.request("thread/list", params)
        data = result.get("data", [])
        if not isinstance(data, list):
            raise AppServerError("thread/list returned malformed data")

        should_continue = True
        for thread in data:
            if not isinstance(thread, dict):
                continue
            updated_at = thread.get("updatedAt")
            if not isinstance(updated_at, (int, float)):
                continue
            if float(updated_at) >= cutoff_epoch:
                should_continue = False
                break

            thread_id = thread.get("id")
            cwd = thread.get("cwd")
            if not isinstance(thread_id, str) or not isinstance(cwd, str):
                continue
            if not thread_belongs_to_managed_repo(thread, scope):
                continue
            candidates.append(
                Candidate(
                    thread_id=thread_id,
                    name=str(thread.get("name") or thread.get("preview") or ""),
                    cwd=cwd,
                    updated_at=int(updated_at),
                    source=thread.get("source"),
                    status=thread.get("status"),
                    path=thread.get("path") if isinstance(thread.get("path"), str) else None,
                )
            )

        cursor = result.get("nextCursor")
        if not should_continue or not isinstance(cursor, str) or not cursor:
            return candidates


def candidate_to_output(
    candidate: Candidate,
    *,
    finalized: bool,
    finalizer_status: str | None,
    finalizer_error: str | None,
    skipped_reason: str | None,
) -> dict[str, Any]:
    return {
        "thread_id": candidate.thread_id,
        "name": candidate.name,
        "cwd": candidate.cwd,
        "updated_at": candidate.updated_at,
        "updated_at_utc": candidate.updated_at_utc,
        "source": candidate.source,
        "status": candidate.status,
        "path": candidate.path,
        "finalized": finalized,
        "finalizer_status": finalizer_status,
        "finalizer_error": finalizer_error,
        "skipped_reason": skipped_reason,
    }


def run_thread_finalizer(
    *,
    command: Path,
    candidate: Candidate,
    timeout_seconds: float,
    finalization_timeout_seconds: float,
) -> dict[str, Any]:
    if not command.is_file() or not os.access(command, os.X_OK):
        raise AppServerError(f"finalizer command is not executable: {command}")

    completed = subprocess.run(
        [
            sys.executable,
            str(command),
            "--thread-id",
            candidate.thread_id,
            "--reason",
            "stale-cleanup",
            "--timeout-seconds",
            str(timeout_seconds),
            "--finalization-timeout-seconds",
            str(finalization_timeout_seconds),
            "--apply",
            "--json",
            "--no-input",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=finalization_timeout_seconds + max(timeout_seconds, 1.0) + 10.0,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        if completed.returncode == 0:
            raise AppServerError(f"finalizer returned invalid JSON for {candidate.thread_id}: {exc}") from exc
        payload = None
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        stdout = completed.stdout.strip()
        error = payload.get("error") if isinstance(payload, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        detail = message or stderr or stdout or f"exit {completed.returncode}"
        raise AppServerError(f"finalizer failed for {candidate.thread_id}: {detail}")
    if not isinstance(payload, dict):
        raise AppServerError(f"finalizer returned malformed JSON for {candidate.thread_id}")
    if payload.get("status") != "ok":
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else str(error)
        raise AppServerError(f"finalizer reported {payload.get('status')} for {candidate.thread_id}: {message}")
    result = ((payload.get("data") or {}).get("result")) if isinstance(payload.get("data"), dict) else None
    if not isinstance(result, dict):
        raise AppServerError(f"finalizer JSON missing data.result for {candidate.thread_id}")
    return result


def process_candidates(
    *,
    candidates: list[Candidate],
    apply: bool,
    max_finalize: int,
    command: Path,
    timeout_seconds: float,
    finalization_timeout_seconds: float,
    finalizer_runner: Callable[..., dict[str, Any]] = run_thread_finalizer,
) -> dict[str, Any]:
    finalized_count = 0
    skipped_count = 0
    failed_count = 0
    output_items: list[dict[str, Any]] = []
    for candidate in candidates:
        skipped_reason: str | None = None
        finalized = False
        finalizer_status: str | None = None
        finalizer_error: str | None = None
        if max_finalize and finalized_count >= max_finalize:
            skipped_reason = "max_finalize_reached"

        if skipped_reason is None and apply:
            try:
                finalizer_result = finalizer_runner(
                    command=command,
                    candidate=candidate,
                    timeout_seconds=timeout_seconds,
                    finalization_timeout_seconds=finalization_timeout_seconds,
                )
                finalizer_status = str(finalizer_result.get("finalizer_status") or "")
                finalizer_error = (
                    truncate_text(str(finalizer_result.get("error")))
                    if finalizer_result.get("error") is not None
                    else None
                )
                if finalizer_result.get("archived") is True:
                    finalized = True
                    finalized_count += 1
                else:
                    skipped_reason = str(finalizer_result.get("skipped_reason") or "not_archived")
                    skipped_count += 1
            except Exception as exc:
                finalizer_status = "failed"
                finalizer_error = truncate_text(str(exc))
                skipped_reason = "finalizer_failed"
                skipped_count += 1
                failed_count += 1
        elif skipped_reason is not None:
            skipped_count += 1

        output_items.append(
            candidate_to_output(
                candidate,
                finalized=finalized,
                finalizer_status=finalizer_status,
                finalizer_error=finalizer_error,
                skipped_reason=skipped_reason,
            )
        )

    return {
        "finalized_count": finalized_count,
        "skipped_count": skipped_count,
        "failed_count": failed_count,
        "candidates": output_items,
    }


def acquire_lock(lock_path: Path) -> Any:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError(f"another finalize run already holds {lock_path}") from exc
    lock_file.write(f"{os.getpid()}\n")
    lock_file.flush()
    return lock_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Finalize stale Codex threads through the global finalize-codex-thread command."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Finalize eligible threads.")
    mode.add_argument("--dry-run", action="store_true", help="Only report eligible threads.")

    age = parser.add_mutually_exclusive_group()
    age.add_argument(
        "--older-than-hours",
        type=float,
        default=DEFAULT_OLDER_THAN_HOURS,
        help=f"Finalize threads whose updatedAt is older than this many hours (default: {DEFAULT_OLDER_THAN_HOURS:g}).",
    )
    age.add_argument(
        "--older-than-days",
        type=float,
        help="Finalize threads whose updatedAt is older than this many days.",
    )

    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--repo", action="append", default=[], help="Limit to an exact repo/cwd path. Repeatable.")
    parser.add_argument("--finalizer-command", type=Path, default=DEFAULT_FINALIZER_COMMAND,
                        help="Python finalizer script; executed with this scheduler's interpreter.")
    parser.add_argument("--page-limit", type=int, default=DEFAULT_PAGE_LIMIT)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--finalization-timeout-seconds", type=float, default=DEFAULT_FINALIZATION_TIMEOUT_SECONDS)
    parser.add_argument("--max-finalize", type=int, default=0, help="Maximum threads to finalize per run; 0 means unlimited.")
    parser.add_argument("--max-report", type=int, default=DEFAULT_MAX_REPORT, help="Maximum candidate detail lines in plain output; 0 means unlimited.")
    parser.add_argument("--all-source-kinds", action="store_true", help="Include non-interactive and subagent thread sources.")
    parser.add_argument("--source-kind", action="append", choices=SOURCE_KINDS, default=[], help="Filter to a Codex thread source kind. Repeatable.")
    parser.add_argument("--state-db-only", action="store_true", help="Use only Codex's state DB instead of scan-and-repair listing.")
    parser.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    parser.add_argument("--now", type=parse_timestamp, help="Override current time for testing; epoch or ISO timestamp.")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON.")
    parser.add_argument("--plain", action="store_true", help="Emit compact plain text (default).")
    parser.add_argument("--no-input", action="store_true", help="Accepted for non-interactive callers; this command never prompts.")
    return parser.parse_args()


def main() -> int:
    started_at = time.time()
    args = parse_args()
    plain = not args.json
    apply = bool(args.apply)

    try:
        if args.older_than_days is not None:
            older_than_hours = args.older_than_days * 24
        else:
            older_than_hours = args.older_than_hours
        if older_than_hours <= 0:
            raise ValueError("--older-than-hours/--older-than-days must be positive")
        if args.page_limit <= 0:
            raise ValueError("--page-limit must be positive")
        if args.max_finalize < 0:
            raise ValueError("--max-finalize cannot be negative")
        if args.max_report < 0:
            raise ValueError("--max-report cannot be negative")
        if args.timeout_seconds <= 0:
            raise ValueError("--timeout-seconds must be positive")
        if args.finalization_timeout_seconds <= 0:
            raise ValueError("--finalization-timeout-seconds must be positive")

        repos = [expand_path(repo) for repo in args.repo]
        if not repos:
            repos = load_registry_repos(args.registry.expanduser())
        if not repos:
            raise ValueError("no repos selected")

        source_kinds: list[str] | None = None
        if args.all_source_kinds:
            source_kinds = SOURCE_KINDS
        elif args.source_kind:
            source_kinds = list(dict.fromkeys(args.source_kind))

        now_epoch = float(args.now if args.now is not None else time.time())
        cutoff_epoch = now_epoch - older_than_hours * 3600

        lock_file = acquire_lock(args.lock.expanduser())
        try:
            with AppServerClient(args.timeout_seconds) as client:
                candidates = list_candidates(
                    client,
                    repos=repos,
                    cutoff_epoch=cutoff_epoch,
                    page_limit=args.page_limit,
                    source_kinds=source_kinds,
                    use_state_db_only=bool(args.state_db_only),
                )

            processing = process_candidates(
                candidates=candidates,
                apply=apply,
                max_finalize=args.max_finalize,
                command=args.finalizer_command.expanduser(),
                timeout_seconds=args.timeout_seconds,
                finalization_timeout_seconds=args.finalization_timeout_seconds,
            )
        finally:
            lock_file.close()

        data = {
            "applied": apply,
            "older_than_hours": older_than_hours,
            "cutoff_epoch": int(cutoff_epoch),
            "cutoff_utc": datetime.fromtimestamp(cutoff_epoch, UTC).isoformat(timespec="seconds"),
            "repo_count": len(repos),
            "candidate_count": len(candidates),
            "finalized_count": processing["finalized_count"],
            "skipped_count": processing["skipped_count"],
            "failed_count": processing["failed_count"],
            "max_report": args.max_report,
            "candidates": processing["candidates"],
        }
        if processing["failed_count"]:
            return finish(
                status="error",
                started_at=started_at,
                plain=plain,
                data=data,
                error={
                    "code": "PartialFinalizeFailure",
                    "message": (
                        f"{processing['failed_count']} thread finalization(s) failed; "
                        f"the remaining eligible threads were still processed"
                    ),
                    "retryable": True,
                    "hint": "Inspect candidates with skipped_reason=finalizer_failed and retry after the thread writer or repo finalizer is available.",
                },
                exit_code=4,
            )

        return finish(
            status="ok",
            started_at=started_at,
            plain=plain,
            data=data,
            exit_code=0,
        )
    except Exception as exc:
        return finish(
            status="error",
            started_at=started_at,
            plain=plain,
            error={
                "code": exc.__class__.__name__,
                "message": str(exc),
                "retryable": not isinstance(exc, ValueError),
                "hint": "Run with --dry-run --json for details, then check Codex app-server availability.",
            },
            exit_code=1,
        )


if __name__ == "__main__":
    raise SystemExit(main())
