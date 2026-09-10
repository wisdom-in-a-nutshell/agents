"""Find repository candidates in executed commands without evaluating shell code."""

from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
from typing import Any


MAX_COMMAND_CHARS = 1_000_000
MAX_TURN_COMMAND_CHARS = 8_000_000
MAX_SHELL_PATHS = 2048
_PATH_START = r"(?:/|~/|\.\.?/)"
_QUOTED_PATH = re.compile(r"([\"'])(" + _PATH_START + r"[^\n]*?)\1")
_BARE_PATH = re.compile(
    r"(?<![\w:/~.])(" + _PATH_START + r"(?:\\[^\n]|[^\s\"'`;$|&<>(){}\[\],\\])+)"
)


def command_repository_paths(turn: dict[str, Any]) -> set[str]:
    """Collect cwd and literal paths, including commands that failed after writes.

    These are repository-use evidence, not file-write attribution. Outputs and
    user messages are never inspected. Computed paths require explicit registration.
    """
    paths: set[str] = set()
    command_chars = 0
    items = turn.get("items")
    if not isinstance(items, list):
        return paths
    for item in items:
        if not isinstance(item, dict) or item.get("type") != "commandExecution":
            continue
        if item.get("status") not in {"completed", "failed"}:
            continue
        cwd = item.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            continue
        paths.add(os.path.normpath(cwd))
        if len(paths) > MAX_SHELL_PATHS:
            raise ValueError("Codex shell repository discovery exceeded path limits.")
        command = item.get("command")
        if not isinstance(command, str):
            continue
        command_chars += len(command)
        if len(command) > MAX_COMMAND_CHARS or command_chars > MAX_TURN_COMMAND_CHARS:
            raise ValueError("Codex shell repository discovery exceeded command text limits.")
        # App Server wraps execution as /bin/zsh -lc <quoted script>. Decode
        # that argument as text; never run it, expand variables or substitutions.
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = []
        if (
            len(argv) == 3
            and Path(argv[0]).name in {"sh", "bash", "zsh", "dash", "fish"}
            and argv[1].startswith("-")
            and "c" in argv[1]
        ):
            command = argv[2]
        literals = [match[1] for match in _QUOTED_PATH.findall(command)]
        # Do not rediscover the prefix of a quoted path containing spaces as
        # another repository (e.g. '/work/app extra' must not select /work/app).
        literals.extend(
            re.sub(r"\\(.)", r"\1", value)
            for value in _BARE_PATH.findall(_QUOTED_PATH.sub(" ", command))
        )
        for literal in literals:
            if any(marker in literal for marker in ("$", "`", "\x00")):
                continue
            path = Path(literal).expanduser()
            if not path.is_absolute():
                path = Path(cwd) / path
            paths.add(os.path.normpath(path))
        if len(paths) > MAX_SHELL_PATHS:
            raise ValueError("Codex shell repository discovery exceeded path limits.")
    return paths
