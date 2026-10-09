from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_ascend_workflow_is_manual_main_only_and_read_only() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ascend-npu.yml").read_text(
        encoding="utf-8"
    )
    trigger_block = workflow.split("permissions:", 1)[0]
    assert "workflow_dispatch:" in trigger_block
    assert "pull_request:" not in trigger_block
    assert "push:" not in trigger_block
    assert "contents: read" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "environment: ascend-npu" in workflow
    assert "group: sage-mate-ascend" in workflow
    assert "- sage-mate-ephemeral" in workflow


def test_ascend_workflow_pins_third_party_actions() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ascend-npu.yml").read_text(
        encoding="utf-8"
    )
    references = re.findall(r"^\s*uses:\s*\S+@([^\s]+)$", workflow, re.MULTILINE)
    assert references
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in references)


def test_runner_launcher_requires_ephemeral_restricted_group() -> None:
    launcher = (ROOT / "tools" / "run_ascend_ci_once.sh").read_text(encoding="utf-8")
    assert "--ephemeral" in launcher
    assert "restricted_to_workflows == true" in launcher
    assert 'visibility == "selected"' in launcher
    assert "@refs/heads/main" in launcher
    assert 'orgs/$organization/actions/runners/registration-token' in launcher
    assert 'runner-groups/$group_id/repositories' in launcher
    assert "runner remained registered" in launcher


def test_host_probe_is_non_destructive_and_keeps_graph_mode() -> None:
    probe = (ROOT / "tools" / "verify_ascend_ci_host.sh").read_text(encoding="utf-8")
    assert "workflow_dispatch" in probe
    assert "refs/heads/main" in probe
    assert "--network none" in probe
    assert "VLLM_ENGINE_ENFORCE_EAGER=0" in probe
    assert "--enforce-eager" in probe
    assert '"result":"failed"' in probe
    assert "systemctl start" not in probe
    assert "systemctl restart" not in probe
