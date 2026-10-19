"""Owned loopback qualification with public sources and RAM-only inherited auth.

Never copies private records, account state, credentials or production work.
The chosen runtime directory must be fresh; restart reuses it only explicitly.
"""

import argparse
import os
from pathlib import Path
import shutil
import socket
import sqlite3
import subprocess
from typing import get_args

READONLY_PATHS = frozenset({"owner_style_profile_path", "workflow_policy_path", "workflow_scenario_path",
    "service_manager_script", "capability_plugin_dir"})


def isolate_paths(env, fields, runtime):
    """Isolate every typed mutable path, including explicit nonstandard overrides."""
    for name, field in fields.items():
        if name in READONLY_PATHS or not (field.annotation is Path or Path in get_args(field.annotation)):
            continue
        key = "DIGITAL_TWIN_" + name.upper()
        if field.default is None and key not in env:
            continue
        filename = field.default.name if isinstance(field.default, Path) and field.default.suffix else ""
        env[key] = str(runtime / "isolated" / name / filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--wiki", type=Path, required=True)
    parser.add_argument("--port", type=int, default=59676)
    parser.add_argument("--reuse-runtime", action="store_true")
    args = parser.parse_args()
    release, runtime, wiki_source = (path.resolve() for path in (args.release, args.runtime, args.wiki))
    if not (release / "source/pyproject.toml").is_file() or not (release / "venv/bin/python").is_file():
        parser.error("an existing immutable release and venv are required")
    if not wiki_source.is_dir() or runtime == wiki_source or wiki_source in runtime.parents or runtime in wiki_source.parents:
        parser.error("runtime and public Wiki source must be disjoint")
    if runtime.exists() and not args.reuse_runtime:
        parser.error("runtime already exists; explicit reuse required")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    runtime.mkdir(parents=True, exist_ok=True)
    wiki = runtime / "wiki"
    if not wiki.exists():
        if any(path.is_symlink() for path in wiki_source.rglob("*")):
            raise RuntimeError("public source snapshot must not follow symlinks")
        # Rebuild only the isolated derived vector index with the real configured
        # embedding service; never copy a live index's partially checkpointed WAL.
        shutil.copytree(wiki_source, wiki, ignore=shutil.ignore_patterns(
            ".sagevdb-index", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm"))
    source_catalog = wiki_source.parent / f".{wiki_source.name}.knowledge-state.sqlite3"
    catalog = runtime / ".wiki.knowledge-state.sqlite3"
    if source_catalog.is_file() and not catalog.exists():
        with sqlite3.connect(f"file:{source_catalog}?mode=ro", uri=True) as source:
            with sqlite3.connect(catalog) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchone() != ("ok",):
                    raise RuntimeError("public catalog snapshot failed integrity check")
    pid = subprocess.check_output(["systemctl", "--user", "show", "sage-mate-app.service",
        "--property=MainPID", "--value"], text=True).strip()
    if not pid.isdigit() or int(pid) <= 0:
        raise RuntimeError("active production unit required for configuration lookup")
    inherited = dict(item.decode().split("=", 1) for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        if b"=" in item)
    env = {key: value for key, value in os.environ.items()
        if key.upper() not in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"}
        and not key.startswith("DIGITAL_TWIN_")}
    old_runtime = inherited.get("DIGITAL_TWIN_RUNTIME_DIR", "/home/shuhao/sage-mate-runtime-private")
    for key, value in inherited.items():
        if not key.startswith("DIGITAL_TWIN_") or "SLACK" in key:
            continue
        if key.removeprefix("DIGITAL_TWIN_").lower() not in READONLY_PATHS and (
                value == old_runtime or value.startswith(old_runtime + "/")):
            value = str(runtime) + value[len(old_runtime):]
        env[key] = value
    for key in list(env):
        if "SLACK" in key:
            env.pop(key)
    os.chdir(release / "source")
    from sage_faculty_twin.config import AppSettings
    isolate_paths(env, AppSettings.model_fields, runtime)
    env.update({
        "DIGITAL_TWIN_RUNTIME_DIR": str(runtime), "DIGITAL_TWIN_WIKI_INDEX_DIR": str(wiki),
        "DIGITAL_TWIN_CONVERSATION_MEMORY_DIR": str(runtime / "memory"),
        "PYTHONPATH": str(release / "source/src"), "PYTHONNOUSERSITE": "1", "CUDA_VISIBLE_DEVICES": "",
        "APP_HOST": "127.0.0.1", "APP_PORT": str(args.port),
        "DIGITAL_TWIN_SMTP_HOST": "", "DIGITAL_TWIN_SMTP_USERNAME": "",
        "DIGITAL_TWIN_SMTP_PASSWORD": "", "DIGITAL_TWIN_SMTP_SENDER": "",
        "DIGITAL_TWIN_BOOKING_NOTIFICATION_EMAIL": "booking-preview@example.test",
        "DIGITAL_TWIN_CALENDAR_BRIDGE_ENABLED": "false",
    })
    os.chdir(release / "source")
    python = str(release / "venv/bin/python")
    os.execve(python, [python, "-m", "uvicorn", "sage_faculty_twin.api:app",
        "--host", "127.0.0.1", "--port", str(args.port)], env)


if __name__ == "__main__":
    main()
