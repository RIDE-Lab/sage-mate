from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tools.inspect_vllm_model import inspect_model


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "inspect_vllm_model.py"
ENGINE_SCRIPT = REPO_ROOT / "tools" / "run_vllm_engine.sh"
LOCK_SCRIPT = REPO_ROOT / "tools" / "lock_sage_mate_engine.sh"
METADATA_LIBRARY = REPO_ROOT / "tools" / "lib" / "vllm_model_metadata.sh"
CONTAINER_LIBRARY = REPO_ROOT / "tools" / "lib" / "vllm_container_identity.sh"


def test_explicit_checkpoint_metadata_comes_from_config_not_name_rules(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "opaque-checkpoint"
    model_path.mkdir()
    (model_path / "config.json").write_text(
        json.dumps(
            {
                "model_type": "research_arch_v2",
                "architectures": ["ResearchArchForCausalLM"],
                "_name_or_path": "organization/model-release",
            }
        ),
        encoding="utf-8",
    )

    metadata = inspect_model(model_path, "public-served-name")

    assert metadata == {
        "VLLM_ENGINE_ACTUAL_MODEL_ID": "organization/model-release",
        "VLLM_ENGINE_MODEL_SOURCE": "local",
        "VLLM_ENGINE_MODEL_FAMILY": "research_arch_v2",
        "VLLM_ENGINE_ARCHITECTURE": "ResearchArchForCausalLM",
    }


def test_missing_or_invalid_config_has_safe_honest_fallback(tmp_path: Path) -> None:
    model_path = tmp_path / "configured-later"

    metadata = inspect_model(model_path, "served-model")

    assert metadata["VLLM_ENGINE_ACTUAL_MODEL_ID"] == "served-model"
    assert metadata["VLLM_ENGINE_MODEL_SOURCE"] == "configured"
    assert metadata["VLLM_ENGINE_MODEL_FAMILY"] == "unknown"
    assert metadata["VLLM_ENGINE_ARCHITECTURE"] == "unknown"


def test_cli_emits_shell_safe_environment(tmp_path: Path) -> None:
    model_path = tmp_path / "model with spaces"
    model_path.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--model-path",
            str(model_path),
            "--served-name",
            "served model",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "VLLM_ENGINE_ACTUAL_MODEL_ID='served model'" in result.stdout


def test_engine_launcher_has_bounded_logs_and_run_boundaries() -> None:
    script = ENGINE_SCRIPT.read_text(encoding="utf-8")
    lock_script = LOCK_SCRIPT.read_text(encoding="utf-8")
    metadata_library = METADATA_LIBRARY.read_text(encoding="utf-8")

    assert "VLLM_ENGINE_LOG_MAX_BYTES" in script
    assert "VLLM_ENGINE_LOG_BACKUP_COUNT" in script
    assert "=== Sage Mate engine launch" in script
    assert "vllm_model_metadata.sh" in script
    assert "vllm_model_metadata.sh" in lock_script
    assert "inspect_vllm_model.py" in metadata_library


def test_lock_launcher_and_verifier_share_portable_container_identity() -> None:
    library = CONTAINER_LIBRARY.read_text(encoding="utf-8")
    launcher = ENGINE_SCRIPT.read_text(encoding="utf-8")
    lock = LOCK_SCRIPT.read_text(encoding="utf-8")
    verifier = (REPO_ROOT / "tools" / "verify_sage_mate_engine.sh").read_text(
        encoding="utf-8"
    )

    assert "default_vllm_engine_container_name" in library
    assert "VLLM_ENGINE_MODEL" not in library
    for consumer in (launcher, lock, verifier):
        assert "vllm_container_identity.sh" in consumer
        assert "normalize_vllm_engine_container_name" in consumer
