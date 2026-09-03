from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUICKSTART_SCRIPT = REPO_ROOT / "quickstart.sh"
HOSTED_WEB_INSTALLER = REPO_ROOT / "release" / "hosted-web.sh"
PROXY_SCRIPT = REPO_ROOT / "tools" / "run_vllm_openai_proxy.sh"
ENGINE_SCRIPT = REPO_ROOT / "tools" / "run_vllm_engine.sh"
ENGINE_LOCK_SCRIPT = REPO_ROOT / "tools" / "lock_sage_mate_engine.sh"
APP_SCRIPT = REPO_ROOT / "tools" / "run_app_server.sh"
DEPLOY_HELPERS = REPO_ROOT / "tools" / "lib" / "deploy_common.sh"


def test_runtime_dependency_contract_separates_portable_core_from_full_neuromem() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    dependencies = {
        dependency.split(">=", 1)[0].lower() for dependency in project["dependencies"]
    }

    assert project["requires-python"] == ">=3.11"
    assert {"isage", "isage-anns"} <= dependencies
    assert any(
        dependency.lower().startswith("isage-neuromem")
        for dependency in project["optional-dependencies"]["neuromem"]
    )
    assert not any(
        dependency.lower().startswith("isage-neuromem")
        for extra in ("vdb", "vdb-anns")
        for dependency in project["optional-dependencies"][extra]
    )


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _make_fake_python(path: Path, *, has_uvicorn: bool) -> Path:
    exit_code = "0" if has_uvicorn else "1"
    _write_executable(
        path,
        "#!/usr/bin/env bash\n"
        f"exit {exit_code}\n",
    )
    return path


def _make_fake_systemctl(path: Path) -> Path:
    _write_executable(
        path,
        "#!/usr/bin/env bash\n"
        "printf '%s\n' \"$*\" >>\"$SYSTEMCTL_LOG\"\n"
        "exit 0\n",
    )
    return path


def _run_quickstart_install(
    tmp_path: Path,
    *,
    extra_args: list[str] | None = None,
    python_bin: str | None = None,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run ``quickstart.sh`` with the systemd install path active.

    Exercise real unit rendering in a minimal checkout, without reading the
    deployment's .env, installing packages, accessing hardware or the network.
    """
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir(exist_ok=True)
    _make_fake_systemctl(fake_bin_dir / "systemctl")
    network_log = tmp_path / "unexpected-network.log"
    _write_executable(
        fake_bin_dir / "git",
        '#!/usr/bin/env bash\n'
        'printf "%s\\n" "$*" >> "$UNEXPECTED_NETWORK_LOG"\nexit 1\n',
    )
    _write_executable(fake_bin_dir / "curl", '#!/usr/bin/env bash\nprintf 200\n')
    for command in ("npu-smi", "nvidia-smi"):
        _write_executable(fake_bin_dir / command, "#!/usr/bin/env bash\nexit 0\n")

    checkout = tmp_path / "checkout"
    checkout.mkdir(exist_ok=True)
    for relative in ("quickstart.sh", "pyproject.toml"):
        shutil.copyfile(REPO_ROOT / relative, checkout / relative)
    for relative in ("tools/lib", "deploy/systemd/user"):
        shutil.copytree(REPO_ROOT / relative, checkout / relative, dirs_exist_ok=True)
    # Only synthetic configuration is allowed in this fixture. In particular,
    # never copy production credentials or point at its private runtime data.
    (checkout / ".env").write_text(
        "VLLM_PROXY_CONNECT_HOST=127.0.0.1\n"
        "VLLM_PROXY_PORT=18001\n"
        "APP_HEALTH_HOST=127.0.0.1\n"
        "APP_PORT=55601\n",
        encoding="utf-8",
    )

    systemctl_log = tmp_path / "systemctl.log"
    xdg_config_home = tmp_path / "xdg"

    env = {
        "HOME": os.environ["HOME"],
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "PATH": f"{fake_bin_dir}:{os.environ['PATH']}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "UNEXPECTED_NETWORK_LOG": str(network_log),
        "FACULTY_TWIN_PARENT_DIR": str(tmp_path / "siblings"),
        "DIGITAL_TWIN_RUNTIME_DIR": str(tmp_path / "runtime"),
        "VLLM_NVIDIA_CONNECT_HOST": "127.0.0.1",
        "PYTHON_BIN": python_bin or str(
            _make_fake_python(fake_bin_dir / "python", has_uvicorn=True)
        ),
    }

    args = [
        "bash", str(checkout / "quickstart.sh"),
        "--target", "hosted-web", "--systemd-only",
    ]
    if extra_args:
        args.extend(extra_args)

    result = subprocess.run(
        args,
        cwd=checkout,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert not network_log.exists(), "Unit rendering unexpectedly invoked git"
    return result, systemctl_log


def test_quickstart_install_renders_service_units(tmp_path: Path) -> None:
    """quickstart.sh renders __REPO_ROOT__ and __PYTHON_BIN__ placeholders."""
    good_python = _make_fake_python(tmp_path / "good-python", has_uvicorn=True)

    result, systemctl_log = _run_quickstart_install(
        tmp_path, python_bin=str(good_python)
    )

    assert result.returncode == 0, (
        f"quickstart.sh failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )

    target_dir = tmp_path / "xdg" / "systemd" / "user"
    rendered_app = (target_dir / "sage-mate-app.service").read_text(
        encoding="utf-8"
    )
    rendered_site = (target_dir / "sage-mate-site.service").read_text(
        encoding="utf-8"
    )
    rendered_tunnel = (target_dir / "sage-mate-tunnel.service").read_text(
        encoding="utf-8"
    )
    assert f"Environment=PYTHON_BIN={good_python}" in rendered_app
    assert "__REPO_ROOT__" not in rendered_app
    assert str(tmp_path / "checkout") in rendered_app
    assert "Skipping Python dependency installation" in result.stdout
    assert "Skipping sibling repo cloning" in result.stdout
    assert (tmp_path / "runtime" / "data" / "changelog.json").is_file()
    assert "Wants=sage-mate-app.service" in rendered_site
    assert "Requires=sage-mate-app.service" not in rendered_site
    assert "Wants=sage-mate-site.service" in rendered_tunnel
    assert "Requires=sage-mate-site.service" not in rendered_tunnel

    # Systemctl was called for daemon-reload and enable
    log_text = systemctl_log.read_text(encoding="utf-8")
    assert "daemon-reload" in log_text
    assert "sage-mate-app.service" in log_text


def test_release_installer_uses_multi_gpu_tp_for_qwen3_32b_awq() -> None:
    """Large NVIDIA presets should not silently pin tensor parallelism to one GPU."""

    script = HOSTED_WEB_INSTALLER.read_text(encoding="utf-8")
    assert "default_nvidia_tensor_parallel_size" in script
    qwen32_case = script.split("qwen3-32b-awq)", 1)[1].split(";;", 1)[0]
    assert 'tp="${tp_override:-1}"' not in qwen32_case
    assert 'tp="${tp_override:-$(default_nvidia_tensor_parallel_size "$gpus" "$min_mem")}"' in qwen32_case


def test_quickstart_install_only_enables_optional_services_with_flags(
    tmp_path: Path,
) -> None:
    """Optional services are NOT enabled unless their flag is passed."""
    good_python = _make_fake_python(tmp_path / "good-python", has_uvicorn=True)

    # Default install — only app should be enabled
    result, systemctl_log = _run_quickstart_install(
        tmp_path, python_bin=str(good_python)
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    default_log = systemctl_log.read_text(encoding="utf-8")
    assert "sage-mate-app.service" in default_log
    assert "sage-mate-tunnel.service" not in default_log
    assert "sage-mate-vllm-openai-proxy.service" not in default_log
    assert "sage-mate-vllm-engine.service" not in default_log

    # --with-tunnel
    systemctl_log.write_text("", encoding="utf-8")
    result, _ = _run_quickstart_install(
        tmp_path,
        extra_args=["--with-tunnel"],
        python_bin=str(good_python),
    )
    assert result.returncode == 0
    tunnel_log = systemctl_log.read_text(encoding="utf-8")
    assert "sage-mate-tunnel.service" in tunnel_log

    # --with-vllm-proxy
    systemctl_log.write_text("", encoding="utf-8")
    result, _ = _run_quickstart_install(
        tmp_path,
        extra_args=["--with-vllm-proxy"],
        python_bin=str(good_python),
    )
    assert result.returncode == 0
    proxy_log = systemctl_log.read_text(encoding="utf-8")
    assert "sage-mate-vllm-openai-proxy.service" in proxy_log

    # --with-nvidia-vllm-engine
    systemctl_log.write_text("", encoding="utf-8")
    result, _ = _run_quickstart_install(
        tmp_path,
        extra_args=["--with-nvidia-vllm-engine", "--skip-python-install"],
        python_bin=str(good_python),
    )
    assert result.returncode == 0
    engine_log = systemctl_log.read_text(encoding="utf-8")
    assert "sage-mate-vllm-nvidia-engine.service" in engine_log


def test_run_vllm_openai_proxy_fails_fast_when_port_is_occupied(tmp_path: Path) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    occupied_port = listener.getsockname()[1]

    env = os.environ.copy()
    env.update(
        {
            "PYTHON_BIN": sys.executable,
            "VLLM_PROXY_HOST": "127.0.0.1",
            "VLLM_PROXY_PORT": str(occupied_port),
        }
    )

    try:
        result = subprocess.run(
            ["bash", str(PROXY_SCRIPT)],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        listener.close()

    assert result.returncode == 1
    assert "already in use" in result.stderr
    assert "VLLM_PROXY_PORT" in result.stderr


def test_app_runtime_auto_installs_enabled_sage_anns_backend() -> None:
    script = APP_SCRIPT.read_text(encoding="utf-8")
    helpers = DEPLOY_HELPERS.read_text(encoding="utf-8")
    quickstart = QUICKSTART_SCRIPT.read_text(encoding="utf-8")

    assert "_ensure_base_runtime_deps" in script
    assert "version('isage')" in script
    assert "version('isage-neuromem')" in script
    assert "from sage.neuromem import UnifiedCollection" in script
    assert '"$py" -m pip install --quiet -e "$repo_root"' in script
    assert "ensure_neuromem_collection_runtime \"$repo_root\" \"$py\"" in script
    assert '"$python_bin" -m pip install --quiet --no-deps "$requirement"' in helpers
    assert '"$uv_bin" pip install --python "$python_bin" --no-deps "$requirement"' in helpers
    assert "ensure_neuromem_collection_runtime \"$repo_root\" \"$python_bin\"" in quickstart
    assert '"$py" -m pip install --quiet -e "$repo_root[vdb-anns]"' in script
    assert 'DIGITAL_TWIN_KNOWLEDGE_BACKEND:-neuromem' in script
    assert 'DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE:-segment' in script
    assert '"$conversation_index" == "sage_vdb_ann"' in script
    assert '"$conversation_index" == "sagedb_ann"' in script


def test_engine_lock_waits_for_ascend_namespace_release_with_a_finite_bound() -> None:
    script = ENGINE_LOCK_SCRIPT.read_text(encoding="utf-8")

    assert "VLLM_ENGINE_DEVICE_RELEASE_TIMEOUT_SECONDS" in script
    assert "VLLM_ENGINE_DEVICE_RELEASE_POLL_SECONDS" in script
    assert 'release_deadline=$((SECONDS + release_timeout))' in script
    assert "while ! \"$python_bin\" \"$repo_root/tools/select_idle_npus.py\"" in script
    assert "configured NPU devices did not become idle within" in script


def test_engine_lock_help_is_non_mutating_and_unknown_args_fail(tmp_path: Path) -> None:
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    _make_fake_systemctl(fake_bin_dir / "systemctl")
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin_dir}:{env['PATH']}",
            "SYSTEMCTL_LOG": str(systemctl_log),
        }
    )

    help_result = subprocess.run(
        ["bash", str(ENGINE_LOCK_SCRIPT), "--help"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "Apply the machine-local" in help_result.stdout
    assert not systemctl_log.exists()

    invalid_result = subprocess.run(
        ["bash", str(ENGINE_LOCK_SCRIPT), "--unexpected"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid_result.returncode == 2
    assert "unsupported argument" in invalid_result.stderr
    assert not systemctl_log.exists()


def test_run_vllm_engine_script_errors_without_container(tmp_path: Path) -> None:
    """Engine launcher fails fast when the Docker container is not found."""
    env = os.environ.copy()
    # Set a non-empty value so the .env loader skips it (already set).
    # docker inspect will fail because this container doesn't exist.
    env["VLLM_ENGINE_CONTAINER"] = "nonexistent-test-container"
    env["VLLM_ENGINE_AUTO_CREATE_CONTAINER"] = "false"
    env["VLLM_ENGINE_REPLACE_EXISTING"] = "false"
    env["VLLM_HUST_API_KEY"] = "test-api-key"
    env["VLLM_ENGINE_MODEL_PATH"] = "/tmp/nonexistent-test-model"
    env["DIGITAL_TWIN_RUNTIME_DIR"] = str(tmp_path / "runtime")
    env["VLLM_ENGINE_CONTAINER_LOG_FILE"] = str(tmp_path / "engine.log")
    idle_probe = _make_fake_python(tmp_path / "idle-probe", has_uvicorn=True)
    env["PYTHON_BIN"] = str(idle_probe)

    result = subprocess.run(
        ["bash", str(ENGINE_SCRIPT)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    diagnostics = result.stdout + result.stderr
    assert (
        "nonexistent-test-container" in diagnostics
        or "docker not found on PATH" in diagnostics
        or "vLLM-HUST dev-hub submodule launcher not found" in diagnostics
        or "VLLM_ENGINE_NPU_DEVICES" in diagnostics
        or "not executable in the container" in diagnostics
    )


def test_engine_lock_clears_stale_kv_cache_contract() -> None:
    """Manager environment must not override machine-local KV cache settings."""

    script = ENGINE_LOCK_SCRIPT.read_text(encoding="utf-8")
    unset_block = script.split("systemctl --user unset-environment", 1)[1].split(
        ">/dev/null 2>&1 || true", 1
    )[0]
    assert "VLLM_ENGINE_KV_CACHE_DTYPE" in unset_block
    assert "VLLM_ENGINE_KV_CACHE_MEMORY_BYTES" in unset_block


def test_engine_lock_records_configured_model_provenance() -> None:
    script = ENGINE_LOCK_SCRIPT.read_text(encoding="utf-8")
    receipt_block = script.split("umask 077", 1)[1].split("> \"$lock_file\"", 1)[0]

    for key in (
        "VLLM_ENGINE_MODEL_PATH",
        "VLLM_ENGINE_SERVED_MODEL_NAME",
        "VLLM_ENGINE_ACTUAL_MODEL_ID",
        "VLLM_ENGINE_MODEL_SOURCE",
        "VLLM_ENGINE_MODEL_FAMILY",
        "VLLM_ENGINE_ARCHITECTURE",
    ):
        assert key in receipt_block


def test_engine_example_disables_foreign_pythonpath_inheritance() -> None:
    """Pinned submodules must win over plugin sources baked into an image."""

    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "VLLM_ENGINE_INHERIT_PYTHONPATH=0" in example


def test_engine_launcher_preserves_explicit_immutable_wheel_profile() -> None:
    """Empty conda/source-path values must not be replaced by dev defaults."""

    script = ENGINE_SCRIPT.read_text(encoding="utf-8")
    assert '${VLLM_ENGINE_CONDA_ENV-vllm-hust-dev}' in script
    assert (
        '${VLLM_ENGINE_BASE_PYTHONPATH-/workspace/vllm-hust:/workspace/vllm-ascend-hust}'
        in script
    )
    assert '${VLLM_ENGINE_CONDA_ENV:-vllm-hust-dev}' not in script
    assert '${VLLM_ENGINE_BASE_PYTHONPATH:-/workspace/vllm-hust:/workspace/vllm-ascend-hust}' not in script


def test_engine_verifier_checks_runtime_import_origins() -> None:
    """Verification must accept only declared sources or exact owned wheels."""

    script = (REPO_ROOT / "tools" / "verify_sage_mate_engine.sh").read_text(
        encoding="utf-8"
    )
    assert "undeclared engine/plugin source found in runtime PYTHONPATH" in script
    assert "VLLM_ENGINE_INSTALLED_MODULES_JSON" in script
    assert "distribution.version != expected_version" in script
    assert "is not owned by" in script
    assert "import_origins=" in script
    assert "warnings.catch_warnings()" in script
    assert 'message=r"Failed to read commit hash:.*"' in script


def test_engine_verifier_accepts_immutable_wheels_without_source_roots() -> None:
    """An explicit wheel contract must not require a source PYTHONPATH."""

    script = (REPO_ROOT / "tools" / "verify_sage_mate_engine.sh").read_text(
        encoding="utf-8"
    )
    assert '[[ -n "$runtime_pythonpath" ]]' in script
    assert '[[ -n "$declared_pythonpath" || "$installed_modules_json" != \'{}\' ]]' in script
    assert "runtime or declared engine PYTHONPATH is empty" not in script
