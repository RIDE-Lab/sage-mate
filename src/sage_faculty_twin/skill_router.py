"""Skill router for the agent skill system.

Pattern-matching router that selects the first matching enabled skill
for a given user question. Skills are loaded from JSON manifests and
filtered by compatibility and enabled status.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from .skills import (
    SkillDefinition,
    check_skill_compatibility,
    load_all_skill_manifests,
)

logger = logging.getLogger(__name__)

_NEGATED_TRIGGER_PREFIX = re.compile(
    r"(?:不是来|不是|并非|不要|不想|无需|无意|没打算|not|do not|don't)"
    r"[^，。；;,.!?]{0,8}$",
    re.IGNORECASE,
)


class SkillRouter:
    """Routes user questions to matching skills based on trigger patterns."""

    def __init__(
        self,
        skill_dir: Path | str,
        current_version: str,
    ) -> None:
        self._current_version = current_version
        self._all_skills: list[SkillDefinition] = []
        self._enabled_skills: list[SkillDefinition] = []
        self._load(skill_dir)

    def _load(self, skill_dir: Path | str) -> None:
        """Load all skill manifests and filter to enabled, compatible ones."""
        dir_path = Path(skill_dir)
        all_skills = load_all_skill_manifests(dir_path)

        self._all_skills = all_skills
        self._enabled_skills = []

        for skill in all_skills:
            if not skill.enabled:
                logger.debug("Skill %s is disabled, skipping", skill.skill_id)
                continue

            if not check_skill_compatibility(skill, self._current_version):
                logger.debug(
                    "Skill %s requires version %s but current is %s",
                    skill.skill_id,
                    skill.min_app_version,
                    self._current_version,
                )
                continue

            self._enabled_skills.append(skill)
            logger.info(
                "Loaded skill %s with %d trigger patterns and %d tools",
                skill.skill_id,
                len(skill.trigger_patterns),
                len(skill.tools),
            )

    def match(self, question: str) -> SkillDefinition | None:
        """Find the first matching enabled skill for the given question.

        Matching is done by checking if any trigger pattern appears in
        the lowercased question. The first match wins.

        Args:
            question: The user's question text.

        Returns:
            The matching SkillDefinition, or None if no skill matches.
        """
        q = question.lower()
        for skill in self._enabled_skills:
            for pattern in skill.trigger_patterns:
                if self._matches_non_negated_pattern(q, pattern):
                    logger.debug(
                        "Skill %s matched question via pattern '%s'",
                        skill.skill_id,
                        pattern,
                    )
                    return skill
        return None

    def get_all_skills(self) -> list[SkillDefinition]:
        """Return all loaded skills (including disabled ones)."""
        return list(self._all_skills)

    def get_enabled_skills(self) -> list[SkillDefinition]:
        """Return only enabled and compatible skills."""
        return list(self._enabled_skills)

    def get_skill_by_id(self, skill_id: str) -> SkillDefinition | None:
        """Look up a skill by its ID."""
        for skill in self._all_skills:
            if skill.skill_id == skill_id:
                return skill
        return None

    def match_all(self, question: str) -> list[SkillDefinition]:
        """Find all matching enabled skills for the given question.

        Useful for skill composition where multiple skills can chain.

        Args:
            question: The user's question text.

        Returns:
            List of all matching SkillDefinitions (may be empty).
        """
        q = question.lower()
        matches = []
        for skill in self._enabled_skills:
            for pattern in skill.trigger_patterns:
                if self._matches_non_negated_pattern(q, pattern):
                    matches.append(skill)
                    break  # Only add each skill once
        return matches

    @staticmethod
    def _matches_non_negated_pattern(question: str, pattern: str) -> bool:
        normalized_pattern = pattern.lower()
        search_from = 0
        while True:
            index = question.find(normalized_pattern, search_from)
            if index < 0:
                return False
            prefix = question[max(0, index - 12) : index]
            if not _NEGATED_TRIGGER_PREFIX.search(prefix):
                return True
            search_from = index + len(normalized_pattern)
