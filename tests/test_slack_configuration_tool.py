import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("invalid", [None, "missing_team", "argument"])
def test_configuration_helper_requires_workspace_and_keeps_secret_out_of_arguments(tmp_path, invalid):
    tools = tmp_path / "tools"
    tools.mkdir()
    script = tools / "configure_slack_twin.sh"
    shutil.copyfile(Path(__file__).parents[1] / "tools/configure_slack_twin.sh", script)
    env_file = tmp_path / ".env"
    env_file.write_text("OTHER=preserved\n")
    environment = {"PATH": os.environ["PATH"], "PYTHON_BIN": sys.executable,
        "SLACK_TWIN_SIGNING_SECRET": "synthetic-test-signing-value",
        "SLACK_TWIN_TEAM_ID": "T123", "SLACK_TWIN_RESTART_APP": "false"}
    if invalid == "missing_team":
        environment.pop("SLACK_TWIN_TEAM_ID")
    arguments = ["bash", str(script)] + (["disallowed-placeholder"] if invalid == "argument" else [])
    result = subprocess.run(arguments, env=environment, capture_output=True, text=True, check=False)
    assert environment["SLACK_TWIN_SIGNING_SECRET"] not in result.stdout + result.stderr
    if invalid:
        assert result.returncode == 2
        assert env_file.read_text() == "OTHER=preserved\n"
    else:
        assert result.returncode == 0
        assert "SLACK_TWIN_TEAM_ID=T123" in env_file.read_text()
        assert "SLACK_TWIN_ALLOWED_USER_IDS=\n" in env_file.read_text()
        assert env_file.stat().st_mode & 0o777 == 0o600
