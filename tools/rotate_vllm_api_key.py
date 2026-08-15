#!/usr/bin/env python3
"""Atomically rotate the shared vLLM credential without printing its value."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import secrets
import tempfile


DEFAULT_NAMES = (
    "DIGITAL_TWIN_API_KEY",
    "VLLM_PROXY_UPSTREAM_API_KEY",
    "VLLM_HUST_API_KEY",
)


def rotate(env_path: Path, names: tuple[str, ...]) -> None:
    original = env_path.read_text(encoding="utf-8")
    credential = secrets.token_urlsafe(48)
    remaining = set(names)
    updated: list[str] = []

    for line in original.splitlines(keepends=True):
        stripped = line.lstrip()
        if stripped.startswith("#") or "=" not in line:
            updated.append(line)
            continue
        name = line.split("=", 1)[0].strip()
        if name not in remaining:
            updated.append(line)
            continue
        newline = "\n" if line.endswith("\n") else ""
        updated.append(f"{name}={credential}{newline}")
        remaining.remove(name)

    if remaining:
        missing = ", ".join(sorted(remaining))
        raise SystemExit(f"refusing to rotate: missing variables in {env_path}: {missing}")

    stat = env_path.stat()
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=env_path.parent,
        prefix=f".{env_path.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.writelines(updated)
        handle.flush()
        os.fsync(handle.fileno())

    try:
        os.chmod(temporary, stat.st_mode)
        os.replace(temporary, env_path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("env_file", type=Path)
    parser.add_argument("--name", action="append", dest="names")
    args = parser.parse_args()
    names = tuple(args.names) if args.names else DEFAULT_NAMES
    rotate(args.env_file.resolve(), names)
    print(f"rotated {len(names)} credential variables in {args.env_file}")


if __name__ == "__main__":
    main()
