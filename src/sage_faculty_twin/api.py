import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from datetime import date
from io import BytesIO
from pathlib import Path
from uuid import uuid4

from . import __version__
from .runtime_env import bootstrap_runtime_env

bootstrap_runtime_env(require_policy=True, require_fastapi=False)

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sage.edge.app import create_app as create_edge_app
from sage.runtime.work_journal import WorkCapacityError
from sage.runtime.dependency_verifier import DependencyRejected, DependencyVerifier

from .auth import (
    ADMIN_COOKIE_NAME,
    USER_COOKIE_NAME,
    clear_admin_session_cookie,
    clear_user_session_cookie,
    set_admin_session_cookie,
    set_user_session_cookie,
)
from .api_token_store import LabMemberApiTokenStore
from .config import settings
from .conversation_trace_store import ConversationTraceStore
from .workflow_events import WorkflowEventBroker
from .slack_workflow import SlackAnswerRecovery, SlackIngress, SlackReply, SlackWorkflow, slack_reply_method
from .request_context import (
    RequestCancellationController,
    RequestCancelledError,
    request_cancellation_scope,
)
from .request_timing import RequestTimingLedger
from .models import (
    AdminLoginRequest,
    AdminSessionResponse,
    AnonymousSuggestionCreate,
    AnonymousSuggestionRecord,
    ArtifactMemoryDraftRecordResponse,
    AvailabilitySchedule,
    BookingDecisionRequest,
    BookingRecord,
    BookingRequest,
    BookingProposalDecision,
    BookingResponse,
    ChatAttachment,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatFeedbackOperationResponse,
    ChatRequest,
    ChatResponse,
    ExchangeExecutionResponse,
    CodeAssistRequest,
    CodeAssistResponse,
    CodeCommandRequest,
    CodeCommandResponse,
    CodeContextRequest,
    CodeContextResponse,
    CodeDirectoryListRequest,
    CodeDirectoryListResponse,
    CodeFileReadRequest,
    CodeFileReadResponse,
    CodeGitDiffRequest,
    CodeGitDiffResponse,
    CodeGitStatusRequest,
    CodeGitStatusResponse,
    CodeProposeRequest,
    CodeProposeResponse,
    CodeSessionAppendMessageRequest,
    CodeSessionCreateRequest,
    CodeSessionListResponse,
    CodeSessionRecord,
    CodeSearchRequest,
    CodeSearchResponse,
    CodeWorkspaceListResponse,
    ConversationHistoryListResponse,
    ConversationTranscriptResponse,
    EscalationDecisionRequest,
    EscalationRecord,
    FollowUpDispatchResponse,
    FollowUpQueueRecord,
    KnowledgeDocumentActionResponse,
    KnowledgeDocumentCreate,
    KnowledgeDocumentRecord,
    KnowledgeDocumentReviewRequest,
    KnowledgeDocumentReviewSummary,
    KnowledgeGapDraftCreateRequest,
    KnowledgeGapDraftRecordResponse,
    KnowledgeSearchResponse,
    LocalCodeConfigRequest,
    LocalCodeConfigResponse,
    MemoryProfileListResponse,
    OnlinePresenceHeartbeatRequest,
    OnlinePresenceHeartbeatResponse,
    OperationsOverviewResponse,
    OperationsTaskStateRecord,
    OperationsTaskStateUpdateRequest,
    OperationsWorkbenchResponse,
    QuestionAnalyticsReportResponse,
    RuntimeFeatureFlagsResponse,
    RuntimeFeatureFlagsUpdateRequest,
    ServiceControlResponse,
    SlackTwinLinkCodeResponse,
    SlackTwinLinkStatusResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserSessionResponse,
    WorkflowReplayReportResponse,
)
from .code_workbench import CODE_WORKBENCH_PROFILES
from .history_auth import resolve_authenticated_history_email
from .chat_identity import ChatIdentity
from .http_authority import HttpAuthorityResolver
from .http_workflow import HttpIngress, HttpWorkflow, HttpWorkRejected, public_http_failure
from sage.runtime.async_work_service import WorkStartTimeoutError
from .guest_identity import GUEST_COOKIE_NAME
from .service import (
    DigitalTwinService,
    PromptCapacityError,
    build_stack_versions_payload,
    build_hardware_payload,
)
from .capability_plugins import CapabilityPluginRegistry, CapabilityPluginStatus
from .slack_link_store import SlackUserLinkStore
from .slack_channel_context import (
    SlackHistoryUnavailable,
    asks_about_prior_context,
    fetch_slack_channel_context,
)


_logger = logging.getLogger(__name__)
STARTUP_WARMUP_MAX_WAIT_SECONDS = 5.0


def configure_local_cors(target_app: FastAPI) -> None:
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=[],
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Trace-Id"],
    )


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    try:
        await startup_event()
        yield
    finally:
        await shutdown_event()


llm_app = FastAPI(title="Sage Mate", version="1.1", lifespan=_app_lifespan)
configure_local_cors(llm_app)


class LazyDigitalTwinService:
    """Delay heavy service construction until endpoints actually need it."""

    def __init__(self) -> None:
        self._instance: DigitalTwinService | None = None
        self._lock = threading.Lock()

    def is_initialized(self) -> bool:
        return self._instance is not None

    def ensure_initialized(self) -> DigitalTwinService:
        if self._instance is not None:
            return self._instance
        with self._lock:
            if self._instance is None:
                self._instance = DigitalTwinService(settings)
        return self._instance

    def reset(self) -> None:
        with self._lock:
            self._instance = None

    def __getattr__(self, name: str):
        return getattr(self.ensure_initialized(), name)

    def __setattr__(self, name: str, value: object) -> None:
        # Private attributes (_instance, _lock) belong to the proxy itself.
        # Everything else is forwarded to the underlying DigitalTwinService
        # so that test fixtures (``service._llm_client = stub``) correctly
        # mutate the real service instance.
        if name.startswith("_") and name in self.__dict__:
            object.__setattr__(self, name, value)
        elif name.startswith("_"):
            # Use object.__getattribute__ to avoid __getattr__ recursion.
            inst = object.__getattribute__(self, "__dict__").get("_instance")
            if inst is None:
                object.__setattr__(self, name, value)
            else:
                setattr(inst, name, value)
        else:
            setattr(self.ensure_initialized(), name, value)


service = LazyDigitalTwinService()
slack_link_store = SlackUserLinkStore(settings.slack_user_link_dir)
lab_member_api_token_store = LabMemberApiTokenStore(settings)
web_dir = Path(__file__).with_name("web")
NO_STORE_HEADERS = {"Cache-Control": "no-store, no-cache, must-revalidate"}
MAX_CHAT_ATTACHMENTS = 4
MAX_CHAT_ATTACHMENT_BYTES = 5 * 1024 * 1024
MAX_CHAT_ATTACHMENT_TEXT_CHARS = 12000
# Hard upper bound for one /chat round-trip. Slightly under Cloudflare's free
# tier 100s edge-timeout so we can return a structured 504 with workflow
# trace progress before the proxy gives up. Tune via
# ``DIGITAL_TWIN_CHAT_REQUEST_TIMEOUT_SECONDS`` if the upstream LLM is
# consistently slower than this budget.
CHAT_REQUEST_TIMEOUT_SECONDS = settings.chat_request_timeout_seconds
CHAT_LONG_REQUEST_TIMEOUT_SECONDS = settings.long_chat_request_timeout_seconds
_streamed_chat_request: ContextVar[bool] = ContextVar("streamed_chat_request", default=False)
CHAT_MAX_INFLIGHT_REQUESTS = settings.chat_max_inflight_requests
CHAT_ADMISSION_TIMEOUT_SECONDS = settings.chat_admission_timeout_seconds


# Chat Latency Optimizations Task 4: SSE keepalive cadence on the
# ``/chat/workflow-events`` stream. While ``/chat`` is in-flight the LLM
# stage may not emit a trace step for tens of seconds; without a heartbeat
# Cloudflare's idle proxy timeout (~100s on the free plan) can drop the SSE
# connection mid-answer. We emit a typed ``{"type": "keepalive"}`` event
# every ``CHAT_SSE_KEEPALIVE_SECONDS`` seconds so the connection stays warm.
CHAT_SSE_KEEPALIVE_SECONDS = settings.chat_sse_keepalive_seconds
# Upstream chunks are buffered until validation; the public SSE publishes
# only the delivered answer, never an unvalidated partial attempt.
STREAM_CHAT_ANSWER = settings.stream_chat_answer
CONVERSATION_TRACE_RETENTION_DAYS = int(
    os.environ.get("DIGITAL_TWIN_CONVERSATION_TRACE_RETENTION_DAYS", "365")
)
_conversation_trace_stores: dict[tuple[Path, Path | None], ConversationTraceStore] = {}
_conversation_trace_stores_lock = threading.Lock()


def get_conversation_trace_store() -> ConversationTraceStore:
    explicit_db = os.environ.get("DIGITAL_TWIN_CONVERSATION_TRACE_DB", "").strip()
    if explicit_db:
        db_path = Path(explicit_db)
    else:
        # The dynamic environment lookup keeps pytest's per-test runtime
        # isolation effective even though this module is imported at collection time.
        isolated_memory_dir = os.environ.get("DIGITAL_TWIN_CONVERSATION_MEMORY_DIR", "").strip()
        if isolated_memory_dir:
            db_path = Path(isolated_memory_dir).parent / "conversation_traces" / "traces.sqlite3"
        else:
            db_path = settings.runtime_dir / "data/conversation_traces/traces.sqlite3"
    db_path = db_path.expanduser().resolve()
    archive_value = os.environ.get(
        "DIGITAL_TWIN_CONVERSATION_TRACE_ARCHIVE_DB", ""
    ).strip()
    archive_db_path = Path(archive_value).expanduser().resolve() if archive_value else None
    store_key = (db_path, archive_db_path)
    with _conversation_trace_stores_lock:
        store = _conversation_trace_stores.get(store_key)
        if store is None:
            store = ConversationTraceStore(
                db_path,
                retention_days=CONVERSATION_TRACE_RETENTION_DAYS,
                archive_db_path=archive_db_path,
            )
            _conversation_trace_stores[store_key] = store
    return store
SLACK_TWIN_SIGNING_SECRET = os.environ.get("SLACK_TWIN_SIGNING_SECRET", "").strip()
SLACK_TWIN_TEAM_ID = os.environ.get("SLACK_TWIN_TEAM_ID", "").strip()
# Explicit authorization pairs only (T123:U456); bare IDs grant no access.
SLACK_TWIN_ALLOWED_USER_IDS = {
    user_id.strip()
    for user_id in os.environ.get("SLACK_TWIN_ALLOWED_USER_IDS", "").split(",")
    if user_id.strip()
}
SLACK_TWIN_VISITOR_PROFILE = os.environ.get("SLACK_TWIN_VISITOR_PROFILE", "lab_member").strip()
SLACK_TWIN_RESPONSE_TIMEOUT_SECONDS = float(
    os.environ.get("SLACK_TWIN_RESPONSE_TIMEOUT_SECONDS", "95")
)
SLACK_TWIN_BIND_CODE_TTL_SECONDS = int(os.environ.get("SLACK_TWIN_BIND_CODE_TTL_SECONDS", "600"))
SLACK_TWIN_BOT_TOKEN = (
    os.environ.get("SLACK_TWIN_BOT_TOKEN", "").strip()
    or os.environ.get("TWIN_MONITOR_SLACK_BOT_TOKEN", "").strip()
)
SUPPORTED_CHAT_ATTACHMENT_SUFFIXES = {
    ".pdf",
    ".txt",
    ".md",
    ".csv",
    ".json",
    ".py",
    ".yaml",
    ".yml",
    ".log",
}
SUPPORTED_CHAT_ATTACHMENT_MEDIA_TYPES = {
    "application/json",
    "application/pdf",
    "application/x-yaml",
    "text/csv",
    "text/markdown",
    "text/plain",
    "text/x-python",
}


def _owned_chat_request_key(owner_id: str, request_id: str) -> str:
    """Internal channel identity; public request IDs are not capabilities."""
    if not owner_id:
        raise ValueError("chat_request_requires_trusted_owner")
    return hashlib.sha256(json.dumps([owner_id, request_id], separators=(",", ":")).encode()).hexdigest()


workflow_event_broker = WorkflowEventBroker(
    lambda: service._conversation_store.state_store,
    heartbeat_provider=lambda: CHAT_SSE_KEEPALIVE_SECONDS,
)
slack_workflow = None
slack_dependency = None
_slack_verified_configuration = None
http_workflow = None


def _create_http_workflow(instance):
    from sage.runtime.event_journal import EventJournal

    state = instance._conversation_store.state_store
    resolver = HttpAuthorityResolver(settings=instance._settings, users=instance._user_store,
        tokens=lab_member_api_token_store, guests=instance._guest_identity_store)
    events = EventJournal(state, namespace="twin-workflow-events-v1")
    broker = WorkflowEventBroker(lambda: state)

    async def authorize(ingress):
        await asyncio.to_thread(resolver.resolve, ingress.authority, ingress.request)
        return True

    async def recover(ingress):
        cursor = 0
        while True:
            page = await asyncio.to_thread(events.read, ingress.delivery.stream_id, after=cursor, limit=100)
            for event in page["events"]:
                if event["id"] == "answer_done":
                    return ChatResponse.model_validate(event["payload"]["response"])
            if page["caught_up"]:
                return None
            cursor = page["cursor"]

    async def answer(ingress, *, admission_guard):
        budget = (CHAT_LONG_REQUEST_TIMEOUT_SECONDS if ingress.request.deep_thinking
                  and ingress.request.deep_thinking_explicit else CHAT_REQUEST_TIMEOUT_SECONDS)
        controller = RequestCancellationController()
        key = ingress.delivery.stream_id
        try:
            with request_cancellation_scope(controller, deadline_at=time.monotonic() + budget):
                # Native SAGE worker capacity owns queueing. This budget starts
                # after the first claim; observer admission waits are separate.
                async with asyncio.timeout(budget):
                    await authorize(ingress)
                    return await instance.answer(
                        ingress.request, delivery=ingress.delivery,
                        admin_session_reference=ingress.authority.admin_session,
                        admission_guard=admission_guard,
                        trace_callback=lambda step: broker.publish_step(key, step),
                        answer_chunk_callback=_untraced_answer_chunk_callback(),
                    )
        except PromptCapacityError as exc:
            controller.cancel()
            raise HttpWorkRejected("prompt_capacity") from exc
        except RequestCancelledError as exc:
            controller.cancel()
            raise HttpWorkRejected("request_cancelled") from exc
        except HTTPException as exc:
            controller.cancel()
            # Service-level rejections have already exhausted their own
            # validation/retry policy. Persist only the safe classification.
            code = {403: "authority_denied", 413: "prompt_capacity", 503: "answer_unavailable"}.get(
                exc.status_code, "invalid_request")
            raise HttpWorkRejected(code) from exc
        except BaseException:
            controller.cancel()
            raise

    return HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                        cancellation_targets=instance.cancellation_targets(),
                        max_workers=CHAT_MAX_INFLIGHT_REQUESTS)


def _raise_chat_validation_error(exc: ValidationError) -> None:
    raise RequestValidationError(exc.errors()) from exc


def _coerce_optional_form_value(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truncate_attachment_text(text: str) -> str:
    normalized = text.strip()
    if len(normalized) <= MAX_CHAT_ATTACHMENT_TEXT_CHARS:
        return normalized
    return normalized[: MAX_CHAT_ATTACHMENT_TEXT_CHARS - 9].rstrip() + "\n[已截断]"


def _extract_pdf_text(content: bytes, file_name: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise HTTPException(status_code=500, detail="当前环境缺少 PDF 解析依赖 pypdf。") from exc

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"无法读取 PDF 文件：{file_name}") from exc

    text = "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"PDF 文件没有可提取的文本内容：{file_name}")
    return _truncate_attachment_text(text)


def _extract_text_attachment(content: bytes, file_name: str) -> str:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"文本附件必须使用 UTF-8 编码：{file_name}"
        ) from exc

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail=f"附件没有可用文本内容：{file_name}")
    return _truncate_attachment_text(text)


def _extract_chat_attachment_text(file_name: str, media_type: str, content: bytes) -> str:
    suffix = Path(file_name).suffix.lower()
    normalized_media_type = (media_type or "application/octet-stream").lower()

    if suffix == ".pdf" or normalized_media_type == "application/pdf":
        return _extract_pdf_text(content, file_name)

    if (
        suffix in SUPPORTED_CHAT_ATTACHMENT_SUFFIXES
        or normalized_media_type.startswith("text/")
        or normalized_media_type in SUPPORTED_CHAT_ATTACHMENT_MEDIA_TYPES
    ):
        return _extract_text_attachment(content, file_name)

    raise HTTPException(
        status_code=400,
        detail=f"暂只支持 PDF、TXT、MD、CSV、JSON、PY、YAML、LOG 文件：{file_name}",
    )


async def _parse_chat_attachments(files: list[object]) -> list[ChatAttachment]:
    if len(files) > MAX_CHAT_ATTACHMENTS:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {MAX_CHAT_ATTACHMENTS} 个附件。")

    attachments: list[ChatAttachment] = []
    for upload in files:
        if (
            not hasattr(upload, "filename")
            or not hasattr(upload, "read")
            or not hasattr(upload, "close")
        ):
            continue
        file_name = (upload.filename or "").strip()
        if not file_name:
            raise HTTPException(status_code=400, detail="上传文件必须带文件名。")

        content = await upload.read()
        await upload.close()
        if not content:
            raise HTTPException(status_code=400, detail=f"附件为空：{file_name}")
        if len(content) > MAX_CHAT_ATTACHMENT_BYTES:
            raise HTTPException(status_code=400, detail=f"附件超过 5MB 限制：{file_name}")

        media_type = (
            upload.content_type or "application/octet-stream"
        ).strip() or "application/octet-stream"
        text_content = _extract_chat_attachment_text(file_name, media_type, content)
        try:
            attachments.append(
                ChatAttachment(
                    file_name=file_name,
                    media_type=media_type,
                    size_bytes=len(content),
                    text_content=text_content,
                )
            )
        except ValidationError as exc:
            _raise_chat_validation_error(exc)

    return attachments


async def _parse_chat_request(raw_request: Request) -> ChatRequest:
    content_type = raw_request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        form = await raw_request.form()
        files = [item for item in form.getlist("files") if hasattr(item, "filename")]
        payload = {
            "student_name": _coerce_optional_form_value(form.get("student_name")),
            "student_email": _coerce_optional_form_value(form.get("student_email")),
            "course_context": _coerce_optional_form_value(form.get("course_context")),
            "visitor_profile": _coerce_optional_form_value(form.get("visitor_profile")),
            "question": _coerce_optional_form_value(form.get("question")),
            "conversation_id": _coerce_optional_form_value(form.get("conversation_id")),
            "deep_thinking": _coerce_optional_form_value(form.get("deep_thinking")) not in ("false", "0", None),
            "deep_thinking_explicit": _coerce_optional_form_value(
                form.get("deep_thinking_explicit")
            )
            not in ("false", "0", None),
            "web_search": _coerce_optional_form_value(form.get("web_search"))
            in ("true", "1", "on", "yes") if form.get("web_search") is not None else None,
            "attachments": await _parse_chat_attachments(files),
        }
        if form.get("web_mode") is not None:
            payload["web_mode"] = _coerce_optional_form_value(form.get("web_mode"))
        payload["visitor_profile"] = _normalize_visitor_profile(payload.get("visitor_profile"))
        try:
            return ChatRequest.model_validate(payload)
        except ValidationError as exc:
            _raise_chat_validation_error(exc)

    try:
        payload = await raw_request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="聊天请求体不是合法 JSON。") from exc

    if isinstance(payload, dict):
        payload["visitor_profile"] = _normalize_visitor_profile(payload.get("visitor_profile"))
    try:
        return ChatRequest.model_validate(payload)
    except ValidationError as exc:
        _raise_chat_validation_error(exc)


def _normalize_visitor_profile(value: object) -> object:
    """Accept common public aliases without weakening profile validation."""
    aliases = {
        "guest": "general_visitor",
        "visitor": "general_visitor",
        "student": "hust_undergraduate",
    }
    normalized = str(value or "").strip().lower()
    return aliases.get(normalized, value)


def _resolve_chat_identity(
    raw_request: Request,
    requested_visitor_profile: str | None,
    refresh: bool = False,
) -> ChatIdentity:
    cached = getattr(raw_request.state, "chat_identity", None)
    if cached is not None and not refresh:
        if cached.kind != "anonymous":
            return cached
        return ChatIdentity(kind="anonymous", subject_id=cached.subject_id,
                            visitor_profile=("general_visitor" if requested_visitor_profile == "lab_member"
                                             else requested_visitor_profile))
    authorization = raw_request.headers.get("authorization", "").strip()
    if authorization:
        scheme, _, token = authorization.partition(" ")
        identity = (
            lab_member_api_token_store.authenticate(token.strip())
            if scheme.lower() == "bearer" and token.strip()
            else None
        )
        if identity is None:
            raise HTTPException(status_code=401, detail="无效或过期的 API token。")
        return ChatIdentity(kind="api_token", subject_id=identity.token_id,
                            visitor_profile=identity.visitor_profile)
    session_token = raw_request.cookies.get(USER_COOKIE_NAME)
    user_session = service.get_user_session(session_token) if session_token else None
    if user_session is not None and user_session.is_authenticated and user_session.account is not None:
        account = user_session.account
        return ChatIdentity(kind="account", subject_id=account.user_id,
                            visitor_profile=account.visitor_profile,
                            account_email=account.email, account_name=account.name)
    if refresh:
        credential = (getattr(raw_request.state, "new_guest_credential", None)
                      or raw_request.cookies.get(GUEST_COOKIE_NAME))
        guest = service._guest_identity_store.authenticate(credential) if credential else None
        if guest is None:
            raise HTTPException(status_code=401, detail="当前身份已失效，请重新验证。")
        return ChatIdentity(kind="anonymous", subject_id=guest.subject_id,
                            visitor_profile=requested_visitor_profile)
    credential, guest = service._guest_identity_store.resolve(raw_request.cookies.get(GUEST_COOKIE_NAME))
    if credential is not None:
        raw_request.state.new_guest_credential = credential
    return ChatIdentity(kind="anonymous", subject_id=guest.subject_id, visitor_profile=(
        "general_visitor"
        if requested_visitor_profile == "lab_member"
        else requested_visitor_profile
    ))


def _deliver_guest_cookie(raw_request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    credential = getattr(raw_request.state, "new_guest_credential", None)
    if credential is not None:
        response.set_cookie(GUEST_COOKIE_NAME, credential, httponly=True, secure=True,
                            samesite="lax", path="/", max_age=settings.user_session_ttl_seconds)


def _require_user_account(raw_request: Request):
    session = service.get_user_session(raw_request.cookies.get(USER_COOKIE_NAME))
    if not session.is_authenticated or session.account is None:
        raise HTTPException(status_code=403, detail="需要先登录用户账号。")
    return session.account


def _slack_link_status_for_account(account) -> SlackTwinLinkStatusResponse:
    is_lab_member = account.visitor_profile == "lab_member"
    link = slack_link_store.get_link_for_user(account.user_id)
    if not is_lab_member:
        return SlackTwinLinkStatusResponse(
            is_authenticated=True,
            is_lab_member=False,
            can_link=False,
            linked=False,
            message="需要邀请码升级为课题组成员后才能绑定 Slack /twin。",
        )
    return SlackTwinLinkStatusResponse(
        is_authenticated=True,
        is_lab_member=True,
        can_link=True,
        linked=link is not None,
        slack_user_id=link.slack_user_id if link else None,
        linked_at=link.linked_at if link else None,
        message="Slack /twin 已绑定。" if link else "可以生成 Slack 绑定码。",
    )


def _verify_slack_signature(raw_body: bytes, request: Request) -> None:
    if not SLACK_TWIN_SIGNING_SECRET:
        raise HTTPException(status_code=503, detail="Slack twin command is not configured.")

    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    try:
        request_ts = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Slack request timestamp.") from exc

    if abs(time.time() - request_ts) > 60 * 5:
        raise HTTPException(status_code=401, detail="Stale Slack request.")

    base = b"v0:" + str(request_ts).encode("ascii") + b":" + raw_body
    expected = "v0=" + hmac.new(
        SLACK_TWIN_SIGNING_SECRET.encode("utf-8"),
        base,
        hashlib.sha256,
    ).hexdigest()
    if not secrets.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid Slack signature.")


def _post_slack_api(method: str, payload: dict[str, object]) -> dict[str, object]:
    if not SLACK_TWIN_BOT_TOKEN:
        raise RuntimeError("Slack bot token is not configured.")
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {SLACK_TWIN_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    response = urllib.request.urlopen(req, timeout=8)
    body = response.read().decode("utf-8")
    parsed = json.loads(body) if body else {}
    if not isinstance(parsed, dict):
        raise RuntimeError("slack_invalid_response")
    if parsed.get("ok") is not True:
        if parsed.get("error") in {"invalid_auth", "not_authed", "token_revoked", "account_inactive"}:
            raise DependencyRejected("slack_authentication_rejected")
        raise RuntimeError(f"Slack API {method} failed: {parsed.get('error', 'unknown_error')}")
    return parsed


def _slack_configuration():
    # Only retained in memory; never passed to the journal or diagnostics.
    return (SLACK_TWIN_TEAM_ID, SLACK_TWIN_BOT_TOKEN, SLACK_TWIN_SIGNING_SECRET)


def _slack_dependency_ready():
    return (slack_dependency is not None
            and slack_workflow is not None
            and _slack_verified_configuration == _slack_configuration()
            and slack_dependency.work_status().ready
            and slack_workflow.work_status().ready
            and slack_dependency.ready())


def _slack_readiness():
    if not any(_slack_configuration()):
        return "disabled"
    if slack_dependency is None:
        return "not_started"
    if _slack_verified_configuration != _slack_configuration():
        return "configuration_changed"
    if (not slack_dependency.work_status().ready
            or (slack_workflow is not None and not slack_workflow.work_status().ready)):
        return "worker_unavailable"
    return slack_dependency.status()["state"]


def _http_readiness():
    if http_workflow is None:
        return "not_started"
    workers = tuple(http_workflow.work_status().values())
    if any(status.phase in {"failed", "stopped", "draining", "closed"}
           or status.execution_context_running is False for status in workers):
        return "degraded"
    return "ready" if all(status.ready for status in workers) else "not_started"


def _create_slack_dependency(state, **options):
    configuration = _slack_configuration()

    def verify():
        if configuration != _slack_configuration():
            raise DependencyRejected("slack_configuration_changed")
        identity = _post_slack_api("auth.test", {})
        if configuration != _slack_configuration() or identity.get("team_id") != configuration[0]:
            raise DependencyRejected("slack_bot_workspace_mismatch")
        return True

    return DependencyVerifier(state, name="twin-slack-workspace", probe=verify, **options)


def _require_slack_destination(team_id):
    if not SLACK_TWIN_TEAM_ID or not SLACK_TWIN_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Slack workspace credentials are not configured.")
    if team_id != SLACK_TWIN_TEAM_ID:
        raise HTTPException(status_code=403, detail="Slack workspace is not configured.")
    if not _slack_dependency_ready():
        raise HTTPException(status_code=503, detail="Slack workspace verification is not ready.",
                            headers={"Retry-After": "2", "Cache-Control": "no-store"})


async def _deliver_slack_reply(ingress, reply):
    _require_slack_destination(ingress.team_id)
    # All currently authorized Slack answers use the requester's member scope,
    # including private retrieval and personal context. Neither an app mention
    # nor a historical private_reply=False grants the channel that access.
    # Enforce the audience at the final transport boundary on every retry.
    method = slack_reply_method(ingress)
    payload = {"channel": ingress.channel_id, "markdown_text": reply.text,
               "link_names": False, "parse": "none"}
    if method == "chat.postEphemeral":
        payload["user"] = ingress.user_id
    # A mention can be the root of a thread that does not yet exist. Slack only
    # displays ephemeral thread replies in an already active thread. Keep the
    # thread identity in durable ingress, but deliver in the same channel so
    # an initial mention does not become an accepted-but-invisible reply.
    result = await asyncio.to_thread(_post_slack_api, method, payload)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("slack_delivery_requires_success_response")
    metadata = result.get("response_metadata")
    warnings = [result.get("warning")]
    if isinstance(metadata, dict) and isinstance(metadata.get("warnings"), list):
        warnings.extend(metadata["warnings"])
    if "message_truncated" in warnings:
        raise ValueError("slack_delivery_truncated")
    stamp = result.get("ts" if method == "chat.postMessage" else "message_ts")
    if not isinstance(stamp, str) or not re.fullmatch(r"[0-9]+\.[0-9]+", stamp):
        raise ValueError("slack_delivery_requires_message_receipt")
    if method == "chat.postMessage" and result.get("channel") != ingress.channel_id:
        raise ValueError("slack_delivery_receipt_destination_mismatch")
    return {"accepted": True, "message_ts": stamp}


def _slack_twin_link_instructions() -> str:
    return (
        "请先登录 twin 网页端账号，生成 Slack 绑定码，然后在 Slack 输入 "
        "`/twin bind 你的绑定码` 完成绑定。"
    )


def _normalize_slack_twin_question(text: str, *, bot_user_ids=()) -> str:
    """Consume verified invocation prefixes, never rewrite the question body."""
    question = text
    while True:
        mention = re.match(r"^[ \t\r\n]*<@([A-Za-z0-9]+)(?:\|[^>\r\n]*)?>", question)
        if mention is None or mention.group(1) not in bot_user_ids:
            break
        question = question[mention.end():]
        # Slack's ordinary invocation separator is one space. Preserve every
        # remaining byte, including code indentation and trailing line breaks.
        if question.startswith(" "):
            question = question[1:]
    return question if question.strip() else ""


async def _bind_slack_ingress(ingress, code):
    if slack_workflow is None:
        raise HTTPException(status_code=503, detail="Slack workflow is not ready.")
    try:
        await slack_workflow.bind(ingress, slack_link_store, code)
    except WorkCapacityError:
        raise HTTPException(status_code=503, detail="服务队列已满，本次请求未接收；请稍后重试。",
                            headers={"Retry-After": "2", "Cache-Control": "no-store"}) from None
    except ValueError as exc:
        if str(exc) == "slack_binding_code_invalid":
            await _admit_slack(ingress, notice="绑定码无效或已过期。请回到 twin 网页端重新生成 Slack 绑定码。")
        elif str(exc) == "journal_commit_conflict":
            # A previously rejected code has a durable failure notice. Replay
            # that immutable decision; do not turn the retry into a new bind.
            try:
                if await slack_workflow.resume_if_admitted(ingress):
                    return
            except ValueError:
                pass
            raise HTTPException(status_code=409, detail="Slack binding request conflict.") from None
        elif str(exc) == "slack_binding_operation_conflict":
            raise HTTPException(status_code=409, detail="Slack binding request conflict.") from None
        else:
            raise


def _resolve_slack_twin_identity(user_id: str, *, team_id: str) -> tuple[str, str | None, str | None]:
    visitor_profile = SLACK_TWIN_VISITOR_PROFILE or "lab_member"
    user_email = None
    linked_account = slack_link_store.get_link_for_slack_user(user_id, team_id=team_id)
    if linked_account is None:
        if f"{team_id}:{user_id}" in SLACK_TWIN_ALLOWED_USER_IDS:
            return visitor_profile, user_email, None
        return visitor_profile, user_email, _slack_twin_link_instructions()
    current_account = service.get_slack_link_account(linked_account.user_id)
    if current_account is None or current_account.visitor_profile != "lab_member":
        return (
            visitor_profile,
            user_email,
            "已绑定账号不是课题组成员。需要邀请码升级为课题组成员后才能使用 Slack /twin。",
        )
    return current_account.visitor_profile, current_account.email, None


def _format_slack_twin_answer(
    response: ChatResponse,
    *,
    question: str | None = None,
    slack_message_count: int = 0,
    slack_history_unavailable: bool = False,
) -> str:
    lines: list[str] = []
    normalized_question = (question or "").strip()
    if normalized_question:
        # Keep question markup in its own container. An unmatched user fence
        # must not swallow the generated answer or its source links.
        quoted = "\n> ".join(normalized_question.splitlines())
        lines.append(f"> 你问：{quoted}\n")
    lines.append(response.answer)
    if response.answer_basis:
        basis = "；".join(item.title for item in response.answer_basis)
        if basis:
            lines.append(f"\n依据：{basis}")
    if slack_message_count:
        lines.append(f"\nSlack 上下文：已读取当前会话最近 {slack_message_count} 条消息。")
    elif slack_history_unavailable:
        lines.append("\nSlack 上下文：本次未能读取频道历史，仅回答了当前问题。")
    if response.used_model:
        lines.append(f"\n模型：`{response.used_model}`")
    return "\n".join(line for line in lines if line)


async def _authorize_slack_ingress(ingress):
    _require_slack_destination(ingress.team_id)
    _, _, denial = await asyncio.to_thread(_resolve_slack_twin_identity,
                                           ingress.user_id, team_id=ingress.team_id)
    return denial


def _render_slack_reply(response, ingress):
    return (_format_slack_twin_answer(response, question=ingress.question)
            + f"\n查询状态：`/twin status {ingress.request_id}`")


async def _slack_status_text(ingress, words):
    if len(words) != 2 or not re.fullmatch(r"slack-[a-f0-9]{64}", words[1]):
        return "用法：`/twin status 请求编号`，或私信 `status 请求编号`。"
    if slack_workflow is None:
        raise HTTPException(status_code=503, detail="Slack workflow is not ready.")
    status = await slack_workflow.status(ingress, words[1])
    labels = {"failed": "处理已失败并停止重试，请联系管理员检查；本次请求不会自动重跑。",
              "done": "处理完成，Slack 已接受投递（不代表消息已被阅读）。",
              "pending": "仍在处理或等待重试。"}
    text = "未找到当前账号在此会话中的请求。" if status is None else labels[status["status"]]
    if status is not None:
        text += "\n" + "；".join(f"{name}: {task['status']}，尝试 {task['attempt']} 次"
                                  for name, task in status["tasks"].items())
    return text


async def _produce_slack_reply(ingress, *, admission_guard):
    _require_slack_destination(ingress.team_id)
    role, email, denial = await asyncio.to_thread(_resolve_slack_twin_identity,
                                                 ingress.user_id, team_id=ingress.team_id)
    if denial is not None:
        return SlackReply(text=denial)
    slack_context = None
    try:
        slack_context = await asyncio.to_thread(fetch_slack_channel_context,
            bot_token=SLACK_TWIN_BOT_TOKEN, channel_id=ingress.channel_id, latest_ts=ingress.latest_ts)
    except SlackHistoryUnavailable as exc:
        _logger.warning("Slack channel history unavailable: %s", exc.code)
    if asks_about_prior_context(ingress.question) and (slack_context is None or not slack_context.message_count):
        return SlackReply(text="我已接入 Twin，但目前读不到当前 Slack 会话的历史消息，不能可靠地总结前文。请确认 Bot 的历史读取权限。")
    request = ChatRequest(owner_id=ingress.owner_id, student_name=ingress.user_id,
        student_email=email, question=ingress.question, visitor_profile=role,
        course_context={"command": "Slack /twin", "message": "Slack DM", "mention": "Slack @twin"}[ingress.kind],
        slack_channel_context=slack_context.text if slack_context else None,
        conversation_id=ingress.request_id, deep_thinking=False, deep_thinking_explicit=False, web_search=False)
    cancellation = RequestCancellationController()
    deadline_at = time.perf_counter() + SLACK_TWIN_RESPONSE_TIMEOUT_SECONDS

    async def answer_with_context():
        # Use the same request context consumed by model I/O and the SAGE
        # pipeline. Cancelling only wait_for's task leaves synchronous I/O
        # without either a deadline or its registered cancellation signal.
        with request_cancellation_scope(cancellation, deadline_at=deadline_at):
            try:
                return await service.answer(request, delivery=ingress.delivery, admission_guard=admission_guard)
            except BaseException:
                cancellation.cancel()
                raise

    response = await asyncio.wait_for(answer_with_context(),
                                      timeout=SLACK_TWIN_RESPONSE_TIMEOUT_SECONDS)
    # Recovery renders this same immutable canonical answer; transient channel
    # history is never stored in the ingress or used to regenerate a committed turn.
    return SlackReply(text=_render_slack_reply(response, ingress))


def _create_slack_workflow(state):
    return SlackWorkflow(state, authorize=_authorize_slack_ingress,
        recover=SlackAnswerRecovery(state, render=_render_slack_reply),
        answer=_produce_slack_reply, deliver=_deliver_slack_reply,
        accept_task=lambda *_: _slack_dependency_ready(),
        max_attempts=int(os.environ.get("SLACK_TWIN_MAX_ATTEMPTS", "5")))


async def _admit_slack(ingress, *, notice=None):
    _require_slack_destination(ingress.team_id)
    if slack_workflow is None:
        raise HTTPException(status_code=503, detail="Slack workflow is not ready.")
    try:
        if notice is not None:
            await slack_workflow.submit_notice(ingress, SlackReply(text=notice))
        else:
            await slack_workflow.submit(ingress)
    except WorkCapacityError:
        raise HTTPException(status_code=503, detail="服务队列已满，本次请求未接收；请稍后重试。",
                            headers={"Retry-After": "2", "Cache-Control": "no-store"}) from None
    except ValueError as exc:
        if str(exc) == "journal_commit_conflict":
            raise HTTPException(status_code=409, detail="Slack request identity conflict.") from None
        raise


def frontend_asset(filename: str) -> FileResponse:
    return FileResponse(web_dir / filename, headers=NO_STORE_HEADERS)


def require_admin_session(request: Request) -> dict:
    return service.require_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))


def require_code_access(request: Request) -> dict:
    if (
        settings.deployment_mode == "local_code"
        and settings.code_workbench_enabled
        and settings.app_profile in CODE_WORKBENCH_PROFILES
    ):
        return {"mode": "local_code"}
    raise HTTPException(status_code=403, detail="Code tools require local Code Assistant mode.")


def require_code_session_access(request: Request) -> dict:
    if (
        settings.deployment_mode == "local_code"
        and settings.code_workbench_enabled
        and settings.app_profile in CODE_WORKBENCH_PROFILES
    ):
        return {"mode": "local_code", "profile": settings.app_profile}
    raise HTTPException(
        status_code=403,
        detail="Code sessions require the Code Assistant or Auto Scientist profile.",
    )


def require_local_code_config_access(request: Request) -> dict:
    client_host = request.client.host if request.client else ""
    allowed_hosts = {"127.0.0.1", "::1", "localhost"}
    try:
        allowed_hosts.add(socket.gethostbyname("localhost"))
    except Exception:
        pass
    if client_host not in allowed_hosts:
        raise HTTPException(status_code=403, detail="Local code setup is only available locally.")
    if settings.deployment_mode != "local_code":
        raise HTTPException(status_code=403, detail="Local code setup requires local_code mode.")
    return {"mode": "local_code"}


def _repo_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _read_env_values(env_path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _runtime_prefill_env_path() -> Path | None:
    explicit = os.environ.get("SAGE_MATE_PREFILL_ENV", "").strip()
    if explicit:
        explicit_path = Path(explicit).expanduser()
        if explicit_path.exists():
            return explicit_path
    return None


def _load_local_model_prefill() -> dict[str, str]:
    prefill_path = _runtime_prefill_env_path()
    if prefill_path is None:
        return {}
    values = _read_env_values(prefill_path)
    updates: dict[str, str] = {}
    base_url = values.get("DIGITAL_TWIN_LLM_BASE_URL") or values.get("VLLM_HUST_API_BASE_URL")
    api_key = values.get("DIGITAL_TWIN_API_KEY") or values.get("VLLM_HUST_API_KEY")
    model_name = values.get("DIGITAL_TWIN_MODEL_NAME") or values.get("VLLM_HUST_MODEL")
    if base_url:
        updates["DIGITAL_TWIN_LLM_BASE_URL"] = base_url
    if api_key:
        updates["DIGITAL_TWIN_API_KEY"] = api_key
    if model_name:
        updates["DIGITAL_TWIN_MODEL_NAME"] = model_name
    return updates


def _write_env_updates(env_path: Path, updates: dict[str, str]) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            next_lines.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            next_lines.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            next_lines.append(line)
    for key, value in updates.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")
    env_path.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")


def _sync_local_code_settings_from_env() -> None:
    values = _read_env_values(_repo_env_path())
    prefill_updates = _load_local_model_prefill()
    repaired: dict[str, str] = {}
    if prefill_updates:
        if values.get("DIGITAL_TWIN_LLM_BASE_URL", "").strip() in {
            "",
            "http://127.0.0.1:8000/v1",
        } and prefill_updates.get("DIGITAL_TWIN_LLM_BASE_URL"):
            repaired["DIGITAL_TWIN_LLM_BASE_URL"] = prefill_updates["DIGITAL_TWIN_LLM_BASE_URL"]
        if values.get("DIGITAL_TWIN_API_KEY", "").strip() in {"", "EMPTY"} and prefill_updates.get(
            "DIGITAL_TWIN_API_KEY"
        ):
            repaired["DIGITAL_TWIN_API_KEY"] = prefill_updates["DIGITAL_TWIN_API_KEY"]
        if not values.get("DIGITAL_TWIN_MODEL_NAME", "").strip() and prefill_updates.get(
            "DIGITAL_TWIN_MODEL_NAME"
        ):
            repaired["DIGITAL_TWIN_MODEL_NAME"] = prefill_updates["DIGITAL_TWIN_MODEL_NAME"]
        if repaired:
            _write_env_updates(_repo_env_path(), repaired)
            values.update(repaired)
    if not values:
        return
    settings.app_profile = values.get("DIGITAL_TWIN_APP_PROFILE", settings.app_profile)
    settings.deployment_mode = values.get("DIGITAL_TWIN_DEPLOYMENT_MODE", settings.deployment_mode)
    code_flag = values.get("DIGITAL_TWIN_CODE_WORKBENCH_ENABLED")
    if code_flag is not None:
        settings.code_workbench_enabled = code_flag.strip().lower() in {"1", "true", "yes", "on"}
    settings.code_workspace_roots = values.get(
        "DIGITAL_TWIN_CODE_WORKSPACE_ROOTS", settings.code_workspace_roots
    )
    runtime_dir = values.get("DIGITAL_TWIN_RUNTIME_DIR")
    if runtime_dir:
        settings.runtime_dir = Path(runtime_dir).expanduser()
        _apply_runtime_path_settings(str(settings.runtime_dir))
    settings.llm_base_url = values.get("DIGITAL_TWIN_LLM_BASE_URL", settings.llm_base_url)
    settings.model_name = values.get("DIGITAL_TWIN_MODEL_NAME", settings.model_name)
    settings.code_agent_backend = values.get(
        "DIGITAL_TWIN_CODE_AGENT_BACKEND", settings.code_agent_backend
    )
    settings.claude_hust_cli_path = values.get(
        "DIGITAL_TWIN_CLAUDE_HUST_CLI_PATH", settings.claude_hust_cli_path
    )
    if "DIGITAL_TWIN_API_KEY" in values:
        settings.api_key = values["DIGITAL_TWIN_API_KEY"]
    if not runtime_dir:
        settings.apply_runtime_dir_defaults()


def _local_code_config_response(*, message: str = "") -> LocalCodeConfigResponse:
    cli_status = _claude_hust_cli_status(settings.claude_hust_cli_path)
    return LocalCodeConfigResponse(
        app_profile=settings.app_profile,
        deployment_mode=settings.deployment_mode,
        code_workbench_enabled=(
            settings.code_workbench_enabled
            and settings.app_profile in CODE_WORKBENCH_PROFILES
        ),
        llm_base_url=settings.llm_base_url,
        api_key="" if not settings.api_key or settings.api_key == "EMPTY" else settings.api_key,
        api_key_set=bool(settings.api_key and settings.api_key != "EMPTY"),
        model_name=settings.model_name,
        runtime_dir=str(settings.runtime_dir),
        workspace_roots=[
            part.strip()
            for part in settings.code_workspace_roots.split(",")
            if part.strip()
        ],
        code_agent_backend=settings.code_agent_backend,
        claude_hust_cli_path=settings.claude_hust_cli_path,
        claude_hust_cli_available=cli_status["available"] == "true",
        claude_hust_cli_resolved_path=cli_status["resolved_path"],
        claude_hust_cli_issue=cli_status["issue"],
        message=message,
    )


def _claude_hust_cli_status(configured_path: str = "") -> dict[str, str]:
    candidates = [
        configured_path.strip(),
        shutil.which("claude-hust") or "",
        str(Path.home() / "Library/Application Support/Sage Mate/claude-code-hust/bin/claude-hust"),
        str(Path.home() / "claude-code-hust/bin/claude-hust"),
        str(Path.home() / "Documents/claude-code-hust/bin/claude-hust"),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if path.is_file() and os.access(path, os.X_OK):
            return {"available": "true", "resolved_path": str(path), "issue": ""}
    return {
        "available": "false",
        "resolved_path": "",
        "issue": "claude-hust CLI 不存在或不可执行。请设置 Claude Hust CLI Path。",
    }


def _runtime_path_env_updates(runtime_root: str) -> dict[str, str]:
    root = Path(runtime_root)
    return {
        "DIGITAL_TWIN_HOMEPAGE_DIR": str(root / "data/homepage"),
        "DIGITAL_TWIN_KNOWLEDGE_BASE_DIR": str(root / "data/knowledge_base"),
        "DIGITAL_TWIN_CONVERSATION_MEMORY_DIR": str(root / "data/conversation_memory"),
        "DIGITAL_TWIN_AVAILABILITY_SCHEDULE_PATH": str(root / "data/availability/current_week.json"),
        "DIGITAL_TWIN_INSTALLED_SKILL_PROMPT_PATH": str(root / "data/installed_skills/fixed_prompt_skills.md"),
        "DIGITAL_TWIN_USER_ACCOUNT_STORE_DIR": str(root / "data/user_accounts"),
        "DIGITAL_TWIN_CODE_SESSION_DIR": str(root / "data/code_sessions"),
        "DIGITAL_TWIN_CAPABILITY_PLUGIN_DIR": str(root / "data/capability_plugins"),
        "DIGITAL_TWIN_WORKFLOW_POLICY_PATH": str(root / "data/workflow_policies/faculty-default-2026-05.json"),
        "DIGITAL_TWIN_WORKFLOW_SCENARIO_PATH": str(root / "data/workflow_scenarios/v3_preview_scenarios.json"),
    }


def _apply_runtime_path_settings(runtime_root: str) -> None:
    root = Path(runtime_root)
    settings.homepage_dir = root / "data/homepage"
    settings.knowledge_base_dir = root / "data/knowledge_base"
    settings.conversation_memory_dir = root / "data/conversation_memory"
    settings.availability_schedule_path = root / "data/availability/current_week.json"
    settings.installed_skill_prompt_path = root / "data/installed_skills/fixed_prompt_skills.md"
    settings.user_account_store_dir = root / "data/user_accounts"
    settings.code_session_dir = root / "data/code_sessions"
    settings.capability_plugin_dir = root / "data/capability_plugins"
    settings.workflow_policy_path = root / "data/workflow_policies/faculty-default-2026-05.json"
    settings.workflow_scenario_path = root / "data/workflow_scenarios/v3_preview_scenarios.json"


def _apply_local_code_config(payload: LocalCodeConfigRequest) -> LocalCodeConfigResponse:
    app_profile = payload.app_profile.strip()
    code_enabled = app_profile in CODE_WORKBENCH_PROFILES
    runtime_dir = Path(payload.runtime_dir).expanduser()
    runtime_dir.mkdir(parents=True, exist_ok=True)
    resolved_runtime = str(runtime_dir.resolve())

    workspace_roots: list[str] = []
    for raw_root in payload.workspace_roots:
        if not raw_root.strip():
            continue
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            raise ValueError(f"Workspace does not exist: {raw_root}")
        workspace_roots.append(str(root.resolve()))
    workspace_csv = ",".join(workspace_roots) if code_enabled else ""

    requested_backend = (
        payload.code_agent_backend
        if "code_agent_backend" in payload.model_fields_set
        else settings.code_agent_backend
    )
    requested_cli_path = (
        (payload.claude_hust_cli_path or "").strip()
        if "claude_hust_cli_path" in payload.model_fields_set
        else settings.claude_hust_cli_path
    )

    updates = {
        "DIGITAL_TWIN_APP_PROFILE": app_profile,
        "DIGITAL_TWIN_DEPLOYMENT_MODE": "local_code",
        "DIGITAL_TWIN_CODE_WORKBENCH_ENABLED": "true" if code_enabled else "false",
        "DIGITAL_TWIN_CODE_WORKSPACE_ROOTS": workspace_csv,
        "DIGITAL_TWIN_RUNTIME_DIR": resolved_runtime,
        "DIGITAL_TWIN_LLM_BASE_URL": payload.llm_base_url.strip(),
        "DIGITAL_TWIN_MODEL_NAME": (payload.model_name or "").strip(),
        "DIGITAL_TWIN_LLM_USER_CONFIGURED": "true",
        "DIGITAL_TWIN_CODE_AGENT_BACKEND": requested_backend,
        "DIGITAL_TWIN_CLAUDE_HUST_CLI_PATH": requested_cli_path,
        "DIGITAL_TWIN_KNOWLEDGE_BACKEND": "neuromem",
        "DIGITAL_TWIN_NEUROMEM_INDEX_TYPE": "segment",
        "DIGITAL_TWIN_CONVERSATION_MEMORY_COLLECTION_TYPE": "unified",
        "DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE": "segment",
        "DIGITAL_TWIN_WARM_SERVICE_ON_STARTUP": "false",
    }
    updates.update(_runtime_path_env_updates(resolved_runtime))
    if payload.api_key is not None:
        updates["DIGITAL_TWIN_API_KEY"] = payload.api_key

    _write_env_updates(_repo_env_path(), updates)

    settings.app_profile = app_profile
    settings.deployment_mode = "local_code"
    settings.code_workbench_enabled = code_enabled
    settings.code_workspace_roots = workspace_csv if code_enabled else ""
    settings.runtime_dir = Path(resolved_runtime)
    _apply_runtime_path_settings(resolved_runtime)
    settings.llm_base_url = updates["DIGITAL_TWIN_LLM_BASE_URL"]
    settings.model_name = updates["DIGITAL_TWIN_MODEL_NAME"]
    settings.code_agent_backend = updates["DIGITAL_TWIN_CODE_AGENT_BACKEND"]
    settings.claude_hust_cli_path = updates["DIGITAL_TWIN_CLAUDE_HUST_CLI_PATH"]
    if payload.api_key is not None:
        settings.api_key = payload.api_key
    settings.knowledge_backend = "neuromem"
    settings.neuromem_index_type = "segment"
    settings.conversation_memory_collection_type = "unified"
    settings.conversation_memory_index_type = "segment"
    settings.warm_service_on_startup = False
    service.reset()
    return _local_code_config_response(message="Local code settings saved.")


llm_app.mount("/static", StaticFiles(directory=web_dir), name="static")


@llm_app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(web_dir / "index.html", headers=NO_STORE_HEADERS)


@llm_app.api_route("/styles.css", methods=["GET", "HEAD"], include_in_schema=False)
@llm_app.api_route("/styles.4217.css", methods=["GET", "HEAD"], include_in_schema=False)
@llm_app.api_route("/styles.4222.css", methods=["GET", "HEAD"], include_in_schema=False)
async def styles() -> FileResponse:
    return frontend_asset("styles.css")


@llm_app.get("/home", include_in_schema=False)
@llm_app.get("/home/", include_in_schema=False)
async def homepage_redirect() -> RedirectResponse:
    target = settings.homepage_public_url or "https://shuhaozhangtony.github.io/"
    return RedirectResponse(url=target, status_code=302)


@llm_app.api_route("/app.js", methods=["GET", "HEAD"], include_in_schema=False)
@llm_app.api_route("/app.4217.js", methods=["GET", "HEAD"], include_in_schema=False)
@llm_app.api_route("/app.4218.js", methods=["GET", "HEAD"], include_in_schema=False)
@llm_app.api_route("/app.4222.js", methods=["GET", "HEAD"], include_in_schema=False)
async def app_js() -> FileResponse:
    return frontend_asset("app.js")


@llm_app.api_route("/companion.js", methods=["GET", "HEAD"], include_in_schema=False)
async def companion_js() -> FileResponse:
    return frontend_asset("companion.js")


@llm_app.api_route("/companion.css", methods=["GET", "HEAD"], include_in_schema=False)
async def companion_css() -> FileResponse:
    return frontend_asset("companion.css")


@llm_app.get("/auth/session", response_model=AdminSessionResponse)
async def auth_session(request: Request) -> AdminSessionResponse:
    return service.get_admin_session(request.cookies.get(ADMIN_COOKIE_NAME))


@llm_app.get("/auth/user/session", response_model=UserSessionResponse)
async def user_auth_session(request: Request, response: Response) -> UserSessionResponse:
    session = service.get_user_session(request.cookies.get(USER_COOKIE_NAME))
    if not session.is_authenticated:
        request.state.chat_identity = await asyncio.to_thread(_resolve_chat_identity, request, None)
        _deliver_guest_cookie(request, response)
    return session


@llm_app.get("/slack/twin-link/status", response_model=SlackTwinLinkStatusResponse)
async def slack_twin_link_status(request: Request) -> SlackTwinLinkStatusResponse:
    session = service.get_user_session(request.cookies.get(USER_COOKIE_NAME))
    if not session.is_authenticated or session.account is None:
        return SlackTwinLinkStatusResponse(
            is_authenticated=False,
            is_lab_member=False,
            can_link=False,
            linked=False,
            message="需要先登录用户账号。",
        )
    return _slack_link_status_for_account(session.account)


@llm_app.post("/slack/twin-link/code", response_model=SlackTwinLinkCodeResponse)
async def create_slack_twin_link_code(request: Request) -> SlackTwinLinkCodeResponse:
    account = _require_user_account(request)
    is_lab_member = account.visitor_profile == "lab_member"
    if not is_lab_member:
        return SlackTwinLinkCodeResponse(
            is_authenticated=True,
            is_lab_member=False,
            can_link=False,
            message="需要邀请码升级为课题组成员后才能绑定 Slack /twin。",
        )
    code = slack_link_store.create_code(
        user_id=account.user_id,
        email=account.email,
        visitor_profile=account.visitor_profile,
        ttl_seconds=SLACK_TWIN_BIND_CODE_TTL_SECONDS,
    )
    return SlackTwinLinkCodeResponse(
        is_authenticated=True,
        is_lab_member=True,
        can_link=True,
        code=code.code,
        expires_at=code.expires_at,
        message="绑定码已生成，请在 Slack 输入 /twin bind 你的绑定码。",
    )


@llm_app.get("/admin/services", response_model=ServiceControlResponse)
async def get_managed_services(
    _: dict = Depends(require_admin_session),
) -> ServiceControlResponse:
    return service.get_managed_services()


@llm_app.post("/admin/services/{action}", response_model=ServiceControlResponse)
async def control_managed_services(
    action: str,
    _: dict = Depends(require_admin_session),
) -> ServiceControlResponse:
    try:
        return service.control_managed_services(action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.get("/code/workspaces", response_model=CodeWorkspaceListResponse)
async def list_code_workspaces(
    _: dict = Depends(require_code_access),
) -> CodeWorkspaceListResponse:
    return service.list_code_workspaces()


@llm_app.get("/code/sessions", response_model=CodeSessionListResponse)
async def list_code_sessions(
    workspace_id: str | None = Query(default=None, max_length=64),
    _: dict = Depends(require_code_session_access),
) -> CodeSessionListResponse:
    return service.list_code_sessions(workspace_id=workspace_id)


@llm_app.post("/code/sessions", response_model=CodeSessionRecord)
async def create_code_session(
    payload: CodeSessionCreateRequest,
    _: dict = Depends(require_code_session_access),
) -> CodeSessionRecord:
    try:
        return service.create_code_session(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.get("/code/sessions/{session_id}", response_model=CodeSessionRecord)
async def get_code_session(
    session_id: str,
    _: dict = Depends(require_code_session_access),
) -> CodeSessionRecord:
    record = service.get_code_session(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Code session not found.")
    return record


@llm_app.post("/code/sessions/{session_id}/messages", response_model=CodeSessionRecord)
async def append_code_session_message(
    session_id: str,
    payload: CodeSessionAppendMessageRequest,
    _: dict = Depends(require_code_session_access),
) -> CodeSessionRecord:
    record = service.append_code_session_message(session_id, payload)
    if record is None:
        raise HTTPException(status_code=404, detail="Code session not found.")
    return record


@llm_app.get("/local-code/config", response_model=LocalCodeConfigResponse)
async def get_local_code_config(
    _: dict = Depends(require_local_code_config_access),
) -> LocalCodeConfigResponse:
    _sync_local_code_settings_from_env()
    return _local_code_config_response()


@llm_app.post("/local-code/config", response_model=LocalCodeConfigResponse)
async def save_local_code_config(
    payload: LocalCodeConfigRequest,
    _: dict = Depends(require_local_code_config_access),
) -> LocalCodeConfigResponse:
    try:
        return _apply_local_code_config(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/search", response_model=CodeSearchResponse)
async def search_code(
    payload: CodeSearchRequest,
    _: dict = Depends(require_code_access),
) -> CodeSearchResponse:
    try:
        return service.search_code(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/file", response_model=CodeFileReadResponse)
async def read_code_file(
    payload: CodeFileReadRequest,
    _: dict = Depends(require_code_access),
) -> CodeFileReadResponse:
    try:
        return service.read_code_file(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/list", response_model=CodeDirectoryListResponse)
async def list_code_directory(
    payload: CodeDirectoryListRequest,
    _: dict = Depends(require_code_access),
) -> CodeDirectoryListResponse:
    try:
        return service.list_code_directory(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/git/status", response_model=CodeGitStatusResponse)
async def get_code_git_status(
    payload: CodeGitStatusRequest,
    _: dict = Depends(require_code_access),
) -> CodeGitStatusResponse:
    try:
        return service.get_code_git_status(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/git/diff", response_model=CodeGitDiffResponse)
async def get_code_git_diff(
    payload: CodeGitDiffRequest,
    _: dict = Depends(require_code_access),
) -> CodeGitDiffResponse:
    try:
        return service.get_code_git_diff(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/context", response_model=CodeContextResponse)
async def build_code_context(
    payload: CodeContextRequest,
    _: dict = Depends(require_code_access),
) -> CodeContextResponse:
    try:
        return service.build_code_context(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/run", response_model=CodeCommandResponse)
async def run_code_command(
    payload: CodeCommandRequest,
    _: dict = Depends(require_code_access),
) -> CodeCommandResponse:
    try:
        return service.run_code_command(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/assist", response_model=CodeAssistResponse)
async def assist_with_code(
    payload: CodeAssistRequest,
    _: dict = Depends(require_code_access),
) -> CodeAssistResponse:
    try:
        return await service.assist_with_code(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/code/propose", response_model=CodeProposeResponse)
async def propose_code_change(
    payload: CodeProposeRequest,
    _: dict = Depends(require_code_access),
) -> CodeProposeResponse:
    try:
        return await service.propose_code_change(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@llm_app.post("/auth/admin/login", response_model=AdminSessionResponse)
async def admin_login(payload: AdminLoginRequest, response: Response) -> AdminSessionResponse:
    result = service.login_admin(payload)
    set_admin_session_cookie(response, result.session_token, settings)
    return result.session


@llm_app.post("/auth/admin/logout", response_model=AdminSessionResponse)
async def admin_logout(response: Response) -> AdminSessionResponse:
    clear_admin_session_cookie(response)
    return service.logout_admin()


@llm_app.post("/auth/user/register", response_model=UserSessionResponse)
async def user_register(payload: UserRegisterRequest, response: Response) -> UserSessionResponse:
    result = service.register_user(payload)
    set_user_session_cookie(response, result.session_token, settings)
    return result.session


@llm_app.post("/auth/user/login", response_model=UserSessionResponse)
async def user_login(payload: UserLoginRequest, response: Response) -> UserSessionResponse:
    result = service.login_user(payload)
    set_user_session_cookie(response, result.session_token, settings)
    return result.session


@llm_app.post("/auth/user/logout", response_model=UserSessionResponse)
async def user_logout(response: Response) -> UserSessionResponse:
    clear_user_session_cookie(response)
    return service.logout_user()


@llm_app.get("/health")
async def health(response: Response) -> dict[str, object]:
    if not service.is_initialized():
        return {
            "status": "starting",
            "readiness": "starting",
            "app_version": __version__,
            "message": "Service is initializing in background.",
            "owner_name": settings.owner_name,
            "owner_role": settings.owner_role,
            "homepage_public_url": settings.homepage_public_url,
        }
    snapshot = await asyncio.to_thread(service.serving_readiness)
    http_readiness = _http_readiness()
    degraded = snapshot.get("status") == "degraded" or http_readiness == "degraded"
    if degraded:
        response.status_code = 503
    return {
        "status": "degraded" if degraded else snapshot.get("status", "ok"),
        "readiness": "degraded" if degraded else "ready",
        "background_readiness": snapshot.get("background_readiness", "unknown"),
        "http_readiness": http_readiness,
        "app_version": snapshot.get("app_version", __version__),
        "owner_name": snapshot.get("owner_name", settings.owner_name),
        "owner_role": snapshot.get("owner_role", settings.owner_role),
        "homepage_public_url": snapshot.get(
            "homepage_public_url", settings.homepage_public_url
        ),
        "model_name": snapshot.get("model_name", settings.model_name or "sage-auto"),
        "slack_readiness": _slack_readiness(),
    }


@llm_app.get("/admin/health")
async def admin_health(_: dict = Depends(require_admin_session)) -> dict[str, object]:
    return service.health()


@llm_app.get("/stack/versions")
async def stack_versions() -> dict[str, str]:
    payload = build_stack_versions_payload()
    # Environment preferences can be stale after the OpenAI-compatible engine
    # resolves the actually served model. The public stack panel must report
    # the model used by the live client, just like /health does.
    payload["model_name"] = service._llm_client.model_name
    runtime_snapshot = getattr(service._llm_client, "runtime_snapshot", None)
    if callable(runtime_snapshot):
        snapshot = runtime_snapshot()
        for key in ("model_name", "engine_image", "npu_devices"):
            if snapshot.get(key):
                payload[key] = str(snapshot[key])
    return payload


@llm_app.get("/stack/hardware")
async def stack_hardware(
    _: dict = Depends(require_admin_session),
) -> dict[str, str]:
    return build_hardware_payload()


@llm_app.post("/presence/heartbeat", response_model=OnlinePresenceHeartbeatResponse)
async def record_presence_heartbeat(
    request: OnlinePresenceHeartbeatRequest,
) -> OnlinePresenceHeartbeatResponse:
    return service.record_online_presence(request)


@llm_app.get("/lucky-question")
async def lucky_question(
    visitor_profile: str = Query(default="general_visitor"),
    recent: str = Query(default="", description="Comma-separated recent questions to avoid"),
    onboarding_step: str = Query(default="", description="Current onboarding step label for context"),
) -> dict[str, str]:
    """Ask the LLM to generate a contextual question for the
    "I'm feeling lucky" button.  Returns ``{}`` on failure so the
    frontend can fall back to the static question bank."""
    if not service.is_initialized():
        return {}
    recent_questions = [q.strip() for q in recent.split(",") if q.strip()] if recent else None
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(
                service.generate_lucky_question,
                visitor_profile=visitor_profile,
                recent_questions=recent_questions,
                onboarding_step=onboarding_step,
            ),
            timeout=8.0,
        )
    except asyncio.TimeoutError:
        return {}
    return result if isinstance(result, dict) else {}


@llm_app.get('/wiki/questions')
async def public_wiki_questions() -> dict[str, object]:
    if not service.is_initialized():
        return {'questions': []}
    return {'questions': service.public_wiki_questions()}


@llm_app.get('/wiki/citation')
async def recover_public_citation(source_url: str = Query(min_length=1, max_length=2048)):
    if not service.is_initialized():
        raise HTTPException(status_code=503, detail='知识来源尚未就绪，请稍后重试。')
    return service.recover_public_citation(source_url)


@llm_app.get("/availability", response_model=AvailabilitySchedule)
async def get_availability(
    _: dict = Depends(require_admin_session),
) -> AvailabilitySchedule:
    return service.get_availability_schedule()


@llm_app.put("/availability", response_model=AvailabilitySchedule)
async def update_availability(
    request: AvailabilitySchedule,
    _: dict = Depends(require_admin_session),
) -> AvailabilitySchedule:
    return service.update_availability_schedule(request)


@llm_app.get("/availability/previous-week", response_model=AvailabilitySchedule)
async def get_previous_week_availability_template(
    week_of: date | None = Query(default=None),
    _: dict = Depends(require_admin_session),
) -> AvailabilitySchedule:
    return service.get_previous_week_availability_template(week_of)


@llm_app.get("/chat/workflow-events", include_in_schema=False)
async def chat_workflow_events(
    raw_request: Request,
    request_id: str = Query(min_length=1, max_length=128),
) -> StreamingResponse:
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    key = _owned_chat_request_key(identity.owner_id, request_id)
    raw_cursor = raw_request.headers.get("last-event-id", "0")
    if not raw_cursor.isascii() or not raw_cursor.isdigit() or len(raw_cursor) > 20:
        raise HTTPException(status_code=400, detail="无效的事件游标。")
    after = int(raw_cursor)
    try:
        await asyncio.to_thread(workflow_event_broker.validate_cursor, key, after)
    except ValueError as exc:
        if str(exc) != "event_cursor_ahead":
            raise
        raise HTTPException(status_code=409, detail="事件游标超出当前记录，请重新同步。") from exc
    async def authorized_events():
        iterator = workflow_event_broker.stream(key, after=after)
        try:
            async for frame in iterator:
                try:
                    current = await asyncio.to_thread(_resolve_chat_identity, raw_request,
                                                      identity.visitor_profile, True)
                    if current != identity:
                        raise HTTPException(status_code=403, detail="身份权限已变更。")
                except HTTPException:
                    # Observer-local denial: never close or alter durable work.
                    yield 'data: {"type":"error","code":"authority_denied","status":403,"message":"当前身份或权限已失效，请重新验证。"}\n\n'
                    return
                yield frame
        finally:
            await iterator.aclose()

    response = StreamingResponse(
        authorized_events(),
        media_type="text/event-stream",
        headers={
            **NO_STORE_HEADERS,
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    _deliver_guest_cookie(raw_request, response)
    return response


@llm_app.post("/chat/cancel", include_in_schema=False)
async def cancel_chat_request(
    raw_request: Request,
    response: Response,
    request_id: str = Query(min_length=1, max_length=128),
) -> dict[str, bool | str]:
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    if http_workflow is None:
        raise HTTPException(status_code=503, detail="工作流尚未就绪，请稍后重试。")
    outcome = await http_workflow.cancel(owner_id=identity.owner_id, request_id=request_id)
    _deliver_guest_cookie(raw_request, response)
    if outcome == "cancelling":
        response.status_code = 202
        response.headers["Retry-After"] = "2"
    return {"request_id": request_id, "cancelled": outcome == "cancelled", "status": outcome}


def _slack_conversation_owner(team_id: str, user_id: str, channel_id: str) -> str:
    """Use signed transport IDs, not names/emails, for channel-private memory.

    Binding a Slack user to a website account currently grants a role only.
    Do not merge website/DM/channel histories implicitly: public mentions must
    not retrieve a private conversation from another channel.
    """
    if not all(re.fullmatch(r"[A-Za-z0-9]+", value or "") for value in (team_id, user_id, channel_id)):
        raise HTTPException(status_code=400, detail="Slack request missing valid conversation identity.")
    identity = json.dumps([team_id, user_id, channel_id], separators=(",", ":"))
    return "slack:" + hashlib.sha256(identity.encode()).hexdigest()


@llm_app.post("/slack/commands/twin", include_in_schema=False)
async def slack_twin_command(
    raw_request: Request,
) -> JSONResponse:
    raw_body = await raw_request.body()
    _verify_slack_signature(raw_body, raw_request)

    form = {
        key: values[-1] if values else ""
        for key, values in urllib.parse.parse_qs(raw_body.decode("utf-8")).items()
    }
    user_id = form.get("user_id", "").strip()
    team_id = form.get("team_id", "").strip()
    channel_id = form.get("channel_id", "").strip()
    _slack_conversation_owner(team_id, user_id, channel_id)
    _require_slack_destination(team_id)
    question = form.get("text", "")
    if not question.strip():
        return JSONResponse(
            {
                "response_type": "ephemeral",
                "text": "用法：`/twin 你的问题`",
            }
        )

    words = question.split()
    if words[0].lower() == "bind":
        if len(words) < 2:
            return JSONResponse({"response_type": "ephemeral", "text": "用法：`/twin bind 你的绑定码`"})
        try:
            ingress = SlackIngress(team_id=team_id, user_id=user_id, channel_id=channel_id,
                transport_id=form.get("trigger_id", "").strip(), kind="command", question="Slack binding status")
        except ValidationError:
            raise HTTPException(status_code=400, detail="Slack request missing valid transport identity.") from None
        await _bind_slack_ingress(ingress, words[1])
        return JSONResponse({"response_type": "ephemeral", "text": "收到绑定请求，结果稍后发回这里。"})

    _, _, denial_message = await asyncio.to_thread(_resolve_slack_twin_identity, user_id, team_id=team_id)
    if denial_message is not None:
        return JSONResponse({"response_type": "ephemeral", "text": denial_message})

    try:
        ingress = SlackIngress(team_id=team_id, user_id=user_id, channel_id=channel_id,
            transport_id=form.get("trigger_id", "").strip(), kind="command", question=question)
    except ValidationError:
        raise HTTPException(status_code=400, detail="Slack request missing valid transport identity.") from None
    if words[0].lower() == "status":
        return JSONResponse({"response_type": "ephemeral", "text": await _slack_status_text(ingress, words)})
    await _admit_slack(ingress)
    current = await slack_workflow.status(ingress, ingress.request_id)
    if current is not None and current["status"] == "failed":
        return JSONResponse({"response_type": "ephemeral",
            "text": f"本次请求已失败并停止重试，不会自动重跑。\n查询详情：`/twin status {ingress.request_id}`"})
    question_preview = question if len(question) <= 80 else f"{question[:77]}..."
    return JSONResponse(
        {
            "response_type": "ephemeral",
            "text": f"收到，我正在问 twin。\n你问：{question_preview}\n答案会稍后发回这里。\n查询进度：`/twin status {ingress.request_id}`",
        }
    )


@llm_app.post("/slack/events", include_in_schema=False)
async def slack_events(
    raw_request: Request,
) -> JSONResponse:
    raw_body = await raw_request.body()
    _verify_slack_signature(raw_body, raw_request)
    try:
        payload = json.loads(raw_body.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid Slack event payload.") from exc

    if payload.get("type") == "url_verification":
        return JSONResponse({"challenge": payload.get("challenge", "")})

    if payload.get("type") != "event_callback":
        return JSONResponse({"ok": True})

    event = payload.get("event")
    if not isinstance(event, dict):
        return JSONResponse({"ok": True})
    event_type = str(event.get("type") or "").strip()
    if event_type not in {"message", "app_mention"}:
        return JSONResponse({"ok": True})
    if event.get("subtype") or event.get("bot_id"):
        return JSONResponse({"ok": True})

    channel_type = str(event.get("channel_type") or "").strip()
    is_direct_message = channel_type == "im"
    is_mention = event_type == "app_mention"
    if not is_direct_message and not is_mention:
        return JSONResponse({"ok": True})

    user_id = str(event.get("user") or "").strip()
    channel_id = str(event.get("channel") or "").strip()
    thread_ts = str(event.get("thread_ts") or "").strip() or None
    if is_mention and thread_ts is None:
        thread_ts = str(event.get("ts") or "").strip() or None
    if not user_id or not channel_id:
        return JSONResponse({"ok": True})
    team_id = str(payload.get("team_id") or "").strip()
    _slack_conversation_owner(team_id, user_id, channel_id)
    _require_slack_destination(team_id)
    authorizations = payload.get("authorizations")
    bot_ids = {item["user_id"] for item in (authorizations if isinstance(authorizations, list) else [])
               if isinstance(item, dict) and item.get("is_bot") is True
               and (item.get("team_id") is None or item.get("team_id") == team_id)
               and isinstance(item.get("user_id"), str)
               and re.fullmatch(r"[A-Za-z0-9]+", item["user_id"])}
    if not bot_ids:
        # Do not guess which mentioned person is the bot, or accidentally
        # persist a misparsed bind command as a model question.
        raise HTTPException(status_code=400, detail="Slack event missing verified bot authorization.")
    question = _normalize_slack_twin_question(str(event.get("text") or ""), bot_user_ids=bot_ids)
    try:
        ingress = SlackIngress(team_id=team_id, user_id=user_id, channel_id=channel_id,
            transport_id=str(payload.get("event_id") or "").strip(),
            kind="mention" if is_mention else "message",
            conversation_type="im" if is_direct_message and not is_mention else None,
            question=question or "Slack usage", thread_ts=thread_ts,
            latest_ts=str(event.get("ts") or "").strip())
    except ValidationError:
        raise HTTPException(status_code=400, detail="Slack event missing valid transport identity.") from None
    if question.split()[:1] and question.split()[0].lower() == "bind":
        ingress = ingress.model_copy(update={"question": "Slack binding status", "private_reply": is_mention})
    elif question.split()[:1] and question.split()[0].lower() == "status":
        ingress = ingress.model_copy(update={"private_reply": True})
    if slack_workflow is None:
        raise HTTPException(status_code=503, detail="Slack workflow is not ready.")
    words = question.split()
    if words and words[0].lower() == "bind":
        if len(words) < 2:
            await _admit_slack(ingress, notice="用法：`/twin bind 你的绑定码`")
        else:
            await _bind_slack_ingress(ingress, words[1])
        return JSONResponse({"ok": True})
    try:
        if await slack_workflow.resume_if_admitted(ingress):
            return JSONResponse({"ok": True})
    except ValueError as exc:
        if str(exc) == "journal_commit_conflict":
            raise HTTPException(status_code=409, detail="Slack request identity conflict.") from None
        raise
    if not question:
        usage_text = "用法：`@twin 你的问题`、`/twin 你的问题`，或者直接给 twin 发私信。"
        await _admit_slack(ingress, notice=usage_text)
        return JSONResponse({"ok": True})

    _, _, denial_message = await asyncio.to_thread(_resolve_slack_twin_identity, user_id, team_id=team_id)
    if denial_message is not None:
        await _admit_slack(ingress, notice=denial_message)
        return JSONResponse({"ok": True})

    if words and words[0].lower() == "status":
        await _admit_slack(ingress, notice=await _slack_status_text(ingress, words))
        return JSONResponse({"ok": True})
    await _admit_slack(ingress)
    return JSONResponse({"ok": True})


async def startup_event() -> None:
    global slack_workflow, http_workflow, slack_dependency, _slack_verified_configuration
    slack_configuration = {
        "SLACK_TWIN_SIGNING_SECRET": SLACK_TWIN_SIGNING_SECRET,
        "SLACK_TWIN_TEAM_ID": SLACK_TWIN_TEAM_ID,
        "SLACK_TWIN_BOT_TOKEN": SLACK_TWIN_BOT_TOKEN,
    }
    if any(slack_configuration.values()) and not all(slack_configuration.values()):
        missing = ",".join(key for key, value in slack_configuration.items() if not value)
        raise RuntimeError(f"slack_configuration_incomplete:{missing}")
    instance = await asyncio.to_thread(service.ensure_initialized)
    await asyncio.to_thread(slack_link_store.migrate_to, instance._conversation_store.state_store)
    await asyncio.to_thread(instance.start_background_work)
    http_workflow = _create_http_workflow(instance)
    await http_workflow.start()
    if SLACK_TWIN_TEAM_ID and SLACK_TWIN_BOT_TOKEN:
        _slack_verified_configuration = _slack_configuration()
        slack_dependency = _create_slack_dependency(instance._conversation_store.state_store)
        await asyncio.to_thread(slack_dependency.start)
        slack_workflow = _create_slack_workflow(instance._conversation_store.state_store)
        await slack_workflow.start()
    if settings.warm_service_on_startup:
        try:
            # The model-side warmup is opportunistic.  A slow or stalled model
            # must not hold the HTTP listener offline after the KB is ready.
            await asyncio.wait_for(
                asyncio.to_thread(instance.warm_fixed_prefix_cache),
                timeout=STARTUP_WARMUP_MAX_WAIT_SECONDS,
            )
        except TimeoutError:
            _logger.warning("Fixed-prefix KV warmup is still running after startup")


async def shutdown_event() -> None:
    global slack_workflow, http_workflow, slack_dependency, _slack_verified_configuration
    if http_workflow is not None:
        await http_workflow.aclose()
        http_workflow = None
    if slack_workflow is not None:
        await slack_workflow.aclose()
        slack_workflow = None
    if slack_dependency is not None:
        await asyncio.to_thread(slack_dependency.close)
        slack_dependency = None
    _slack_verified_configuration = None
    if service.is_initialized():
        await service.aclose()


def _untraced_answer_chunk_callback() -> Callable[[str], None] | None:
    """Enable upstream streaming only when the deployment explicitly requests it."""

    if not STREAM_CHAT_ANSWER:
        return None
    return lambda _delta: None


@llm_app.post("/chat", response_model=ChatResponse)
async def chat(
    raw_request: Request,
    http_response: Response,
    request_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> ChatResponse:
    timing = RequestTimingLedger(
        trace_id=request_id or uuid4().hex,
        budget_seconds=CHAT_REQUEST_TIMEOUT_SECONDS,
    )
    stage_started = time.perf_counter()
    payload = await _parse_chat_request(raw_request)
    long_deep_request = (
        _streamed_chat_request.get()
        and payload.deep_thinking
        and payload.deep_thinking_explicit
    )
    if long_deep_request:
        timing.budget_seconds = CHAT_LONG_REQUEST_TIMEOUT_SECONDS
    identity = await asyncio.to_thread(_resolve_chat_identity,
        raw_request,
        payload.visitor_profile,
    )
    payload = identity.bind(payload)
    raw_request.state.chat_identity = identity
    _deliver_guest_cookie(raw_request, http_response)
    timing.record("request_parse", stage_started)
    trace_id = secrets.token_hex(16)
    http_response.headers["X-Trace-Id"] = trace_id
    http_response.headers["X-Sage-Trace-ID"] = timing.trace_id
    trace_store = get_conversation_trace_store()
    trace_store.begin(
        trace_id=trace_id,
        request_id=request_id,
        conversation_id=payload.conversation_id,
        student_name=payload.student_name,
        student_email=payload.student_email,
        source="http_sse" if request_id is not None else "http",
        request_payload=payload.model_dump(mode="json"),
    )

    def _record_success(response: ChatResponse, *, route: str) -> ChatResponse:
        delivered = timing.attach(response, route=route)
        trace_store.set_response(trace_id, delivered.model_dump(mode="json"))
        for step in delivered.workflow_trace:
            trace_store.append_event(trace_id, "trace-step", {"step": step.model_dump(mode="json")})
        trace_store.append_event(trace_id, "answer_done", {"response_stored": True})
        trace_store.append_event(trace_id, "complete", {})
        trace_store.finish(trace_id, status="completed")
        return delivered

    def _record_error(exc: Exception, *, status: str = "error") -> None:
        error_payload = {"type": type(exc).__name__, "message": str(exc)}
        trace_store.append_event(trace_id, "error", error_payload)
        trace_store.finish(trace_id, status=status, error=error_payload)

    def _http_error(status, detail, *, code=None):
        # FastAPI replaces the injected Response when handling an exception.
        # Preserve the newly issued guest identity so retry keeps its owner.
        headers = {"Cache-Control": "no-store", "X-Sage-Trace-ID": timing.trace_id}
        if code is not None:
            headers["X-Sage-Error-Code"] = code
        if status in {429, 503}:
            headers["Retry-After"] = "2"
        if "set-cookie" in http_response.headers:
            headers["Set-Cookie"] = http_response.headers["set-cookie"]
        return HTTPException(status_code=status, detail=detail, headers=headers)

    if http_workflow is None:
        raise _http_error(503, "HTTP workflow is not ready.")
    resolver = HttpAuthorityResolver(settings=service._settings, users=service._user_store,
        tokens=lab_member_api_token_store, guests=service._guest_identity_store)
    try:
        authority = await asyncio.to_thread(resolver.capture, raw_request, identity, payload)
        ingress = HttpIngress(request_id=request_id or timing.trace_id, request=payload, authority=authority)
        stage_started = time.perf_counter()
        await http_workflow.admit(ingress)
        response = await http_workflow.wait(ingress, timeout=max(0, timing.remaining_seconds()),
                                            start_timeout=CHAT_ADMISSION_TIMEOUT_SECONDS)
        timing.record("sage_workflow", stage_started)
        return _record_success(response, route="sage_workflow")
    except WorkCapacityError as exc:
        _record_error(exc, status="rejected")
        raise _http_error(429, "服务队列已满，本次请求未入队；请保留相同请求 ID，稍后重试。",
                          code="work_capacity_exhausted") from None
    except PermissionError as exc:
        _record_error(exc, status="rejected")
        raise _http_error(403, "当前身份或权限已失效，请重新验证。") from exc
    except HttpWorkRejected as exc:
        _record_error(exc, status="rejected")
        status, detail = public_http_failure(exc.code)
        raise _http_error(status, detail, code=exc.code) from exc
    except ValueError as exc:
        _record_error(exc, status="rejected")
        if str(exc) in {"journal_commit_conflict", "http_work_input_conflict"}:
            raise _http_error(409, "该请求 ID 已绑定不同输入，请使用新的请求 ID。") from exc
        raise
    except WorkStartTimeoutError as exc:
        _record_error(exc, status="queued")
        raise _http_error(429, "请求正在排队；请使用相同请求 ID 重试查询，不会重复执行。") from exc
    except TimeoutError as exc:
        _record_error(exc, status="timeout")
        # Observer timeout never closes/cancels the durable producer or event stream.
        raise _http_error(504, "本次等待未完成响应，任务仍在处理；请使用相同请求 ID 重试查询。") from exc
    except Exception as exc:
        _record_error(exc)
        raise


@llm_app.post("/chat/stream")
async def stream_chat(
    raw_request: Request,
    request_id: str | None = Query(default=None, min_length=1, max_length=128),
) -> StreamingResponse:
    """Keep a deep web request alive while preserving the normal /chat API.

    The final event contains the same validated ChatResponse as /chat. Progress
    remains on the existing workflow-events channel; this stream only carries
    keepalives and the final result, never an unvalidated partial answer.
    """

    # Starlette's streaming disconnect watcher reads the ASGI receive channel.
    # Cache the upload first so it cannot race with chat's JSON/form parsing.
    await raw_request.body()
    raw_request.state.chat_identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)

    async def events() -> AsyncIterator[str]:
        async def run_chat() -> ChatResponse:
            token = _streamed_chat_request.set(True)
            try:
                return await chat(raw_request, Response(), request_id=request_id)
            finally:
                _streamed_chat_request.reset(token)

        task = asyncio.create_task(run_chat())
        try:
            while True:
                try:
                    result = await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except HTTPException as exc:
                    error_event = {"type": "error", "status": exc.status_code, "detail": exc.detail}
                    if code := (exc.headers or {}).get("X-Sage-Error-Code"):
                        error_event["code"] = code
                    yield "data: " + json.dumps(
                        error_event,
                        ensure_ascii=False,
                    ) + "\n\n"
                    break
                except Exception:
                    _logger.exception("streamed chat request failed")
                    yield 'data: {"type":"error","status":500,"detail":"回答生成失败，请稍后重试。"}\n\n'
                    break
                yield "data: " + json.dumps(
                    {"type": "result", "response": result.model_dump(mode="json")},
                    ensure_ascii=False,
                ) + "\n\n"
                break
        finally:
            if not task.done():
                task.cancel()

    response = StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
    _deliver_guest_cookie(raw_request, response)
    return response


@llm_app.post("/chat/feedback", response_model=ChatFeedbackResponse)
async def submit_chat_feedback(request: ChatFeedbackRequest, raw_request: Request,
                               response: Response) -> ChatFeedbackResponse:
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    result = await asyncio.to_thread(service.submit_chat_feedback, request, owner_id=identity.owner_id)
    _deliver_guest_cookie(raw_request, response)
    return result


@llm_app.get("/chat/feedback/{operation_id}", response_model=ChatFeedbackOperationResponse)
async def get_feedback_operation(operation_id: str, raw_request: Request, response: Response,
                                 exchange_id: str = Query(min_length=1, max_length=128)):
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    result = await asyncio.to_thread(service.get_feedback_operation, operation_id,
                                    exchange_id=exchange_id, owner_id=identity.owner_id)
    _deliver_guest_cookie(raw_request, response)
    return result


@llm_app.get("/chat/execution", response_model=ExchangeExecutionResponse)
async def get_exchange_execution(raw_request: Request, response: Response,
                                 exchange_id: str = Query(min_length=1, max_length=128)):
    # The opaque exchange ID is not authority; resolve current identity exactly
    # as feedback does, then require the canonical record's verified owner.
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    result = await asyncio.to_thread(service.get_exchange_execution, exchange_id,
                                    owner_id=identity.owner_id)
    _deliver_guest_cookie(raw_request, response)
    response.headers["Cache-Control"] = "no-store"
    return result


@llm_app.get("/chat/feedback", response_model=ChatFeedbackOperationResponse)
async def get_current_feedback_operation(raw_request: Request, response: Response,
                                         exchange_id: str = Query(min_length=1, max_length=128)):
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    result = await asyncio.to_thread(service.get_feedback_operation, None,
                                    exchange_id=exchange_id, owner_id=identity.owner_id)
    _deliver_guest_cookie(raw_request, response)
    return result


@llm_app.get("/admin/conversation-traces")
async def list_conversation_traces(
    limit: int = Query(default=50, ge=1, le=500),
    conversation_id: str | None = Query(default=None, max_length=128),
    _: dict = Depends(require_admin_session),
) -> dict[str, object]:
    store = get_conversation_trace_store()
    return {
        "traces": store.list_traces(limit=limit, conversation_id=conversation_id),
        "stats": store.stats(),
    }


@llm_app.get("/admin/conversation-traces/stats")
async def get_conversation_trace_stats(
    _: dict = Depends(require_admin_session),
) -> dict[str, object]:
    return get_conversation_trace_store().stats()


@llm_app.get("/admin/conversation-traces/{trace_id}")
async def get_conversation_trace(
    trace_id: str,
    _: dict = Depends(require_admin_session),
) -> dict[str, object]:
    trace = get_conversation_trace_store().get_trace(trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return trace


@llm_app.post("/context/compress")
async def compress_context(raw_request: Request) -> JSONResponse:
    """Manually trigger context compression for a conversation."""
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    try:
        payload = await raw_request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="无效的 JSON 请求体") from None

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="请求体必须是对象")
    conversation_id = str(payload.get("conversation_id", "") or "").strip()
    if not conversation_id or len(conversation_id) > 128:
        raise HTTPException(status_code=422, detail="conversation_id 长度必须为 1–128")

    result = await asyncio.to_thread(service.compress_conversation_context, conversation_id,
                                     owner_id=identity.owner_id)
    response = JSONResponse(content=result)
    _deliver_guest_cookie(raw_request, response)
    return response


@llm_app.get("/chat/conversations", response_model=ConversationHistoryListResponse)
async def list_chat_conversations(
    request: Request,
    student_email: str | None = Query(default=None, max_length=256),
    limit: int = Query(default=30, ge=1, le=100),
) -> ConversationHistoryListResponse:
    user_session = service.get_user_session(request.cookies.get(USER_COOKIE_NAME))
    resolved_email = resolve_authenticated_history_email(
        is_authenticated=user_session.is_authenticated,
        account_email=(user_session.account.email if user_session.account else None),
        requested_email=student_email,
    )
    return service.list_chat_conversations(student_email=resolved_email, limit=limit,
                                           owner_id=f"account:{user_session.account.user_id}")


@llm_app.get(
    "/chat/conversations/{conversation_id}",
    response_model=ConversationTranscriptResponse,
)
async def get_chat_conversation(
    conversation_id: str,
    request: Request,
    student_email: str | None = Query(default=None, max_length=256),
) -> ConversationTranscriptResponse:
    user_session = service.get_user_session(request.cookies.get(USER_COOKIE_NAME))
    resolved_email = resolve_authenticated_history_email(
        is_authenticated=user_session.is_authenticated,
        account_email=(user_session.account.email if user_session.account else None),
        requested_email=student_email,
    )
    return service.get_chat_conversation(
        conversation_id=conversation_id, student_email=resolved_email,
        owner_id=f"account:{user_session.account.user_id}"
    )


@llm_app.post("/suggestions", response_model=AnonymousSuggestionRecord)
async def submit_anonymous_suggestion(
    request: AnonymousSuggestionCreate, raw_request: Request
) -> AnonymousSuggestionRecord:
    return service.submit_anonymous_suggestion(
        request,
        admin_session_token=raw_request.cookies.get(ADMIN_COOKIE_NAME),
    )


@llm_app.get("/suggestions", response_model=list[AnonymousSuggestionRecord])
async def list_anonymous_suggestions(
    raw_request: Request,
    limit: int = Query(default=50, ge=1, le=100),
) -> list[AnonymousSuggestionRecord]:
    return service.list_anonymous_suggestions(
        limit=limit,
        admin_session_token=raw_request.cookies.get(ADMIN_COOKIE_NAME),
    )


@llm_app.post("/knowledge", response_model=KnowledgeDocumentRecord)
async def create_knowledge_document(
    request: KnowledgeDocumentCreate,
    _: dict = Depends(require_admin_session),
) -> KnowledgeDocumentRecord:
    return service.add_knowledge(request)


@llm_app.post("/knowledge/{document_id}/review", response_model=KnowledgeDocumentActionResponse)
async def review_knowledge_document(
    document_id: str,
    request: KnowledgeDocumentReviewRequest,
    _: dict = Depends(require_admin_session),
) -> KnowledgeDocumentActionResponse:
    return service.review_knowledge_document(document_id, request)


@llm_app.delete("/knowledge/{document_id}", response_model=KnowledgeDocumentActionResponse)
async def delete_knowledge_document(
    document_id: str,
    _: dict = Depends(require_admin_session),
) -> KnowledgeDocumentActionResponse:
    return service.delete_knowledge_document(document_id)


@llm_app.get("/knowledge", response_model=list[KnowledgeDocumentRecord])
async def list_knowledge_documents(
    _: dict = Depends(require_admin_session),
) -> list[KnowledgeDocumentRecord]:
    return service.list_knowledge()


@llm_app.get("/knowledge/reviews/summary", response_model=KnowledgeDocumentReviewSummary)
async def get_knowledge_review_summary(
    limit: int = Query(default=20, ge=1, le=100),
    _: dict = Depends(require_admin_session),
) -> KnowledgeDocumentReviewSummary:
    return service.list_knowledge_review_summary(limit=limit)


@llm_app.get("/knowledge/search", response_model=KnowledgeSearchResponse)
async def search_knowledge_documents(
    query: str = Query(min_length=1, max_length=256),
    visitor_profile: str | None = Query(
        default=None,
        pattern="^(hust_undergraduate|paper_writing_student|lab_member|general_visitor)$",
    ),
    admin_session: dict = Depends(require_admin_session),
) -> KnowledgeSearchResponse:
    return service.search_knowledge(
        query,
        visitor_profile=visitor_profile,
        admin_role=str(admin_session.get("role") or "super_admin"),
    )


@llm_app.get("/memory/profiles", response_model=MemoryProfileListResponse)
async def list_memory_profiles(
    category: str | None = Query(default=None, max_length=64),
    student_query: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=50, ge=1, le=200),
    _: dict = Depends(require_admin_session),
) -> MemoryProfileListResponse:
    return service.list_memory_profiles(category=category, student_query=student_query, limit=limit)


@llm_app.get("/analytics/questions", response_model=QuestionAnalyticsReportResponse)
async def get_question_analytics_report(
    days: int = Query(default=7, ge=1, le=90),
    _: dict = Depends(require_admin_session),
) -> QuestionAnalyticsReportResponse:
    return service.get_question_analytics_report(days=days)


@llm_app.get("/operations/overview", response_model=OperationsOverviewResponse)
async def get_operations_overview(
    days: int = Query(default=7, ge=1, le=90),
    _: dict = Depends(require_admin_session),
) -> OperationsOverviewResponse:
    return service.get_operations_overview(days=days)


@llm_app.get("/operations/workbench", response_model=OperationsWorkbenchResponse)
async def get_operations_workbench(
    days: int = Query(default=7, ge=1, le=90),
    limit: int = Query(default=10, ge=1, le=50),
    _: dict = Depends(require_admin_session),
) -> OperationsWorkbenchResponse:
    return service.get_operations_workbench(days=days, limit=limit)


@llm_app.get("/workflow/replay", response_model=WorkflowReplayReportResponse)
async def get_workflow_replay_report(
    _: dict = Depends(require_admin_session),
) -> WorkflowReplayReportResponse:
    return service.get_workflow_replay_report()


@llm_app.get(
    "/operations/runtime-features",
    response_model=RuntimeFeatureFlagsResponse,
)
async def get_runtime_feature_flags(
    _: dict = Depends(require_admin_session),
) -> RuntimeFeatureFlagsResponse:
    return service.get_runtime_feature_flags()


@llm_app.patch(
    "/operations/runtime-features",
    response_model=RuntimeFeatureFlagsResponse,
)
async def update_runtime_feature_flags(
    request: RuntimeFeatureFlagsUpdateRequest,
    _: dict = Depends(require_admin_session),
) -> RuntimeFeatureFlagsResponse:
    return service.update_runtime_feature_flags(request)


@llm_app.patch("/operations/tasks/{task_key}", response_model=OperationsTaskStateRecord)
async def update_operations_task_state(
    task_key: str,
    request: OperationsTaskStateUpdateRequest,
    _: dict = Depends(require_admin_session),
) -> OperationsTaskStateRecord:
    return service.update_operations_task_state(task_key, request)


@llm_app.get(
    "/analytics/questions/gap-drafts",
    response_model=list[KnowledgeGapDraftRecordResponse],
)
async def list_knowledge_gap_drafts(
    _: dict = Depends(require_admin_session),
) -> list[KnowledgeGapDraftRecordResponse]:
    return service.list_knowledge_gap_drafts()


@llm_app.get("/memory/artifact-drafts", response_model=list[ArtifactMemoryDraftRecordResponse])
async def list_artifact_memory_drafts(
    _: dict = Depends(require_admin_session),
) -> list[ArtifactMemoryDraftRecordResponse]:
    return service.list_artifact_memory_drafts()


@llm_app.post(
    "/memory/artifact-drafts/{draft_id}/accept",
    response_model=ArtifactMemoryDraftRecordResponse,
)
async def accept_artifact_memory_draft(
    draft_id: str,
    _: dict = Depends(require_admin_session),
) -> ArtifactMemoryDraftRecordResponse:
    return service.accept_artifact_memory_draft(draft_id)


@llm_app.post(
    "/memory/artifact-drafts/{draft_id}/reject",
    response_model=ArtifactMemoryDraftRecordResponse,
)
async def reject_artifact_memory_draft(
    draft_id: str,
    _: dict = Depends(require_admin_session),
) -> ArtifactMemoryDraftRecordResponse:
    return service.reject_artifact_memory_draft(draft_id)


@llm_app.post("/analytics/questions/gap-drafts", response_model=KnowledgeGapDraftRecordResponse)
async def create_knowledge_gap_draft(
    request: KnowledgeGapDraftCreateRequest,
    _: dict = Depends(require_admin_session),
) -> KnowledgeGapDraftRecordResponse:
    return service.create_knowledge_gap_draft(request)


@llm_app.post(
    "/analytics/questions/gap-drafts/{draft_id}/publish",
    response_model=KnowledgeGapDraftRecordResponse,
)
async def publish_knowledge_gap_draft(
    draft_id: str,
    _: dict = Depends(require_admin_session),
) -> KnowledgeGapDraftRecordResponse:
    return service.publish_knowledge_gap_draft(draft_id)


@llm_app.get("/escalations", response_model=list[EscalationRecord])
async def list_escalations(
    status: str | None = Query(default=None, max_length=32),
    route: str | None = Query(default=None, max_length=32),
    _: dict = Depends(require_admin_session),
) -> list[EscalationRecord]:
    return service.list_escalations(status=status, route=route)


@llm_app.post("/escalations/{escalation_id}/resolve", response_model=EscalationRecord)
async def resolve_escalation(
    escalation_id: str,
    request: EscalationDecisionRequest,
    _: dict = Depends(require_admin_session),
) -> EscalationRecord:
    return service.resolve_escalation(escalation_id, request)


@llm_app.get("/follow-ups", response_model=list[FollowUpQueueRecord])
async def list_follow_ups(
    status: str | None = Query(default=None, max_length=32),
    action_type: str | None = Query(default=None, max_length=64),
    _: dict = Depends(require_admin_session),
) -> list[FollowUpQueueRecord]:
    return service.list_follow_up_actions(status=status, action_type=action_type)


@llm_app.post("/follow-ups/dispatch", response_model=FollowUpDispatchResponse)
async def dispatch_follow_ups(
    _: dict = Depends(require_admin_session),
) -> FollowUpDispatchResponse:
    return service.dispatch_due_follow_ups()


@llm_app.post("/bookings", response_model=BookingResponse)
async def create_booking(request: BookingRequest, raw_request: Request, response: Response) -> BookingResponse:
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    if identity.account_email and request.student_email.strip().lower() != identity.account_email.lower():
        raise HTTPException(status_code=403, detail="请使用当前登录账号的邮箱准备预约。")
    result = await asyncio.to_thread(service._meeting_service.prepare_proposal, identity.owner_id, request)
    _deliver_guest_cookie(raw_request, response)
    return result


async def _decide_booking_proposal(payload, raw_request, response, decision):
    from sage.runtime.action_confirmation import ConfirmationError
    identity = await asyncio.to_thread(_resolve_chat_identity, raw_request, None)
    try:
        result = await asyncio.to_thread(service._meeting_service.decide_proposal,
                                        identity.owner_id, payload.action_id, decision=decision)
    except ConfirmationError as error:
        messages = {
            "confirmation_expired": "预约草稿已过期，未提交；请重新核对并生成草稿。",
            "confirmation_already_cancelled": "草稿已经放弃，不能再提交；请重新准备。",
            "confirmation_already_confirmed": "已确认提交，不能通过放弃草稿撤回；请查询预约状态。",
        }
        raise HTTPException(status_code=409, detail=messages.get(str(error), "草稿不可用或不属于当前身份；未执行新的预约操作。")) from error
    _deliver_guest_cookie(raw_request, response)
    return result


@llm_app.post("/bookings/confirm-proposal", response_model=BookingResponse)
async def confirm_booking_proposal(payload: BookingProposalDecision, raw_request: Request, response: Response):
    return await _decide_booking_proposal(payload, raw_request, response, "confirm")


@llm_app.post("/bookings/cancel-proposal", response_model=BookingResponse)
async def cancel_booking_proposal(payload: BookingProposalDecision, raw_request: Request, response: Response):
    return await _decide_booking_proposal(payload, raw_request, response, "cancel")


@llm_app.get("/bookings", response_model=list[BookingRecord])
async def list_bookings(
    status: str | None = Query(default=None, max_length=32),
    _: dict = Depends(require_admin_session),
) -> list[BookingRecord]:
    return service.list_bookings(status=status)


@llm_app.post("/bookings/{booking_id}/confirm", response_model=BookingResponse)
async def confirm_booking(
    booking_id: str,
    _: dict = Depends(require_admin_session),
) -> BookingResponse:
    return service.confirm_booking(booking_id)


@llm_app.post("/bookings/{booking_id}/reject", response_model=BookingResponse)
async def reject_booking(
    booking_id: str,
    request: BookingDecisionRequest,
    _: dict = Depends(require_admin_session),
) -> BookingResponse:
    return service.reject_booking(booking_id, request)


@llm_app.get("/changelog")
async def get_changelog() -> list[dict[str, object]]:
    """Return version changelog entries from data/changelog.json."""
    changelog_file = settings.changelog_path
    if not changelog_file.is_file():
        return []
    try:
        raw = json.loads(changelog_file.read_text(encoding="utf-8"))
        if not isinstance(raw, list):
            return []
        return raw  # type: ignore[return-value]
    except Exception:
        return []


@llm_app.get("/capabilities")
async def get_capabilities(
    _: dict = Depends(require_admin_session),
) -> list[CapabilityPluginStatus]:
    """Return loaded capability plugin statuses for the operations console."""
    registry = CapabilityPluginRegistry(
        plugin_dir=settings.capability_plugin_dir,
        current_version=__version__,
    )
    registry.load()
    return registry.statuses


app = create_edge_app(mount_llm=True, llm_prefix="/", llm_app=llm_app)
configure_local_cors(app)
app.mount("/static", StaticFiles(directory=web_dir), name="static")
