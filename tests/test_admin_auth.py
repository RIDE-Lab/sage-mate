import asyncio
from uuid import uuid4
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin.analytics_store import ConversationAnalyticsStore
from sage_faculty_twin.api import app, service
from sage_faculty_twin.config import settings
from sage_faculty_twin.escalation_store import EscalationQueueStore
from sage_faculty_twin.follow_up_store import FollowUpQueueStore
from sage_faculty_twin.knowledge_gap_draft_store import KnowledgeGapDraftStore
from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.models import ManagedServiceStatus
from sage_faculty_twin.models import ServiceControlResponse
from sage_faculty_twin.models import ChatResponse
from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.models import InteractionIntent
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.meeting import MeetingService


client = TestClient(app)


class RecordingNotifier:
    def __init__(self) -> None:
        self.approved_bookings = []
        self.rejected_bookings = []
        self.follow_up_emails = []
        self.runtime_manager = None

    def send_booking_request_notification(self, booking) -> str:
        return settings.booking_notification_email

    def send_booking_approved_notification(self, booking) -> str:
        self.approved_bookings.append(booking)
        return booking.student_email

    def send_booking_rejected_notification(self, booking) -> str:
        self.rejected_bookings.append(booking)
        return booking.student_email

    def send_follow_up_email(
        self, recipient: str, subject: str, lines: list[str]
    ) -> str:
        self.follow_up_emails.append(
            {"recipient": recipient, "subject": subject, "lines": list(lines)}
        )
        return recipient


class FakeRuntimeManager:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def _response(self, action: str, active_state: str) -> ServiceControlResponse:
        return ServiceControlResponse(
            action=action,
            success=True,
            message=f"{action} ok",
            services=[
                ManagedServiceStatus(
                    name="应用服务",
                    unit="sage-mate-app.service",
                    active_state=active_state,
                    sub_state="running" if active_state == "active" else "dead",
                    description="Sage Mate application server",
                )
            ],
        )

    def status(self) -> ServiceControlResponse:
        self.calls.append("status")
        return self._response("status", "active")

    def start(self) -> ServiceControlResponse:
        self.calls.append("start")
        return self._response("start", "active")

    def stop(self) -> ServiceControlResponse:
        self.calls.append("stop")
        return self._response("stop", "inactive")

    def restart(self) -> ServiceControlResponse:
        self.calls.append("restart")
        return self._response("restart", "active")


@pytest.fixture
def isolated_availability_store(tmp_path: Path):
    original_meeting_service = service._meeting_service
    original_knowledge_store = service._knowledge_store
    original_conversation_store = service._conversation_store
    original_analytics_store = service._analytics_store
    original_knowledge_gap_draft_store = service._knowledge_gap_draft_store
    original_escalation_store = service._escalation_store
    original_follow_up_store = service._follow_up_store
    original_user_store = service._user_store
    original_llm_client = service._llm_client
    original_notifier = service._email_notifier
    original_runtime_manager = service._runtime_manager
    notifier = RecordingNotifier()
    runtime_manager = FakeRuntimeManager()
    isolated_settings = settings.model_copy(
        update={
            "knowledge_base_dir": tmp_path / "knowledge-base",
            "conversation_memory_dir": tmp_path / "conversation-memory",
            "knowledge_gap_draft_dir": tmp_path / "knowledge-gap-drafts",
        }
    )
    service._knowledge_store = service._knowledge_store.__class__(isolated_settings)
    service._meeting_service = MeetingService(
        settings.model_copy(
            update={
                "availability_schedule_path": tmp_path
                / "availability"
                / "current_week.json"
            }
        )
    )
    service._conversation_store = NeuroMemConversationStore(isolated_settings)
    service._analytics_store = ConversationAnalyticsStore(
        isolated_settings, service._conversation_store
    )
    service._knowledge_gap_draft_store = KnowledgeGapDraftStore(isolated_settings)
    service._escalation_store = EscalationQueueStore(
        settings.model_copy(update={"escalation_queue_dir": tmp_path / "escalations"})
    )
    service._follow_up_store = FollowUpQueueStore(
        settings.model_copy(update={"follow_up_queue_dir": tmp_path / "follow-ups"})
    )
    service._user_store = service._user_store.__class__(
        settings.model_copy(
            update={"user_account_store_dir": tmp_path / "user-accounts"}
        )
    )
    service._email_notifier = notifier
    service._runtime_manager = runtime_manager
    notifier.runtime_manager = runtime_manager
    try:
        yield notifier
    finally:
        service._meeting_service = original_meeting_service
        service._knowledge_store = original_knowledge_store
        service._conversation_store = original_conversation_store
        service._analytics_store = original_analytics_store
        service._knowledge_gap_draft_store = original_knowledge_gap_draft_store
        service._escalation_store = original_escalation_store
        service._follow_up_store = original_follow_up_store
        service._user_store = original_user_store
        service._llm_client = original_llm_client
        service._email_notifier = original_notifier
        service._runtime_manager = original_runtime_manager


class CollaborationMemoryLLMClient:
    model_name = "test-model"

    def classify_booking_intent_sync(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        return "建议先准备 agenda、当前 blocker、相关 draft，再带上你最想讨论的问题。"

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return self.answer_question_sync(system_prompt, user_prompt)


class AdminKnowledgeIntentLLMClient(CollaborationMemoryLLMClient):
    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="admin_add_knowledge", domain="general", confidence=0.99
        )


class AttachmentClarificationLLMClient(CollaborationMemoryLLMClient):
    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="ask_followup",
            domain="general",
            decision_mode="direct_answer",
            needs_clarification=True,
            clarification_message="请上传附件或提供更多关于附件的信息。",
            confidence=0.92,
        )

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        assert "Draft outline" in user_prompt
        return "附件主要写了 related work、contribution framing 和 evaluation plan。"


def test_knowledge_requires_admin_authentication(isolated_availability_store) -> None:
    client.cookies.clear()
    response = client.get("/knowledge")

    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员身份验证。"

    availability_read_response = client.get("/availability")
    assert availability_read_response.status_code == 403
    assert availability_read_response.json()["detail"] == "需要管理员身份验证。"

    availability_response = client.put(
        "/availability",
        json={"week_of": "2026-05-25", "days": []},
    )
    assert availability_response.status_code == 403
    assert availability_response.json()["detail"] == "需要管理员身份验证。"

    memory_profiles_response = client.get("/memory/profiles")
    assert memory_profiles_response.status_code == 403
    assert memory_profiles_response.json()["detail"] == "需要管理员身份验证。"

    analytics_response = client.get("/analytics/questions")
    assert analytics_response.status_code == 403
    assert analytics_response.json()["detail"] == "需要管理员身份验证。"

    gap_drafts_response = client.get("/analytics/questions/gap-drafts")
    assert gap_drafts_response.status_code == 403
    assert gap_drafts_response.json()["detail"] == "需要管理员身份验证。"

    escalations_response = client.get("/escalations")
    assert escalations_response.status_code == 403
    assert escalations_response.json()["detail"] == "需要管理员身份验证。"

    follow_ups_response = client.get("/follow-ups")
    assert follow_ups_response.status_code == 403
    assert follow_ups_response.json()["detail"] == "需要管理员身份验证。"

    services_response = client.get("/admin/services")
    assert services_response.status_code == 403
    assert services_response.json()["detail"] == "需要管理员身份验证。"

    restart_response = client.post("/admin/services/restart")
    assert restart_response.status_code == 403
    assert restart_response.json()["detail"] == "需要管理员身份验证。"


def test_auth_session_reflects_admin_login_state() -> None:
    client.cookies.clear()

    anonymous_response = client.get("/auth/session")
    assert anonymous_response.status_code == 200
    assert anonymous_response.json() == {
        "is_admin": False,
        "mode": "user",
        "username": None,
        "role": None,
    }

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    admin_response = client.get("/auth/session")
    assert admin_response.status_code == 200
    assert admin_response.json() == {
        "is_admin": True,
        "mode": "admin",
        "username": settings.admin_username,
        "role": "super_admin",
    }


def test_manager_login_can_access_admin_search_and_has_manager_role(
    isolated_availability_store,
) -> None:
    client.cookies.clear()
    service._knowledge_store.add_document(
        KnowledgeDocumentCreate(
            title="管理资料｜课题组成员信息总表",
            content="课题组成员 Slack账号、微信号、角色分工和备注仅供 admin 与 manager 查看。",
            tags=["team", "members", "audience:manager", "audience:admin"],
            source_name="manual:team-members-directory",
        )
    )

    login_response = client.post(
        "/auth/admin/login",
        json={
            "username": settings.manager_username,
            "password": settings.manager_password,
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["username"] == settings.manager_username
    assert login_response.json()["role"] == "manager"

    session_response = client.get("/auth/session")
    assert session_response.status_code == 200
    assert session_response.json()["username"] == settings.manager_username
    assert session_response.json()["role"] == "manager"

    search_response = client.get(
        "/knowledge/search",
        params={"query": "课题组成员 Slack账号"},
    )
    assert search_response.status_code == 200
    assert search_response.json()["hits"][0]["title"] == "管理资料｜课题组成员信息总表"


def test_admin_can_inject_knowledge_via_chat_after_login(
    isolated_availability_store,
) -> None:
    client.cookies.clear()

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    chat_response = client.post(
        "/chat",
        json={
            "student_name": "admin",
            "student_email": None,
            "course_context": "科研指导",
            "conversation_id": "admin-knowledge-injection",
            "question": (
                "加入知识库：\n"
                "标题：预约前准备清单\n"
                "标签：advising, booking\n"
                "内容：学生预约前需要先发送 agenda、当前 blocker 和相关 draft。"
            ),
        },
    )

    assert chat_response.status_code == 200
    chat_payload = chat_response.json()
    assert chat_payload["workflow_action"] == "admin_add_knowledge"
    assert "已写入知识库：预约前准备清单" in chat_payload["answer"]
    assert any(
        item["basis_label"] == "知识入库结果" for item in chat_payload["answer_basis"]
    )

    knowledge_response = client.get("/knowledge")
    assert knowledge_response.status_code == 200
    documents = knowledge_response.json()
    assert any(
        document["title"] == "预约前准备清单"
        and "agenda" in document["content"]
        and set(document["tags"]) == {"advising", "booking"}
        for document in documents
    )


def test_non_admin_chat_cannot_inject_knowledge_even_if_intent_says_so(
    isolated_availability_store,
) -> None:
    client.cookies.clear()
    service._llm_client = AdminKnowledgeIntentLLMClient()

    response = client.post(
        "/chat",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": "科研指导",
            "conversation_id": "non-admin-knowledge-injection",
            "question": "加入知识库：标题：不应该写入 内容：这条内容不能由普通用户写入。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_action"] == "answer"
    assert "建议先准备" in payload["answer"]
    assert service._knowledge_store.count_documents() == 0


def test_chat_accepts_multipart_uploads(monkeypatch) -> None:
    captured = {}

    async def fake_answer(
        request,
        admin_session_token=None,
        trace_callback=None,
        answer_chunk_callback=None,
    ):
        captured["request"] = request
        captured["answer_chunk_callback"] = answer_chunk_callback
        return ChatResponse(
            answer="已读取附件。",
            owner_name="张书豪",
            used_model="mock-model",
            conversation_id=request.conversation_id,
        )

    monkeypatch.setattr(service, "answer", fake_answer)

    response = client.post(
        "/chat",
        data={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": "论文写作课",
            "visitor_profile": "paper_writing_student",
            "question": "请结合我上传的草稿给我提建议。",
            "conversation_id": "multipart-upload-test",
        },
        files=[
            (
                "files",
                (
                    "draft-notes.txt",
                    b"Current draft focus: related work and contribution framing.",
                    "text/plain",
                ),
            )
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["answer"] == "已读取附件。"
    assert callable(captured["answer_chunk_callback"])
    request = captured["request"]
    assert request.question == "请结合我上传的草稿给我提建议。"
    assert len(request.attachments) == 1
    assert request.attachments[0].file_name == "draft-notes.txt"
    assert "related work" in request.attachments[0].text_content


def test_chat_uses_authenticated_visitor_profile_over_client_payload(monkeypatch) -> None:
    captured = {}
    email = f"lab-user-{uuid4().hex}@example.com"

    async def fake_answer(
        request,
        admin_session_token=None,
        trace_callback=None,
        answer_chunk_callback=None,
    ):
        captured["request"] = request
        captured["answer_chunk_callback"] = answer_chunk_callback
        return ChatResponse(
            answer="ok",
            owner_name="张书豪",
            used_model="mock-model",
            conversation_id=request.conversation_id,
        )

    monkeypatch.setattr(service, "answer", fake_answer)
    client.cookies.clear()

    register_response = client.post(
        "/auth/user/register",
        json={
            "name": "Lab User",
            "email": email,
            "visitor_profile": "lab_member",
            "password": "StrongPass123!",
            "invitation_code": settings.lab_member_invitation_code,
        },
    )
    assert register_response.status_code == 200

    response = client.post(
        "/chat",
        json={
            "student_name": "Lab User",
            "student_email": email,
            "course_context": "科研指导",
            "visitor_profile": "general_visitor",
            "question": "帮我看看该先读哪些资料。",
            "conversation_id": "chat-authenticated-visitor-profile",
        },
    )

    assert response.status_code == 200
    assert captured["request"].visitor_profile == "lab_member"
    assert callable(captured["answer_chunk_callback"])


def test_chat_rejects_unsupported_upload_type() -> None:
    response = client.post(
        "/chat",
        data={
            "student_name": "Alice",
            "question": "看看这个文件。",
        },
        files=[("files", ("diagram.png", b"not-a-text-file", "image/png"))],
    )

    assert response.status_code == 400
    assert "暂只支持" in response.text


def test_attachment_request_bypasses_upload_clarification(
    isolated_availability_store,
) -> None:
    client.cookies.clear()
    service._llm_client = AttachmentClarificationLLMClient()

    response = client.post(
        "/chat",
        data={
            "student_name": "Alice",
            "question": "请只概括附件里写了什么。",
        },
        files=[
            (
                "files",
                (
                    "draft-notes.txt",
                    b"Draft outline: related work, contribution framing, evaluation plan.",
                    "text/plain",
                ),
            )
        ],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["workflow_action"] != "ask_follow_up"
    assert "related work" in payload["answer"]


def test_health_exposes_neuromem_runtime_summary(isolated_availability_store) -> None:
    client.cookies.clear()
    service._conversation_store.add_exchange(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            question="我想讨论论文相关工作应该怎么开始？",
            course_context="科研指导",
        ),
        conversation_id="conv-neuromem-health",
        answer="建议先整理 related work，再明确 contribution framing。",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=1,
        booking_result=None,
    )
    service._conversation_store.search(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            question="根据刚才的上下文再提醒我一次。",
            course_context="科研指导",
        ),
        conversation_id="conv-neuromem-health",
    )

    health_response = client.get("/health")

    assert health_response.status_code == 200
    payload = health_response.json()
    assert payload["neuromem_service_type"] in (
        "NeuroMemConversationStore", "online_continual_memory",
    )
    assert payload["neuromem_collection_name"] == "conversation-memory"
    assert int(payload["neuromem_total_entries"]) >= 1
    assert int(payload["neuromem_event_count"]) >= 2
    assert int(payload["neuromem_write_count"]) >= 1
    assert int(payload["neuromem_query_count"]) >= 1
    assert payload["neuromem_last_event_type"] == "retrieve"
    assert "write_conversation" in payload["neuromem_recent_event_types"]
    assert "retrieve" in payload["neuromem_recent_event_types"]


def test_user_can_register_login_and_logout(isolated_availability_store) -> None:
    client.cookies.clear()

    anonymous_response = client.get("/auth/user/session")
    assert anonymous_response.status_code == 200
    assert anonymous_response.json() == {
        "is_authenticated": False,
        "mode": "guest",
        "account": None,
    }

    register_response = client.post(
        "/auth/user/register",
        json={
            "name": "Alice",
            "email": "alice@example.com",
            "visitor_profile": "lab_member",
            "password": "alice-password-123",
            "invitation_code": settings.lab_member_invitation_code,
        },
    )
    assert register_response.status_code == 200
    register_payload = register_response.json()
    assert register_payload["is_authenticated"] is True
    assert register_payload["mode"] == "user"
    assert register_payload["account"]["name"] == "Alice"
    assert register_payload["account"]["email"] == "alice@example.com"
    assert register_payload["account"]["visitor_profile"] == "lab_member"

    health_response = client.get("/health")
    assert health_response.status_code == 200
    health_payload = health_response.json()
    assert health_payload["registered_user_accounts"] == "1"
    assert health_payload["llm_status"] in {"not_checked", "ok", "error"}
    assert "llm_cache_entries" in health_payload

    session_response = client.get("/auth/user/session")
    assert session_response.status_code == 200
    assert session_response.json()["account"]["email"] == "alice@example.com"
    assert session_response.json()["account"]["visitor_profile"] == "lab_member"

    logout_response = client.post("/auth/user/logout")
    assert logout_response.status_code == 200
    assert logout_response.json() == {
        "is_authenticated": False,
        "mode": "guest",
        "account": None,
    }

    login_response = client.post(
        "/auth/user/login",
        json={
            "email": "alice@example.com",
            "password": "alice-password-123",
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["account"]["name"] == "Alice"
    assert login_response.json()["account"]["visitor_profile"] == "lab_member"


def test_user_registration_rejects_duplicate_email(isolated_availability_store) -> None:
    client.cookies.clear()

    first_response = client.post(
        "/auth/user/register",
        json={
            "name": "Alice",
            "email": "alice@example.com",
            "visitor_profile": "general_visitor",
            "password": "alice-password-123",
        },
    )
    assert first_response.status_code == 200

    client.post("/auth/user/logout")

    duplicate_response = client.post(
        "/auth/user/register",
        json={
            "name": "Alice 2",
            "email": "Alice@example.com",
            "visitor_profile": "hust_undergraduate",
            "password": "alice-password-456",
        },
    )
    assert duplicate_response.status_code == 409
    assert duplicate_response.json()["detail"] == "该邮箱已注册，请直接登录。"


def test_user_registration_requires_visitor_profile(
    isolated_availability_store,
) -> None:
    client.cookies.clear()

    response = client.post(
        "/auth/user/register",
        json={
            "name": "Alice",
            "email": "alice@example.com",
            "password": "alice-password-123",
        },
    )

    assert response.status_code == 422


def test_admin_knowledge_search_accepts_visitor_profile(
    isolated_availability_store,
) -> None:
    client.cookies.clear()
    service._knowledge_store.add_document(
        KnowledgeDocumentCreate(
            title="课程资料｜研究生论文写作课程材料",
            content="课程重点覆盖论文结构、related work、投稿准备和毕业论文常见问题。",
            tags=["homepage", "teaching", "courseware", "lecture"],
            source_name="homepage:contents/teaching/graduate-paper-writing-course.md#intro",
        )
    )
    service._knowledge_store.add_document(
        KnowledgeDocumentCreate(
            title="研究总览｜论文写作智能体",
            content="研究方向聚焦自动写作 agent、审稿反馈吸收和协同 drafting workflow。",
            tags=["homepage", "research", "publication", "overview"],
            source_name="homepage:research-overview",
        )
    )

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    paper_response = client.get(
        "/knowledge/search",
        params={
            "query": "论文写作相关内容我应该先看什么？",
            "visitor_profile": "paper_writing_student",
        },
    )
    lab_response = client.get(
        "/knowledge/search",
        params={
            "query": "论文写作相关内容我应该先看什么？",
            "visitor_profile": "lab_member",
        },
    )

    assert paper_response.status_code == 200
    paper_titles = [h["title"] for h in paper_response.json()["hits"]]
    assert "课程资料｜研究生论文写作课程材料" in paper_titles
    assert lab_response.status_code == 200
    lab_titles = [h["title"] for h in lab_response.json()["hits"]]
    assert "研究总览｜论文写作智能体" in lab_titles


def test_admin_login_unlocks_admin_endpoints(isolated_availability_store) -> None:
    client.cookies.clear()
    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200
    assert login_response.json()["is_admin"] is True

    knowledge_response = client.get("/knowledge")
    assert knowledge_response.status_code == 200

    availability_read_response = client.get("/availability")
    assert availability_read_response.status_code == 200

    availability_response = client.put(
        "/availability",
        json={
            "week_of": "2026-05-25",
            "days": [
                {
                    "date": "2026-05-26",
                    "windows": [{"start": "14:00", "end": "16:00"}],
                }
            ],
        },
    )
    assert availability_response.status_code == 200
    assert availability_response.json()["days"][0]["date"] == "2026-05-26"

    services_response = client.get("/admin/services")
    assert services_response.status_code == 200
    assert services_response.json()["action"] == "status"

    logout_response = client.post("/auth/admin/logout")
    assert logout_response.status_code == 200
    assert logout_response.json()["is_admin"] is False


def test_admin_can_control_managed_services(isolated_availability_store) -> None:
    runtime_manager = isolated_availability_store.runtime_manager
    client.cookies.clear()

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    status_response = client.get("/admin/services")
    assert status_response.status_code == 200
    assert status_response.json()["services"][0]["active_state"] == "active"

    restart_response = client.post("/admin/services/restart")
    assert restart_response.status_code == 200
    assert restart_response.json()["action"] == "restart"
    assert runtime_manager.calls == ["status", "restart"]


def test_admin_can_confirm_pending_booking(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()

    created_response = client.post(
        "/bookings",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文讨论",
            "preferred_start": "2026-05-26T14:00:00",
            "preferred_end": "2026-05-26T14:45:00",
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()
    assert created["booking"]["status"] == "待确认"

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    pending_response = client.get("/bookings", params={"status": "待确认"})
    assert pending_response.status_code == 200
    assert len(pending_response.json()) == 1

    confirm_response = client.post(
        f"/bookings/{created['booking']['booking_id']}/confirm"
    )
    assert confirm_response.status_code == 200
    assert confirm_response.json()["booking"]["status"] == "已确认"
    assert len(isolated_availability_store.approved_bookings) == 1


def test_admin_can_reject_pending_booking(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()

    created_response = client.post(
        "/bookings",
        json={
            "student_name": "Bob",
            "student_email": "bob@example.com",
            "topic": "科研讨论",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        },
    )
    assert created_response.status_code == 200
    created = created_response.json()

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    reject_response = client.post(
        f"/bookings/{created['booking']['booking_id']}/reject",
        json={"rejection_reason": "这周安排已满，请改约下周。"},
    )
    assert reject_response.status_code == 200
    assert reject_response.json()["booking"]["status"] == "已拒绝"
    assert (
        reject_response.json()["booking"]["rejection_reason"]
        == "这周安排已满，请改约下周。"
    )
    assert len(isolated_availability_store.rejected_bookings) == 1


def test_admin_can_load_previous_week_availability_template(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    previous_response = client.put(
        "/availability",
        json={
            "week_of": "2026-05-18",
            "days": [
                {
                    "date": "2026-05-18",
                    "windows": [{"start": "09:00", "end": "11:00"}],
                }
            ],
        },
    )
    assert previous_response.status_code == 200

    current_response = client.put(
        "/availability",
        json={
            "week_of": "2026-05-25",
            "days": [],
        },
    )
    assert current_response.status_code == 200

    template_response = client.get(
        "/availability/previous-week", params={"week_of": "2026-05-25"}
    )
    assert template_response.status_code == 200
    assert template_response.json()["days"][0]["date"] == "2026-05-25"


def test_admin_can_view_memory_profiles(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()
    service._llm_client = CollaborationMemoryLLMClient()

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-admin-memory",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    response = client.get("/memory/profiles", params={"limit": 20})
    assert response.status_code == 200
    payload = response.json()
    assert "collaboration_preference" in payload["available_categories"]
    assert payload["category_counts"]["collaboration_preference"] >= 1
    assert any(
        profile["category"] == "collaboration_preference"
        for profile in payload["profiles"]
    )


def test_admin_can_view_question_analytics_report(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()
    service._llm_client = CollaborationMemoryLLMClient()

    first = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-analytics-1",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )
    second = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Bob",
                student_email="bob@example.com",
                course_context="科研指导",
                conversation_id="conv-analytics-2",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )
    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Carol",
                student_email="carol@example.com",
                course_context="科研指导",
                conversation_id="conv-analytics-3",
                question="FlowRAG 主要做什么？",
            )
        )
    )

    feedback_response = client.post(
        "/chat/feedback",
        json={
            "exchange_id": first.exchange_id,
            "rating": "down",
            "resolved": False,
            "needs_human_followup": True,
            "issue_summary": "没有给出具体准备材料清单。",
        },
    )
    assert feedback_response.status_code == 200
    assert feedback_response.json()["needs_human_followup"] is True

    positive_feedback_response = client.post(
        "/chat/feedback",
        json={
            "exchange_id": second.exchange_id,
            "rating": "up",
        },
    )
    assert positive_feedback_response.status_code == 200
    assert positive_feedback_response.json()["resolved"] is True

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    report_response = client.get("/analytics/questions", params={"days": 30})
    assert report_response.status_code == 200
    payload = report_response.json()
    assert payload["overview"]["total_exchanges"] == 3
    assert payload["overview"]["feedback_count"] == 2
    assert payload["overview"]["unresolved_count"] == 1
    assert payload["overview"]["human_handoff_count"] == 1
    assert payload["top_clusters"][0]["count"] >= 2
    assert payload["top_clusters"][0]["interaction_domain"] == "advising"
    assert payload["knowledge_gap_suggestions"]
    assert payload["knowledge_gap_suggestions"][0]["sample_questions"]
    assert (
        payload["unresolved_questions"][0]["issue_summary"]
        == "没有给出具体准备材料清单。"
    )
    assert payload["handoff_categories"][0]["category"] == "advising"


def test_question_analytics_report_excludes_booking_interactions(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()

    booking_record = service._conversation_store.add_exchange(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="科研指导",
            conversation_id="conv-booking-report",
            question="帮我预约下周三下午讨论论文提纲。",
        ),
        conversation_id="conv-booking-report",
        answer="已记录预约请求，等待管理员确认。",
        workflow_action="book_meeting",
        interaction_domain="booking",
        knowledge_hit_count=0,
        booking_result=None,
    )
    advising_record = service._conversation_store.add_exchange(
        ChatRequest(
            student_name="Bob",
            student_email="bob@example.com",
            course_context="科研指导",
            conversation_id="conv-advising-report",
            question="见老师前论文提纲应该准备到什么程度？",
        ),
        conversation_id="conv-advising-report",
        answer="建议先准备研究问题、提纲、当前 blocker 和想讨论的决策点。",
        workflow_action="advise_only",
        interaction_domain="advising",
        knowledge_hit_count=1,
        booking_result=None,
    )

    service._analytics_store.submit_feedback(
        exchange_id=booking_record.memory_id,
        rating="down",
        resolved=False,
        needs_human_followup=True,
        issue_summary="这是预约申请，不该出现在问答周报里。",
    )
    service._analytics_store.submit_feedback(
        exchange_id=advising_record.memory_id,
        rating="up",
        resolved=True,
        needs_human_followup=False,
        issue_summary=None,
    )

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    report_response = client.get("/analytics/questions", params={"days": 30})
    assert report_response.status_code == 200
    payload = report_response.json()

    assert payload["overview"]["total_exchanges"] == 1
    assert payload["overview"]["feedback_count"] == 1
    assert payload["overview"]["unresolved_count"] == 0
    assert payload["overview"]["human_handoff_count"] == 0
    assert payload["top_clusters"][0]["interaction_domain"] == "advising"
    assert all(
        "预约" not in " ".join(cluster["sample_questions"])
        for cluster in payload["top_clusters"]
    )
    assert payload["unresolved_questions"] == []
    assert payload["handoff_categories"] == []


def test_admin_can_generate_and_publish_gap_draft(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()
    service._llm_client = CollaborationMemoryLLMClient()

    exchange_ids: list[str] = []
    for student_name in ("Alice", "Bob"):
        response = asyncio.run(
            service.answer(
                ChatRequest(
                    student_name=student_name,
                    student_email=f"{student_name.lower()}@example.com",
                    course_context="科研指导",
                    conversation_id=f"conv-gap-{student_name}",
                    question="和老师约时间前，我应该先准备什么？",
                )
            )
        )
        assert response.exchange_id is not None
        exchange_ids.append(response.exchange_id)

    feedback_response = client.post(
        "/chat/feedback",
        json={
            "exchange_id": exchange_ids[0],
            "rating": "down",
            "resolved": False,
            "issue_summary": "还缺少更标准的准备清单。",
        },
    )
    assert feedback_response.status_code == 200

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    report_response = client.get("/analytics/questions", params={"days": 30})
    assert report_response.status_code == 200
    gap = report_response.json()["knowledge_gap_suggestions"][0]

    create_response = client.post(
        "/analytics/questions/gap-drafts",
        json={"cluster_id": gap["cluster_id"], "days": 30},
    )
    assert create_response.status_code == 200
    draft = create_response.json()
    assert draft["cluster_id"] == gap["cluster_id"]
    assert draft["status"] == "draft"
    assert draft["title"].startswith("FAQ草稿｜")
    assert "建议 FAQ/知识正文草稿" in draft["content"]

    drafts_response = client.get("/analytics/questions/gap-drafts")
    assert drafts_response.status_code == 200
    drafts = drafts_response.json()
    assert len(drafts) == 1
    assert drafts[0]["draft_id"] == draft["draft_id"]

    publish_response = client.post(
        f"/analytics/questions/gap-drafts/{draft['draft_id']}/publish"
    )
    assert publish_response.status_code == 200
    published = publish_response.json()
    assert published["status"] == "published"
    assert published["published_document_id"]

    knowledge_response = client.get("/knowledge")
    assert knowledge_response.status_code == 200
    documents = knowledge_response.json()
    published_documents = [
        document
        for document in documents
        if document["document_id"] == published["published_document_id"]
    ]
    assert published_documents
    assert published_documents[0]["source_name"].startswith("knowledge-gap:advising:")
    assert published_documents[0]["title"].startswith("常见问题：")
    assert "faq-draft" not in published_documents[0]["tags"]

    refreshed_report_response = client.get("/analytics/questions", params={"days": 30})
    assert refreshed_report_response.status_code == 200
    refreshed_gap = refreshed_report_response.json()["knowledge_gap_suggestions"][0]
    assert refreshed_gap["draft_id"] == draft["draft_id"]
    assert refreshed_gap["draft_status"] == "published"


def test_admin_can_view_and_resolve_escalations(
    isolated_availability_store: RecordingNotifier,
) -> None:
    client.cookies.clear()

    response = client.post(
        "/chat",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": "科研指导",
            "conversation_id": "conv-escalation-admin",
            "question": "我想正式投诉这次成绩处理有误，请你马上联系老师。",
        },
    )
    assert response.status_code == 200
    escalation_record = response.json()["escalation_record"]
    assert escalation_record["route"] == "human_handoff"

    login_response = client.post(
        "/auth/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert login_response.status_code == 200

    list_response = client.get("/escalations", params={"status": "待处理"})
    assert list_response.status_code == 200
    payload = list_response.json()
    assert len(payload) == 1
    assert payload[0]["route"] == "human_handoff"

    resolve_response = client.post(
        f"/escalations/{escalation_record['escalation_id']}/resolve",
        json={"resolution_note": "已由老师本人接手处理。"},
    )
    assert resolve_response.status_code == 200
    assert resolve_response.json()["status"] == "已处理"
    assert resolve_response.json()["resolution_note"] == "已由老师本人接手处理。"
