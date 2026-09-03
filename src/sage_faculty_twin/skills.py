"""Skill definition schema for the agent skill system.

V4.0 introduces self-contained, executable skill units with built-in prompts,
tool definitions, and composability. Each skill declares:
- Trigger patterns for activation
- System prompt and user prompt template
- Tool definitions for function calling
- Multi-turn reasoning loop configuration
- Output format specification
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .models import KnowledgeSearchHit


class SkillToolParameter(BaseModel):
    """Parameter definition for a skill tool (OpenAI function-calling format)."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(min_length=1, max_length=32)  # "string" | "integer" | "boolean"
    description: str = Field(min_length=1, max_length=512)
    default: Any | None = None
    required: bool = True

    def to_openai_property(self) -> dict[str, Any]:
        """Convert to OpenAI function parameter property format."""
        prop: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.default is not None:
            prop["default"] = self.default
        return prop


class SkillToolDefinition(BaseModel):
    """A tool that a skill can invoke via function calling."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=512)
    parameters: dict[str, SkillToolParameter] = Field(default_factory=dict)
    handler: str = Field(min_length=1, max_length=64)

    def to_openai_tool(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling tool format."""
        required_params = [
            name for name, param in self.parameters.items() if param.required
        ]
        properties = {
            name: param.to_openai_property() for name, param in self.parameters.items()
        }
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required_params,
                },
            },
        }


class SkillDefinition(BaseModel):
    """A self-contained, executable skill unit.

    Skills are prompt-driven agent capabilities with built-in tool definitions
    and composability. They run as mini-agents with their own reasoning loop.
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    trigger_patterns: list[str] = Field(default_factory=list)
    system_prompt: str = Field(min_length=1, max_length=4096)
    user_prompt_template: str = Field(min_length=1, max_length=4096)
    tools: list[SkillToolDefinition] = Field(default_factory=list)
    max_turns: int = Field(default=5, ge=1, le=20)
    output_format: str = Field(
        default="free_form",
        pattern="^(free_form|structured_json|draft)$",
    )
    composes_with: list[str] = Field(default_factory=list)
    enabled: bool = False
    min_app_version: str = Field(default="4.0.0", max_length=32)


class SkillContext(BaseModel):
    """Runtime context passed to a skill during execution."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=4000)
    visitor_profile: str = Field(default="general_visitor", max_length=64)
    pre_fetched_context: str = Field(default="", max_length=8192)
    session_identity: str = Field(default="anonymous", max_length=64)
    course_context: str | None = Field(default=None, max_length=512)


class SkillResult(BaseModel):
    """Result of a skill execution."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=64)
    knowledge_hits: list[KnowledgeSearchHit] = Field(default_factory=list)
    answer: str = Field(default="", max_length=16384)
    tool_calls_made: int = Field(default=0, ge=0)
    turns_used: int = Field(default=0, ge=0)
    output_format: str = Field(default="free_form")
    success: bool = True
    error: str | None = None


def check_skill_compatibility(skill: SkillDefinition, current_version: str) -> bool:
    """Check if a skill is compatible with the current app version."""
    try:
        skill_parts = tuple(int(x) for x in skill.min_app_version.split("."))
        current_parts = tuple(int(x) for x in current_version.split("."))
        return current_parts >= skill_parts
    except (ValueError, AttributeError):
        return False


def load_skill_manifest(path: str | Any) -> SkillDefinition:
    """Load and validate a skill manifest from a JSON file."""
    import json
    from pathlib import Path

    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"Skill manifest not found: {file_path}")

    raw = json.loads(file_path.read_text(encoding="utf-8"))
    return SkillDefinition.model_validate(raw)


def load_all_skill_manifests(skill_dir: str | Any) -> list[SkillDefinition]:
    """Load all valid skill manifests from a directory."""
    import logging
    from pathlib import Path

    logger = logging.getLogger(__name__)
    dir_path = Path(skill_dir)
    if not dir_path.is_dir():
        return []

    skills: list[SkillDefinition] = []
    for file_path in sorted(dir_path.glob("*.json")):
        try:
            skill = load_skill_manifest(file_path)
            skills.append(skill)
        except Exception as exc:
            logger.warning("Failed to load skill manifest %s: %s", file_path, exc)

    return skills
