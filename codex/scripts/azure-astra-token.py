#!/usr/bin/env python3
"""Codex auth helper: emit only the existing generated gateway token."""
from pathlib import Path
import shlex
import sys


def read_token(path: Path) -> str:
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        # Parse data, never execute or expand env-file shell expressions.
        words = shlex.split(line, comments=True)
        if words and words[0] == "export":
            words = words[1:]
        if words and words[0].startswith("LLM_API_KEY="):
            if len(words) != 1:
                raise ValueError("invalid token entry")
            values.append(words[0].split("=", 1)[1])
    if len(values) != 1 or not values[0] or any(c.isspace() for c in values[0]):
        raise ValueError("missing, duplicate, or invalid token")
    return values[0]


def main() -> int:
    try:
        token = read_token(Path.home() / ".secrets/litellm/env")
    except (OSError, ValueError):
        print("Azure Astra credential unavailable: check generated ~/.secrets/litellm/env", file=sys.stderr)
        return 1
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
