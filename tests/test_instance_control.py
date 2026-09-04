"""Hermetic owner-binding contracts, including the real pinned closed-gate producer.

No shared service is called. Neither fake adapters nor the installed closed gate
are evidence of production lifecycle qualification.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "sage_instance_control", ROOT / "tools/sage_mate_instance_control.py"
)
assert SPEC and SPEC.loader
control = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(control)
GUARDED_PRODUCER_FILES = (
    "docs/instance-backend-contract-proposal.md",
    "scripts/instance_control/backend.py",
    "scripts/instance_control/foreground.py",
    "tests/test_instance_foreground.py",
)


def run_git(root: Path, *args: str) -> str:
    return subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-C",
            str(root),
            *args,
        ],
        env={k: v for k, v in os.environ.items() if k in control.SAFE_ENV_KEYS},
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout.strip()


def commit_fixture(root: Path) -> str:
    run_git(root, "add", ".")
    run_git(root, "commit", "-qm", "test fixture")
    return run_git(root, "rev-parse", "HEAD")


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "parent"
    repo.mkdir()
    for relative in (
        "manage.sh",
        "quickstart.sh",
        "tools/retry_deploy_vllm_ascend_until_success.sh",
        "tools/sage_mate_instance_control.py",
        "tools/run_vllm_engine.sh",
        "tools/lock_sage_mate_engine.sh",
        "tools/cleanup_vllm_engine.sh",
        "tools/monitor_twin_inference.sh",
        "tools/reserve_vllm_devices.sh",
        "tools/lib/instance_control.sh",
        "tools/lib/runtime_env.sh",
    ):
        dest = repo / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, dest)
    module = repo / control.SUBMODULE
    (module / "scripts").mkdir(parents=True)
    (module / "config").mkdir()
    (module / control.BACKEND).write_text(
        "import json, os, sys\n"
        "print(json.dumps({'request': json.load(sys.stdin), 'env': dict(os.environ)}))\n"
    )
    (module / control.MANIFEST).write_text(
        json.dumps(
            {
                "protocol": control.PROTOCOL,
                "entrypoint": control.BACKEND,
                "actions": sorted(control.ACTIONS),
            }
        )
    )
    launcher = module / "scripts/run_vllm_hust_engine.sh"
    launcher.write_text("#!/bin/bash\necho LEGACY_LAUNCHER_MUST_NOT_RUN\nexit 73\n")
    launcher.chmod(0o755)
    run_git(module, "init", "-q", "--template=")
    pin = commit_fixture(module)
    run_git(repo, "init", "-q", "--template=")
    run_git(repo, "add", "tools", "manage.sh")
    run_git(
        repo,
        "update-index",
        "--add",
        "--cacheinfo",
        f"160000,{pin},{control.SUBMODULE}",
    )
    run_git(repo, "commit", "-qm", "pinned fixture")
    return repo


def enroll(repo: Path, *, enabled: str = "1") -> Path:
    file = repo.parent / "binding.json"
    file.write_text(
        json.dumps(
            {
                "schema": control.BINDING_SCHEMA,
                "instance_id": "test-instance",
                "owner_id": "registered-owner",
                "profile_id": "frozen-profile",
            }
        )
    )
    file.chmod(0o600)
    (repo / ".env").write_text(
        f"SAGE_MATE_INSTANCE_CONTROL_ENABLED={enabled}\n"
        f"SAGE_MATE_INSTANCE_REGISTRATION={file}\n"
    )
    return file


def command(repo: Path, argv: list[str], extra: dict | None = None):
    env = {k: v for k, v in os.environ.items() if k in control.SAFE_ENV_KEYS}
    env.update(PYTHON_BIN=sys.executable)
    env.update(extra or {})
    return subprocess.run(
        argv, cwd=repo, env=env, capture_output=True, text=True, timeout=10
    )


def shell_route(repo: Path, action: str, extra: dict | None = None):
    return command(
        repo,
        [
            "bash",
            "-c",
            'set -eu; repo_root="$1"; source "$repo_root/tools/lib/instance_control.sh"; '
            'sage_mate_route_instance_operation "$2"; echo LEGACY_PATH',
            "fixture",
            str(repo),
            action,
        ],
        extra,
    )


@pytest.mark.parametrize("flag", [None, "0", "false"])
def test_default_off_never_loads_backend_or_requires_python(tmp_path, flag):
    (tmp_path / "tools/lib").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "tools/lib/instance_control.sh",
        tmp_path / "tools/lib/instance_control.sh",
    )
    if flag is not None:
        (tmp_path / ".env").write_text(f"SAGE_MATE_INSTANCE_CONTROL_ENABLED={flag}\n")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    result = shell_route(tmp_path, "restart", {"PYTHON_BIN": "/missing-python"})
    assert result.returncode == 0
    assert result.stdout.strip() == "LEGACY_PATH"
    assert sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*")) == before
    assert not (tmp_path / "deps").exists()


def test_describe_off_does_not_claim_lifecycle(checkout):
    result = command(
        checkout,
        [sys.executable, "-I", "tools/sage_mate_instance_control.py", "--describe"],
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "protocol": control.PROTOCOL,
        "enabled": False,
        "enrolled": False,
        "lifecycleAvailable": False,
        "reason": "disabled",
    }


@pytest.mark.parametrize("flag", ["1", "true", "0", "false"])
def test_enrollment_routes_even_when_new_operations_disabled(checkout, flag):
    enroll(checkout, enabled=flag)
    result = shell_route(
        checkout,
        "serve",
        {
            "SAGE_MATE_INSTANCE_CONTROL_ENABLED": "0",
            "DIGITAL_TWIN_API_KEY": "SECRET_CANARY",
            "PYTHONPATH": "/evil",
            "VLLM_ENGINE_MODEL_PATH": "/old-model",
            "VLLM_ENGINE_EXTRA_ARGS_JSON": '["--enforce-eager"]',
            "INVOCATION_ID": "a" * 32,
        },
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["request"] == {
        "schema": control.PROTOCOL,
        "consumer": "sage-mate",
        "action": "serve",
        "instance_id": "test-instance",
        "owner_id": "registered-owner",
        "profile_id": "frozen-profile",
        "new_operations_enabled": flag in {"1", "true"},
        "invocation_id": "a" * 32,
    }
    assert not (
        {"PYTHONPATH", "VLLM_ENGINE_MODEL_PATH", "DIGITAL_TWIN_API_KEY"}
        & data["env"].keys()
    )
    assert "SECRET_CANARY" not in result.stdout + result.stderr
    assert "LEGACY" not in result.stdout


@pytest.mark.parametrize(
    "action",
    ["reserve", "mixed-services", "apply", "disable", "rollback", "approve", "shell"],
)
def test_caller_cannot_smuggle_control_plane_actions(checkout, action):
    enroll(checkout)
    result = shell_route(checkout, action)
    assert result.returncode == 2
    assert "unsupported operation" in result.stderr
    assert "LEGACY" not in result.stdout


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "public-mode",
        "symlink",
        "unknown-field",
        "duplicate",
        "relative",
        "fifo",
    ],
)
def test_invalid_enrollment_is_rejected(checkout, case):
    binding = enroll(checkout)
    if case == "missing":
        binding.unlink()
    elif case == "public-mode":
        binding.chmod(0o644)
    elif case == "symlink":
        target = binding.with_suffix(".target")
        binding.rename(target)
        binding.symlink_to(target)
    elif case == "unknown-field":
        data = json.loads(binding.read_text())
        data["command"] = "SECRET_CANARY"
        binding.write_text(json.dumps(data))
    elif case == "duplicate":
        binding.write_text('{"schema":"x","schema":"y"}')
    elif case == "relative":
        (checkout / ".env").write_text(
            "SAGE_MATE_INSTANCE_CONTROL_ENABLED=1\nSAGE_MATE_INSTANCE_REGISTRATION=binding.json\n"
        )
    elif case == "fifo":
        binding.unlink()
        os.mkfifo(binding, mode=0o600)
    result = shell_route(checkout, "restart")
    assert result.returncode == 2
    assert "SECRET_CANARY" not in result.stdout + result.stderr
    assert "LEGACY" not in result.stdout


@pytest.mark.parametrize("flag", ["yes", "TRUE", "1 ", "$(false)"])
def test_invalid_flag_rejected_before_legacy(checkout, flag):
    enroll(checkout, enabled=flag)
    result = shell_route(checkout, "restart")
    assert result.returncode == 2
    assert "LEGACY" not in result.stdout


@pytest.mark.parametrize(
    "case",
    [
        "missing",
        "dirty",
        "wrong-pin",
        "wrong-protocol",
        "untracked-shim",
        "unsupported-action",
        "redirect",
    ],
)
def test_bad_producer_never_falls_back(checkout, case):
    enroll(checkout)
    module = checkout / control.SUBMODULE
    if case == "missing":
        (module / control.BACKEND).unlink()
    elif case == "dirty":
        (module / control.BACKEND).write_text("print('SECRET_CANARY')")
    elif case == "wrong-pin":
        (module / "new-file").write_text("drift")
        commit_fixture(module)
    elif case in {"wrong-protocol", "unsupported-action", "untracked-shim"}:
        if case == "untracked-shim":
            run_git(module, "rm", "--cached", control.BACKEND)
            run_git(module, "commit", "-qm", "remove tracked entry")
            pin = run_git(module, "rev-parse", "HEAD")
        else:
            manifest = json.loads((module / control.MANIFEST).read_text())
            if case == "wrong-protocol":
                manifest["protocol"] = "other/v1"
            else:
                manifest["actions"] = ["serve"]
            (module / control.MANIFEST).write_text(json.dumps(manifest))
            pin = commit_fixture(module)
        run_git(
            checkout, "update-index", "--cacheinfo", f"160000,{pin},{control.SUBMODULE}"
        )
        run_git(checkout, "commit", "-qm", "repin fixture")
    elif case == "redirect":
        target = checkout.parent / "external"
        module.rename(target)
        module.symlink_to(target)
    result = shell_route(checkout, "restart")
    assert result.returncode == 2
    assert "LEGACY" not in result.stdout
    assert "SECRET_CANARY" not in result.stdout + result.stderr


def test_producer_error_is_not_retried_or_hidden(checkout):
    enroll(checkout)
    module = checkout / control.SUBMODULE
    (module / control.BACKEND).write_text("raise SystemExit(7)\n")
    pin = commit_fixture(module)
    run_git(
        checkout, "update-index", "--cacheinfo", f"160000,{pin},{control.SUBMODULE}"
    )
    run_git(checkout, "commit", "-qm", "repin failure fixture")
    result = shell_route(checkout, "restart")
    assert result.returncode == 7
    assert result.stdout == ""


def test_dispatch_preserves_pipe_when_stdin_was_closed(checkout):
    enroll(checkout)
    result = command(
        checkout,
        [
            "bash",
            "-c",
            'exec 0<&-; exec "$1" -I tools/sage_mate_instance_control.py --action serve',
            "fixture",
            sys.executable,
        ],
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["request"]["action"] == "serve"


def test_installed_producer_is_not_reported_as_authorization(checkout):
    enroll(checkout)
    result = command(
        checkout,
        [sys.executable, "-I", "tools/sage_mate_instance_control.py", "--describe"],
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["producerInstalled"] is True
    assert data["lifecycleAvailable"] is False
    assert "authorization" in data["reason"]


@pytest.mark.parametrize("case", ["bad-invocation", "invalid-dotenv-encoding"])
def test_invalid_configuration_never_exposes_private_values(checkout, case):
    enroll(checkout)
    if case == "invalid-dotenv-encoding":
        (checkout / ".env").write_bytes(b"PRIVATE_CANARY=\xff\n")
    result = command(
        checkout,
        [
            sys.executable,
            "-I",
            "tools/sage_mate_instance_control.py",
            "--action",
            "serve",
        ],
        {"INVOCATION_ID": "PRIVATE_CANARY"} if case == "bad-invocation" else {},
    )
    assert result.returncode == 2
    assert "PRIVATE_CANARY" not in result.stderr
    assert json.loads(result.stderr)["lifecycleAvailable"] is False


@pytest.mark.parametrize(
    ("entry", "args", "action"),
    [
        ("tools/run_vllm_engine.sh", [], "serve"),
        ("tools/lock_sage_mate_engine.sh", [], "reconcile"),
        ("tools/cleanup_vllm_engine.sh", [], "cleanup"),
        ("tools/monitor_twin_inference.sh", [], "monitor"),
        ("tools/retry_deploy_vllm_ascend_until_success.sh", [], "reconcile"),
        ("manage.sh", ["restart", "--with-vllm-engine"], "restart"),
        ("manage.sh", ["stop", "--with-vllm-engine"], "stop"),
        ("manage.sh", ["start", "--with-vllm-engine"], "start"),
    ],
)
def test_actual_owner_entrypoints_do_not_touch_legacy_services(
    checkout, entry, args, action
):
    enroll(checkout)
    fake_bin = checkout.parent / "bin"
    fake_bin.mkdir()
    for tool in ("systemctl", "docker", "sudo", "npu-smi", "curl", "ps"):
        file = fake_bin / tool
        file.write_text("#!/bin/bash\necho LEGACY_COMMAND_MUST_NOT_RUN >&2\nexit 73\n")
        file.chmod(0o755)
    result = command(
        checkout, ["bash", entry, *args], {"PATH": f"{fake_bin}:{os.environ['PATH']}"}
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["request"]["action"] == action
    assert "LEGACY" not in result.stdout + result.stderr
    assert not (checkout.parent / "sage-mate-runtime-private").exists()


@pytest.mark.parametrize(
    ("entry", "args"),
    [
        ("manage.sh", ["restart", "--all"]),
        ("tools/reserve_vllm_devices.sh", ["4,5,6,7"]),
        ("quickstart.sh", ["--systemd-only", "--start", "--with-vllm-engine"]),
    ],
)
def test_combined_restart_and_device_mutation_fail_closed(checkout, entry, args):
    enroll(checkout)
    before = (checkout / ".env").read_bytes()
    result = command(checkout, ["bash", entry, *args])
    assert result.returncode == 2
    assert "unsupported operation" in result.stderr
    assert (checkout / ".env").read_bytes() == before


@pytest.fixture
def real_producer_checkout(checkout):
    """Use exact Git objects, not copied/mocked producer code or sibling paths.

    The override is only for reviewing a fetched candidate BEFORE updating the live
    parent gitlink. Normal CI resolves the committed parent's pin and never fetches.
    """
    source = ROOT / control.SUBMODULE
    pin = (
        os.environ.get("SAGE_MATE_TEST_PRODUCER_REVISION")
        or run_git(ROOT, "ls-tree", "HEAD", "--", control.SUBMODULE).split()[2]
    )
    pin = run_git(source, "rev-parse", "--verify", f"{pin}^{{commit}}")
    module = checkout / control.SUBMODULE
    module.rename(checkout.parent / "unused-fake-producer")
    run_git(checkout, "clone", "--shared", "--no-checkout", str(source), str(module))
    run_git(module, "checkout", "--detach", pin)
    run_git(
        checkout, "update-index", "--cacheinfo", f"160000,{pin},{control.SUBMODULE}"
    )
    run_git(checkout, "commit", "-qm", "pin real producer for isolated acceptance")
    assert run_git(module, "rev-parse", "HEAD") == pin
    assert (module / control.MANIFEST).is_file(), (
        "initialize the committed dev-hub producer"
    )
    return checkout


def closed_gate_command(repo, args):
    """Deny legacy service/network commands and isolate all writable runtime roots."""
    sandbox = repo.parent / "sandbox"
    sandbox.mkdir()
    denied = repo.parent / "deny-bin"
    denied.mkdir()
    for name in ("systemctl", "docker", "sudo", "npu-smi", "curl", "ps"):
        executable = denied / name
        executable.write_text("#!/bin/sh\necho FORBIDDEN_HOST_COMMAND >&2\nexit 73\n")
        executable.chmod(0o755)
    dotenv_before = (repo / ".env").read_bytes()
    result = command(
        repo,
        args,
        {
            "PATH": f"{denied}:{os.environ['PATH']}",
            "HOME": str(sandbox),
            "TMPDIR": str(sandbox),
            "XDG_RUNTIME_DIR": str(sandbox),
            "DBUS_SESSION_BUS_ADDRESS": f"unix:path={sandbox}/no-bus",
            "DIGITAL_TWIN_RUNTIME_DIR": str(sandbox),
            "DIGITAL_TWIN_API_KEY": "SECRET_CANARY",
            "INVOCATION_ID": "a" * 32,
        },
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert json.loads(result.stderr) == {
        "protocol": control.PROTOCOL,
        "error": "production_backend_not_qualified",
        "lifecycleAvailable": False,
    }
    assert result.stdout == ""
    assert "FORBIDDEN_HOST_COMMAND" not in result.stderr
    assert "SECRET_CANARY" not in result.stderr
    assert (repo / ".env").read_bytes() == dotenv_before
    assert list(sandbox.iterdir()) == []
    assert not list(repo.rglob("authority.sqlite3"))


@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize("action", sorted(control.ACTIONS))
def test_real_producer_rejects_every_owner_action(real_producer_checkout, flag, action):
    repo = real_producer_checkout
    enroll(repo, enabled=flag)
    closed_gate_command(
        repo,
        [
            sys.executable,
            "-I",
            "tools/sage_mate_instance_control.py",
            "--action",
            action,
        ],
    )


@pytest.mark.parametrize("flag", ["0", "1"])
@pytest.mark.parametrize(
    ("entry", "args"),
    [
        ("tools/run_vllm_engine.sh", []),
        ("tools/lock_sage_mate_engine.sh", []),
        ("tools/cleanup_vllm_engine.sh", []),
        ("tools/monitor_twin_inference.sh", []),
        ("tools/retry_deploy_vllm_ascend_until_success.sh", []),
        ("manage.sh", ["start", "--with-vllm-engine"]),
        ("manage.sh", ["stop", "--with-vllm-engine"]),
        ("manage.sh", ["restart", "--with-vllm-engine"]),
    ],
)
def test_real_producer_never_falls_back_from_owner_scripts(
    real_producer_checkout, flag, entry, args
):
    repo = real_producer_checkout
    enroll(repo, enabled=flag)
    closed_gate_command(repo, ["bash", entry, *args])


def test_real_producer_description_does_not_imply_approval(real_producer_checkout):
    repo = real_producer_checkout
    enroll(repo)
    result = command(
        repo,
        [sys.executable, "-I", "tools/sage_mate_instance_control.py", "--describe"],
    )
    assert result.returncode == 0
    status = json.loads(result.stdout)
    assert status["producerInstalled"] is True
    assert status["lifecycleAvailable"] is False
    assert not list(repo.rglob("authority.sqlite3"))


def test_guarded_producer_additions_are_in_exact_gitlink(real_producer_checkout):
    repo = real_producer_checkout
    module = repo / control.SUBMODULE
    pin = run_git(repo, "ls-tree", "HEAD", "--", control.SUBMODULE).split()[2]
    assert run_git(module, "rev-parse", "HEAD") == pin
    for relative in GUARDED_PRODUCER_FILES:
        assert (module / relative).is_file()
        assert run_git(module, "hash-object", "--", relative) == run_git(
            module, "rev-parse", f"{pin}:{relative}"
        )


@pytest.mark.parametrize("relative", GUARDED_PRODUCER_FILES)
def test_guarded_producer_addition_drift_blocks_owner_entry(
    real_producer_checkout, relative
):
    repo = real_producer_checkout
    enroll(repo)
    (repo / control.SUBMODULE / relative).write_text("UNTRUSTED_LOCAL_REPLACEMENT\n")
    result = shell_route(repo, "serve")
    assert result.returncode == 2
    assert "uncommitted tracked changes" in result.stderr
    assert "LEGACY" not in result.stdout


@pytest.mark.parametrize(
    "field",
    [
        "deployment_spec",
        "generation",
        "fence",
        "approved",
        "peer_uid",
        "executor_id",
        "quiescence_receipt",
    ],
)
def test_real_binding_cannot_inject_authority_state(real_producer_checkout, field):
    repo = real_producer_checkout
    binding = enroll(repo)
    data = json.loads(binding.read_text())
    data[field] = "UNTRUSTED_AUTHORITY_CANARY"
    binding.write_text(json.dumps(data))
    result = shell_route(repo, "serve")
    assert result.returncode == 2
    assert json.loads(result.stderr)["error"] == "unsupported registration schema"
    assert "CANARY" not in result.stderr and "LEGACY" not in result.stdout
    assert not list(repo.rglob("authority.sqlite3"))
