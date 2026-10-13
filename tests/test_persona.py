from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.persona import build_system_prompt


def test_build_system_prompt_includes_owner_style_profile(tmp_path: Path) -> None:
    style_profile_path = tmp_path / "style_profile.md"
    style_profile_path.write_text(
        "- 先给判断，再给依据。\n- 语气克制、直接，不要过度寒暄。\n- 讨论预约时优先要求 agenda、blocker 和 draft。\n",
        encoding="utf-8",
    )
    settings = AppSettings(
        owner_name="张书豪",
        owner_role="faculty advisor",
        owner_style_profile_path=style_profile_path,
    )

    prompt = build_system_prompt(settings)

    assert "Style profile:" in prompt
    assert "先给判断，再给依据" in prompt
    assert "agenda、blocker 和 draft" in prompt


def test_build_system_prompt_falls_back_to_default_style_guide(tmp_path: Path) -> None:
    settings = AppSettings(owner_style_profile_path=tmp_path / "missing.md")

    prompt = build_system_prompt(settings)

    assert "Voice and style baseline:" in prompt
    assert "Calm, direct, and academically grounded." in prompt


def test_build_system_prompt_includes_installed_skill_prefix(tmp_path: Path) -> None:
    skill_prompt_path = tmp_path / "fixed_prompt_skills.md"
    skill_prompt_path.write_text(
        "- Paper feedback: check claims against evidence.\n"
        "- Permission boundary: do not expose restricted material.\n",
        encoding="utf-8",
    )
    settings = AppSettings(
        owner_style_profile_path=tmp_path / "missing.md",
        installed_skill_prompt_path=skill_prompt_path,
    )

    prompt = build_system_prompt(settings)

    assert "Installed reusable skills, always available:" in prompt
    assert "Paper feedback: check claims against evidence." in prompt
    assert "Permission boundary: do not expose restricted material." in prompt


def test_build_system_prompt_can_disable_installed_skill_prefix(tmp_path: Path) -> None:
    skill_prompt_path = tmp_path / "fixed_prompt_skills.md"
    skill_prompt_path.write_text("Should not load", encoding="utf-8")
    settings = AppSettings(
        owner_style_profile_path=tmp_path / "missing.md",
        installed_skill_prompt_path=skill_prompt_path,
        installed_skill_prompt_enabled=False,
    )

    prompt = build_system_prompt(settings)

    assert "Should not load" not in prompt


def test_build_system_prompt_loads_repo_default_style_profile() -> None:
    prompt = build_system_prompt(AppSettings())

    assert "默认优先使用中文回答" in prompt
    assert "先给判断，再给依据" in prompt
    assert "当前主方向：大模型推理引擎、推理服务系统与记忆智能体中间件" in prompt
    assert "数据库管理系统、流处理和并行分布式系统应作为历史基础" in prompt


def test_build_system_prompt_allows_general_technical_guidance() -> None:
    prompt = build_system_prompt(AppSettings())

    assert "For general academic or technical questions" in prompt
    assert "broadly accepted domain knowledge" in prompt
    assert "rather than quoting the owner" in prompt
    assert "owner-specific facts, private lab details" in prompt
    assert "Do not claim to remember a private meeting" in prompt
