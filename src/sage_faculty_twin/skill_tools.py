"""Tool registry for the agent skill system.

Maps handler names to Python callables that skills can invoke via function calling.
Built-in tools wrap existing service functionality (knowledge store, memory store,
KB queries) so skills don't need to reimplement retrieval.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .knowledge_base import LocalKnowledgeStore
    from .memory_store import ConversationMemoryStore

logger = logging.getLogger(__name__)


class SkillToolRegistry:
    """Maps handler names to Python callables for skill tool execution."""

    def __init__(
        self,
        knowledge_store: LocalKnowledgeStore | None = None,
        memory_store: ConversationMemoryStore | None = None,
    ) -> None:
        self._handlers: dict[str, Callable[..., str]] = {}
        self._auto_invoke_safe_handlers: set[str] = set()
        self._parallel_safe_handlers: set[str] = set()
        self._context_parameters: dict[str, frozenset[str]] = {}
        self._knowledge_store = knowledge_store
        self._memory_store = memory_store
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register built-in tool handlers."""
        self.register(
            "knowledge_search",
            self._knowledge_search,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("visitor_profile",),
        )
        self.register(
            "memory_search",
            self._memory_search,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("conversation_id",),
        )
        self.register(
            "get_team_schedule",
            self._get_team_schedule,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("visitor_profile",),
        )
        self.register(
            "get_blockers",
            self._get_blockers,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("conversation_id", "visitor_profile"),
        )
        self.register(
            "get_paper_digest",
            self._get_paper_digest,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("visitor_profile",),
        )
        self.register(
            "get_courseware",
            self._get_courseware,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("visitor_profile",),
        )
        self.register(
            "get_writing_rubric",
            self._get_writing_rubric,
            auto_invoke_safe=True,
            parallel_safe=True,
            context_parameters=("visitor_profile",),
        )

    def register(
        self,
        handler_name: str,
        handler: Callable[..., str],
        *,
        auto_invoke_safe: bool = False,
        parallel_safe: bool = False,
        context_parameters: tuple[str, ...] = (),
    ) -> None:
        """Register a tool handler."""
        self._handlers[handler_name] = handler
        if auto_invoke_safe:
            self._auto_invoke_safe_handlers.add(handler_name)
        else:
            self._auto_invoke_safe_handlers.discard(handler_name)
        if parallel_safe:
            self._parallel_safe_handlers.add(handler_name)
        else:
            self._parallel_safe_handlers.discard(handler_name)
        self._context_parameters[handler_name] = frozenset(context_parameters)

    def has_handler(self, handler_name: str) -> bool:
        """Check if a handler is registered."""
        return handler_name in self._handlers

    def is_auto_invoke_safe(self, handler_name: str) -> bool:
        """Whether deterministic compatibility mode may call this handler."""
        return handler_name in self._auto_invoke_safe_handlers

    def is_parallel_safe(self, handler_name: str) -> bool:
        """Whether independent calls to this handler may overlap."""
        return handler_name in self._parallel_safe_handlers

    def execute(
        self,
        handler_name: str,
        arguments: dict[str, Any],
        *,
        context_values: dict[str, Any] | None = None,
    ) -> str:
        """Execute a tool handler with the given arguments.

        Returns a JSON string with the result or error.
        """
        handler = self._handlers.get(handler_name)
        if handler is None:
            return json.dumps({"error": f"Unknown tool: {handler_name}"})
        try:
            execution_arguments = dict(arguments)
            if context_values:
                for name in self._context_parameters.get(handler_name, ()):
                    value = context_values.get(name)
                    if value is None:
                        execution_arguments.pop(name, None)
                    else:
                        execution_arguments[name] = value
            result = handler(**execution_arguments)
            return result if isinstance(result, str) else json.dumps(result)
        except Exception as exc:
            logger.warning("Tool %s failed: %s", handler_name, exc)
            return json.dumps({"error": str(exc)})

    def list_handlers(self) -> list[str]:
        """Return list of registered handler names."""
        return sorted(self._handlers.keys())

    # ── Built-in tool handlers ─────────────────────────────────────────────

    def _knowledge_search(
        self,
        query: str,
        limit: int = 5,
        tags: str | None = None,
        visitor_profile: str | None = None,
    ) -> str:
        """Search the knowledge base for relevant documents.

        Args:
            query: Search query text.
            limit: Maximum number of results.
            tags: Optional comma-separated tags to filter by.

        Returns:
            JSON string with search results.
        """
        if self._knowledge_store is None:
            return json.dumps(
                {"results": [], "message": "Knowledge store not available"}
            )

        try:
            hits = self._knowledge_store.search(
                query=query,
                top_k=limit,
                visitor_profile=visitor_profile,
            )
            results = []
            for hit in hits[:limit]:
                result_item = {
                    "title": hit.title or "Untitled",
                    "score": hit.score,
                    "excerpt": (hit.excerpt or "")[:500],
                }
                if tags:
                    tag_list = [t.strip() for t in tags.split(",")]
                    if hit.tags and not any(t in hit.tags for t in tag_list):
                        continue
                results.append(result_item)
            return json.dumps({"results": results, "total": len(hits)})
        except Exception as exc:
            logger.warning("Knowledge search failed: %s", exc)
            return json.dumps({"results": [], "error": str(exc)})

    def _memory_search(
        self,
        query: str,
        limit: int = 3,
        conversation_id: str | None = None,
    ) -> str:
        """Search conversation memory for relevant context.

        Args:
            query: Search query text.
            limit: Maximum number of results.
            conversation_id: Optional conversation ID to scope the search.

        Returns:
            JSON string with memory hits.
        """
        if self._memory_store is None:
            return json.dumps({"results": [], "message": "Memory store not available"})
        normalized_conversation_id = (conversation_id or "").strip()
        if not normalized_conversation_id or normalized_conversation_id == "anonymous":
            return json.dumps(
                {"results": [], "message": "Conversation scope not available"}
            )

        bounded_limit = max(1, min(int(limit), 10))
        records = self._memory_store.list_recent_conversation_records(
            normalized_conversation_id,
            limit=bounded_limit,
        )
        results = [
            {
                "question": record.question[:500],
                "answer": record.answer[:800],
                "course_context": record.course_context,
                "created_at": record.created_at.isoformat(),
            }
            for record in records
        ]
        return json.dumps({"results": results, "total": len(results)})

    def _get_team_schedule(
        self,
        days_ahead: int = 7,
        visitor_profile: str | None = None,
    ) -> str:
        """Get team schedule and meeting availability.

        Args:
            days_ahead: Number of days to look ahead.

        Returns:
            JSON string with schedule information.
        """
        # Search knowledge base for schedule-related documents
        if self._knowledge_store is None:
            return json.dumps(
                {"schedule": [], "message": "Knowledge store not available"}
            )

        try:
            hits = self._knowledge_store.search(
                query="团队工作安排 周会 会议 schedule",
                top_k=5,
                visitor_profile=visitor_profile,
            )
            schedule_items = []
            for hit in hits:
                if hit.tags and any(
                    t in hit.tags for t in ["team-management", "schedule", "meeting"]
                ):
                    schedule_items.append(
                        {
                            "title": hit.title,
                            "excerpt": (hit.excerpt or "")[:300],
                        }
                    )
            return json.dumps({"schedule": schedule_items[:3]})
        except Exception as exc:
            return json.dumps({"schedule": [], "error": str(exc)})

    def _get_blockers(
        self,
        conversation_id: str | None = None,
        visitor_profile: str | None = None,
    ) -> str:
        """Get blockers and unresolved items from previous sessions.

        Args:
            conversation_id: Optional conversation ID to scope the search.

        Returns:
            JSON string with blocker information.
        """
        if self._knowledge_store is None:
            return json.dumps(
                {"blockers": [], "message": "Knowledge store not available"}
            )

        try:
            hits = self._knowledge_store.search(
                query="blocker 待解决 问题 issue",
                top_k=5,
                visitor_profile=visitor_profile,
            )
            blockers = []
            for hit in hits:
                blockers.append(
                    {
                        "title": hit.title,
                        "excerpt": (hit.excerpt or "")[:300],
                    }
                )
            return json.dumps({"blockers": blockers[:5]})
        except Exception as exc:
            return json.dumps({"blockers": [], "error": str(exc)})

    def _get_paper_digest(
        self,
        query: str,
        limit: int = 5,
        visitor_profile: str | None = None,
    ) -> str:
        """Get paper digests/summaries from the knowledge base.

        Args:
            query: Search query for paper topics.
            limit: Maximum number of results.

        Returns:
            JSON string with paper digest results.
        """
        if self._knowledge_store is None:
            return json.dumps(
                {"papers": [], "message": "Knowledge store not available"}
            )

        try:
            hits = self._knowledge_store.search(
                query=query,
                top_k=limit * 2,  # Get more to filter by tag
                visitor_profile=visitor_profile,
            )
            papers = []
            for hit in hits:
                if hit.tags and "paper-digest" in hit.tags:
                    papers.append(
                        {
                            "title": hit.title,
                            "score": hit.score,
                            "excerpt": (hit.excerpt or "")[:500],
                        }
                    )
                    if len(papers) >= limit:
                        break
            return json.dumps({"papers": papers})
        except Exception as exc:
            return json.dumps({"papers": [], "error": str(exc)})

    def _get_courseware(
        self,
        course_name: str | None = None,
        limit: int = 5,
        visitor_profile: str | None = None,
    ) -> str:
        """Get course materials and resources.

        Args:
            course_name: Optional course name to filter by.
            limit: Maximum number of results.

        Returns:
            JSON string with courseware results.
        """
        if self._knowledge_store is None:
            return json.dumps(
                {"courseware": [], "message": "Knowledge store not available"}
            )

        query = f"课程 {course_name}" if course_name else "课程 讲义 实验"
        try:
            hits = self._knowledge_store.search(
                query=query,
                top_k=limit * 2,
                visitor_profile=visitor_profile,
            )
            courseware = []
            for hit in hits:
                if hit.tags and any(
                    t in hit.tags for t in ["teaching", "courseware", "material"]
                ):
                    courseware.append(
                        {
                            "title": hit.title,
                            "score": hit.score,
                            "excerpt": (hit.excerpt or "")[:400],
                        }
                    )
                    if len(courseware) >= limit:
                        break
            return json.dumps({"courseware": courseware})
        except Exception as exc:
            return json.dumps({"courseware": [], "error": str(exc)})

    def _get_writing_rubric(
        self,
        paper_type: str | None = None,
        visitor_profile: str | None = None,
    ) -> str:
        """Get writing rubrics and evaluation criteria.

        Args:
            paper_type: Optional paper type (thesis, conference, journal).

        Returns:
            JSON string with rubric information.
        """
        if self._knowledge_store is None:
            return json.dumps(
                {"rubrics": [], "message": "Knowledge store not available"}
            )

        query = (
            f"评分标准 rubric {paper_type}" if paper_type else "评分标准 rubric 写作"
        )
        try:
            hits = self._knowledge_store.search(
                query=query,
                top_k=5,
                visitor_profile=visitor_profile,
            )
            rubrics = []
            for hit in hits:
                rubrics.append(
                    {
                        "title": hit.title,
                        "excerpt": (hit.excerpt or "")[:500],
                    }
                )
            return json.dumps({"rubrics": rubrics[:3]})
        except Exception as exc:
            return json.dumps({"rubrics": [], "error": str(exc)})
