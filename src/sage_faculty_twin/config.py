from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_DIR = REPO_ROOT.parent / "sage-mate-runtime-private"
DEFAULT_RUNTIME_SEED_DATA_DIR = REPO_ROOT / "release/runtime-seed/data"


class AppSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DIGITAL_TWIN_",
        env_file=(".env", str(REPO_ROOT.parent / "SAGE" / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    owner_name: str = Field(default="张书豪")
    owner_role: str = Field(default="华中科技大学计算机学院教师")
    model_name: str = Field(default="")
    llm_base_url: str = Field(default="http://127.0.0.1:8000/v1")
    local_model_backend: str = Field(
        default="none",
        pattern="^(none|vllm_metal)$",
        description="Local model runtime selected by Sage Mate installers. "
        "Remote endpoints should only be used when the user explicitly configures them.",
    )
    vllm_metrics_url: str = Field(
        default="",
        description="Optional authenticated vLLM Prometheus metrics URL. "
        "Set this when DIGITAL_TWIN_LLM_BASE_URL points at a proxy that does "
        "not expose /metrics.",
    )
    api_key: str = Field(default="EMPTY")
    intent_model_name: str = Field(default="")
    intent_llm_base_url: str = Field(default="")
    llm_timeout_seconds: int = Field(default=60, ge=1, le=300)
    llm_retry_attempts: int = Field(default=2, ge=0, le=5)
    llm_retry_backoff_seconds: float = Field(default=1.0, ge=0.0, le=30.0)
    llm_tool_calling_mode: str = Field(
        default="compat",
        pattern="^(compat|native)$",
        description="Tool execution transport. 'compat' runs approved read-only manifest tools "
        "and uses an ordinary chat completion for synthesis; 'native' requires the upstream "
        "OpenAI-compatible server to be launched with tool-call parsing enabled.",
    )
    skill_tool_parallelism: int = Field(
        default=4,
        ge=1,
        le=16,
        description="Maximum parallel read-only tool calls within one agent skill.",
    )
    skill_answer_max_tokens: int = Field(
        default=768,
        ge=128,
        le=2048,
        description="Maximum completion tokens for one agent-skill answer.",
    )
    llm_policy_enabled: bool = Field(default=True)
    llm_policy_variant_kind: str = Field(default="ablation")
    llm_policy_variant_name: str = Field(default="adaptive-controller")
    llm_policy_execution_priority_mode: str = Field(default="off")
    llm_policy_output_max_tokens_cap: int = Field(default=4096, ge=64, le=8192)
    llm_policy_output_min_tokens_floor: int = Field(default=192, ge=32, le=4096)
    llm_fast_answer_max_tokens: int = Field(
        default=1024,
        ge=128,
        le=4096,
        description="Default max_tokens for non-thinking interactive answers.",
    )
    llm_deep_answer_max_tokens: int = Field(
        default=256,
        ge=128,
        le=4096,
        description=(
            "Maximum completion tokens for explicit deep-thinking answers. "
            "Keep this bounded on low-throughput accelerators."
        ),
    )
    llm_answer_temperature: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Default temperature for final student-facing answers.",
    )
    llm_answer_frequency_penalty: float = Field(
        default=0.35,
        ge=-2.0,
        le=2.0,
        description="OpenAI-compatible frequency penalty used to reduce repeated phrases.",
    )
    llm_answer_presence_penalty: float = Field(
        default=0.0,
        ge=-2.0,
        le=2.0,
        description="OpenAI-compatible presence penalty for final answers.",
    )
    llm_answer_repetition_penalty: float = Field(
        default=1.08,
        ge=1.0,
        le=2.0,
        description="vLLM repetition penalty for final answers when supported by the backend.",
    )
    fast_answer_concise_guidance_enabled: bool = Field(default=True)
    chat_runtime_pipeline_enabled: bool = Field(
        default=False,
        description="When True, run chat through FlowNetEnvironment DAG runtime. "
        "When False, use the in-process stage chain to avoid per-request runtime compilation.",
    )
    post_answer_background: bool = Field(
        default=True,
        description="Run post-answer persistence outside the response critical path when a trace consumer is attached.",
    )
    prompt_soft_cap: int = Field(
        default=24000,
        ge=1,
        le=1000000,
        description="Maximum assembled chat prompt size in characters before deterministic truncation.",
    )
    chat_request_timeout_seconds: float = Field(default=80.0, ge=1.0, le=300.0)
    chat_max_inflight_requests: int = Field(
        default=1,
        ge=1,
        le=64,
        description=(
            "Maximum number of interactive /chat workflows admitted at once. "
            "Keep this aligned with the deployed engine's usable batching capacity."
        ),
    )
    chat_admission_timeout_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=30.0,
        description=(
            "How long an interactive request waits for a chat slot before receiving "
            "a retryable 429 instead of timing out at the edge."
        ),
    )
    chat_sse_keepalive_seconds: float = Field(default=15.0, ge=1.0, le=90.0)
    stream_chat_answer: bool = Field(
        default=False,
        description="Use upstream streaming transport internally; public delivery remains validated-answer-only.",
    )
    warm_service_on_startup: bool = Field(
        default=True,
        description="When True, construct the DigitalTwinService during app startup "
        "so the first real chat request does not pay the lazy initialization cost.",
    )
    app_profile: str = Field(
        default="faculty_twin",
        pattern="^(faculty_twin|code_assistant|auto_scientist)$",
        description="Local desktop product profile. faculty_twin keeps the current "
        "digital-twin experience; code_assistant enables local code tools; "
        "auto_scientist combines research workflow guidance with local code tools.",
    )
    deployment_mode: str = Field(
        default="hosted",
        pattern="^(hosted|local_code)$",
        description="hosted disables local repository tools; local_code enables "
        "user-installed code workbench access to explicitly selected local paths.",
    )
    code_workbench_enabled: bool = Field(
        default=False,
        description="Enable local repository tools. Effective only in local_code mode.",
    )
    code_workspace_roots: str = Field(
        default="",
        description="Comma-separated allowlist of local repository roots for local_code mode.",
    )
    code_workspace_root: Path = Field(
        default=REPO_ROOT.parent / "sage-mate-code-workspaces",
        description="Legacy managed workspace root used when code_workspace_roots is empty.",
    )
    code_session_dir: Path = Field(
        default=Path("data/code_sessions"),
        description="Runtime-private storage for local Code Assistant sessions.",
    )
    code_command_timeout_seconds: int = Field(default=20, ge=1, le=120)
    code_agent_backend: str = Field(
        default="internal",
        pattern="^(internal|claude_hust)$",
        description="Code assistant backend. internal uses the built-in propose-only harness; "
        "claude_hust delegates code ask/propose to a user-installed claude-hust CLI.",
    )
    claude_hust_cli_path: str = Field(
        default="",
        description="Optional path to the user-installed claude-hust executable.",
    )
    claude_hust_timeout_seconds: int = Field(default=180, ge=10, le=900)
    llm_policy_congestion_waiting_threshold: float = Field(default=1.0, ge=0.0, le=10000.0)
    llm_policy_congestion_kv_usage_threshold: float = Field(default=0.75, ge=0.0, le=1.0)
    llm_policy_congestion_total_requests_threshold: float = Field(
        default=8.0, ge=0.0, le=10000.0
    )
    llm_cache_ttl_seconds: int = Field(default=3600, ge=0, le=86400)
    llm_cache_max_entries: int = Field(default=512, ge=0, le=4096)
    system_prompt: str = Field(
        default=(
            "You are a trusted academic digital twin. Answer clearly, avoid fabricating policy, "
            "and route scheduling requests into structured booking guidance."
        )
    )
    runtime_dir: Path = Field(
        default=DEFAULT_RUNTIME_DIR,
        description="External runtime-data repository root. Production data, KB, "
        "logs, account records, and mutable state should live here instead of "
        "inside the code repository.",
    )
    owner_style_profile_path: Path = Field(
        default=DEFAULT_RUNTIME_SEED_DATA_DIR / "persona/style_profile.md"
    )
    homepage_dir: Path = Field(default=REPO_ROOT / "data/homepage")
    homepage_public_url: str = Field(default="")
    booking_timezone: str = Field(default="Asia/Shanghai")
    booking_start_hour: int = Field(default=9, ge=0, le=23)
    booking_end_hour: int = Field(default=18, ge=1, le=24)
    meeting_duration_minutes: int = Field(default=30, ge=15, le=120)
    availability_schedule_path: Path = Field(default=Path("data/availability/current_week.json"))
    booking_notification_email: str = Field(default="faculty@example.edu")
    calendar_bridge_enabled: bool = Field(
        default=False,
        description="Enable read-only access to the owner's live calendar through a local bridge.",
    )
    calendar_bridge_url: str = Field(
        default="",
        description="Base URL for the read-only yxcal bridge, typically an SSH reverse tunnel on 127.0.0.1.",
    )
    calendar_bridge_token: str = Field(
        default="",
        description="Bearer token required by the read-only yxcal bridge.",
    )
    calendar_bridge_lookahead_days: int = Field(default=14, ge=1, le=31)
    calendar_bridge_max_events: int = Field(default=40, ge=1, le=100)
    calendar_bridge_timeout_seconds: float = Field(default=2.5, ge=0.2, le=15.0)
    calendar_bridge_include_description: bool = Field(default=False)
    smtp_host: str | None = Field(default=None)
    smtp_port: int = Field(default=587, ge=1, le=65535)
    smtp_username: str | None = Field(default=None)
    smtp_password: str | None = Field(default=None)
    smtp_sender: str | None = Field(default=None)
    smtp_use_tls: bool = Field(default=True)
    smtp_use_ssl: bool = Field(default=False)
    smtp_timeout_seconds: int = Field(default=15, ge=1, le=120)
    knowledge_base_dir: Path = Field(default=Path("data/knowledge_base"))
    knowledge_backend: str = Field(default="neuromem")
    # Neuromem search index choice. "auto" prefers faiss (dense retrieval via
    # sentence-transformers) and falls back to bm25 when unavailable.
    neuromem_index_type: str = Field(default="auto")
    neuromem_embedding_model: str = Field(default="BAAI/bge-small-zh-v1.5")
    neuromem_embedding_dim: int = Field(default=512, ge=32, le=4096)
    retrieval_top_k: int = Field(default=3, ge=1, le=10)
    knowledge_search_cache_ttl_seconds: int = Field(default=300, ge=0, le=3600)
    knowledge_search_cache_max_entries: int = Field(default=256, ge=0, le=4096)
    web_search_enabled: bool = Field(default=True)
    web_search_timeout_seconds: float = Field(default=8.0, ge=1.0, le=30.0)
    web_search_max_results: int = Field(default=3, ge=1, le=8)
    web_search_auto_trigger: bool = Field(default=False)
    tavily_api_key: str = Field(default="")
    conversation_memory_dir: Path = Field(default=Path("data/conversation_memory"))
    online_presence_dir: Path = Field(default=Path(".runtime/online_presence"))
    online_presence_window_seconds: int = Field(default=300, ge=60, le=3600)
    online_presence_retention_seconds: int = Field(default=172800, ge=3600, le=604800)
    artifact_memory_draft_dir: Path = Field(default=Path("data/artifact_memory_drafts"))
    knowledge_gap_draft_dir: Path = Field(default=Path("data/knowledge_gap_drafts"))
    escalation_queue_dir: Path = Field(default=Path("data/escalations"))
    follow_up_queue_dir: Path = Field(default=Path("data/follow_up_actions"))
    operations_task_state_dir: Path = Field(default=Path("data/operations_task_state"))
    runtime_feature_flags_path: Path = Field(default=Path("data/runtime_feature_flags.json"))
    suggestion_board_dir: Path = Field(default=Path("data/suggestions"))
    user_account_store_dir: Path = Field(default=Path("data/user_accounts"))
    slack_user_link_dir: Path = Field(default=Path("data/slack_user_links"))
    workflow_policy_path: Path = Field(
        default=DEFAULT_RUNTIME_SEED_DATA_DIR
        / "workflow_policies/faculty-default-2026-05.json"
    )
    workflow_scenario_path: Path = Field(
        default=DEFAULT_RUNTIME_SEED_DATA_DIR
        / "workflow_scenarios/v3_preview_scenarios.json"
    )
    planner_comparison_dir: Path | None = Field(default=None)
    planner_metrics_dir: Path | None = Field(default=None)
    thinking_token_budget: int | None = Field(
        default=2048, ge=64, le=4096,
    )
    auto_disable_thinking_intents: str = Field(
        default="general,booking",
    )
    fast_intent_classifier_enabled: bool = Field(
        default=True,
        description="When True, skip the LLM intent classifier for high-confidence "
        "requests that can be routed by deterministic guardrails.",
    )
    shadow_planner_enabled: bool = Field(
        default=False,
        description="Run the experimental LLM shadow planner before each chat request. "
        "Disabled by default because it does not affect execution and adds model latency.",
    )
    shadow_planner_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    shadow_planner_max_tokens: int = Field(default=384, ge=64, le=2048)
    conversation_memory_top_k: int = Field(default=4, ge=1, le=10)
    # Conversation memory collection type. "auto" defaults to trainable
    # neural continual memory for short-term conversation recall.
    conversation_memory_collection_type: str = Field(default="auto")
    # Conversation memory index type. Use "auto" to prefer vector-capable indexes
    # (sage_vdb_ann/sagedb_ann/faiss) and fall back to segment/fifo when unavailable.
    conversation_memory_index_type: str = Field(default="auto")
    conversation_memory_neural_feature_dim: int = Field(default=128, ge=32, le=2048)
    conversation_memory_neural_learning_rate: float = Field(default=0.15, ge=0.001, le=1.0)
    conversation_memory_neural_weight_decay: float = Field(default=0.01, ge=0.0, le=1.0)
    conversation_memory_neural_replay_buffer_size: int = Field(default=256, ge=16, le=8192)
    conversation_memory_neural_replay_batch_size: int = Field(default=8, ge=1, le=512)
    conversation_memory_neural_score_blend: float = Field(default=0.5, ge=0.0, le=1.0)
    conversation_memory_neural_recency_bias: float = Field(default=0.0, ge=0.0, le=2.0)
    conversation_memory_neural_query_overlap_bias: float = Field(default=0.0, ge=0.0, le=2.0)
    sagevdb_embedding_backend: str = Field(default="sentence-transformers")
    sagevdb_embedding_model: str = Field(default="sentence-transformers/all-MiniLM-L6-v2")
    sagevdb_dimension: int = Field(default=256, ge=32, le=4096)
    sagevdb_backend: str = Field(default="cpp")
    sagevdb_anns_algorithm: str = Field(default="faiss_hnsw")
    service_manager_script: Path = Field(default=REPO_ROOT / "manage.sh")
    capability_plugin_dir: Path = Field(
        default=DEFAULT_RUNTIME_SEED_DATA_DIR / "capability_plugins"
    )
    skill_dir: Path = Field(default=Path("data/skills"))
    changelog_path: Path = Field(default=Path("data/changelog.json"))
    # --- Context Digest (rolling conversation compression) ---
    context_digest_enabled: bool = Field(default=True)
    context_digest_turn_threshold: int = Field(
        default=4, ge=2, le=16,
    )
    context_digest_max_chars: int = Field(default=1500, ge=200, le=4000)
    context_digest_dir: Path = Field(default=Path("data/conversation_memory/digests"))
    admin_username: str = Field(default="admin")
    admin_password: str = Field(default="change-me-admin-password")
    manager_username: str = Field(default="manager")
    manager_password: str = Field(default="hust-manager")
    admin_session_secret: str = Field(default="change-me-admin-session-secret")
    admin_session_ttl_seconds: int = Field(default=43200, ge=300, le=604800)
    user_session_secret: str = Field(default="change-me-user-session-secret")
    user_session_ttl_seconds: int = Field(default=2592000, ge=300, le=7776000)
    # --- Lab member invitation gate ---
    lab_member_invitation_code: str = Field(
        default="SAGE-LAB-2026",
        description="Invitation code required for lab_member registration.",
    )
    lab_member_invitation_code_enabled: bool = Field(
        default=True,
        description="When True, lab_member registration requires a valid invitation code.",
    )
    # --- DeltaKV session continuity ---
    kv_continuity_enabled: bool = Field(
        default=False,
        description="Enable DeltaKV-aware session continuity hints. When True, "
        "the chat client annotates requests with a stable session identifier "
        "so the vLLM external prefix cache (via DeltaKV connector) can match "
        "against previously transferred KV state after a server restart.",
    )
    kv_continuity_session_prefix: str = Field(
        default="twin-session",
        description="Prefix for the stable session identifier used in KV "
        "transfer annotations. The full session key is "
        "'{prefix}-{user_id}-{conversation_id}'.",
    )
    kv_fixed_prefix_materialization_enabled: bool = Field(
        default=False,
        description="When True, annotate vLLM-HUST OpenAI requests with a "
        "stable cache_salt for the fixed system/persona prompt prefix.",
    )
    kv_fixed_prefix_anchor_prefix: str = Field(
        default="sage-mate-fixed-prefix",
        description="Human-readable prefix used when constructing the stable "
        "fixed-prompt KV anchor id.",
    )
    kv_fixed_prefix_anchor_version: str = Field(
        default="v1",
        description="Operator-controlled version for invalidating fixed-prompt "
        "KV anchors after prompt-template or model changes.",
    )
    kv_fixed_prefix_warmup_on_startup: bool = Field(
        default=True,
        description="When True, issue one tiny startup request to materialize "
        "the stable system/persona/installed-skill prefix in vLLM's prefix cache.",
    )
    kv_fixed_prefix_warmup_max_tokens: int = Field(
        default=1,
        ge=1,
        le=16,
        description="Maximum output tokens for fixed-prefix startup warmup.",
    )
    dynamic_context_materialization_enabled: bool = Field(
        default=True,
        description="When True, place reusable non-private retrieved KB context "
        "before per-user fields so hot dynamic prompt prefixes can be reused by "
        "vLLM's native prefix cache.",
    )
    segment_reuse_hints_enabled: bool = Field(
        default=False,
        description="When True, attach segment-reuse extra_key metadata to "
        "vLLM-HUST OpenAI requests. This is fail-open control-plane metadata "
        "for segment-aware runtimes and does not replace exact prefix caching.",
    )
    segment_reuse_namespace_prefix: str = Field(
        default="sage-mate",
        description="Namespace prefix used when constructing segment-reuse extra_key values.",
    )
    segment_reuse_boundary_class: str = Field(
        default="control-only",
        description="Segment-reuse boundary class advertised in extra_key metadata.",
    )
    segment_reuse_max_leading_tokens: int = Field(
        default=4096,
        ge=1,
        le=131072,
        description="Upper bound advertised for the dynamic leading envelope. "
        "The exact leading token count is intentionally omitted until the app "
        "can compute it with the same tokenizer/chat template as vLLM-HUST.",
    )
    installed_skill_prompt_enabled: bool = Field(
        default=True,
        description="When True, include the curated installed-skill prompt prefix "
        "from the runtime repository in the system prompt.",
    )
    installed_skill_prompt_path: Path = Field(
        default=Path("data/installed_skills/fixed_prompt_skills.md"),
        description="Runtime path to low-risk fixed skill guidance loaded into "
        "the stable system prompt.",
    )

    @model_validator(mode="after")
    def apply_runtime_dir_defaults(self) -> "AppSettings":
        runtime_root = self.runtime_dir
        defaults: dict[str, Path] = {
            "homepage_dir": runtime_root / "data/homepage",
            "availability_schedule_path": runtime_root / "data/availability/current_week.json",
            "knowledge_base_dir": runtime_root / "data/knowledge_base",
            "conversation_memory_dir": runtime_root / "data/conversation_memory",
            "code_session_dir": runtime_root / "data/code_sessions",
            "online_presence_dir": runtime_root / ".runtime/online_presence",
            "artifact_memory_draft_dir": runtime_root / "data/artifact_memory_drafts",
            "knowledge_gap_draft_dir": runtime_root / "data/knowledge_gap_drafts",
            "escalation_queue_dir": runtime_root / "data/escalations",
            "follow_up_queue_dir": runtime_root / "data/follow_up_actions",
            "operations_task_state_dir": runtime_root / "data/operations_task_state",
            "runtime_feature_flags_path": runtime_root / "data/runtime_feature_flags.json",
            "suggestion_board_dir": runtime_root / "data/suggestions",
            "user_account_store_dir": runtime_root / "data/user_accounts",
            "slack_user_link_dir": runtime_root / "data/slack_user_links",
            "skill_dir": runtime_root / "data/skills",
            "changelog_path": runtime_root / "data/changelog.json",
            "context_digest_dir": runtime_root / "data/conversation_memory/digests",
            "installed_skill_prompt_path": runtime_root
            / "data/installed_skills/fixed_prompt_skills.md",
        }
        for field_name, runtime_default in defaults.items():
            if field_name not in self.model_fields_set:
                setattr(self, field_name, runtime_default)
        return self


settings = AppSettings()
