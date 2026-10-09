from pathlib import Path

from sage_faculty_twin.config import AppSettings, DEFAULT_RUNTIME_SEED_DATA_DIR


def test_runtime_dir_supplies_mutable_store_defaults(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DIGITAL_TWIN_CONVERSATION_MEMORY_DIR", raising=False)

    settings = AppSettings(_env_file=None, runtime_dir=tmp_path)

    assert settings.knowledge_base_dir == tmp_path / "data/knowledge_base"
    assert settings.knowledge_sagevdb_persistence_dir == (
        tmp_path / "data/knowledge_base/.sagevdb-index"
    )
    assert settings.conversation_memory_dir == tmp_path / "data/conversation_memory"
    assert settings.user_account_store_dir == tmp_path / "data/user_accounts"
    assert settings.slack_user_link_dir == tmp_path / "data/slack_user_links"
    assert settings.online_presence_dir == tmp_path / ".runtime/online_presence"
    assert settings.changelog_path == tmp_path / "data/changelog.json"
    assert (
        settings.workflow_scenario_path
        == DEFAULT_RUNTIME_SEED_DATA_DIR / "workflow_scenarios/v3_preview_scenarios.json"
    )
    assert (
        settings.installed_skill_prompt_path
        == tmp_path / "data/installed_skills/fixed_prompt_skills.md"
    )


def test_explicit_store_paths_are_not_overridden_by_runtime_dir(tmp_path: Path) -> None:
    knowledge_dir = tmp_path / "custom-knowledge"
    memory_dir = tmp_path / "custom-memory"

    settings = AppSettings(
        _env_file=None,
        runtime_dir=tmp_path / "runtime",
        knowledge_base_dir=knowledge_dir,
        conversation_memory_dir=memory_dir,
    )

    assert settings.knowledge_base_dir == knowledge_dir
    assert settings.conversation_memory_dir == memory_dir
