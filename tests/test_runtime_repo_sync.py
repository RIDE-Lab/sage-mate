from __future__ import annotations

import os
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SYNC_SCRIPT = REPO_ROOT / "tools" / "sync_runtime_repo.sh"
HOSTED_WEB_SCRIPT = REPO_ROOT / "release" / "hosted-web.sh"
RUNTIME_ENV_SCRIPT = REPO_ROOT / "tools" / "lib" / "runtime_env.sh"


def run_git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        text=True,
        capture_output=True,
    )


def test_runtime_repo_replaces_seed_folder_and_preserves_local_only_files(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_git("init", "--initial-branch=main", cwd=source)
    run_git("config", "user.email", "test@example.com", cwd=source)
    run_git("config", "user.name", "Runtime Test", cwd=source)
    (source / "data").mkdir()
    (source / "data" / "canonical.json").write_text("repo", encoding="utf-8")
    (source / "cloudflared").mkdir()
    (source / "cloudflared" / "config.yml").write_text("repo-tunnel", encoding="utf-8")
    run_git("add", ".", cwd=source)
    run_git("commit", "-m", "seed runtime", cwd=source)

    runtime_dir = tmp_path / "runtime-private"
    (runtime_dir / "data").mkdir(parents=True)
    (runtime_dir / "data" / "canonical.json").write_text("stale", encoding="utf-8")
    (runtime_dir / "cloudflared").mkdir()
    (runtime_dir / "cloudflared" / "token").write_text("secret", encoding="utf-8")
    (runtime_dir / "cloudflared" / "config.yml").write_text(
        "local-tunnel", encoding="utf-8"
    )

    result = subprocess.run(
        [
            "bash",
            str(SYNC_SCRIPT),
            "--runtime-dir",
            str(runtime_dir),
            "--repo-url",
            str(source),
            "--required",
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert (runtime_dir / ".git").is_dir()
    assert (runtime_dir / "data" / "canonical.json").read_text(
        encoding="utf-8"
    ) == "repo"
    assert (runtime_dir / "cloudflared" / "token").read_text(
        encoding="utf-8"
    ) == "secret"
    assert (runtime_dir / "cloudflared" / "config.yml").read_text(
        encoding="utf-8"
    ) == "local-tunnel"
    assert "secret" not in result.stdout
    assert list(tmp_path.glob("runtime-private.pre-repo-*"))


def test_runtime_repo_fast_forwards_clean_checkout(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    run_git("init", "--initial-branch=main", cwd=source)
    run_git("config", "user.email", "test@example.com", cwd=source)
    run_git("config", "user.name", "Runtime Test", cwd=source)
    (source / "version.txt").write_text("one", encoding="utf-8")
    run_git("add", ".", cwd=source)
    run_git("commit", "-m", "version one", cwd=source)

    runtime_dir = tmp_path / "runtime-private"
    subprocess.run(
        [
            "bash",
            str(SYNC_SCRIPT),
            "--runtime-dir",
            str(runtime_dir),
            "--repo-url",
            str(source),
            "--required",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    (runtime_dir / "local-only.json").write_text("local", encoding="utf-8")
    (source / "version.txt").write_text("two", encoding="utf-8")
    run_git("add", ".", cwd=source)
    run_git("commit", "-m", "version two", cwd=source)

    subprocess.run(
        [
            "bash",
            str(SYNC_SCRIPT),
            "--runtime-dir",
            str(runtime_dir),
            "--repo-url",
            str(source),
            "--required",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert (runtime_dir / "version.txt").read_text(encoding="utf-8") == "two"
    assert (runtime_dir / "local-only.json").read_text(encoding="utf-8") == "local"


def test_hosted_release_configures_private_runtime_before_quickstart() -> None:
    script = HOSTED_WEB_SCRIPT.read_text(encoding="utf-8")
    main_body = script.split("main() {", 1)[1]
    configure_index = main_body.index(
        'set_env_kv "$env_file" FACULTY_TWIN_RUNTIME_REPO_URL'
    )
    quickstart_index = main_body.index('./quickstart.sh "${quickstart_args[@]}"')

    assert configure_index < quickstart_index
    assert "Qixin-Gaoke/sage-mate-runtime-private.git" in script


def test_runtime_env_file_is_loaded_before_shared_defaults(tmp_path: Path) -> None:
    configured_runtime = tmp_path / "dedicated-runtime"
    (tmp_path / ".env").write_text(
        f"DIGITAL_TWIN_RUNTIME_DIR={configured_runtime}\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("DIGITAL_TWIN_RUNTIME_DIR", None)

    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; load_repo_env_if_unset "$2"; '
            'export_repo_runtime_env "$2"; printf "%s" "$DIGITAL_TWIN_RUNTIME_DIR"',
            "runtime-env-test",
            str(RUNTIME_ENV_SCRIPT),
            str(tmp_path),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout == str(configured_runtime)


def test_runtime_pythonpath_uses_installed_neuromem_unless_explicitly_opted_in(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "sage-mate"
    (repo_root / "src").mkdir(parents=True)
    (tmp_path / "SAGE" / "src").mkdir(parents=True)
    (tmp_path / "neuromem").mkdir()
    env = os.environ.copy()
    env.pop("DIGITAL_TWIN_USE_LOCAL_NEUROMEM_SOURCE", None)

    default_result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; build_repo_pythonpath "$2"',
            "runtime-env-test",
            str(RUNTIME_ENV_SCRIPT),
            str(repo_root),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    default_entries = {
        Path(entry).resolve() for entry in default_result.stdout.strip().split(":") if entry
    }
    assert (tmp_path / "neuromem").resolve() not in default_entries

    env["DIGITAL_TWIN_USE_LOCAL_NEUROMEM_SOURCE"] = "true"
    opted_in_result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; build_repo_pythonpath "$2"',
            "runtime-env-test",
            str(RUNTIME_ENV_SCRIPT),
            str(repo_root),
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    opted_in_entries = {
        Path(entry).resolve() for entry in opted_in_result.stdout.strip().split(":") if entry
    }
    assert (tmp_path / "neuromem").resolve() in opted_in_entries
