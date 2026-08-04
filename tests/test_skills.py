"""Tests for the V4.0 agent skill system.

Covers skill manifest loading, routing, tool registry, skill runner,
and service integration.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sage_faculty_twin.skill_router import SkillRouter
from sage_faculty_twin.skill_runner import SkillRunner
from sage_faculty_twin.skill_tools import SkillToolRegistry
from sage_faculty_twin.skills import (
    SkillContext,
    SkillDefinition,
    SkillToolDefinition,
    SkillToolParameter,
    check_skill_compatibility,
    load_all_skill_manifests,
    load_skill_manifest,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _write_skill(directory: Path, name: str, data: dict) -> Path:
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def skill_dir(tmp_path: Path) -> Path:
    d = tmp_path / "skills"
    d.mkdir()
    return d


@pytest.fixture()
def sample_skill_data() -> dict:
    return {
        "skill_id": "test_skill",
        "name": "Test Skill",
        "description": "A test skill for unit tests.",
        "trigger_patterns": ["test keyword", "another trigger"],
        "system_prompt": "You are a test assistant.",
        "user_prompt_template": "Question: {question}\nProfile: {profile}\nContext: {retrieved_context}",
        "tools": [
            {
                "tool_id": "search_tool",
                "name": "search_knowledge",
                "description": "Search the knowledge base.",
                "parameters": {
                    "query": {
                        "type": "string",
                        "description": "Search query",
                        "required": True,
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results",
                        "default": 5,
                        "required": False,
                    },
                },
                "handler": "knowledge_search",
            }
        ],
        "max_turns": 3,
        "output_format": "free_form",
        "enabled": True,
        "min_app_version": "4.0.0",
    }


@pytest.fixture()
def skill_context() -> SkillContext:
    return SkillContext(
        question="What is the test keyword about?",
        visitor_profile="test_student",
        pre_fetched_context="Some test context.",
        session_identity="test_session",
    )


# ── Skill Model Tests ───────────────────────────────────────────────────────


class TestSkillModels:
    def test_skill_tool_parameter_to_openai(self) -> None:
        param = SkillToolParameter(
            type="string",
            description="A test parameter",
            default=None,
            required=True,
        )
        prop = param.to_openai_property()
        assert prop["type"] == "string"
        assert prop["description"] == "A test parameter"
        assert "default" not in prop

    def test_skill_tool_parameter_with_default(self) -> None:
        param = SkillToolParameter(
            type="integer",
            description="Max results",
            default=5,
            required=False,
        )
        prop = param.to_openai_property()
        assert prop["default"] == 5

    def test_skill_tool_definition_to_openai(self) -> None:
        tool = SkillToolDefinition(
            tool_id="test_tool",
            name="test_function",
            description="A test function",
            parameters={
                "query": SkillToolParameter(
                    type="string",
                    description="Query text",
                    required=True,
                ),
                "limit": SkillToolParameter(
                    type="integer",
                    description="Max results",
                    default=5,
                    required=False,
                ),
            },
            handler="knowledge_search",
        )
        openai_tool = tool.to_openai_tool()
        assert openai_tool["type"] == "function"
        assert openai_tool["function"]["name"] == "test_function"
        assert "query" in openai_tool["function"]["parameters"]["properties"]
        assert openai_tool["function"]["parameters"]["required"] == ["query"]

    def test_skill_definition_defaults(self) -> None:
        skill = SkillDefinition(
            skill_id="test",
            name="Test",
            system_prompt="Test prompt",
            user_prompt_template="{question}",
        )
        assert skill.tools == []
        assert skill.max_turns == 5
        assert skill.output_format == "free_form"
        assert skill.enabled is False
        assert skill.min_app_version == "4.0.0"


# ── Compatibility Check Tests ───────────────────────────────────────────────


class TestSkillCompatibility:
    def test_compatible_version(self) -> None:
        skill = SkillDefinition(
            skill_id="test",
            name="Test",
            system_prompt="Test",
            user_prompt_template="{question}",
            min_app_version="4.0.0",
        )
        assert check_skill_compatibility(skill, "4.0.0") is True
        assert check_skill_compatibility(skill, "4.1.0") is True
        assert check_skill_compatibility(skill, "5.0.0") is True

    def test_incompatible_version(self) -> None:
        skill = SkillDefinition(
            skill_id="test",
            name="Test",
            system_prompt="Test",
            user_prompt_template="{question}",
            min_app_version="5.0.0",
        )
        assert check_skill_compatibility(skill, "4.0.0") is False
        assert check_skill_compatibility(skill, "4.9.9") is False

    def test_invalid_version_format(self) -> None:
        skill = SkillDefinition(
            skill_id="test",
            name="Test",
            system_prompt="Test",
            user_prompt_template="{question}",
            min_app_version="invalid",
        )
        assert check_skill_compatibility(skill, "4.0.0") is False


# ── Manifest Loading Tests ──────────────────────────────────────────────────


class TestManifestLoading:
    def test_load_valid_manifest(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        path = _write_skill(skill_dir, "test", sample_skill_data)
        skill = load_skill_manifest(path)
        assert skill.skill_id == "test_skill"
        assert skill.name == "Test Skill"
        assert len(skill.tools) == 1
        assert skill.tools[0].handler == "knowledge_search"

    def test_load_manifest_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_skill_manifest(tmp_path / "nonexistent.json")

    def test_load_manifest_invalid_json(self, skill_dir: Path) -> None:
        path = skill_dir / "bad.json"
        path.write_text("{invalid json", encoding="utf-8")
        with pytest.raises(Exception):
            load_skill_manifest(path)

    def test_load_manifest_missing_required_field(self, skill_dir: Path) -> None:
        data = {"name": "No ID"}  # missing skill_id
        path = _write_skill(skill_dir, "missing", data)
        with pytest.raises(Exception):
            load_skill_manifest(path)

    def test_load_all_manifests_skips_invalid(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "good", sample_skill_data)
        (skill_dir / "bad.json").write_text("{broken", encoding="utf-8")
        skills = load_all_skill_manifests(skill_dir)
        assert len(skills) == 1
        assert skills[0].skill_id == "test_skill"

    def test_load_all_manifests_empty_dir(self, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        assert load_all_skill_manifests(empty) == []

    def test_load_all_manifests_nonexistent_dir(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "does_not_exist"
        assert load_all_skill_manifests(nonexistent) == []


# ── Skill Router Tests ──────────────────────────────────────────────────────


class TestSkillRouter:
    def test_match_found(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "test", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        result = router.match("What is the test keyword about?")
        assert result is not None
        assert result.skill_id == "test_skill"

    def test_match_not_found(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "test", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        result = router.match("Something completely unrelated")
        assert result is None

    def test_disabled_skill_not_matched(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        sample_skill_data["enabled"] = False
        _write_skill(skill_dir, "disabled", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        result = router.match("What is the test keyword about?")
        assert result is None

    def test_incompatible_version_not_matched(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        sample_skill_data["min_app_version"] = "99.0.0"
        _write_skill(skill_dir, "future", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        result = router.match("What is the test keyword about?")
        assert result is None

    def test_match_all_returns_multiple(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "test1", sample_skill_data)
        skill2 = sample_skill_data.copy()
        skill2["skill_id"] = "test_skill_2"
        skill2["trigger_patterns"] = ["test keyword", "extra pattern"]
        _write_skill(skill_dir, "test2", skill2)
        router = SkillRouter(skill_dir, "4.0.0")
        results = router.match_all("What is the test keyword about?")
        assert len(results) == 2

    def test_get_skill_by_id(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "test", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        skill = router.get_skill_by_id("test_skill")
        assert skill is not None
        assert skill.name == "Test Skill"

    def test_get_skill_by_id_not_found(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "test", sample_skill_data)
        router = SkillRouter(skill_dir, "4.0.0")
        skill = router.get_skill_by_id("nonexistent")
        assert skill is None

    def test_get_enabled_skills(
        self, skill_dir: Path, sample_skill_data: dict
    ) -> None:
        _write_skill(skill_dir, "enabled", sample_skill_data)
        disabled = sample_skill_data.copy()
        disabled["skill_id"] = "disabled_skill"
        disabled["enabled"] = False
        _write_skill(skill_dir, "disabled", disabled)
        router = SkillRouter(skill_dir, "4.0.0")
        enabled = router.get_enabled_skills()
        assert len(enabled) == 1
        assert enabled[0].skill_id == "test_skill"


# ── Tool Registry Tests ─────────────────────────────────────────────────────


class TestSkillToolRegistry:
    def test_builtin_handlers_registered(self) -> None:
        registry = SkillToolRegistry()
        handlers = registry.list_handlers()
        assert "knowledge_search" in handlers
        assert "memory_search" in handlers
        assert "get_team_schedule" in handlers
        assert "get_blockers" in handlers
        assert "get_paper_digest" in handlers
        assert "get_courseware" in handlers
        assert "get_writing_rubric" in handlers

    def test_has_handler(self) -> None:
        registry = SkillToolRegistry()
        assert registry.has_handler("knowledge_search") is True
        assert registry.has_handler("nonexistent") is False

    def test_register_custom_handler(self) -> None:
        registry = SkillToolRegistry()
        registry.register("custom_tool", lambda x: f"Result: {x}")
        assert registry.has_handler("custom_tool") is True
        assert registry.is_auto_invoke_safe("custom_tool") is False
        result = registry.execute("custom_tool", {"x": "test"})
        assert result == "Result: test"

    def test_builtin_read_only_handlers_are_auto_invoke_safe(self) -> None:
        registry = SkillToolRegistry()
        assert registry.is_auto_invoke_safe("knowledge_search") is True
        assert registry.is_auto_invoke_safe("get_team_schedule") is True

    def test_execute_unknown_handler(self) -> None:
        registry = SkillToolRegistry()
        result = registry.execute("nonexistent", {})
        assert "error" in result
        assert "Unknown tool" in result

    def test_knowledge_search_without_store(self) -> None:
        registry = SkillToolRegistry(knowledge_store=None)
        result = json.loads(registry.execute("knowledge_search", {"query": "test"}))
        assert result["results"] == []
        assert "not available" in result["message"]

    def test_knowledge_search_with_mock_store(self) -> None:
        mock_store = MagicMock()
        mock_hit = MagicMock()
        mock_hit.title = "Test Document"
        mock_hit.score = 0.95
        mock_hit.excerpt = "This is a test excerpt."
        mock_hit.tags = ["test"]
        mock_store.search.return_value = [mock_hit]

        registry = SkillToolRegistry(knowledge_store=mock_store)
        result = json.loads(registry.execute("knowledge_search", {"query": "test"}))
        assert len(result["results"]) == 1
        assert result["results"][0]["title"] == "Test Document"

    def test_execute_handler_exception(self) -> None:
        def failing_handler():
            raise ValueError("Test error")

        registry = SkillToolRegistry()
        registry.register("failing", failing_handler)
        result = registry.execute("failing", {})
        assert "error" in result
        assert "Test error" in result


# ── Skill Runner Tests ──────────────────────────────────────────────────────


class TestSkillRunner:
    def test_run_no_tools_single_turn(self, skill_context: SkillContext) -> None:
        mock_llm = MagicMock()
        mock_llm.answer_question_sync.return_value = "Test answer"

        tool_registry = SkillToolRegistry()
        runner = SkillRunner(mock_llm, tool_registry)

        skill = SkillDefinition(
            skill_id="no_tools_skill",
            name="No Tools Skill",
            system_prompt="You are a test assistant.",
            user_prompt_template="Question: {question}",
            tools=[],
            enabled=True,
        )

        result = runner.run(skill, skill_context)
        assert result.success is True
        assert result.answer == "Test answer"
        assert result.tool_calls_made == 0
        assert result.turns_used == 1

    def test_run_with_tool_calls(self, skill_context: SkillContext) -> None:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools_sync.side_effect = [
            # First turn: LLM calls a tool
            {
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "search_knowledge",
                        "arguments": {"query": "test query"},
                    }
                ],
                "finish_reason": "tool_calls",
            },
            # Second turn: LLM returns final answer
            {
                "content": "Final answer based on search results.",
                "tool_calls": [],
                "finish_reason": "stop",
            },
        ]

        mock_store = MagicMock()
        mock_store.search.return_value = []

        tool_registry = SkillToolRegistry(knowledge_store=mock_store)
        runner = SkillRunner(mock_llm, tool_registry)

        skill = SkillDefinition(
            skill_id="tool_skill",
            name="Tool Skill",
            system_prompt="You are a test assistant.",
            user_prompt_template="Question: {question}\nProfile: {profile}\nContext: {retrieved_context}",
            tools=[
                SkillToolDefinition(
                    tool_id="search_tool",
                    name="search_knowledge",
                    description="Search",
                    parameters={
                        "query": SkillToolParameter(
                            type="string",
                            description="Query",
                            required=True,
                        ),
                    },
                    handler="knowledge_search",
                )
            ],
            max_turns=5,
            enabled=True,
        )

        result = runner.run(skill, skill_context)
        assert result.success is True
        assert result.answer == "Final answer based on search results."
        assert result.tool_calls_made == 1
        assert result.turns_used == 2

    def test_run_with_compatible_tool_transport(self, skill_context: SkillContext) -> None:
        mock_llm = MagicMock()
        mock_llm.supports_native_tool_calling = False
        mock_llm.answer_question_sync.return_value = (
            "Meeting agenda based on validated progress."
        )

        mock_store = MagicMock()
        mock_store.search.return_value = []
        runner = SkillRunner(mock_llm, SkillToolRegistry(knowledge_store=mock_store))
        skill = SkillDefinition(
            skill_id="portable_tool_skill",
            name="Portable Tool Skill",
            system_prompt="Prepare a meeting.",
            user_prompt_template="Question: {question}\nContext: {retrieved_context}",
            tools=[
                SkillToolDefinition(
                    tool_id="search_tool",
                    name="search_knowledge",
                    description="Search progress",
                    parameters={
                        "query": SkillToolParameter(
                            type="string", description="Search query", required=True
                        )
                    },
                    handler="knowledge_search",
                )
            ],
            enabled=True,
        )

        result = runner.run(skill, skill_context)

        assert result.success is True
        assert result.answer == "Meeting agenda based on validated progress."
        assert result.tool_calls_made == 1
        assert result.turns_used == 1
        mock_llm.chat_with_tools_sync.assert_not_called()
        mock_store.search.assert_called_once_with(
            query=skill_context.question,
            top_k=5,
        )

    def test_compatible_transport_rejects_unresolved_required_arguments(
        self, skill_context: SkillContext
    ) -> None:
        mock_llm = MagicMock()
        mock_llm.supports_native_tool_calling = False
        mock_llm.answer_question_sync.return_value = "must not be called"
        runner = SkillRunner(mock_llm, SkillToolRegistry())
        skill = SkillDefinition(
            skill_id="validated_tool_skill",
            name="Validated Tool Skill",
            system_prompt="Test",
            user_prompt_template="Question: {question}",
            tools=[
                SkillToolDefinition(
                    tool_id="search_tool",
                    name="search_knowledge",
                    description="Search progress",
                    parameters={
                        "shell": SkillToolParameter(
                            type="string", description="Unsupported input", required=True
                        )
                    },
                    handler="knowledge_search",
                )
            ],
            enabled=True,
        )

        result = runner.run(skill, skill_context)

        assert result.success is False
        assert result.error == "No manifest tools have compatible request inputs"
        assert result.tool_calls_made == 0
        mock_llm.answer_question_sync.assert_not_called()

    def test_compatible_transport_retries_non_substantive_answer(
        self, skill_context: SkillContext
    ) -> None:
        mock_llm = MagicMock()
        mock_llm.supports_native_tool_calling = False
        mock_llm.answer_question_sync.side_effect = [
            "![](https://example.test/empty.png)",
            "A complete meeting agenda with decisions and next actions.",
        ]
        runner = SkillRunner(mock_llm, SkillToolRegistry())
        skill = SkillDefinition(
            skill_id="retry_tool_skill",
            name="Retry Tool Skill",
            system_prompt="Prepare a meeting.",
            user_prompt_template="Question: {question}",
            tools=[
                SkillToolDefinition(
                    tool_id="schedule_tool",
                    name="get_schedule",
                    description="Get the schedule",
                    parameters={},
                    handler="get_team_schedule",
                )
            ],
            enabled=True,
        )

        result = runner.run(skill, skill_context)

        assert result.success is True
        assert result.turns_used == 2
        assert result.answer.startswith("A complete meeting agenda")
        assert mock_llm.answer_question_sync.call_count == 2

    def test_run_max_turns_reached(self, skill_context: SkillContext) -> None:
        mock_llm = MagicMock()
        # Always return tool calls, never a final answer
        mock_llm.chat_with_tools_sync.return_value = {
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "name": "search_knowledge",
                    "arguments": {"query": "test"},
                }
            ],
            "finish_reason": "tool_calls",
        }

        tool_registry = SkillToolRegistry()
        runner = SkillRunner(mock_llm, tool_registry)

        skill = SkillDefinition(
            skill_id="loop_skill",
            name="Loop Skill",
            system_prompt="You loop forever.",
            user_prompt_template="Question: {question}\nProfile: {profile}\nContext: {retrieved_context}",
            tools=[
                SkillToolDefinition(
                    tool_id="search_tool",
                    name="search_knowledge",
                    description="Search",
                    parameters={
                        "query": SkillToolParameter(
                            type="string",
                            description="Query",
                            required=True,
                        ),
                    },
                    handler="knowledge_search",
                )
            ],
            max_turns=2,
            enabled=True,
        )

        result = runner.run(skill, skill_context)
        assert result.success is False
        assert result.error == "Max turns reached"
        assert result.turns_used == 2

    def test_run_llm_call_fails(self, skill_context: SkillContext) -> None:
        mock_llm = MagicMock()
        mock_llm.chat_with_tools_sync.side_effect = RuntimeError("LLM unavailable")

        tool_registry = SkillToolRegistry()
        runner = SkillRunner(mock_llm, tool_registry)

        skill = SkillDefinition(
            skill_id="failing_skill",
            name="Failing Skill",
            system_prompt="You will fail.",
            user_prompt_template="Question: {question}\nProfile: {profile}\nContext: {retrieved_context}",
            tools=[
                SkillToolDefinition(
                    tool_id="search_tool",
                    name="search_knowledge",
                    description="Search",
                    parameters={
                        "query": SkillToolParameter(
                            type="string",
                            description="Query",
                            required=True,
                        ),
                    },
                    handler="knowledge_search",
                )
            ],
            max_turns=3,
            enabled=True,
        )

        result = runner.run(skill, skill_context)
        assert result.success is False
        assert "LLM call failed" in result.error

    def test_run_template_missing_variable(self) -> None:
        mock_llm = MagicMock()
        tool_registry = SkillToolRegistry()
        runner = SkillRunner(mock_llm, tool_registry)

        # Template requires {course} but context doesn't have it set properly
        skill = SkillDefinition(
            skill_id="bad_template_skill",
            name="Bad Template Skill",
            system_prompt="Test",
            user_prompt_template="Question: {question}\nCourse: {nonexistent_var}",
            tools=[],
            enabled=True,
        )

        context = SkillContext(
            question="Test question",
            visitor_profile="test",
            pre_fetched_context="",
        )

        result = runner.run(skill, context)
        assert result.success is False
        assert "Missing template variable" in result.error


# ── Production Skill Manifest Tests ─────────────────────────────────────────


class TestProductionSkillManifests:
    """Test that the production skill manifests in data/skills/ load correctly."""

    @pytest.fixture()
    def production_skill_dir(self) -> Path:
        return Path(__file__).parent.parent / "data" / "skills"

    def test_all_production_skills_load(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skills = load_all_skill_manifests(production_skill_dir)
        assert len(skills) >= 5, f"Expected at least 5 skills, got {len(skills)}"

    def test_research_mentoring_skill(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skill = load_skill_manifest(production_skill_dir / "research_mentoring.json")
        assert skill.skill_id == "research_mentoring"
        assert skill.enabled is True
        assert len(skill.trigger_patterns) >= 3
        assert len(skill.tools) >= 1

    def test_meeting_prep_skill(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skill = load_skill_manifest(production_skill_dir / "meeting_prep.json")
        assert skill.skill_id == "meeting_prep"
        assert skill.enabled is True
        assert "get_team_schedule" in [t.handler for t in skill.tools]

    def test_thesis_review_skill(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skill = load_skill_manifest(production_skill_dir / "thesis_review.json")
        assert skill.skill_id == "thesis_review"
        assert skill.output_format == "draft"

    def test_course_advising_skill(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skill = load_skill_manifest(production_skill_dir / "course_advising.json")
        assert skill.skill_id == "course_advising"
        assert "get_courseware" in [t.handler for t in skill.tools]

    def test_paper_feedback_skill(self, production_skill_dir: Path) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        skill = load_skill_manifest(production_skill_dir / "paper_feedback.json")
        assert skill.skill_id == "paper_feedback"
        assert skill.max_turns >= 2

    def test_router_loads_all_production_skills(
        self, production_skill_dir: Path
    ) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        router = SkillRouter(production_skill_dir, "4.0.0")
        enabled = router.get_enabled_skills()
        assert len(enabled) >= 5, f"Expected at least 5 enabled skills, got {len(enabled)}"

    def test_router_matches_chinese_question(
        self, production_skill_dir: Path
    ) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        router = SkillRouter(production_skill_dir, "4.0.0")
        skill = router.match("我想了解一下研究方向怎么选")
        assert skill is not None
        assert skill.skill_id == "research_mentoring"

    def test_router_matches_english_question(
        self, production_skill_dir: Path
    ) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        router = SkillRouter(production_skill_dir, "4.0.0")
        skill = router.match("Can you help me with literature review?")
        assert skill is not None
        assert skill.skill_id == "research_mentoring"

    def test_router_no_match_for_unrelated(
        self, production_skill_dir: Path
    ) -> None:
        if not production_skill_dir.exists():
            pytest.skip("data/skills directory not found")
        router = SkillRouter(production_skill_dir, "4.0.0")
        skill = router.match("今天天气怎么样")
        assert skill is None
