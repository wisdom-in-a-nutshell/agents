"""Read parent and subagent Codex turn file changes from App Server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

try:
    from hooks.scripts.codex_shell_paths import MAX_SHELL_PATHS, command_repository_paths
    from hooks.scripts.stop_feedback_turn import AppServerClient, FeedbackTurnError
except ModuleNotFoundError:  # Direct script execution adds this directory to sys.path.
    from codex_shell_paths import MAX_SHELL_PATHS, command_repository_paths
    from stop_feedback_turn import AppServerClient, FeedbackTurnError


MAX_THREAD_LIST_PAGES = 5
THREAD_LIST_PAGE_SIZE = 100
MAX_DESCENDANT_THREAD_READS = 48


class CodexTurnChangesError(RuntimeError):
    """Raised when Codex turn attribution cannot be read safely."""


class CodexShellDiscoveryError(CodexTurnChangesError):
    """Shell discovery was incomplete and must not silently skip repositories."""


@dataclass(frozen=True)
class CodexTurnChanges:
    """Attributed file changes for one Codex turn and its subagent tree."""

    thread_id: str
    session_id: str
    parent_thread_id: str
    descendant_thread_ids: tuple[str, ...]
    turn_id: str
    turn_started_at: int
    touched_paths: tuple[str, ...]
    shell_paths: tuple[str, ...] = ()


def collect_codex_turn_changes(
    thread_id: str,
    *,
    timeout_seconds: float = 8.0,
    replay_before: int | None = None,
) -> CodexTurnChanges:
    """Return file changes and shell-used paths for the current turn tree."""
    normalized_thread_id = str(thread_id or "").strip()
    if not normalized_thread_id:
        raise CodexTurnChangesError("Codex Stop payload is missing session_id.")

    try:
        with AppServerClient(timeout_seconds=timeout_seconds) as client:
            owner = _read_thread(client, normalized_thread_id)
            owner_turn = _latest_turn(owner)
            owner_session_id = _text(owner.get("sessionId")) or normalized_thread_id
            turn_started_at = _integer(owner_turn.get("startedAt"))
            owner_turns = [owner_turn]
            if replay_before is not None:
                eligible_starts = [
                    _integer(turn.get("startedAt"))
                    for turn in owner.get("turns", [])
                    if isinstance(turn, dict) and 0 < _integer(turn.get("startedAt")) <= replay_before
                ]
                if not eligible_starts:
                    raise CodexTurnChangesError("Codex history does not cover the failed discovery checkpoint.")
                turn_started_at = max(eligible_starts)
                # Include all turns in the boundary second, then every retry
                # turn. A retry must not forget shell edits in the failed turn.
                owner_turns = [
                    turn for turn in owner.get("turns", [])
                    if isinstance(turn, dict) and _integer(turn.get("startedAt")) >= turn_started_at
                ]
            touched_paths: set[str] = set()
            shell_paths: set[str] = set()
            for turn in owner_turns:
                touched_paths.update(_file_change_paths(turn))
                shell_paths.update(command_repository_paths(turn))
            if len(shell_paths) > MAX_SHELL_PATHS:
                raise ValueError("Codex shell repository discovery exceeded retry path limits.")

            listed_threads = _list_threads(
                client,
                min_updated_at=turn_started_at,
            )
            recent_threads = [
                thread
                for thread in listed_threads
                if _text(thread.get("id")) != normalized_thread_id
                and (
                    _thread_activity_at(thread) == 0
                    or _thread_activity_at(thread) >= turn_started_at
                )
            ]
            descendant_threads: list[dict[str, Any]] = []
            descendant_ids = {normalized_thread_id}
            remaining = list(recent_threads)
            while remaining:
                discovered_ids = {
                    _text(thread.get("id"))
                    for thread in remaining
                    if _text(thread.get("parentThreadId")) in descendant_ids
                }
                discovered_ids.discard("")
                if not discovered_ids:
                    break
                next_remaining: list[dict[str, Any]] = []
                for thread in remaining:
                    if _text(thread.get("id")) in discovered_ids:
                        descendant_threads.append(thread)
                    else:
                        next_remaining.append(thread)
                descendant_ids.update(discovered_ids)
                remaining = next_remaining

            if len(descendant_threads) > MAX_DESCENDANT_THREAD_READS:
                raise CodexTurnChangesError(
                    "Too many descendant Codex threads to attribute safely "
                    f"({len(descendant_threads)})."
                )
            for thread in descendant_threads:
                child_id = _text(thread.get("id"))
                if not child_id:
                    continue
                child = _read_thread(client, child_id)
                touched_paths.update(
                    _file_change_paths_since(child, started_at=turn_started_at)
                )
                for turn in child.get("turns") or []:
                    if isinstance(turn, dict) and _integer(turn.get("startedAt")) >= turn_started_at:
                        shell_paths.update(command_repository_paths(turn))
                if len(shell_paths) > MAX_SHELL_PATHS:
                    raise ValueError("Codex shell repository discovery exceeded turn-tree path limits.")
    except ValueError as exc:
        raise CodexShellDiscoveryError(str(exc)) from exc
    except FeedbackTurnError as exc:
        raise CodexTurnChangesError(str(exc)) from exc

    return CodexTurnChanges(
        thread_id=normalized_thread_id,
        session_id=owner_session_id,
        parent_thread_id=_text(owner.get("parentThreadId")),
        descendant_thread_ids=tuple(
            sorted(
                child_id
                for child_id in (_text(thread.get("id")) for thread in descendant_threads)
                if child_id
            )
        ),
        turn_id=_text(owner_turn.get("id")),
        turn_started_at=turn_started_at,
        touched_paths=tuple(sorted(touched_paths)),
        shell_paths=tuple(sorted(shell_paths)),
    )


def _read_thread(client: AppServerClient, thread_id: str) -> dict[str, Any]:
    result = client.request(
        "thread/read",
        {"threadId": thread_id, "includeTurns": True},
    )
    thread = result.get("thread")
    if not isinstance(thread, dict):
        raise CodexTurnChangesError(
            f"thread/read returned no thread for {thread_id}."
        )
    return thread


def _list_threads(
    client: AppServerClient,
    *,
    min_updated_at: int,
) -> list[dict[str, Any]]:
    threads: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(MAX_THREAD_LIST_PAGES):
        params: dict[str, Any] = {
            "archived": False,
            "limit": THREAD_LIST_PAGE_SIZE,
            "sortDirection": "desc",
        }
        if cursor:
            params["cursor"] = cursor
        result = client.request("thread/list", params)
        page = result.get("data")
        if not isinstance(page, list):
            raise CodexTurnChangesError("thread/list returned malformed data.")
        page_threads = [item for item in page if isinstance(item, dict)]
        threads.extend(page_threads)
        cursor = _text(result.get("nextCursor")) or None
        if not cursor:
            return threads
        page_updates = [_integer(item.get("updatedAt")) for item in page_threads]
        if page_updates and all(value > 0 for value in page_updates):
            if min(page_updates) < min_updated_at:
                return threads
    if cursor:
        raise CodexTurnChangesError(
            "Codex thread listing exceeded the safety pagination limit."
        )
    return threads


def _latest_turn(thread: dict[str, Any]) -> dict[str, Any]:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        raise CodexTurnChangesError("thread/read did not include turns.")
    candidates = [turn for turn in turns if isinstance(turn, dict)]
    if not candidates:
        raise CodexTurnChangesError("Codex thread has no turn to attribute.")
    return max(
        candidates,
        key=lambda turn: (
            _integer(turn.get("startedAt")),
            _text(turn.get("id")),
        ),
    )


def _file_change_paths_since(
    thread: dict[str, Any],
    *,
    started_at: int,
) -> set[str]:
    turns = thread.get("turns")
    if not isinstance(turns, list):
        return set()
    paths: set[str] = set()
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if _integer(turn.get("startedAt")) < started_at:
            continue
        paths.update(_file_change_paths(turn))
    return paths


def _file_change_paths(turn: dict[str, Any]) -> set[str]:
    items = turn.get("items")
    if not isinstance(items, list):
        return set()
    paths: set[str] = set()
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "fileChange":
            continue
        if item.get("status") not in {None, "completed"}:
            continue
        changes = item.get("changes")
        if not isinstance(changes, list):
            continue
        for change in changes:
            if not isinstance(change, dict):
                continue
            path = _text(change.get("path"))
            if path:
                paths.add(path)
            kind = change.get("kind")
            if isinstance(kind, dict):
                move_path = _text(kind.get("move_path"))
                if move_path:
                    paths.add(move_path)
    return paths


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _integer(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _thread_activity_at(thread: dict[str, Any]) -> int:
    return _integer(thread.get("updatedAt")) or _integer(thread.get("createdAt"))


__all__ = [
    "CodexShellDiscoveryError",
    "CodexTurnChanges",
    "CodexTurnChangesError",
    "collect_codex_turn_changes",
]
