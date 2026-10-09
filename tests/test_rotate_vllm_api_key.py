from __future__ import annotations

from pathlib import Path
import stat

from tools.rotate_vllm_api_key import DEFAULT_NAMES, rotate


def test_rotate_updates_all_shared_names_without_touching_other_values(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "DIGITAL_TWIN_API_KEY=old\n"
        "UNCHANGED=value\n"
        "VLLM_PROXY_UPSTREAM_API_KEY=old\n"
        "VLLM_HUST_API_KEY=old\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    rotate(env_path, DEFAULT_NAMES)

    values = dict(
        line.split("=", 1)
        for line in env_path.read_text(encoding="utf-8").splitlines()
    )
    rotated = {values[name] for name in DEFAULT_NAMES}
    assert len(rotated) == 1
    assert rotated != {"old"}
    assert values["UNCHANGED"] == "value"
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_rotate_refuses_partial_configuration(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("VLLM_HUST_API_KEY=old\n", encoding="utf-8")

    try:
        rotate(env_path, DEFAULT_NAMES)
    except SystemExit as error:
        assert "missing variables" in str(error)
    else:
        raise AssertionError("partial credential configuration must be rejected")
