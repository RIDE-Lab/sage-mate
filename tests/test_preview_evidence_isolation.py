import importlib.util
from pathlib import Path
from types import SimpleNamespace


def test_preview_remaps_all_mutable_typed_paths_not_just_standard_production_prefix():
    spec = importlib.util.spec_from_file_location("preview_evidence", Path(__file__).parents[1] /
        "tools/preview_evidence_workflow.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    def field(annotation, default):
        return SimpleNamespace(annotation=annotation, default=default)
    fields = {"knowledge_base_dir": field(Path, Path("data/knowledge")),
        "new_mutable_path": field(Path, Path("anywhere/state.sqlite3")),
        "optional_state_dir": field(Path | None, None),
        "disabled_optional_dir": field(Path | None, None),
        "workflow_policy_path": field(Path, Path("policy.json")),
        "token": field(str, "")}
    env = {"DIGITAL_TWIN_KNOWLEDGE_BASE_DIR": "/unusual/production/path",
        "DIGITAL_TWIN_OPTIONAL_STATE_DIR": "/other/production/path",
        "DIGITAL_TWIN_WORKFLOW_POLICY_PATH": "/readonly/policy.json"}
    runtime = Path("/isolated/acceptance")
    module.isolate_paths(env, fields, runtime)
    for name in ("knowledge_base_dir", "new_mutable_path", "optional_state_dir"):
        assert Path(env["DIGITAL_TWIN_"+name.upper()]).is_relative_to(runtime)
    assert env["DIGITAL_TWIN_NEW_MUTABLE_PATH"].endswith("state.sqlite3")
    assert "DIGITAL_TWIN_DISABLED_OPTIONAL_DIR" not in env
    assert env["DIGITAL_TWIN_WORKFLOW_POLICY_PATH"] == "/readonly/policy.json"
