"""Offline guard against deploying stale registry packages for pinned Git code."""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit


def check_pins(project, lock):
    errors = []
    packages = lock.get("package", [])
    for requirement in project["project"].get("dependencies", []):
        if "git+" not in requirement:
            continue
        match = re.fullmatch(r"([\w-]+)\s*@\s*git\+(.+)@([0-9a-f]{40})", requirement)
        if match is None:
            errors.append("Git dependencies must use full commit pins")
            continue
        name, repository, commit = match.groups()
        candidates = [entry for entry in packages if entry["name"] == name]
        if len(candidates) != 1:
            errors.append(f"{name}: missing or ambiguous locked package")
            continue
        source = candidates[0].get("source", {}).get("git", "")
        parsed = urlsplit(source)
        origin = parsed._replace(query="", fragment="").geturl()
        if origin != repository or parsed.fragment != commit:
            errors.append(f"{name}: lock source does not match the declared commit")
    return errors


def main():
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text())
    lock = tomllib.loads((root / "uv.lock").read_text())
    errors = check_pins(project, lock)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Pinned Git dependencies match uv.lock")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
