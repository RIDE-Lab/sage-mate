from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import logging
import re
import socket
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Iterator
from contextlib import contextmanager
from time import perf_counter
from typing import Any, Callable, Literal
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger(__name__)

from .runtime_env import bootstrap_runtime_env

bootstrap_runtime_env(require_policy=True, require_fastapi=False)

from sage.serving.integrations import policy as serving_policy

from .config import AppSettings
from .interaction_policy import (
    asks_for_booking_information,
    requires_faculty_review,
    requires_human_handoff,
)
from .models import InteractionIntent
from .request_context import (
    bounded_request_timeout,
    RequestCancelledError,
    raise_if_request_cancelled,
    register_request_cancel_callback,
    request_has_cancellation_controller,
    request_remaining_seconds,
    request_runtime_diagnostics,
    request_was_cancelled,
)
from .workflow_context import WorkflowRequestContext
from .workflow_planner import PlanSpec, ShadowPlanCandidate
from .workflow_steps import get_default_step_registry

_DEFAULT_RETRIEVAL_SCOPES: dict[str, list[str]] = {
    "general": [],
    "research": ["publications", "profile"],
    "teaching": ["courseware"],
    "advising": ["preparation", "meeting_policy", "profile"],
    "booking": ["meeting_policy"],
}

_DEFAULT_EXCLUDE_SCOPES: dict[str, list[str]] = {
    "general": [],
    "research": ["courseware"],
    "teaching": ["publications"],
    "advising": ["courseware"],
    "booking": ["courseware", "publications"],
}

_SEMANTIC_CACHE_SIMILARITY_THRESHOLD = 0.88
_VLLM_METRICS_REFRESH_SECONDS = 5.0
_THROUGHPUT_WINDOW_SECONDS = 60.0
_SEGMENT_REUSE_TAG_PREFIX = "segreuse:v1"
_SEGMENT_REUSE_TAG_SEPARATOR = "||"

_LUCKY_QUESTION_CONTEXTS = {
    "初次来访",
    "科研指导",
    "大模型推理引擎课程答疑",
    "数据库实验课答疑",
    "本科课程答疑",
    "论文写作课",
    "组会准备",
}
_LUCKY_QUESTION_RELEVANCE_MARKERS = (
    "张老师",
    "研究",
    "推理",
    "大模型",
    "llm",
    "npu",
    "ascend",
    "数据库",
    "流处理",
    "系统",
    "课程",
    "论文",
    "实验",
    "组会",
    "学术",
    "预约",
    "项目",
    "投稿",
    "写作",
    "文献",
    "blocker",
)
_LUCKY_QUESTION_FORBIDDEN_MARKERS = (
    "初次来访",
    "首次来访",
    "初次造访",
    "来访感受",
    "虚拟形象",
    "数字分身",
    "操纵",
    "有何巧妙",
    "最有启发性",
    "意义何在",
)
_LUCKY_QUESTION_INTENT_MARKERS = (
    "什么",
    "如何",
    "怎么",
    "为什么",
    "为何",
    "哪些",
    "哪个",
    "哪块",
    "是否",
    "能否",
    "可否",
    "有没有",
    "应该",
    "需要",
    "建议",
    "值得",
)


def normalize_generated_lucky_question(result: dict[str, Any]) -> dict[str, str]:
    question = str(result.get("question") or "").strip().strip('"\'“”')
    context = str(result.get("context") or "").strip().strip('"\'“”')
    question = question.rstrip('"\'“”。，；;！？!? ')
    lowered_question = question.lower()

    if not 6 <= len(question) <= 60:
        return {}
    if context not in _LUCKY_QUESTION_CONTEXTS:
        return {}
    if any(marker in lowered_question for marker in _LUCKY_QUESTION_FORBIDDEN_MARKERS):
        return {}
    if any(label in question for label in _LUCKY_QUESTION_CONTEXTS):
        return {}
    if not any(marker in lowered_question for marker in _LUCKY_QUESTION_RELEVANCE_MARKERS):
        return {}
    if not any(marker in question for marker in _LUCKY_QUESTION_INTENT_MARKERS):
        return {}
    return {"question": f"{question}？", "context": context}


class StreamingServerError(RuntimeError):
    """Raised when the vLLM SSE stream delivers an error event.

    vLLM sometimes returns HTTP 200 with an error payload embedded in
    the SSE stream (e.g. ``thinking_token_budget`` rejected because
    ``--reasoning-config`` was not set).  The standard
    ``response.raise_for_status()`` cannot detect this because the HTTP
    status is 200.  This exception carries the original error message
    and an optional HTTP-like status code so callers can react.
    """

    def __init__(self, message: str, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


class EmptyStreamingResponseError(RuntimeError):
    """Raised when an SSE chat response completes without answer content."""


class _InteractionIntentPayload(BaseModel):
    action: Literal["answer", "book_meeting", "ask_followup", "review_queue", "human_handoff"] = (
        "answer"
    )
    domain: Literal["general", "research", "teaching", "advising", "booking"] = "general"
    retrieval_scopes: list[str] = Field(default_factory=list)
    exclude_scopes: list[str] = Field(default_factory=list)
    decision_mode: (
        Literal["direct_answer", "advise_only", "review_queue", "human_handoff"] | None
    ) = None
    needs_clarification: bool = False
    clarification_message: str | None = None
    escalation_reason: str | None = None


class VllmChatClient:
    supports_fast_intent_bypass = True

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._client = self._build_completion_client()
        # Intent classification client: use a smaller/faster model when configured.
        intent_base_url = self._settings.intent_llm_base_url or self._settings.llm_base_url
        self._intent_client = httpx.Client(
            base_url=intent_base_url,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=30.0,
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        # Auto-detect model name from the connected LLM if not explicitly configured.
        self.model_name: str = self._settings.model_name
        if not self.model_name:
            self.model_name = self._detect_model_name()
        else:
            # The served model is runtime state, not a portable application
            # constant.  A machine-local model preference can become stale
            # after the deployment lock resolves a different compatible
            # backend, so reconcile it with /models before sending requests.
            detected_model = self._detect_model_name()
            if detected_model and detected_model != self.model_name:
                logger.warning(
                    "Configured model %s is not served; using runtime model %s",
                    self.model_name,
                    detected_model,
                )
                self.model_name = detected_model
        self._intent_model_name = self._settings.intent_model_name or self.model_name
        self._runtime_model_refresh_at = 0.0
        self._cache_lock = threading.Lock()
        self._response_cache: OrderedDict[str, tuple[float, str, str]] = OrderedDict()
        self._metrics_lock = threading.Lock()
        self._request_count = 0
        self._success_count = 0
        self._error_count = 0
        self._cache_hit_count = 0
        self._exact_cache_hit_count = 0
        self._semantic_cache_hit_count = 0
        self._cache_lookup_count = 0
        self._last_request_at: float | None = None
        self._last_success_at: float | None = None
        self._last_error_at: float | None = None
        self._last_error_message: str | None = None
        # Auto-detected: set to False when the connected vllm instance does
        # not support --reasoning-config. Start conservatively for non-reasoning
        # model families so the first hosted/web deep request does not discover
        # support by failing in production.
        self._supports_thinking_budget = self._model_supports_thinking_budget()
        self._recent_success_timestamps: deque[float] = deque()
        self._recent_completion_token_samples: deque[tuple[float, int]] = deque()
        self._latency_total_ms = 0.0
        self._latency_observation_count = 0
        self._latency_max_ms = 0.0
        self._last_latency_ms = 0.0
        self._prompt_tokens_total = 0
        self._completion_tokens_total = 0
        self._total_tokens_total = 0
        self._vllm_metrics_url = self._resolve_vllm_metrics_url()
        self._vllm_prefix_cache_queries = 0.0
        self._vllm_prefix_cache_hits = 0.0
        self._vllm_external_prefix_cache_queries = 0.0
        self._vllm_external_prefix_cache_hits = 0.0
        self._vllm_num_requests_running = 0.0
        self._vllm_num_requests_waiting = 0.0
        self._vllm_kv_cache_usage_perc = 0.0
        self._vllm_metrics_last_refresh_at = 0.0
        self._vllm_request_success_total = 0.0
        self._vllm_request_error_total = 0.0
        self._vllm_prompt_tokens_prom_total = 0.0
        self._vllm_generation_tokens_prom_total = 0.0
        self._vllm_avg_generation_throughput_tps = 0.0
        self._vllm_avg_time_to_first_token_s = 0.0
        self._vllm_avg_time_per_output_token_s = 0.0
        # --- DeltaKV session continuity tracking ---
        # Maps session_id -> {"turn_count": int, "cumulative_tokens": int,
        # "last_request_at": float, "last_vllm_uptime_s": float}
        self._session_kv_anchors: dict[str, dict[str, Any]] = {}
        self._last_vllm_start_time_s: float = 0.0

    def _build_completion_client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self._settings.llm_base_url,
            headers={
                "Authorization": f"Bearer {self._settings.api_key}",
                "Content-Type": "application/json",
            },
            timeout=float(self._settings.llm_timeout_seconds),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )

    @staticmethod
    def _interrupt_completion_client(client: httpx.Client) -> None:
        """Abort an active sync HTTP exchange, including pre-header reads.

        ``httpx.Client.close()`` does not interrupt a synchronous request that
        is blocked waiting for response headers.  httpx/httpcore expose no
        public synchronous cancellation hook, so first shut down any active
        request-owned sockets and then close the client.  Attribute access is
        deliberately guarded so an httpcore layout change degrades to normal
        close semantics instead of breaking requests.
        """

        try:
            transport = getattr(client, "_transport", None)
            pool = getattr(transport, "_pool", None)
            connections = tuple(getattr(pool, "connections", ()) or ())
            for connection in connections:
                protocol = getattr(connection, "_connection", None)
                stream = getattr(protocol, "_network_stream", None)
                sock = getattr(stream, "_sock", None)
                if sock is None:
                    continue
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
        finally:
            client.close()

    @contextmanager
    def _request_completion_client(self) -> Iterator[httpx.Client]:
        """Yield an interruptible, request-owned model connection when scoped."""

        if not request_has_cancellation_controller():
            yield self._client
            return
        client = self._build_completion_client()
        unregister = register_request_cancel_callback(
            lambda: self._interrupt_completion_client(client)
        )
        try:
            raise_if_request_cancelled()
            yield client
        finally:
            unregister()
            client.close()

    @property
    def supports_native_tool_calling(self) -> bool:
        """Whether tool schemas may be sent directly to the upstream server."""
        return self._settings.llm_tool_calling_mode == "native"

    def _detect_model_name(self, *, timeout_seconds: float = 10.0) -> str:
        """Query the connected LLM's /models endpoint to discover the served model name."""
        try:
            response = self._client.get("/models", timeout=timeout_seconds)
            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])
            if models:
                detected = models[0].get("id", "")
                if detected:
                    logger.info("Auto-detected model name: %s", detected)
                max_len = models[0].get("max_model_len")
                if isinstance(max_len, int) and max_len > 0:
                    self._model_max_len = max_len
                    logger.info("Auto-detected model max context: %d", max_len)
                return detected
        except Exception as exc:
            logger.warning("Failed to auto-detect model name from %s: %s",
                           self._settings.llm_base_url, exc)
        return ""

    def probe_runtime_model_name(self) -> str:
        """Probe the serving endpoint without falling back to configured memory."""
        detected = self._detect_model_name(timeout_seconds=2.0)
        if detected:
            self.model_name = detected
            self._intent_model_name = self._settings.intent_model_name or detected
        return detected

    def probe_runtime_model_metadata(self) -> dict[str, Any]:
        """Return the live public model card needed by runtime identity."""
        try:
            response = self._client.get("/models", timeout=2.0)
            response.raise_for_status()
            models = response.json().get("data", [])
            if not models or not isinstance(models[0], dict):
                return {}
            record = models[0]
            model_name = str(record.get("id") or "").strip()
            if model_name:
                self.model_name = model_name
                self._intent_model_name = self._settings.intent_model_name or model_name
            capability = record.get("speculative_capability")
            return {
                "id": model_name,
                "speculative_capability": capability if isinstance(capability, dict) else {},
            }
        except Exception as exc:
            logger.warning(
                "Failed to probe runtime model metadata from %s: %s",
                self._settings.llm_base_url,
                exc,
            )
            return {}

    def refresh_runtime_model_name(self) -> str:
        """Refresh the displayed model from the serving endpoint.

        The engine can be restarted or swapped independently of the app
        process, so the configured preference is not authoritative for health
        and status views.
        """
        now = time.monotonic()
        if now - self._runtime_model_refresh_at < 15.0:
            return self.model_name
        self._runtime_model_refresh_at = now
        detected = self._detect_model_name()
        if detected:
            self.model_name = detected
            self._intent_model_name = self._settings.intent_model_name or detected
        return self.model_name

    def _probe_model_max_len(self) -> None:
        """Query /models to discover max_model_len when model name is pre-configured."""
        try:
            response = self._client.get("/models", timeout=10.0)
            response.raise_for_status()
            data = response.json()
            models = data.get("data", [])
            if models:
                max_len = models[0].get("max_model_len")
                if isinstance(max_len, int) and max_len > 0:
                    self._model_max_len = max_len
                    logger.info("Probed model max context: %d", max_len)
        except Exception:
            pass

    def _ensure_runtime_state(self) -> None:
        if not hasattr(self, "_cache_lock"):
            self._cache_lock = threading.Lock()
        if not hasattr(self, "_response_cache"):
            self._response_cache = OrderedDict()
        if not hasattr(self, "_metrics_lock"):
            self._metrics_lock = threading.Lock()
        if not hasattr(self, "_request_count"):
            self._request_count = 0
        if not hasattr(self, "_success_count"):
            self._success_count = 0
        if not hasattr(self, "_error_count"):
            self._error_count = 0
        if not hasattr(self, "_cache_hit_count"):
            self._cache_hit_count = 0
        if not hasattr(self, "_exact_cache_hit_count"):
            self._exact_cache_hit_count = 0
        if not hasattr(self, "_semantic_cache_hit_count"):
            self._semantic_cache_hit_count = 0
        if not hasattr(self, "_cache_lookup_count"):
            self._cache_lookup_count = 0
        if not hasattr(self, "_last_request_at"):
            self._last_request_at = None
        if not hasattr(self, "_last_success_at"):
            self._last_success_at = None
        if not hasattr(self, "_last_error_at"):
            self._last_error_at = None
        if not hasattr(self, "_last_error_message"):
            self._last_error_message = None
        if not hasattr(self, "_recent_success_timestamps"):
            self._recent_success_timestamps = deque()
        if not hasattr(self, "_recent_completion_token_samples"):
            self._recent_completion_token_samples = deque()
        if not hasattr(self, "_latency_total_ms"):
            self._latency_total_ms = 0.0
        if not hasattr(self, "_latency_observation_count"):
            self._latency_observation_count = 0
        if not hasattr(self, "_latency_max_ms"):
            self._latency_max_ms = 0.0
        if not hasattr(self, "_last_latency_ms"):
            self._last_latency_ms = 0.0
        if not hasattr(self, "_prompt_tokens_total"):
            self._prompt_tokens_total = 0
        if not hasattr(self, "_completion_tokens_total"):
            self._completion_tokens_total = 0
        if not hasattr(self, "_total_tokens_total"):
            self._total_tokens_total = 0
        if not hasattr(self, "_vllm_metrics_url"):
            self._vllm_metrics_url = self._resolve_vllm_metrics_url()
        if not hasattr(self, "_vllm_prefix_cache_queries"):
            self._vllm_prefix_cache_queries = 0.0
        if not hasattr(self, "_vllm_prefix_cache_hits"):
            self._vllm_prefix_cache_hits = 0.0
        if not hasattr(self, "_vllm_external_prefix_cache_queries"):
            self._vllm_external_prefix_cache_queries = 0.0
        if not hasattr(self, "_vllm_external_prefix_cache_hits"):
            self._vllm_external_prefix_cache_hits = 0.0
        if not hasattr(self, "_vllm_num_requests_running"):
            self._vllm_num_requests_running = 0.0
        if not hasattr(self, "_vllm_num_requests_waiting"):
            self._vllm_num_requests_waiting = 0.0
        if not hasattr(self, "_vllm_kv_cache_usage_perc"):
            self._vllm_kv_cache_usage_perc = 0.0
        if not hasattr(self, "_vllm_metrics_last_refresh_at"):
            self._vllm_metrics_last_refresh_at = 0.0
        if not hasattr(self, "_vllm_request_success_total"):
            self._vllm_request_success_total = 0.0
        if not hasattr(self, "_vllm_request_error_total"):
            self._vllm_request_error_total = 0.0
        if not hasattr(self, "_vllm_prompt_tokens_prom_total"):
            self._vllm_prompt_tokens_prom_total = 0.0
        if not hasattr(self, "_vllm_generation_tokens_prom_total"):
            self._vllm_generation_tokens_prom_total = 0.0
        if not hasattr(self, "_vllm_avg_generation_throughput_tps"):
            self._vllm_avg_generation_throughput_tps = 0.0
        if not hasattr(self, "_vllm_avg_time_to_first_token_s"):
            self._vllm_avg_time_to_first_token_s = 0.0
        if not hasattr(self, "_vllm_avg_time_per_output_token_s"):
            self._vllm_avg_time_per_output_token_s = 0.0
        if not hasattr(self, "_last_request_usage"):
            self._last_request_usage = {}
        if not hasattr(self, "_model_max_len"):
            self._model_max_len = 0
        if not hasattr(self, "_session_kv_anchors"):
            self._session_kv_anchors = {}
        if not hasattr(self, "_last_vllm_start_time_s"):
            self._last_vllm_start_time_s = 0.0

    # ------------------------------------------------------------------
    # DeltaKV session continuity helpers
    # ------------------------------------------------------------------

    def _build_session_kv_key(
        self, user_id: str, conversation_id: str
    ) -> str:
        """Build a stable session identifier for DeltaKV KV anchoring."""
        prefix = getattr(self._settings, "kv_continuity_session_prefix", "twin-session")
        return f"{prefix}-{user_id}-{conversation_id}"

    def _record_session_turn(
        self,
        session_key: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Update session state after a successful chat turn."""
        anchor = self._session_kv_anchors.get(session_key)
        if anchor is None:
            anchor = {
                "turn_count": 0,
                "cumulative_tokens": 0,
                "last_request_at": 0.0,
            }
            self._session_kv_anchors[session_key] = anchor
        anchor["turn_count"] += 1
        anchor["cumulative_tokens"] += prompt_tokens + completion_tokens
        anchor["last_request_at"] = time.time()

    def _detect_vllm_restart(self) -> bool:
        """Check if the vLLM instance appears to have restarted since the
        last recorded uptime.  Returns True when a restart is suspected."""
        if not getattr(self._settings, "kv_continuity_enabled", False):
            return False
        # Use vLLM's Prometheus metrics to detect restart.  If the uptime
        # counter decreases or resets to zero, the server was restarted.
        self._refresh_vllm_prefix_cache_metrics()
        # Heuristic: if external prefix cache queries reset to zero but we
        # have recorded session anchors, a restart likely occurred.
        if (
            self._vllm_external_prefix_cache_queries == 0.0
            and self._session_kv_anchors
        ):
            return True
        return False

    def annotate_request_with_session_hints(
        self,
        payload: dict[str, Any],
        *,
        user_id: str = "anonymous",
        conversation_id: str = "default",
    ) -> dict[str, Any]:
        """Annotate a vLLM chat-completion payload with DeltaKV session
        continuity hints when ``kv_continuity_enabled`` is True.

        The annotation adds a ``kv_transfer_params`` field containing the
        ``logical_request_id`` set to a stable session key, enabling the
        vLLM external prefix cache (via DeltaKV connector) to match against
        previously transferred KV state.
        """
        if not getattr(self._settings, "kv_continuity_enabled", False):
            return payload
        session_key = self._build_session_kv_key(user_id, conversation_id)
        payload["kv_transfer_params"] = {
            "logical_request_id": session_key,
        }
        return payload

    def annotate_request_with_fixed_prefix_hints(
        self,
        payload: dict[str, Any],
        *,
        system_prompt: str,
        logical_request_id: str | None = None,
    ) -> dict[str, Any]:
        """Annotate a chat-completion payload for fixed-prefix KV reuse.

        The annotation uses vLLM's native ``cache_salt`` field to scope
        prefix-cache hashes. vLLM will still require exact token equality for
        reuse, so the salt is only a namespace, not a correctness shortcut.
        """
        if not getattr(
            self._settings,
            "kv_fixed_prefix_materialization_enabled",
            False,
        ):
            return payload

        prompt_digest = hashlib.sha256(
            f"{self.model_name}\n{system_prompt}".encode("utf-8")
        ).hexdigest()[:16]
        anchor_prefix = str(
            getattr(
                self._settings,
                "kv_fixed_prefix_anchor_prefix",
                "sage-mate-fixed-prefix",
            )
            or "sage-mate-fixed-prefix"
        ).strip()
        anchor_version = str(
            getattr(self._settings, "kv_fixed_prefix_anchor_version", "v1") or "v1"
        ).strip()
        anchor_id = f"{anchor_prefix}:{anchor_version}:{prompt_digest}"
        cache_salt = f"kvmat:anchor:{anchor_id}"

        del logical_request_id
        payload["cache_salt"] = cache_salt
        return payload

    def annotate_request_with_segment_reuse_hints(
        self,
        payload: dict[str, Any],
        *,
        reusable_body_text: str,
        scope: str = "default",
        logical_request_id: str | None = None,
        leading_token_count: int | None = None,
    ) -> dict[str, Any]:
        """Attach segment-reuse control-plane metadata to a vLLM request.

        This does not change prompt semantics. It exposes a stable namespace
        and body digest to runtimes that understand the segment-reuse
        ``extra_key`` protocol. We intentionally omit ``leading_tokens`` by
        default because exact token boundaries must be computed with the same
        tokenizer and chat template as the serving runtime.
        """
        if not getattr(self._settings, "segment_reuse_hints_enabled", False):
            return payload
        body = (reusable_body_text or "").strip()
        if not body:
            return payload

        boundary_class = str(
            getattr(self._settings, "segment_reuse_boundary_class", "control-only")
            or "control-only"
        ).strip()
        attention_contract = self._segment_reuse_attention_contract(boundary_class)
        body_digest = hashlib.sha256(
            f"{self.model_name}\n{body}".encode("utf-8")
        ).hexdigest()[:16]
        namespace_prefix = str(
            getattr(self._settings, "segment_reuse_namespace_prefix", "sage-mate")
            or "sage-mate"
        ).strip()
        namespace = ":".join(
            self._sanitize_segment_reuse_field(part)
            for part in (namespace_prefix, scope, body_digest)
            if str(part or "").strip()
        )
        max_leading_tokens = int(
            getattr(self._settings, "segment_reuse_max_leading_tokens", 4096) or 4096
        )
        leading_tokens_text = (
            ""
            if leading_token_count is None
            else str(max(0, int(leading_token_count)))
        )
        note_parts = ["source=twin", f"body={body_digest}"]
        if logical_request_id:
            note_parts.append(
                "rid=" + self._sanitize_segment_reuse_field(logical_request_id)[:48]
            )
        note = "|".join(note_parts)
        tag = (
            f"{_SEGMENT_REUSE_TAG_PREFIX};mode=leading-envelope;segments=1;"
            f"tokens={max_leading_tokens};leading_tokens={leading_tokens_text};"
            f"boundary_class={self._sanitize_segment_reuse_field(boundary_class)};"
            f"attention_contract={self._sanitize_segment_reuse_field(attention_contract)};"
            f"note={note}"
        )
        payload["extra_key"] = f"{namespace}{_SEGMENT_REUSE_TAG_SEPARATOR}{tag}"
        return payload

    @staticmethod
    def _segment_reuse_attention_contract(boundary_class: str) -> str:
        normalized = boundary_class.strip().lower()
        if normalized == "masked-envelope":
            return "masked-envelope-hidden-from-body"
        return "control-envelope-excluded-from-model-body"

    @staticmethod
    def _sanitize_segment_reuse_field(value: object) -> str:
        text = str(value or "").strip()
        sanitized = re.sub(r"[^A-Za-z0-9_.:@+-]+", "-", text)
        return sanitized.strip("-") or "default"

    def get_session_continuity_snapshot(
        self, user_id: str = "anonymous", conversation_id: str = "default"
    ) -> dict[str, Any]:
        """Return the current session continuity state for diagnostics."""
        session_key = self._build_session_kv_key(user_id, conversation_id)
        anchor = self._session_kv_anchors.get(session_key)
        return {
            "session_key": session_key,
            "kv_continuity_enabled": getattr(
                self._settings, "kv_continuity_enabled", False
            ),
            "turn_count": anchor["turn_count"] if anchor else 0,
            "cumulative_tokens": anchor["cumulative_tokens"] if anchor else 0,
            "last_request_at": anchor["last_request_at"] if anchor else None,
            "vllm_restart_detected": self._detect_vllm_restart(),
        }

    def close(self) -> None:
        self._client.close()
        self._intent_client.close()

    async def aclose(self) -> None:
        await asyncio.to_thread(self.close)

    @property
    def last_request_usage(self) -> dict[str, int]:
        """Token usage from the most recent chat completion call."""
        return dict(self._last_request_usage) if self._last_request_usage else {}

    @property
    def model_max_len(self) -> int:
        """Maximum context length reported by the connected LLM, or 0 if unknown."""
        return getattr(self, "_model_max_len", 0)

    def runtime_snapshot(self) -> dict[str, str]:
        self._ensure_runtime_state()
        cache_entries = self._active_cache_entries()
        self._refresh_vllm_prefix_cache_metrics()
        with self._metrics_lock:
            now = time.time()
            self._trim_throughput_samples_locked(now)

            # When the internal request counter is zero but vLLM's Prometheus
            # endpoint reports completed requests, use the Prometheus values
            # so the dashboard reflects direct-to-vLLM inference activity.
            eff_request_count = self._request_count
            eff_success_count = self._success_count
            eff_error_count = self._error_count
            eff_prompt_tokens = self._prompt_tokens_total
            eff_completion_tokens = self._completion_tokens_total
            vllm_prom_success = int(self._vllm_request_success_total)
            vllm_prom_error = int(self._vllm_request_error_total)
            if self._request_count == 0 and vllm_prom_success > 0:
                eff_request_count = vllm_prom_success + vllm_prom_error
                eff_success_count = vllm_prom_success
                eff_error_count = vllm_prom_error
                eff_prompt_tokens = int(self._vllm_prompt_tokens_prom_total)
                eff_completion_tokens = int(self._vllm_generation_tokens_prom_total)

            last_status = "not_checked"
            if eff_success_count > 0:
                if self._last_success_at is not None and (
                    self._last_error_at is None or self._last_success_at >= self._last_error_at
                ):
                    last_status = "ok"
                elif self._last_error_at is not None and eff_error_count > eff_success_count:
                    last_status = "error"
                else:
                    last_status = "ok"
            elif self._last_error_at is not None:
                last_status = "error"

            app_cache_hit_rate = self._exact_cache_hit_count / max(self._cache_lookup_count, 1)
            semantic_cache_hit_rate = self._semantic_cache_hit_count / max(
                self._cache_lookup_count, 1
            )
            prefix_cache_hit_rate = self._vllm_prefix_cache_hits / max(
                self._vllm_prefix_cache_queries, 1.0
            )
            external_prefix_cache_hit_rate = self._vllm_external_prefix_cache_hits / max(
                self._vllm_external_prefix_cache_queries, 1.0
            )
            recent_rps = self._compute_recent_request_throughput_locked(now)
            recent_tps = self._compute_recent_completion_throughput_locked(now)
            # Supplement throughput with vLLM Prometheus values when internal
            # samples are empty (i.e. no chat-pipeline requests in the window).
            if recent_tps <= 0.0 and self._vllm_avg_generation_throughput_tps > 0.0:
                recent_tps = self._vllm_avg_generation_throughput_tps

            # Derive latency from vLLM Prometheus TTFT+TPOT when internal
            # latency observations are absent.
            avg_latency_ms = self._latency_total_ms / max(self._latency_observation_count, 1)
            max_latency_ms = self._latency_max_ms
            if self._latency_observation_count == 0 and (
                self._vllm_avg_time_to_first_token_s > 0.0
                or self._vllm_avg_time_per_output_token_s > 0.0
            ):
                avg_latency_ms = (
                    self._vllm_avg_time_to_first_token_s
                    + self._vllm_avg_time_per_output_token_s
                ) * 1000.0
                max_latency_ms = avg_latency_ms

            return {
                "model_name": getattr(self, "model_name", ""),
                "llm_status": last_status,
                "llm_request_count": str(eff_request_count),
                "llm_success_count": str(eff_success_count),
                "llm_error_count": str(eff_error_count),
                "llm_cache_hit_count": str(self._cache_hit_count),
                "llm_cache_entries": str(cache_entries),
                "llm_app_cache_hit_rate": f"{app_cache_hit_rate:.4f}",
                "llm_semantic_cache_hit_rate": f"{semantic_cache_hit_rate:.4f}",
                "llm_vllm_prefix_cache_hit_rate": f"{prefix_cache_hit_rate:.4f}",
                "llm_vllm_external_prefix_cache_hit_rate": f"{external_prefix_cache_hit_rate:.4f}",
                "llm_vllm_prefix_cache_queries": f"{self._vllm_prefix_cache_queries:.0f}",
                "llm_vllm_prefix_cache_hits": f"{self._vllm_prefix_cache_hits:.0f}",
                "llm_vllm_num_requests_running": f"{self._vllm_num_requests_running:.2f}",
                "llm_vllm_num_requests_waiting": f"{self._vllm_num_requests_waiting:.2f}",
                "llm_vllm_kv_cache_usage_perc": f"{self._vllm_kv_cache_usage_perc:.4f}",
                "llm_avg_latency_ms": f"{avg_latency_ms:.2f}",
                "llm_last_latency_ms": f"{self._last_latency_ms:.2f}",
                "llm_max_latency_ms": f"{max_latency_ms:.2f}",
                "llm_request_throughput_rps": f"{recent_rps:.4f}",
                "llm_completion_throughput_tps": f"{recent_tps:.4f}",
                "llm_prompt_tokens_total": str(eff_prompt_tokens),
                "llm_completion_tokens_total": str(eff_completion_tokens),
                "llm_total_tokens_total": str(eff_prompt_tokens + eff_completion_tokens),
                "llm_last_error": self._last_error_message or "",
                "llm_last_request_at": self._format_timestamp(self._last_request_at),
                "llm_last_success_at": self._format_timestamp(self._last_success_at),
                "llm_last_error_at": self._format_timestamp(self._last_error_at),
            }

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        # Goal: stop the model from reflexively choosing ask_followup. Default
        # to ``answer`` for any question that has even a thin academic anchor.
        # ask_followup is reserved for genuinely unintelligible inputs.
        system_prompt = (
            "Classify the student's request for a faculty digital twin. "
            "Return JSON only with keys: action, domain, retrieval_scopes, exclude_scopes, decision_mode, needs_clarification, clarification_message, escalation_reason. "
            'Allowed action values: "answer", "book_meeting", "ask_followup", "review_queue", "human_handoff". '
            'Allowed domain values: "general", "research", "teaching", "advising", "booking". '
            'Allowed decision_mode values: "direct_answer", "advise_only", "review_queue", "human_handoff". '
            'Allowed retrieval scopes: "publications", "profile", "courseware", "preparation", "meeting_policy". '
            'Allowed exclude scopes: "publications", "profile", "courseware", "preparation", "meeting_policy". '
            "DEFAULT BEHAVIOR: choose action=answer. The downstream pipeline retrieves the owner's papers, course slides and biography and grounds the answer; you do NOT need to ask the student for more info before classifying. "
            "Use ask_followup ONLY as a last resort and ONLY when ALL of the following are true: "
            "(a) the question contains no concrete academic anchor (no paper title, system name, course term, KV/TTFT/batching/scheduling/inference keyword, no '论文/文献/汇报/写作/研究方向/招生/课题'), "
            "(b) the recent_session_context block is empty (so we cannot resolve references like '具体内容', '这个', '上面那个'), "
            "(c) without clarification, no reasonable answer can be drafted at all. "
            "If the question is short but recent_session_context exists, RESOLVE the reference against that prior turn and choose action=answer with the appropriate domain. "
            "Use book_meeting only when the student is actually asking to schedule or submit a meeting request now. Questions about what to prepare before booking are advising, not booking. "
            "Use advise_only when you may give suggestions, checklists, draft ideas, or options but must not make the final decision for the owner or the student. "
            "Use review_queue ONLY for requests for approval, exception, endorsement, recommendation, or commitment that must be reviewed before the avatar can respond formally. Open academic questions like '顶会论文叙事是什么样', '怎么整理文献', '如何选研究方向' are NOT review_queue \u2014 they are answer/advise_only with domain=research or advising. "
            "Use human_handoff only when the topic is sensitive, urgent, grievance-related, confidential, or safety-related. "
            "Use research for research direction, papers, projects, publications, or system-building questions (SAGE / Neuromem / vLLM-HUST / KV cache / TTFT / batching / scheduling / inference engine). "
            "Use teaching for course lectures, tutorials, experiments, or teaching-material questions. "
            "Use advising for preparation, expectations, communication style, meeting-readiness, or paper-writing-process guidance. "
            "Positive examples (all must be answer, not ask_followup): "
            "'我已经整理了十篇文献，然后进行汇报，汇报的内容应该包括什么' -> answer / advising / advise_only. "
            "'研究目标是降低TTFT，希望得到怎么整理相关文献的建议' -> answer / research / advise_only. "
            "'我的研究思路是当kv可复用时重计算也比复用更快...' -> answer / research / advise_only. "
            "'你能帮我看论文吗' -> answer / advising / advise_only (do NOT ask which paper before retrieval). "
            "'kvpr这篇' (with prior turn mentioning a paper) -> answer / research. "
            "'一篇好的顶会论文应该是什么样的叙事' -> answer / advising / advise_only. "
            "'你的学术背景' / '你的主要研究方向' -> answer / research / direct_answer. "
            "If you do choose ask_followup, the clarification_message must be one short Chinese sentence asking ONE specific missing fact, never a generic '能否提供更多信息'."
        )
        context_line = course_context or ""
        recent_block = (recent_session_context or "").strip()
        user_prompt_parts: list[str] = []
        if context_line:
            user_prompt_parts.append(f"course_context: {context_line}")
        if recent_block:
            user_prompt_parts.append(f"recent_session_context (use to resolve short references):\n{recent_block}")
        else:
            user_prompt_parts.append("recent_session_context: <empty>")
        user_prompt_parts.append(f"current_question: {question}")
        user_prompt = "\n".join(user_prompt_parts)
        content = self._request_intent_classification(system_prompt, user_prompt)
        payload = self._parse_interaction_intent_payload(content)
        raw_intent = self._coerce_interaction_intent(payload)
        return self._normalize_interaction_intent(
            question, course_context, raw_intent, recent_session_context=recent_block
        )

    async def classify_interaction_intent(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return await asyncio.to_thread(
            self.classify_interaction_intent_sync,
            question,
            course_context,
            recent_session_context,
        )

    def classify_booking_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
    ) -> bool:
        try:
            return (
                self.classify_interaction_intent_sync(question, course_context).action
                == "book_meeting"
            )
        except Exception:
            return False

    async def classify_booking_intent(
        self,
        question: str,
        course_context: str | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self.classify_booking_intent_sync,
            question,
            course_context,
        )

    def generate_lucky_question_sync(
        self,
        *,
        owner_name: str,
        owner_role: str,
        visitor_profile: str = "general_visitor",
        recent_questions: list[str] | None = None,
        onboarding_step: str = "",
    ) -> dict[str, str]:
        """Use the intent model to generate a contextual question for the
        "I'm feeling lucky" button.  Falls back gracefully on any error so
        the caller can use the static question bank instead."""
        profile_labels = {
            "general_visitor": "general visitor",
            "hust_undergraduate": "HUST undergraduate student",
            "paper_writing_student": "paper-writing course student",
            "lab_member": "lab member / graduate researcher",
        }
        profile_label = profile_labels.get(visitor_profile, "visitor")
        avoided = ""
        if recent_questions:
            unique_recent = list(dict.fromkeys(recent_questions))[:8]
            avoided = (
                "\nDo NOT repeat or closely paraphrase any of these recent questions:\n"
                + "\n".join(f"- {q}" for q in unique_recent)
            )

        system_prompt = (
            "You generate one engaging, specific question that a "
            f"{profile_label} could ask the digital twin of {owner_name} "
            f"({owner_role}). "
            "The question should be grounded in the owner's actual work: "
            "research on LLM inference engines, Ascend NPU systems, "
            "database/stream-processing/runtime systems, teaching courses "
            "on large-model inference engines and databases, or academic advising. "
            "Make it thought-provoking, not generic. The question field must contain "
            "only the visitor's complete question; never include a context label, visitor "
            "state, JSON commentary, or semicolon-delimited metadata in it. "
            "Do not ask about the digital twin itself or the experience of visiting it. "
            "Return JSON only: {\"question\": \"...\", \"context\": \"...\"}. "
            "context is a short label like '科研指导', '大模型推理引擎课程答疑', "
            "'论文写作课', '初次来访', or '组会准备'. "
            "The question must be in Chinese, clearly interrogative, and end with '？'. "
            "Keep it under 40 characters. Good example: "
            "{\"question\": \"张老师目前最关注哪些推理系统问题？\", "
            "\"context\": \"初次来访\"}."
        )
        if onboarding_step:
            system_prompt += (
                f"\nThe visitor is currently on onboarding step: {onboarding_step}. "
                "Generate a question that is relevant to this specific onboarding step."
            )
        user_prompt = (
            f"Generate one question for a {profile_label} visiting "
            f"{owner_name}'s digital twin."
            f"{avoided}"
        )
        try:
            payload: dict[str, Any] = {
                "model": self._intent_model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.85,
                "max_tokens": 120,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            self._record_request_start()
            started_at = perf_counter()
            response = self._intent_client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("Model returned no choices")
            content = str(choices[0].get("message", {}).get("content") or "")
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            self._record_request_success(latency_ms=elapsed_ms)
            result = self._extract_json_object(content)
            return normalize_generated_lucky_question(result)
        except Exception as exc:
            self._record_request_error(exc)
            logger.warning("generate_lucky_question failed: %s", exc)
            return {}

    def propose_shadow_plan_candidate_sync(
        self,
        context: WorkflowRequestContext,
        deterministic_plan: PlanSpec,
    ) -> ShadowPlanCandidate:
        step_registry = get_default_step_registry()
        allowed_step_ids = ", ".join(sorted(step_registry))
        allowed_sources = ", ".join(sorted(context.available_evidence_sources)) or "none"
        deterministic_steps = ", ".join(step.step_id for step in deterministic_plan.steps)
        system_prompt = (
            "You are a shadow workflow planner for an academic digital twin. "
            "Return JSON only. Do not execute anything. Do not invent unregistered steps. "
            "Propose a conservative comparison plan for analysis only."
        )
        user_prompt = (
            "Return a JSON object with exactly these keys: goal, fallback_template, step_ids, allowed_sources, "
            "requires_citations, explain_to_operator.\n"
            f"Allowed step_ids: {allowed_step_ids}.\n"
            f"Allowed evidence sources for this request: {allowed_sources}.\n"
            f"Question: {context.question}\n"
            f"Course context: {context.course_context or 'none'}\n"
            f"Visitor profile: {context.visitor_profile or 'none'}\n"
            f"Role mode: {context.role_mode}\n"
            f"Journey state: {context.journey_state}\n"
            f"Session identity: {context.session_identity}\n"
            f"Deterministic goal: {deterministic_plan.goal}\n"
            f"Deterministic fallback template: {deterministic_plan.fallback_template}\n"
            f"Deterministic steps: {deterministic_steps}\n"
            "Rules: keep the proposal read-only unless a draft step is clearly justified, do not use admin-only steps for "
            "normal users, and do not add any text outside the JSON object."
        )
        content = self.answer_question_sync(
            system_prompt,
            user_prompt,
            temperature=self._settings.shadow_planner_temperature,
            max_tokens=self._settings.shadow_planner_max_tokens,
            enable_thinking=False,
        )
        return self._parse_shadow_plan_candidate(content)

    async def propose_shadow_plan_candidate(
        self,
        context: WorkflowRequestContext,
        deterministic_plan: PlanSpec,
    ) -> ShadowPlanCandidate:
        return await asyncio.to_thread(
            self.propose_shadow_plan_candidate_sync,
            context,
            deterministic_plan,
        )

    def _request_intent_classification(self, system_prompt: str, user_prompt: str) -> str:
        """Run intent classification on the dedicated (smaller) model."""
        payload: dict[str, Any] = {
            "model": self._intent_model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 160,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        started_at = perf_counter()
        self._record_request_start()
        try:
            response = self._intent_client.post("/chat/completions", json=payload)
            response.raise_for_status()
            data = response.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("Intent model returned no choices")
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                raise RuntimeError("Intent model returned empty content")
            elapsed_ms = (perf_counter() - started_at) * 1000.0
            self._record_request_success(latency_ms=elapsed_ms)
            return str(content)
        except Exception as exc:
            self._record_request_error(exc)
            raise

    def answer_question_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = 4096,
        token_callback: Callable[[str], None] | None = None,
        enable_thinking: bool = True,
        thinking_token_budget: int | None = None,
        deadline_class: str | None = None,
        request_priority: int | None = None,
        target_e2e_ms: float | None = None,
        cache_namespace: str | None = None,
        segment_reuse_body_text: str | None = None,
        segment_reuse_scope: str | None = None,
        use_reuse_hints: bool = True,
        continue_on_length: bool = True,
    ) -> str:
        answer_temperature = (
            self._settings.llm_answer_temperature if temperature is None else temperature
        )
        model_name = (self.model_name or "").strip().lower()
        if "glm-4" in model_name:
            # GLM-4-32B is highly sensitive to OpenAI-style frequency penalties:
            # even modest values can turn direct answers into prompt echoes,
            # refusals, or repeated fragments. Keep its decoding neutral.
            frequency_penalty = 0.0
            presence_penalty = 0.0
            repetition_penalty = 1.0
        else:
            frequency_penalty = self._settings.llm_answer_frequency_penalty
            presence_penalty = self._settings.llm_answer_presence_penalty
            repetition_penalty = self._settings.llm_answer_repetition_penalty
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": answer_temperature,
            "frequency_penalty": frequency_penalty,
            "presence_penalty": presence_penalty,
            "repetition_penalty": repetition_penalty,
        }
        if not enable_thinking:
            payload["chat_template_kwargs"] = {"enable_thinking": False}
            # Without thinking, we don't need as many tokens
            if max_tokens is not None and max_tokens > 2048:
                max_tokens = 2048
        else:
            # B2: Cap thinking tokens to reduce wasted CoT generation.
            # Uses vllm-hust's thinking_token_budget parameter.
            # Only send when the server supports --reasoning-config.
            if self._supports_thinking_budget:
                budget = thinking_token_budget or self._settings.thinking_token_budget
                if budget is not None:
                    payload["thinking_token_budget"] = budget
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        logical_request_id = cache_namespace or "default"
        if use_reuse_hints:
            payload = self.annotate_request_with_session_hints(
                payload,
                user_id="anonymous",
                conversation_id=logical_request_id,
            )
            payload = self.annotate_request_with_fixed_prefix_hints(
                payload,
                system_prompt=system_prompt,
                logical_request_id=logical_request_id,
            )
            payload = self.annotate_request_with_segment_reuse_hints(
                payload,
                reusable_body_text=segment_reuse_body_text or system_prompt,
                scope=segment_reuse_scope or "system",
                logical_request_id=logical_request_id,
            )

        # Scope the response cache to a specific conversation/user so that
        # different users asking similar questions don't get identical cached
        # answers (cross-user cache pollution).  The key is stripped before
        # the payload is sent to the LLM server.
        if cache_namespace:
            payload["_cache_ns"] = cache_namespace

        self._apply_serving_policy_to_payload(
            payload,
            deadline_class=deadline_class,
            request_priority=request_priority,
            target_e2e_ms=target_e2e_ms,
        )

        if token_callback is not None:
            stream_payload = dict(payload)
            stream_payload["stream"] = True
            stream_payload["stream_options"] = {"include_usage": True}
            try:
                return self._request_chat_completion_stream(stream_payload, token_callback)
            except httpx.HTTPStatusError as exc:
                if self._is_thinking_budget_unsupported(exc) or (
                    "thinking_token_budget" in stream_payload
                    and exc.response.status_code >= 400
                ):
                    self._supports_thinking_budget = False
                    payload.pop("thinking_token_budget", None)
                    stream_payload = dict(payload)
                    stream_payload["stream"] = True
                    stream_payload["stream_options"] = {"include_usage": True}
                    return self._request_chat_completion_stream(stream_payload, token_callback)
                raise
            except StreamingServerError as exc:
                if self._is_thinking_budget_message(str(exc)):
                    self._supports_thinking_budget = False
                    payload.pop("thinking_token_budget", None)
                    stream_payload = dict(payload)
                    stream_payload["stream"] = True
                    stream_payload["stream_options"] = {"include_usage": True}
                    return self._request_chat_completion_stream(stream_payload, token_callback)
                raise RuntimeError(str(exc)) from exc
            except EmptyStreamingResponseError:
                logger.warning("Streaming chat response was empty; retrying once without streaming")
                payload.pop("stream", None)
                payload.pop("stream_options", None)
                return self._request_chat_completion_sync(
                    payload,
                    continue_on_length=continue_on_length,
                )

        try:
            return self._request_chat_completion_sync(
                payload,
                continue_on_length=continue_on_length,
            )
        except httpx.HTTPStatusError as exc:
            if self._is_thinking_budget_unsupported(exc) or (
                "thinking_token_budget" in payload
                and exc.response.status_code >= 400
            ):
                self._supports_thinking_budget = False
                payload.pop("thinking_token_budget", None)
                return self._request_chat_completion_sync(
                    payload,
                    continue_on_length=continue_on_length,
                )
            raise

    def _is_local_mlx_model(self) -> bool:
        base_url = self._settings.llm_base_url.strip().lower()
        model_name = (self._settings.model_name or self.model_name or "").strip().lower()
        local_base = (
            "127.0.0.1" in base_url
            or "localhost" in base_url
            or "[::1]" in base_url
        )
        return local_base and model_name.startswith("mlx-community/")

    def _model_supports_thinking_budget(self) -> bool:
        if self._is_local_mlx_model():
            return False
        model_name = (self._settings.model_name or self.model_name or "").strip().lower()
        return "qwen3" in model_name

    def _is_thinking_budget_unsupported(self, exc: httpx.HTTPStatusError) -> bool:
        """Return True when the 400 error indicates the vllm instance
        was not started with ``--reasoning-config``."""
        if exc.response.status_code != 400:
            return False
        try:
            body = exc.response.json()
        except Exception:
            return False
        error = body.get("error")
        if isinstance(error, dict):
            message = str(error.get("message", ""))
        else:
            message = str(body.get("message", ""))
        return self._is_thinking_budget_message(message)

    @staticmethod
    def _is_thinking_budget_message(message: str) -> bool:
        """Return True when an error message references thinking budget config."""
        return "reasoning_config" in message or "thinking_token_budget" in message

    def _request_chat_completion_stream(
        self,
        payload: dict[str, Any],
        token_callback: Callable[[str], None],
    ) -> str:
        # We deliberately skip the response cache for streaming requests:
        # the cache stores the final string and cannot replay deltas to
        # ``token_callback``. Cached non-streaming completions are still
        # served by ``_request_chat_completion_sync`` for callers that
        # don't pass a callback.
        cache_payload = dict(payload)
        cache_payload.pop("stream", None)
        cache_payload.pop("stream_options", None)
        cache_payload.pop("_cache_ns", None)
        cache_key = self._cache_key(cache_payload, namespace=payload.get("_cache_ns"))
        semantic_key = self._semantic_cache_key(cache_payload, namespace=payload.get("_cache_ns"))
        cached, hit_type = self._get_cached_response(cache_key, semantic_key)
        diagnostics = request_runtime_diagnostics()
        if diagnostics is not None:
            diagnostics.record_cache_lookup(hit=cached is not None)
        if cached is not None:
            self._record_cache_hit(hit_type or "exact")
            try:
                token_callback(cached)
            except Exception:
                pass
            return cached

        # Strip internal cache-namespace key before sending to the server.
        payload.pop("_cache_ns", None)

        started_at = perf_counter()
        self._record_request_start()
        max_retries = self._settings.llm_retry_attempts
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for attempt in range(max_retries + 1):
            collected: list[str] = []
            first_token_recorded = False
            try:
                raise_if_request_cancelled(minimum_remaining_seconds=0.1)
                if diagnostics is not None:
                    diagnostics.record_llm_call()
                with self._request_completion_client() as request_client, request_client.stream(
                    "POST",
                    "/chat/completions",
                    json=payload,
                    **self._request_timeout_kwargs(),
                ) as response:
                    response.raise_for_status()
                    for raw_line in response.iter_lines():
                        raise_if_request_cancelled()
                        if not raw_line:
                            continue
                        line = (
                            raw_line.decode("utf-8")
                            if isinstance(raw_line, (bytes, bytearray))
                            else raw_line
                        )
                        if not line.startswith("data:"):
                            continue
                        data_text = line[len("data:") :].strip()
                        if not data_text or data_text == "[DONE]":
                            if data_text == "[DONE]":
                                break
                            continue
                        try:
                            chunk = json.loads(data_text)
                        except json.JSONDecodeError:
                            continue
                        # vLLM may return HTTP 200 with an error payload
                        # embedded in the SSE stream (e.g. when
                        # thinking_token_budget is rejected because
                        # --reasoning-config was not set at startup).
                        # Detect this early and surface it as an
                        # exception so the caller can retry without
                        # the offending parameter.
                        sse_error = chunk.get("error")
                        if isinstance(sse_error, dict):
                            err_msg = str(sse_error.get("message", ""))
                            err_code = int(sse_error.get("code") or 500)
                        elif isinstance(sse_error, str) and sse_error:
                            err_msg = sse_error
                            err_code = 500
                        else:
                            err_msg = ""
                            err_code = 0
                        if err_msg:
                            raise StreamingServerError(err_msg, err_code)
                        choices = chunk.get("choices") or []
                        usage = chunk.get("usage") or {}
                        if isinstance(usage, dict):
                            prompt_tokens = max(
                                prompt_tokens,
                                int(usage.get("prompt_tokens") or 0),
                            )
                            completion_tokens = max(
                                completion_tokens,
                                int(usage.get("completion_tokens") or 0),
                            )
                            total_tokens = max(
                                total_tokens,
                                int(usage.get("total_tokens") or 0),
                            )
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        message = choices[0].get("message") or {}
                        delta_content = (
                            delta.get("content")
                            or delta.get("reasoning_content")
                            or message.get("content")
                        )
                        if not delta_content:
                            continue
                        text = str(delta_content)
                        collected.append(text)
                        if diagnostics is not None and not first_token_recorded:
                            diagnostics.record_llm_ttft(
                                (perf_counter() - started_at) * 1000.0
                            )
                            first_token_recorded = True
                        try:
                            token_callback(text)
                        except Exception:  # pragma: no cover - defensive
                            pass
                answer = "".join(collected)
                if not answer:
                    raise EmptyStreamingResponseError(
                        "vllm-hust returned an empty streaming chat message"
                    )
                break
            except httpx.TimeoutException as exc:
                if attempt >= max_retries:
                    self._record_request_error(exc)
                    raise
                if diagnostics is not None:
                    diagnostics.record_llm_retry()
                self._sleep_before_retry(attempt + 1)
            except EmptyStreamingResponseError:
                raise
            except (httpx.ReadError, httpx.WriteError) as exc:
                if request_was_cancelled():
                    raise RequestCancelledError("model request interrupted after cancellation") from exc
                self._record_request_error(exc)
                raise
            except Exception as exc:
                self._record_request_error(exc)
                raise
        elapsed_ms = (perf_counter() - started_at) * 1000.0
        if diagnostics is not None:
            diagnostics.record_llm_complete(elapsed_ms)
        self._record_request_success(
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self._store_cached_response(cache_key, answer, semantic_key)
        self._last_request_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        return answer

    def warm_fixed_prefix_cache_sync(self, system_prompt: str) -> bool:
        if not getattr(self._settings, "kv_fixed_prefix_materialization_enabled", False):
            return False
        if not getattr(self._settings, "kv_fixed_prefix_warmup_on_startup", True):
            return False
        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "固定前缀预热。只回复 OK。"},
            ],
            "temperature": 0.0,
            "max_tokens": int(getattr(self._settings, "kv_fixed_prefix_warmup_max_tokens", 1)),
            "chat_template_kwargs": {"enable_thinking": False},
        }
        payload = self.annotate_request_with_fixed_prefix_hints(
            payload,
            system_prompt=system_prompt,
            logical_request_id="fixed-prefix-warmup",
        )
        try:
            response = self._client.post("/chat/completions", json=payload)
            response.raise_for_status()
            return True
        except Exception as exc:
            logger.warning("Fixed-prefix warmup failed: %s", exc)
            return False

    def _request_chat_completion_sync(
        self,
        payload: dict[str, Any],
        *,
        continue_on_length: bool = True,
    ) -> str:
        cache_ns = payload.pop("_cache_ns", None)

        cache_key = self._cache_key(payload, namespace=cache_ns)
        semantic_key = self._semantic_cache_key(payload, namespace=cache_ns)
        cached, hit_type = self._get_cached_response(cache_key, semantic_key)
        diagnostics = request_runtime_diagnostics()
        if diagnostics is not None:
            diagnostics.record_cache_lookup(hit=cached is not None)
        if cached is not None:
            self._record_cache_hit(hit_type or "exact")
            return cached

        started_at = perf_counter()
        self._record_request_start()
        max_retries = self._settings.llm_retry_attempts
        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0
        for attempt in range(max_retries + 1):
            try:
                raise_if_request_cancelled(minimum_remaining_seconds=0.1)
                if diagnostics is not None:
                    diagnostics.record_llm_call()
                with self._request_completion_client() as request_client:
                    response = request_client.post(
                        "/chat/completions",
                        json=payload,
                        **self._request_timeout_kwargs(),
                    )
                response.raise_for_status()

                data = response.json()
                usage = data.get("usage") or {}
                if isinstance(usage, dict):
                    prompt_tokens = int(usage.get("prompt_tokens") or 0)
                    completion_tokens = int(usage.get("completion_tokens") or 0)
                    total_tokens = int(usage.get("total_tokens") or 0)
                choices = data.get("choices", [])
                if not choices:
                    raise RuntimeError("vllm-hust returned no chat choices")
                message = choices[0].get("message", {})
                content = (
                    message.get("content")
                    or message.get("reasoning_content")
                    or choices[0].get("text")
                )
                if not content:
                    raise RuntimeError("vllm-hust returned an empty chat message")
                answer = str(content)
                finish_reason = str(choices[0].get("finish_reason") or "").strip().lower()
                if finish_reason == "length" and continue_on_length:
                    answer = self._continue_truncated_answer(payload, answer)
                break
            except httpx.TimeoutException as exc:
                if attempt >= max_retries:
                    self._record_request_error(exc)
                    raise
                if diagnostics is not None:
                    diagnostics.record_llm_retry()
                self._sleep_before_retry(attempt + 1)
            except (httpx.ReadError, httpx.WriteError) as exc:
                if request_was_cancelled():
                    raise RequestCancelledError("model request interrupted after cancellation") from exc
                self._record_request_error(exc)
                raise
            except Exception as exc:
                self._record_request_error(exc)
                raise
        elapsed_ms = (perf_counter() - started_at) * 1000.0
        if diagnostics is not None:
            diagnostics.record_llm_ttft(elapsed_ms)
            diagnostics.record_llm_complete(elapsed_ms)
        self._record_request_success(
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self._store_cached_response(cache_key, answer, semantic_key)
        self._last_request_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }
        return answer

    def _continue_truncated_answer(self, payload: dict[str, Any], partial_answer: str) -> str:
        try:
            messages = list(payload.get("messages") or [])
            if not messages:
                return partial_answer

            continuation_messages = list(messages)
            continuation_messages.append({"role": "assistant", "content": partial_answer})
            continuation_messages.append(
                {
                    "role": "user",
                    "content": "继续，直接从上句未完处接着写，保持同一语言，不要重复已写内容。",
                }
            )

            continuation_payload = dict(payload)
            continuation_payload["messages"] = continuation_messages
            with self._request_completion_client() as request_client:
                continuation_response = request_client.post(
                    "/chat/completions",
                    json=continuation_payload,
                    **self._request_timeout_kwargs(),
                )
            continuation_response.raise_for_status()
            data = continuation_response.json()
            choices = data.get("choices", [])
            if not choices:
                return partial_answer + "\n\n[回答因长度限制被截断]"
            message = choices[0].get("message", {})
            continuation_text = str(message.get("content") or "").strip()
            if not continuation_text:
                return partial_answer + "\n\n[回答因长度限制被截断]"
            return partial_answer.rstrip() + "\n" + continuation_text
        except Exception:
            return partial_answer + "\n\n[回答因长度限制被截断]"

    def chat_with_tools_sync(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = 4096,
        tool_choice: str = "auto",
    ) -> dict[str, Any]:
        """Run a single-turn chat completion with tool-calling support.

        Returns a dict with:
        - content: str | None - the text content if no tool calls
        - tool_calls: list[dict] - tool call requests from the model
        - finish_reason: str - why the model stopped

        The caller is responsible for executing tool calls and feeding
        results back into messages for the next turn.
        """
        if not tools:
            raise ValueError("chat_with_tools_sync requires at least one tool")

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "temperature": temperature,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens

        # Disable thinking for tool-calling to reduce latency
        payload["chat_template_kwargs"] = {"enable_thinking": False}

        started_at = perf_counter()
        try:
            raise_if_request_cancelled(minimum_remaining_seconds=0.1)
            with self._request_completion_client() as request_client:
                response = request_client.post(
                    "/chat/completions",
                    json=payload,
                    **self._request_timeout_kwargs(),
                )
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            raise
        except Exception as exc:
            self._record_request_error(exc)
            raise

        elapsed_ms = (perf_counter() - started_at) * 1000.0
        usage = data.get("usage") or {}
        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or 0)
        self._record_request_success(
            latency_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
        self._last_request_usage = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

        choices = data.get("choices", [])
        if not choices:
            return {"content": None, "tool_calls": [], "finish_reason": "no_choices"}

        message = choices[0].get("message", {})
        finish_reason = str(choices[0].get("finish_reason") or "").strip().lower()
        content = message.get("content")
        tool_calls = message.get("tool_calls") or []

        # Normalize tool_calls format
        normalized_calls = []
        for tc in tool_calls:
            func = tc.get("function", {})
            args_str = func.get("arguments", "{}")
            try:
                arguments = json.loads(args_str) if isinstance(args_str, str) else args_str
            except json.JSONDecodeError:
                arguments = {"raw": args_str}
            normalized_calls.append({
                "id": tc.get("id", f"call_{len(normalized_calls)}"),
                "name": func.get("name", ""),
                "arguments": arguments,
            })

        return {
            "content": content,
            "tool_calls": normalized_calls,
            "finish_reason": finish_reason,
        }

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return await asyncio.to_thread(self.answer_question_sync, system_prompt, user_prompt)

    def _extract_json_object(self, text: str) -> dict[str, Any]:
        cleaned = text.strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match is None:
                raise RuntimeError(
                    "vllm-hust did not return JSON for booking intent classification"
                ) from None
            payload = json.loads(match.group(0))

        if not isinstance(payload, dict):
            raise RuntimeError(
                "vllm-hust returned a non-object JSON payload for booking intent classification"
            )
        return payload

    def _parse_interaction_intent_payload(self, content: str) -> _InteractionIntentPayload:
        try:
            payload = self._extract_json_object(content)
            return _InteractionIntentPayload.model_validate(payload)
        except (RuntimeError, ValidationError) as exc:
            repaired_content = self._repair_interaction_intent_payload(content, str(exc))
            repaired_payload = self._extract_json_object(repaired_content)
            return _InteractionIntentPayload.model_validate(repaired_payload)

    def _parse_shadow_plan_candidate(self, content: str) -> ShadowPlanCandidate:
        try:
            payload = self._extract_json_object(content)
            return ShadowPlanCandidate.model_validate(payload)
        except (RuntimeError, ValidationError) as exc:
            repaired_content = self._repair_shadow_plan_candidate(content, str(exc))
            repaired_payload = self._extract_json_object(repaired_content)
            return ShadowPlanCandidate.model_validate(repaired_payload)

    def _repair_interaction_intent_payload(self, content: str, validation_error: str) -> str:
        system_prompt = (
            "You repair JSON emitted by an interaction-intent classifier. "
            "Preserve the original intent meaning, but return valid JSON only. "
            "The JSON object must include exactly these keys: action, domain, retrieval_scopes, exclude_scopes, decision_mode, needs_clarification, clarification_message, escalation_reason. "
            "retrieval_scopes and exclude_scopes must be JSON arrays of strings."
        )
        user_prompt = (
            "Original classifier output:\n"
            f"{content}\n\n"
            "Validation error:\n"
            f"{validation_error}\n\n"
            "Rewrite the JSON so it is valid without adding explanations."
        )
        return self.answer_question_sync(
            system_prompt,
            user_prompt,
            temperature=0.0,
            max_tokens=220,
        )

    def _repair_shadow_plan_candidate(self, content: str, validation_error: str) -> str:
        step_registry = get_default_step_registry()
        allowed_step_ids = ", ".join(sorted(step_registry))
        system_prompt = (
            "You repair JSON emitted by a shadow workflow planner. Return valid JSON only. "
            "The JSON object must include exactly these keys: goal, fallback_template, step_ids, allowed_sources, "
            "requires_citations, explain_to_operator. step_ids and allowed_sources must be JSON arrays of strings."
        )
        user_prompt = (
            "Original shadow planner output:\n"
            f"{content}\n\n"
            "Validation error:\n"
            f"{validation_error}\n\n"
            f"Allowed step_ids: {allowed_step_ids}\n"
            "Rewrite the JSON so it is valid without adding explanations."
        )
        return self.answer_question_sync(
            system_prompt,
            user_prompt,
            temperature=0.0,
            max_tokens=220,
        )

    def _coerce_interaction_intent(self, payload: _InteractionIntentPayload) -> InteractionIntent:
        action = payload.action
        domain = payload.domain
        decision_mode = payload.decision_mode
        retrieval_scopes = payload.retrieval_scopes or _DEFAULT_RETRIEVAL_SCOPES.get(domain, [])
        exclude_scopes = payload.exclude_scopes or _DEFAULT_EXCLUDE_SCOPES.get(domain, [])
        if decision_mode is None:
            decision_mode = (
                action if action in {"review_queue", "human_handoff"} else "direct_answer"
            )
        normalized_payload = {
            "action": action,
            "domain": domain,
            "retrieval_scopes": [item for item in retrieval_scopes if item.strip()],
            "exclude_scopes": [item for item in exclude_scopes if item.strip()],
            "decision_mode": str(decision_mode),
            "needs_clarification": payload.needs_clarification,
            "clarification_message": payload.clarification_message,
            "escalation_reason": payload.escalation_reason,
            "confidence": self._derive_confidence(
                action=action,
                decision_mode=str(decision_mode),
                needs_clarification=payload.needs_clarification,
            ),
        }
        return InteractionIntent.model_validate(normalized_payload)

    def _derive_confidence(
        self, *, action: str, decision_mode: str, needs_clarification: bool
    ) -> float:
        if needs_clarification or action == "ask_followup":
            return 0.45
        if action == "human_handoff" or decision_mode == "human_handoff":
            return 0.92
        if action == "review_queue" or decision_mode == "review_queue":
            return 0.85
        if action == "book_meeting":
            return 0.8
        if decision_mode == "advise_only":
            return 0.72
        return 0.68

    def _normalize_interaction_intent(
        self,
        question: str,
        course_context: str | None,
        intent: InteractionIntent,
        *,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        lowered = question.lower()
        course_context_text = (course_context or "").lower()
        recent_block = (recent_session_context or "").strip()

        # Task 2 guardrails: undo the model's reflexive ask_followup choice when
        # we have enough material to answer. These run BEFORE every other
        # specialized branch so a demoted intent flows through the normal
        # research / advising / teaching dispatch below.
        if intent.action == "ask_followup":
            if recent_block and len(question.strip()) < 25:
                intent = intent.model_copy(
                    update={
                        "action": "answer",
                        "decision_mode": "direct_answer"
                        if intent.decision_mode in {"", "review_queue"}
                        else intent.decision_mode,
                        "needs_clarification": False,
                        "clarification_message": None,
                        "confidence": max(intent.confidence, 0.7),
                    }
                )
            elif _has_research_anchor(lowered, question):
                intent = intent.model_copy(
                    update={
                        "action": "answer",
                        "domain": "research",
                        "retrieval_scopes": ["publications", "profile"],
                        "exclude_scopes": ["courseware"],
                        "decision_mode": "direct_answer"
                        if intent.decision_mode == ""
                        else intent.decision_mode,
                        "needs_clarification": False,
                        "clarification_message": None,
                        "confidence": max(intent.confidence, 0.78),
                    }
                )
            elif _has_advising_anchor(lowered, question):
                intent = intent.model_copy(
                    update={
                        "action": "answer",
                        "domain": "advising",
                        "retrieval_scopes": ["preparation", "meeting_policy", "profile"],
                        "exclude_scopes": ["courseware"],
                        "decision_mode": "advise_only",
                        "needs_clarification": False,
                        "clarification_message": None,
                        "confidence": max(intent.confidence, 0.75),
                    }
                )

        # Task 2: demote review_queue to advise_only when the topic is a
        # clearly answerable academic question.
        if intent.decision_mode == "review_queue" or intent.action == "review_queue":
            if _looks_like_answerable_academic_question(lowered, question):
                intent = intent.model_copy(
                    update={
                        "action": "answer",
                        "domain": "advising"
                        if intent.domain in {"", "general"}
                        else intent.domain,
                        "retrieval_scopes": ["preparation", "profile", "publications"],
                        "exclude_scopes": ["courseware"],
                        "decision_mode": "advise_only",
                        "needs_clarification": False,
                        "clarification_message": None,
                        "escalation_reason": None,
                        "confidence": max(intent.confidence, 0.8),
                    }
                )

        if _looks_like_booking_request(lowered, question):
            return intent.model_copy(
                update={
                    "action": "book_meeting",
                    "domain": "booking",
                    "retrieval_scopes": ["meeting_policy"],
                    "exclude_scopes": ["courseware", "publications"],
                    "decision_mode": "review_queue",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.99),
                }
            )

        if _looks_like_human_handoff_request(lowered, question):
            return intent.model_copy(
                update={
                    "action": "human_handoff",
                    "decision_mode": "human_handoff",
                    "domain": "advising",
                    "retrieval_scopes": [],
                    "exclude_scopes": [],
                    "needs_clarification": False,
                    "clarification_message": None,
                    "escalation_reason": "涉及敏感、紧急或必须由老师本人直接处理的事项。",
                    "confidence": max(intent.confidence, 0.98),
                }
            )

        if _looks_like_review_queue_request(lowered, question):
            return intent.model_copy(
                update={
                    "action": "review_queue",
                    "decision_mode": "review_queue",
                    "domain": "advising",
                    "retrieval_scopes": ["meeting_policy", "profile"],
                    "exclude_scopes": ["courseware"],
                    "needs_clarification": False,
                    "clarification_message": None,
                    "escalation_reason": "这是需要老师审核后才能正式答复的请求。",
                    "confidence": max(intent.confidence, 0.95),
                }
            )

        if _looks_like_mixed_course_research_boundary_question(lowered, question):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "advising",
                    "retrieval_scopes": ["preparation", "meeting_policy", "profile"],
                    "exclude_scopes": ["courseware"],
                    "decision_mode": "advise_only",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.92),
                }
            )

        if _looks_like_progress_followup_advising_question(lowered, question, course_context_text):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "advising",
                    "retrieval_scopes": ["preparation", "meeting_policy", "profile"],
                    "exclude_scopes": ["courseware"],
                    "decision_mode": "advise_only",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.92),
                }
            )

        if _looks_like_teaching_question(lowered, question):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "teaching",
                    "retrieval_scopes": ["courseware"],
                    "exclude_scopes": ["publications"],
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.95),
                }
            )

        if _looks_like_advising_question(lowered, question, course_context_text):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "advising",
                    "retrieval_scopes": ["preparation", "meeting_policy", "profile"],
                    "exclude_scopes": ["courseware"],
                    "decision_mode": "advise_only",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.9),
                }
            )

        if _looks_like_booking_information_question(lowered, question):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "advising",
                    "retrieval_scopes": ["meeting_policy", "profile"],
                    "exclude_scopes": ["courseware"],
                    "decision_mode": "direct_answer",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.9),
                }
            )

        if _looks_like_decision_request(lowered, question):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "advising",
                    "retrieval_scopes": ["profile", "meeting_policy"],
                    "exclude_scopes": ["courseware"],
                    "decision_mode": "advise_only",
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.85),
                }
            )

        if _looks_like_research_question(lowered, question):
            return intent.model_copy(
                update={
                    "action": "answer",
                    "domain": "research",
                    "retrieval_scopes": ["publications", "profile"],
                    "exclude_scopes": ["courseware"],
                    "needs_clarification": False,
                    "clarification_message": None,
                    "confidence": max(intent.confidence, 0.9),
                }
            )

        if intent.action == "book_meeting" and intent.domain != "booking":
            return intent.model_copy(
                update={
                    "domain": "booking",
                    "retrieval_scopes": ["meeting_policy"],
                    "exclude_scopes": ["courseware", "publications"],
                }
            )

        return intent

    def _cache_key(self, payload: dict[str, Any], *, namespace: str | None = None) -> str:
        base = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if namespace:
            return f"{namespace}::{base}"
        return base

    def _get_cached_response(self, cache_key: str, semantic_key: str) -> tuple[str | None, str | None]:
        ttl_seconds = self._settings.llm_cache_ttl_seconds
        max_entries = self._settings.llm_cache_max_entries
        if ttl_seconds <= 0 or max_entries <= 0:
            return None, None

        now = time.time()
        with self._cache_lock:
            self._record_cache_lookup()
            self._evict_expired_locked(now)
            cached = self._response_cache.get(cache_key)
            if cached is None:
                # Conversation-scoped answers must never use fuzzy reuse. A
                # near-identical prompt can still represent a different turn,
                # user, or follow-up; returning an older generated answer here
                # causes cross-conversation and second-turn contamination.
                if self._cache_key_namespace(cache_key) is not None:
                    return None, None
                best_key = ""
                best_score = 0.0
                best_value = ""
                for existing_key, (expires_at, value, existing_semantic_key) in self._response_cache.items():
                    if expires_at <= now:
                        continue
                    score = difflib.SequenceMatcher(
                        None,
                        semantic_key,
                        existing_semantic_key,
                    ).ratio()
                    if score > best_score:
                        best_key = existing_key
                        best_score = score
                        best_value = value
                if best_score >= _SEMANTIC_CACHE_SIMILARITY_THRESHOLD and best_key:
                    self._response_cache.move_to_end(best_key)
                    return best_value, "semantic"
                return None, None
            expires_at, value, _ = cached
            if expires_at <= now:
                self._response_cache.pop(cache_key, None)
                return None, None
            self._response_cache.move_to_end(cache_key)
            return value, "exact"

    @staticmethod
    def _cache_key_namespace(cache_key: str) -> str | None:
        if cache_key.startswith("{") or "::" not in cache_key:
            return None
        namespace, _ = cache_key.split("::", 1)
        return namespace or None

    def _store_cached_response(self, cache_key: str, value: str, semantic_key: str) -> None:
        ttl_seconds = self._settings.llm_cache_ttl_seconds
        max_entries = self._settings.llm_cache_max_entries
        if ttl_seconds <= 0 or max_entries <= 0:
            return

        expires_at = time.time() + ttl_seconds
        with self._cache_lock:
            self._evict_expired_locked(time.time())
            self._response_cache[cache_key] = (expires_at, value, semantic_key)
            self._response_cache.move_to_end(cache_key)
            while len(self._response_cache) > max_entries:
                self._response_cache.popitem(last=False)

    def _evict_expired_locked(self, now: float) -> None:
        expired_keys = [
            key for key, (expires_at, _, _) in self._response_cache.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._response_cache.pop(key, None)

    def _semantic_cache_key(self, payload: dict[str, Any], *, namespace: str | None = None) -> str:
        messages = payload.get("messages") or []
        system_text = ""
        user_texts: list[str] = []
        if isinstance(messages, list):
            for message in messages:
                if not isinstance(message, dict):
                    continue
                role = message.get("role", "")
                content = message.get("content")
                if not content:
                    continue
                if role == "system":
                    system_text = str(content)
                else:
                    user_texts.append(str(content))
        if not user_texts and not system_text:
            return self._cache_key(payload)
        # Prioritize user content (contains the actual question) at the start
        # of the key so it is never truncated. Include only a short fingerprint
        # of the system prompt for differentiation.
        user_merged = "\n".join(user_texts).lower()
        user_merged = re.sub(r"\d+", "#", user_merged)
        user_merged = re.sub(r"[\s\W_]+", "", user_merged)
        system_finger = ""
        if system_text:
            normalized_system = re.sub(r"[\s\W_]+", "", system_text.lower())
            system_finger = normalized_system[:80]
        merged = user_merged + "|" + system_finger
        if namespace:
            merged = f"{namespace}::{merged}"
        if len(merged) > 2400:
            return merged[:2400]
        return merged

    def _record_request_start(self) -> None:
        with self._metrics_lock:
            self._request_count += 1
            self._last_request_at = time.time()

    def _record_request_success(
        self,
        *,
        latency_ms: float | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
    ) -> None:
        self._ensure_runtime_state()
        with self._metrics_lock:
            now = time.time()
            self._success_count += 1
            self._last_success_at = now
            self._last_error_message = None
            self._recent_success_timestamps.append(now)
            if completion_tokens > 0:
                self._recent_completion_token_samples.append((now, completion_tokens))
            self._trim_throughput_samples_locked(now)
            if latency_ms is not None and latency_ms >= 0:
                self._latency_total_ms += latency_ms
                self._latency_observation_count += 1
                self._last_latency_ms = latency_ms
                self._latency_max_ms = max(self._latency_max_ms, latency_ms)
            if prompt_tokens > 0:
                self._prompt_tokens_total += prompt_tokens
            if completion_tokens > 0:
                self._completion_tokens_total += completion_tokens
            if total_tokens > 0:
                self._total_tokens_total += total_tokens

    def _record_request_error(self, exc: Exception) -> None:
        with self._metrics_lock:
            self._error_count += 1
            self._last_error_at = time.time()
            msg = str(exc).strip()
            if not msg:
                msg = type(exc).__name__
            self._last_error_message = msg[:240]

    def _record_cache_hit(self, hit_type: str) -> None:
        with self._metrics_lock:
            self._cache_hit_count += 1
            if hit_type == "semantic":
                self._semantic_cache_hit_count += 1
            else:
                self._exact_cache_hit_count += 1

    def _record_cache_lookup(self) -> None:
        with self._metrics_lock:
            self._cache_lookup_count += 1

    def _trim_throughput_samples_locked(self, now: float) -> None:
        cutoff = now - _THROUGHPUT_WINDOW_SECONDS
        while self._recent_success_timestamps and self._recent_success_timestamps[0] < cutoff:
            self._recent_success_timestamps.popleft()
        while (
            self._recent_completion_token_samples
            and self._recent_completion_token_samples[0][0] < cutoff
        ):
            self._recent_completion_token_samples.popleft()

    def _compute_recent_request_throughput_locked(self, now: float) -> float:
        if not self._recent_success_timestamps:
            return 0.0
        oldest = self._recent_success_timestamps[0]
        duration = max(min(now - oldest, _THROUGHPUT_WINDOW_SECONDS), 1.0)
        return len(self._recent_success_timestamps) / duration

    def _compute_recent_completion_throughput_locked(self, now: float) -> float:
        if not self._recent_completion_token_samples:
            return 0.0
        oldest = self._recent_completion_token_samples[0][0]
        duration = max(min(now - oldest, _THROUGHPUT_WINDOW_SECONDS), 1.0)
        token_total = sum(sample_tokens for _, sample_tokens in self._recent_completion_token_samples)
        return token_total / duration

    def _active_cache_entries(self) -> int:
        with self._cache_lock:
            self._evict_expired_locked(time.time())
            return len(self._response_cache)

    def _derive_vllm_metrics_url(self, base_url: str) -> str:
        try:
            parsed = urlsplit(base_url)
            if not parsed.scheme or not parsed.netloc:
                return ""
            return urlunsplit((parsed.scheme, parsed.netloc, "/metrics", "", ""))
        except Exception:
            return ""

    def _resolve_vllm_metrics_url(self) -> str:
        explicit_metrics_url = str(getattr(self._settings, "vllm_metrics_url", "") or "").strip()
        if explicit_metrics_url:
            return explicit_metrics_url
        return self._derive_vllm_metrics_url(str(getattr(self._settings, "llm_base_url", "")))

    def _refresh_vllm_prefix_cache_metrics(self) -> None:
        self._ensure_runtime_state()
        if not self._vllm_metrics_url:
            return
        now = time.time()
        if now - self._vllm_metrics_last_refresh_at < _VLLM_METRICS_REFRESH_SECONDS:
            return
        try:
            response = self._client.get(self._vllm_metrics_url, timeout=1.5)
            response.raise_for_status()
            text = response.text
        except Exception:
            self._vllm_metrics_last_refresh_at = now
            return

        prefix_queries = 0.0
        prefix_hits = 0.0
        external_prefix_queries = 0.0
        external_prefix_hits = 0.0
        num_requests_running = self._vllm_num_requests_running
        num_requests_waiting = self._vllm_num_requests_waiting
        kv_cache_usage_perc = self._vllm_kv_cache_usage_perc
        request_success_total = 0.0
        request_error_total = 0.0
        prompt_tokens_prom = 0.0
        generation_tokens_prom = 0.0
        avg_gen_throughput = 0.0
        avg_ttft = 0.0
        avg_tpot = 0.0
        for line in text.splitlines():
            if not line or line.startswith("#"):
                continue
            metric_name, _, metric_value = line.partition(" ")
            if not metric_value:
                continue
            metric_name = metric_name.split("{", 1)[0]
            try:
                value = float(metric_value.strip())
            except ValueError:
                continue
            if metric_name in ("vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total"):
                prefix_queries += value
            elif metric_name in ("vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total"):
                prefix_hits += value
            elif metric_name in (
                "vllm:external_prefix_cache_queries",
                "vllm:external_prefix_cache_queries_total",
            ):
                external_prefix_queries += value
            elif metric_name in (
                "vllm:external_prefix_cache_hits",
                "vllm:external_prefix_cache_hits_total",
            ):
                external_prefix_hits += value
            elif metric_name in (
                "vllm:num_requests_running",
                "vllm:num_requests_running_requests",
            ):
                num_requests_running = value
            elif metric_name in (
                "vllm:num_requests_waiting",
                "vllm:request_queue_size",
            ):
                num_requests_waiting = value
            elif metric_name in (
                "vllm:kv_cache_usage_perc",
                "vllm:gpu_cache_usage_perc",
                "vllm:gpu_kv_cache_usage_perc",
            ):
                kv_cache_usage_perc = value / 100.0 if value > 1.0 else value
            elif metric_name in (
                "vllm:request_success_total",
                "vllm:request_success",
            ):
                request_success_total += value
            elif metric_name in (
                "vllm:request_error_total",
                "vllm:request_failure_total",
            ):
                request_error_total += value
            elif metric_name in ("vllm:prompt_tokens_total",):
                prompt_tokens_prom += value
            elif metric_name in ("vllm:generation_tokens_total",):
                generation_tokens_prom += value
            elif metric_name in (
                "vllm:avg_generation_throughput_tokens_per_second",
                "vllm:generation_throughput_tokens_per_second",
            ):
                avg_gen_throughput = value
            elif metric_name in (
                "vllm:avg_time_to_first_token_seconds",
                "vllm:time_to_first_token_seconds",
            ):
                avg_ttft = value
            elif metric_name in (
                "vllm:avg_time_per_output_token_seconds",
                "vllm:time_per_output_token_seconds",
            ):
                avg_tpot = value

        self._vllm_prefix_cache_queries = prefix_queries
        self._vllm_prefix_cache_hits = prefix_hits
        self._vllm_external_prefix_cache_queries = external_prefix_queries
        self._vllm_external_prefix_cache_hits = external_prefix_hits
        self._vllm_num_requests_running = max(0.0, num_requests_running)
        self._vllm_num_requests_waiting = max(0.0, num_requests_waiting)
        self._vllm_kv_cache_usage_perc = min(max(0.0, kv_cache_usage_perc), 1.0)
        self._vllm_request_success_total = max(0.0, request_success_total)
        self._vllm_request_error_total = max(0.0, request_error_total)
        self._vllm_prompt_tokens_prom_total = max(0.0, prompt_tokens_prom)
        self._vllm_generation_tokens_prom_total = max(0.0, generation_tokens_prom)
        self._vllm_avg_generation_throughput_tps = max(0.0, avg_gen_throughput)
        self._vllm_avg_time_to_first_token_s = max(0.0, avg_ttft)
        self._vllm_avg_time_per_output_token_s = max(0.0, avg_tpot)
        self._vllm_metrics_last_refresh_at = now

    def _apply_serving_policy_to_payload(
        self,
        payload: dict[str, Any],
        *,
        deadline_class: str | None,
        request_priority: int | None,
        target_e2e_ms: float | None,
    ) -> None:
        if not self._settings.llm_policy_enabled:
            return
        if "max_tokens" not in payload:
            return

        requested_max_tokens = int(payload.get("max_tokens") or 0)
        if requested_max_tokens <= 0:
            return

        self._refresh_vllm_prefix_cache_metrics()

        policy_max_tokens_cap = max(1, int(self._settings.llm_policy_output_max_tokens_cap))

        policy = serving_policy.variant_policy_for(
            self._settings.llm_policy_variant_kind,
            self._settings.llm_policy_variant_name,
        )
        serving_context = {
            "deadline_class": str(deadline_class or "batch-standard"),
            "priority": int(request_priority if request_priority is not None else 50),
            "max_tokens": requested_max_tokens,
            "target_e2e_ms": float(target_e2e_ms) if target_e2e_ms is not None else None,
        }
        snapshot = {
            "num_requests_running": float(self._vllm_num_requests_running),
            "num_requests_waiting": float(self._vllm_num_requests_waiting),
            "kv_cache_usage_perc": float(self._vllm_kv_cache_usage_perc),
        }

        # Low-congestion path: keep the requested token budget (no shaping),
        # but still enforce a global policy ceiling to avoid pathological long outputs.
        if not self._is_high_congestion(snapshot):
            payload["max_tokens"] = int(min(requested_max_tokens, policy_max_tokens_cap))
            return

        controller, _ = serving_policy.resolve_deadline_class_max_tokens(
            type("_Args", (), {"deadline_class_max_tokens": None})(),
            policy,
        )
        caps, _ = serving_policy.deadline_class_max_tokens_for_request(
            policy,
            controller,
            snapshot,
            event={"serving_context": serving_context},
        )
        _, effective_max_tokens = serving_policy.effective_output_len(serving_context, caps)
        shaped_max_tokens = int(
            max(1, min(requested_max_tokens, int(effective_max_tokens), policy_max_tokens_cap))
        )

        min_tokens_floor = max(1, int(self._settings.llm_policy_output_min_tokens_floor))
        if (
            str(serving_context.get("deadline_class") or "").startswith("interactive")
            and requested_max_tokens >= min_tokens_floor
        ):
            shaped_max_tokens = max(shaped_max_tokens, min_tokens_floor)

        payload["max_tokens"] = int(
            min(requested_max_tokens, policy_max_tokens_cap, shaped_max_tokens)
        )

    def _is_high_congestion(self, snapshot: dict[str, float]) -> bool:
        num_running = float(snapshot.get("num_requests_running") or 0.0)
        num_waiting = float(snapshot.get("num_requests_waiting") or 0.0)
        kv_usage = float(snapshot.get("kv_cache_usage_perc") or 0.0)

        if num_waiting >= float(self._settings.llm_policy_congestion_waiting_threshold):
            return True
        if kv_usage >= float(self._settings.llm_policy_congestion_kv_usage_threshold):
            return True
        if (num_running + num_waiting) >= float(
            self._settings.llm_policy_congestion_total_requests_threshold
        ):
            return True
        return False

    def _format_timestamp(self, timestamp: float | None) -> str:
        if timestamp is None:
            return ""
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(timestamp))

    def _sleep_before_retry(self, retry_number: int) -> None:
        delay_seconds = self._settings.llm_retry_backoff_seconds * (2 ** max(0, retry_number - 1))
        remaining = request_remaining_seconds(reserve_seconds=0.25)
        if remaining is not None:
            if remaining <= delay_seconds:
                raise_if_request_cancelled(minimum_remaining_seconds=delay_seconds)
            delay_seconds = min(delay_seconds, remaining)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

    def _request_timeout_kwargs(self) -> dict[str, float]:
        """Override the shared client timeout only inside a budgeted request."""

        if request_remaining_seconds() is None:
            return {}
        return {
            "timeout": bounded_request_timeout(float(self._settings.llm_timeout_seconds))
        }


def _looks_like_booking_request(lowered: str, question: str) -> bool:
    if _looks_like_booking_information_question(lowered, question):
        return False
    booking_markers = (
        "请帮我预约",
        "帮我预约",
        "请预约",
        "我要预约",
        "我想预约",
        "预定",
        "约时间",
        "约个会",
        "book",
        "schedule a meeting",
        "预约",
    )
    return any(marker in lowered for marker in booking_markers) or any(
        marker in question for marker in booking_markers
    )


def _looks_like_booking_information_question(lowered: str, question: str) -> bool:
    del lowered
    return asks_for_booking_information(question)


def _looks_like_teaching_question(lowered: str, question: str) -> bool:
    if _looks_like_mixed_course_research_boundary_question(lowered, question):
        return False
    if bool(re.search(r"第\s*\d+\s*讲", question)):
        return True
    teaching_markers = ("tutorial", "lecture", "experiment", "assignment", "course")
    if any(marker in lowered for marker in teaching_markers):
        return True
    return any(
        marker in question
        for marker in (
            "讲义",
            "课件",
            "实验",
            "教程",
            "习题",
            "课上问题",
            "作业",
            "端到端",
            "kernel",
            "性能分析",
        )
    )


def _looks_like_mixed_course_research_boundary_question(lowered: str, question: str) -> bool:
    has_course = any(marker in lowered for marker in ("course", "assignment")) or any(
        marker in question for marker in ("课程", "作业", "课上问题")
    )
    has_research = any(marker in lowered for marker in ("research", "paper", "project")) or any(
        marker in question for marker in ("研究", "科研", "论文", "项目")
    )
    has_boundary = any(marker in lowered for marker in ("separately", "split", "together")) or any(
        marker in question
        for marker in ("分开准备", "分开", "分别", "一次都问完", "一起问", "一起聊")
    )
    return has_course and has_research and has_boundary


def _looks_like_advising_question(lowered: str, question: str, course_context_text: str) -> bool:
    advising_markers = (
        "准备什么",
        "提前准备",
        "怎么准备",
        "先准备",
        "先整理",
        "怎么组织",
        "agenda",
        "blocker",
        "更看重哪些准备",
        "反馈更集中",
        "带 draft",
        "初稿",
        "合作前",
        "合作空间",
        "先补什么",
        "安排阅读顺序",
        "下一步",
        "继续补实验",
    )
    scope_markers = (
        "怎么收窄",
        "收窄",
        "收拢结构",
        "题目太大",
        "砍掉哪些",
        "最先会删什么",
    )
    contact_markers = (
        "发邮件",
        "线下聊",
        "当面聊",
        "分开准备",
        "更合适",
        "哪几天",
        "什么时候方便",
    )
    role_markers = ("老师", "导师", "meeting", "预约", "约时间", "找您", "邮件", "线下")
    if any(marker in question for marker in advising_markers) and any(
        marker in question for marker in role_markers
    ):
        return True
    if any(marker in question for marker in scope_markers) and any(
        marker in question for marker in ("老师", "研究", "项目", "题目", "论文", "摘要")
    ):
        return True
    if any(marker in question for marker in contact_markers) and any(
        marker in question for marker in role_markers
    ):
        return True
    if "科研指导" in course_context_text and any(
        marker in question
        for marker in ("准备", "收窄", "整理", "下一步", "发邮件", "线下", "合作")
    ):
        return True
    return False


def _looks_like_progress_followup_advising_question(
    lowered: str,
    question: str,
    course_context_text: str,
) -> bool:
    progress_markers = (
        "上次已被建议",
        "整理成三类",
        "下一步",
        "先发邮件还是继续补实验",
        "减少来回修改",
    )
    contact_markers = ("老师", "请教老师", "邮件", "补实验", "继续补实验", "当前进展")
    has_progress = any(marker in question for marker in progress_markers) or any(
        marker in lowered for marker in progress_markers
    )
    has_contact_context = any(marker in question for marker in contact_markers) or any(
        marker in lowered for marker in contact_markers
    )
    if has_progress and has_contact_context:
        return True
    return "lamp personalization benchmark" in course_context_text and has_progress


def _looks_like_decision_request(lowered: str, question: str) -> bool:
    markers = (
        "帮我决定",
        "替我决定",
        "直接告诉我",
        "该不该",
        "怎么选",
        "选哪个",
        "应该选",
    )
    return any(marker in lowered for marker in markers) or any(
        marker in question for marker in markers
    )


def _looks_like_review_queue_request(lowered: str, question: str) -> bool:
    del lowered
    return requires_faculty_review(question)


def _looks_like_human_handoff_request(lowered: str, question: str) -> bool:
    del lowered
    return requires_human_handoff(question)


def _looks_like_research_question(lowered: str, question: str) -> bool:
    research_markers = (
        "研究主线",
        "研究方向",
        "主要研究",
        "研究什么",
        "做什么研究",
        "科研",
        "flowrag",
        "libamm",
        "publication",
        "publications",
        "research",
    )
    return any(marker in lowered for marker in research_markers) or any(
        marker in question for marker in research_markers
    )


_RESEARCH_ANCHOR_TOKENS: tuple[str, ...] = (
    "kv",
    "kv cache",
    "kvpr",
    "ttft",
    "vllm",
    "sage",
    "neuromem",
    "sagevdb",
    "batching",
    "scheduling",
    "inference",
    "prefill",
    "decoding",
    "throughput",
    "latency",
    "flowrag",
    "libamm",
    "论文",
    "文献",
    "汇报",
    "研究",
    "写作",
    "推理",
    "复用",
    "重计算",
    "开题",
    "组会",
    "投稿",
    "实验",
    "benchmark",
)


_ADVISING_ANCHOR_TOKENS: tuple[str, ...] = (
    "准备",
    "如何整理",
    "怎么整理",
    "看论文",
    "读论文",
    "选研究",
    "选方向",
    "选课题",
    "叙事",
    "招生",
    "招什么",
    "meeting",
    "office hour",
)


_ANSWERABLE_ACADEMIC_TOKENS: tuple[str, ...] = (
    "顶会",
    "叙事",
    "如何整理",
    "怎么整理",
    "如何选",
    "怎么选",
    "研究方向",
    "研究目标",
    "研究方法",
    "文献阅读",
    "读论文",
    "看论文",
    "写论文",
    "论文写作",
    "论文叙事",
    "组会",
    "开题",
    "汇报",
)


def _has_research_anchor(lowered: str, question: str) -> bool:
    return any(token in lowered for token in _RESEARCH_ANCHOR_TOKENS) or any(
        token in question for token in _RESEARCH_ANCHOR_TOKENS
    )


def _has_advising_anchor(lowered: str, question: str) -> bool:
    return any(token in lowered for token in _ADVISING_ANCHOR_TOKENS) or any(
        token in question for token in _ADVISING_ANCHOR_TOKENS
    )


def _looks_like_answerable_academic_question(lowered: str, question: str) -> bool:
    return any(token in lowered for token in _ANSWERABLE_ACADEMIC_TOKENS) or any(
        token in question for token in _ANSWERABLE_ACADEMIC_TOKENS
    )
