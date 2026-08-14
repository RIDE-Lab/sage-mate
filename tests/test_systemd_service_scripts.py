from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
QUICKSTART_SCRIPT = REPO_ROOT / "quickstart.sh"
HOSTED_WEB_INSTALLER = REPO_ROOT / "release" / "hosted-web.sh"
PROXY_SCRIPT = REPO_ROOT / "tools" / "run_vllm_openai_proxy.sh"
ENGINE_SCRIPT = REPO_ROOT / "tools" / "run_vllm_engine.sh"
ENGINE_LOCK_SCRIPT = REPO_ROOT / "tools" / "lock_sage_mate_engine.sh"
APP_SCRIPT = REPO_ROOT / "tools" / "run_app_server.sh"


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

    Sets up fake systemctl, PYTHON_BIN, and lets the script find the
    existing repo .env so it skips .env bootstrap.
    """
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir(exist_ok=True)
    _make_fake_systemctl(fake_bin_dir / "systemctl")

    systemctl_log = tmp_path / "systemctl.log"
    xdg_config_home = tmp_path / "xdg"

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(tmp_path / "home"),
            "XDG_CONFIG_HOME": str(xdg_config_home),
            "PATH": f"{fake_bin_dir}:{env['PATH']}",
            "SYSTEMCTL_LOG": str(systemctl_log),
            "VLLM_NVIDIA_CONNECT_HOST": "127.0.0.1",
        }
    )
    if python_bin is not None:
        env["PYTHON_BIN"] = python_bin

    args = ["bash", str(QUICKSTART_SCRIPT), "--target", "hosted-web"]
    if extra_args:
        args.extend(extra_args)

    result = subprocess.run(
        args,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
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

    assert 'missing+=("isage-vdb>=0.2.0.9")' in script
    assert 'missing+=("isage-anns>=0.2.0")' in script
    assert '"$py" -m pip install --quiet "${missing[@]}"' in script
    assert 'DIGITAL_TWIN_KNOWLEDGE_BACKEND:-neuromem' in script
    assert 'DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE:-segment' in script
    assert '"$conversation_index" == "sage_vdb_ann"' in script
    assert '"$conversation_index" == "sagedb_ann"' in script


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


def test_engine_example_disables_foreign_pythonpath_inheritance() -> None:
    """Pinned submodules must win over plugin sources baked into an image."""

    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "VLLM_ENGINE_INHERIT_PYTHONPATH=0" in example


def test_engine_verifier_checks_runtime_import_origins() -> None:
    """Verification must reject undeclared engine/plugin source trees."""

    script = (REPO_ROOT / "tools" / "verify_sage_mate_engine.sh").read_text(
        encoding="utf-8"
    )
    assert "undeclared engine/plugin source found in runtime PYTHONPATH" in script
    assert "engine/plugin imported from undeclared source" in script
    assert "import_origins=" in script
