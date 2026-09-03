"""Thin, local owner-entry transport to the pinned dev-hub; never a controller.

No approvals, leases, deployment state, Docker/systemd calls or rollback logic
are implemented here. The producer must enforce those before any mutation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

PROTOCOL = "vllm-hust.instance-owner-entry/v1"
BINDING_SCHEMA = "sage-mate.instance-binding/v1"
SUBMODULE = "deps/vllm-hust-dev-hub"
BACKEND = "scripts/instance_owner_entry.py"
MANIFEST = "config/instance-owner-contract.json"
ACTIONS = {"serve", "start", "stop", "restart", "reconcile", "cleanup", "monitor"}
CONTROL_KEYS = {"SAGE_MATE_INSTANCE_CONTROL_ENABLED", "SAGE_MATE_INSTANCE_REGISTRATION"}
SAFE_ENV_KEYS = {
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
}


class BindingError(ValueError):
    """Safe, credential-free error suitable for operator output."""


def control_settings(repo: Path, environ: dict[str, str]) -> tuple[bool, str]:
    values = {k: environ[k] for k in CONTROL_KEYS if k in environ}
    dotenv = repo / ".env"
    if dotenv.is_file():
        for line in dotenv.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator and key in CONTROL_KEYS:
                values[key] = value
    enabled = values.get("SAGE_MATE_INSTANCE_CONTROL_ENABLED", "0")
    if enabled not in {"0", "false", "1", "true"}:
        raise BindingError("invalid instance control flag")
    return enabled in {"1", "true"}, values.get("SAGE_MATE_INSTANCE_REGISTRATION", "")


def unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise BindingError("duplicate configuration field")
        result[key] = value
    return result


def read_binding(path_text: str) -> dict[str, str]:
    path = Path(path_text)
    if not path_text or not path.is_absolute() or path.resolve() != path:
        raise BindingError("an explicit real registration file is required")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid not in {0, os.geteuid()}
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_size > 4096
            ):
                raise BindingError(
                    "registration must be an owner-controlled 0600 regular file"
                )
            data = json.loads(stream.read(4097), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BindingError("registration cannot be read or decoded") from exc
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "instance_id", "owner_id", "profile_id"}
        or data["schema"] != BINDING_SCHEMA
    ):
        raise BindingError("unsupported registration schema")
    for key in ("instance_id", "owner_id", "profile_id"):
        if not isinstance(data[key], str) or not re.fullmatch(
            r"[a-z][a-z0-9-]{0,63}", data[key]
        ):
            raise BindingError("registration contains an invalid identifier")
    return data


def git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            env={k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BindingError("cannot verify pinned dev-hub checkout") from exc
    return result.stdout.strip()


def backend_path(repo: Path, action: str | None = None) -> Path:
    module = repo / SUBMODULE
    if not module.is_dir() or module.resolve() != module:
        raise BindingError("pinned dev-hub checkout is missing or redirected")
    link = git(repo, "ls-tree", "HEAD", "--", SUBMODULE).split()
    if len(link) != 4 or link[:2] != ["160000", "commit"] or link[3] != SUBMODULE:
        raise BindingError("dev-hub is not a pinned submodule")
    if git(module, "rev-parse", "HEAD") != link[2]:
        raise BindingError("dev-hub differs from the parent gitlink")
    if git(module, "status", "--porcelain", "--untracked-files=no"):
        raise BindingError("dev-hub has uncommitted tracked changes")
    # Require both files in the pinned commit, not an untracked local shim.
    for relative in (MANIFEST, BACKEND):
        file = module / relative
        if not file.is_file() or file.resolve() != file:
            raise BindingError("dev-hub owner contract is not installed")
        if git(module, "hash-object", "--", relative) != git(
            module, "rev-parse", f"HEAD:{relative}"
        ):
            raise BindingError("dev-hub owner contract differs from pinned source")
    try:
        manifest = json.loads(
            (module / MANIFEST).read_text(), object_pairs_hook=unique_object
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BindingError("invalid dev-hub owner contract") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("protocol") != PROTOCOL
        or manifest.get("entrypoint") != BACKEND
        or not isinstance(manifest.get("actions"), list)
        or any(not isinstance(x, str) or x not in ACTIONS for x in manifest["actions"])
        or (action is not None and action not in manifest["actions"])
    ):
        raise BindingError(
            "dev-hub does not implement the requested owner protocol/action"
        )
    return module / BACKEND


def build_request(
    binding: dict[str, str], action: str, enabled: bool, invocation: str = ""
) -> dict:
    if action not in ACTIONS:
        raise BindingError(
            "unsupported operation; use a separately approved fixed deployment plan"
        )
    if invocation and not re.fullmatch(r"[a-f0-9]{32}", invocation):
        raise BindingError("invalid supervisor invocation identity")
    return {
        "schema": PROTOCOL,
        "consumer": "sage-mate",
        "action": action,
        "instance_id": binding["instance_id"],
        "owner_id": binding["owner_id"],
        "profile_id": binding["profile_id"],
        "new_operations_enabled": enabled,
        "invocation_id": invocation or None,
    }


def dispatch(backend: Path, request: dict) -> None:
    # IDs and a capability preference only; no credentials, argv, deployment
    # flags or approval assertions enter the protocol from the caller.
    encoded = (json.dumps(request, separators=(",", ":")) + "\n").encode()
    if len(encoded) > 4096:
        raise BindingError("owner request too large")
    reader, writer = os.pipe()
    try:
        os.write(writer, encoded)
    finally:
        os.close(writer)
    os.dup2(reader, 0)
    # pipe() is close-on-exec, including when stdin was closed and reader == 0.
    os.set_inheritable(0, True)
    if reader != 0:
        os.close(reader)
    environment = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    os.execve(sys.executable, [sys.executable, "-I", str(backend)], environment)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--action")
    mode.add_argument("--describe", action="store_true")
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[1]
    try:
        enabled, registration = control_settings(repo, dict(os.environ))
        if not enabled and not registration:
            if not args.describe:
                raise BindingError("instance control is disabled")
            print(
                json.dumps(
                    {
                        "protocol": PROTOCOL,
                        "enabled": False,
                        "enrolled": False,
                        "lifecycleAvailable": False,
                        "reason": "disabled",
                    }
                )
            )
            return 0
        binding = read_binding(registration)
        if args.describe:
            backend_path(repo)
            print(
                json.dumps(
                    {
                        "protocol": PROTOCOL,
                        "enabled": enabled,
                        "enrolled": True,
                        "instanceId": binding["instance_id"],
                        "producerInstalled": True,
                        "lifecycleAvailable": False,
                        "reason": "owner authorization and runtime qualification required",
                    }
                )
            )
            return 0
        request = build_request(
            binding, args.action, enabled, os.environ.get("INVOCATION_ID", "")
        )
        dispatch(backend_path(repo, args.action), request)
    except (BindingError, OSError, UnicodeError) as exc:
        # OSError may contain private paths. Do not return raw OS/backend details.
        message = (
            str(exc)
            if isinstance(exc, BindingError)
            else "instance control transport failed"
        )
        print(
            json.dumps({"error": message, "lifecycleAvailable": False}), file=sys.stderr
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
