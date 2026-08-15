# Changelog

## Unreleased

## v4.6.21 - 2026-08-15

### Fixed

- 客户端断开或请求预算耗尽时，除取消 asyncio 外壳外，还会通过 request-id 注册表、浏览器 workflow SSE、OpenAI 代理和请求独占 socket 逐层传播取消，避免旧模型请求继续后台运行、阻塞重启或占用队列。
- 发送按钮在处理中变为可点击的停止控件；点击停止和页面离开会显式通知后端，不再受空输入框 `required` 校验阻挡。
- 聊天请求账本增加模型调用/重试、TTFT、模型总耗时、缓存命中与未归因耗时，便于定位端到端尾延迟。
- 伙伴装扮面板的响应式高度现在包含内边距与边框，避免 393px 窄屏下切换标签后越出视口。

### Added

- 新增可配置、无机器硬编码的 HTTP/2 线上延迟验收工具，覆盖三类 10 次 warm workload、引用覆盖、时间归因和混合负载 admission。

## v4.6.20 - 2026-08-15

### Changed

- 将聊天超时改为从请求入口开始计算的统一总预算，覆盖解析、fast path、排队、SAGE 工作流与模型重试。
- 为模型同步/流式请求和退避重试传播绝对 deadline，预算耗尽后不再启动无效的后台重试。
- 在聊天响应中加入可核对的请求 trace ID、总耗时、剩余预算、API 阶段耗时与工作流 trace 汇总。

## v4.6.19 - 2026-08-13

### Fixed

- 对“研究项目第一周如何规划”这类有界问题走本地结构化快路径，避免模型重复/串题并将响应压到秒级。

## v4.6.18 - 2026-08-13

### Fixed

- 将非深度交互回答预算进一步收紧到 192 token；超长内容不再触发额外续写，优先保证可用响应时间。

## v4.6.17 - 2026-08-13

### Fixed

- 将非深度交互回答预算从 1024 收紧到 256 token，避免低吞吐 NPU 上普通问题生成几十秒；深度思考仍使用独立预算。

## v4.6.16 - 2026-08-13

### Fixed

- 允许浏览器生成的访客会话安全触发上下文压缩，避免未登录用户误收到 401。
- 默认使用有界的确定性会话摘要，避免压缩额外占用 NPU 请求槽。
- 将非深度模式的模型修复重试限制为单次，避免异常回答触发 60–80 秒的重复生成。

## v4.6.15 - 2026-08-13

### Fixed

- Replaced translucent composer action borders with opaque contrast-safe tokens for reliable 3:1+ boundaries in both themes.

## v4.6.14 - 2026-08-13

### Fixed

- Made disabled/unsupported composer actions opaque, contrast-safe, and explicitly marked; no longer rely on opacity to communicate unavailable input.
- Kept Token expanded state at normal scale; only the instantaneous press uses scale feedback.

## v4.6.13 - 2026-08-13

### Fixed

- Unified composer send, microphone, dice, token, workflow, and upload controls under readable theme-aware action tokens.
- Added explicit hover, focus, active, selected, and disabled affordances without relying on low opacity alone.

## v4.6.12 - 2026-08-12

### Fixed

- Kept the wandering mobile companion in the open greeting area on the empty landing screen.

## v4.6.11 - 2026-08-12

### Fixed

- Corrected mobile companion coordinates when the chat shell establishes a fixed-position containing block.

## v4.6.10 - 2026-08-12

### Fixed

- Darkened the cyan gradient stops used behind deep-thinking labels so every stop keeps white text at least 4.5:1 contrast.

## v4.6.9 - 2026-08-12

### Fixed

- Re-clamped the mobile companion after composer reflow so it cannot cover the input or recommendation chips.

## v4.6.8 - 2026-08-12

### Fixed

- Kept the selected theme button at its resting scale and moved it to the right edge on narrow screens.

## v4.6.7 - 2026-08-12

### Added

- Added an explicit sun/moon theme switch with semantic midnight-glass and cool light palettes.
- Persisted the visitor's theme choice while respecting system preference until explicitly changed.
- Added light-theme contrast coverage for the composer, Support evidence, status states, and narrow screens.

## v4.6.6 - 2026-08-12

### Changed

- Consolidated composer mode controls into one accessible state system with readable deep-thinking and web-search checked, processing, focus, hover, and disabled states.
- Introduced a midnight glass design token system with deep navy surfaces, indigo/electric-cyan accents, and semantic success/warning/error foreground and surface tokens.
- Restyled Support evidence cards, status badges, workflow states, and composer placeholder text for dark-surface contrast.

## v4.6.5 - 2026-08-12

### Changed

- Redesigned deep-thinking progress as a full-width Codex-style reasoning state with persistent selected-mode visibility, progress rail, and completion summary.

## v4.6.4 - 2026-08-12

### Fixed

- Shortcut responses now expose context reuse, local retrieval, workflow trace, memory write-back, and model context capacity instead of rendering all context fields as zero.
- The token/context badge now remains visible for model-bypass responses and clearly reports zero generated tokens rather than hiding the metadata.

## v4.6.3 - 2026-08-12

### Fixed

- Migrated application and vLLM proxy startup/shutdown to FastAPI lifespan handlers.
- Synchronized the machine-local application model label with the served DeepSeek backend to remove misleading startup fallback warnings.

## v4.6.2 - 2026-08-12

### Changed

- Fast-path answers now persist conversation exchanges before returning, so short follow-ups retain their subject.
- Resolved contextual follow-ups can use the local evidence lane and return grounded answers without an unnecessary model round-trip.
- Answer delivery validation now tolerates URLs and technical English terms inside substantive Chinese answers.

### Fixed

- Fixed follow-up requests losing context or timing out after a fast first answer.
- Fixed Chinese web-search answers being rejected as language mismatches and surfacing as HTTP 500.

## v4.6.1 - 2026-08-10

### Changed

- Sage Mate now runs the DeepSeek backend on NPU 4–7 with a portable deployment lock and runtime verification path.
- Research/course factual answers use SAGE evidence first and expose an explicit basis instead of silently presenting unsupported text.
- The lucky-question dice now combines multiple templates and rotates recent topic, analysis lens, outcome, and wording dimensions.

### Fixed

- Bounded interactive chat admission and retry behavior under NPU contention to avoid avoidable 504 responses.
- Added deterministic policy boundaries for prompt/credential disclosure and explicit unknown answers when evidence is insufficient.
- Added a fast path for baseline/fair-comparison/ablation research-direction questions, reducing them from model-timeout latency to a short structured response.
- Corrected the personal homepage link to `https://me.sage.org.ai/` and synchronized the displayed runtime model name with the served `/v1/models` backend.

## v4.5.0 - 2026-07-10

### Changed

- Renamed the product and repository identity to Sage Mate / `sage-mate`; Faculty Twin is now treated as a profile inside Sage Mate.
- Updated installer, release bundle, runtime directory, and systemd unit names to use the `sage-mate` slug.
- Bumped package and app version metadata to `4.5.0`.

### Fixed

- macOS local startup can enter Sage Mate before the bundled Apple GPU model finishes loading, so users can configure a remote endpoint instead of being blocked by local model startup.
- Local Code Assistant keeps using bundled `claude-code-hust` and the local OpenAI-compatible proxy without requiring users to clone extra repositories.

## v4.4.0 - 2026-07-09

### Added

- **Hosted/web release installer**: adds `release/hosted-web.sh`, a fresh-machine installer that
  clones/updates the repo, auto-selects NVIDIA/CUDA or Ascend/NPU hosted inference, configures
  hosted/web safety defaults, installs pinned runtime dependencies, configures Cloudflare tunnel
  DNS/ingress when credentials are available, starts systemd services, and runs
  `manage.sh verify-hosted-web`.
- **Encrypted release secrets hook**: supports an OpenSSL-encrypted `release/secrets.env.enc`
  bundle that deployers can decrypt with a server-local key file and merge into `.env` without
  printing token values.
- **NVIDIA model presets**: supports stable Qwen3-32B, large Qwen3-Next 80B AWQ, and conservative
  Qwen2.5-14B AWQ presets for hosted/web NVIDIA deployments.

### Fixed

- **NVIDIA vLLM proxy wiring**: defaults NVIDIA engine traffic to `127.0.0.1:18000` and lets the
  proxy read `VLLM_PROXY_UPSTREAM_BASE_URL` from `.env`, avoiding stale systemd defaults on `8000`.
- **Qwen3-Next chat template override**: NVIDIA vLLM launcher can pass `VLLM_NVIDIA_CHAT_TEMPLATE`
  through to vLLM for quantized snapshots that do not ship tokenizer chat templates.

## v4.3.5 - 2026-06-29

`v4.3.5` restores the Faculty Twin guided onboarding layout for hosted web users.

### Fixed

- **Onboarding side layout**: keeps the beginner guide in the left empty column while the chat
  transcript and composer stay together in the right column, including after the first guided reply.
- **Layout regression guardrails**: adds frontend contract checks so onboarding remains visible and
  independent from the empty-chat state.

## v4.3.4 - 2026-06-29

`v4.3.4` tightens the local Sage Mate Code Assistant integration and macOS packaging metadata.

### Added

- **Claude Hust backend adapter**: Code Assistant can delegate ask/propose flows to the local
  `claude-hust` backend through a dedicated adapter while keeping Sage Mate in charge of profile,
  workspace allowlist, trace, and safety boundaries.
- **Code Assistant sessions**: adds a local runtime-private Code Session model and API surface for
  future coding conversations, isolated from Faculty Twin chat history.
- **Code doctor**: adds a `/code doctor` diagnostic path for local Code Assistant setup checks.

### Changed

- **macOS bundle versioning**: the DMG build now writes the project version into `Sage Mate.app`
  instead of hardcoding `1.0`.
- **Profile model**: local Sage Mate keeps a clear two-profile model: Faculty Twin or Code Assistant.

### Safety

- Hosted/web deployments reject `/code/*` APIs before admin fallback. Code tools remain local-only,
  allowlist-bound, and propose-only by default.

## v4.3.3 - 2026-06-28

`v4.3.3` packages Sage Mate as a macOS local app release and stabilizes the multi-profile local experience.

### Added

- **Sage Mate macOS DMG**: one-click local app packaging with an embedded native WebKit shell and local backend launcher.
- **Local Code Assistant Profile**: profile-specific entry, workspace guidance, code command surface, and propose-only coding workflow.
- **Profile switcher**: local app users can switch between Faculty Twin and Code Assistant without mixing conversation history.

### Fixed

- **macOS window reopen lifecycle**: closing the red traffic-light window now hides the window instead of destroying it, and reopening the app/Dock icon restores the Sage Mate window.
- **Profile isolation**: Faculty Twin and Code Assistant histories stay separate, preventing cross-profile context leakage in the local UI.
- **Runtime bootstrapping**: local app startup prefers an existing cloned faculty-twin runtime repository before creating a fresh runtime folder.

### Safety

- Code Assistant remains local-only and propose-only by default. The hosted Faculty Twin web deployment does not receive, clone, store, or execute user repositories.

## v4.2.4 - 2026-06-20

`v4.2.4` is a systematic overhaul of the answer basis ("依据") system. No more ad-hoc patches.

### Refactored

- **`_build_answer_basis` rewritten** with documented design rules, numbered sections, and a safety-net filter that guarantees "近期交流记录" can never appear regardless of code path.
- **Deleted `_build_recent_session_basis_item`**: dead code that produced session-context citations.
- **Simplified `_build_memory_basis_item`**: removed the unreachable short-term conversation branch. Only artifact (uploaded) and long-term profile memory are eligible.
- **Memory filtering moved upstream**: eligible hits are pre-filtered before sorting, eliminating inline `continue` guards.

### Design Invariants (enforced)

1. Session context is NEVER cited — it is implicit and always visible in chat.
2. Short-term conversation memory is NEVER cited — same reason.
3. Knowledge hits are deduplicated by canonical source group (no multi-part duplicates).
4. Generic index/listing pages are filtered (no "论文索引" crowding).
5. Safety-net filter strips any "近期交流记录" label regardless of origin.

## v4.2.3 - 2026-06-20

`v4.2.3` fixes intermittent false "error" status and shows the active model name in the top bar.

### Fixed

- **LLM status false-error**: backend used raw counters (`self._error_count`) instead of effective counters (`eff_error_count`) for status comparison, causing stale Prometheus data to trigger false "error" status. Now uses effective counters consistently.
- **Status pill logic**: frontend now trusts `llm_status` from the backend (based on most recent activity) instead of independently checking cumulative `errorCount`. Eliminates phantom "LLM 1err" display after recovered transient errors.

### Added

- **Model name in top bar**: `model-pill` now displays "模型 {name}" using `data.model_name` from the health endpoint, instead of the generic "连接已就绪".

## v4.2.2 - 2026-06-20

`v4.2.2` removes "近期交流记录" from the visible basis section entirely — session context is an implicit reference that never needs citation.

### Changed

- **Remove session context from basis display**: recent conversation history is always available to the LLM but no longer shown as a "依据" item, since the chat UI already displays it.

## v4.2.1 - 2026-06-20

`v4.2.1` fixes the "依据" (answer basis) section showing repeated identical citations across consecutive turns.

### Fixed

- **Knowledge basis deduplication by source group**: multiple parts of the same document (e.g. `part-1`, `part-2`) now collapse into a single basis item, preventing the same page from dominating the citation list.
- **Generic index page filter**: broad listing pages like "论文索引" that match too many queries are now skipped as answer basis items, freeing slots for more specific, relevant citations.
- **Recent session context throttling**: the "近期交流记录" basis item now only appears when there are ≥2 prior exchanges in the session, avoiding redundant echoes of the visible chat history.

## v4.2.0 - 2026-06-20

`v4.2.0` adds on-demand context compression, letting users manually trigger conversation context compression from the token usage panel.

### Added

- **Manual context compression trigger**: a "压缩上下文" button inside the token usage detail panel (accessible by clicking the token icon in the composer). When clicked, the service compresses all unsummarized conversation turns into a rolling digest immediately, bypassing the automatic turn-threshold check.
- **`POST /context/compress` API endpoint**: accepts `{ "conversation_id": "..." }` and forces immediate digest compression. Returns `{ ok, turns_compressed, total_turns, digest_chars }`.
- **`DigitalTwinService.compress_conversation_context()`**: public method that forces digest update for all unsummarized turns (up to 32 turns per call).
- **Button UX states**: loading (spinning icon), success (green, shows turns compressed), error (red), idle (auto-reverts after 3s). Distinguishes timeout, HTTP error, and connection failure.

### Changed

- **Footer banner two-row layout**: restructured the footer into row 1 (hardware + LLM metrics, gradient background) and row 2 (Powered by stack chips, neutral background) for better visual separation.
- **LLM metrics status fix**: status chip now correctly recognizes `"ok"` status value from health endpoint (previously only checked for `"healthy"`).

### Technical

- `service.py`: Added `compress_conversation_context()` method.
- `api.py`: Added `/context/compress` endpoint, imported `JSONResponse`.
- `index.html`: Added compress button in token detail panel.
- `app.js`: Added compress click handler with progress states.
- `styles.css`: Added compress button CSS with loading/success/error animations.
- Tests: 35 passed (frontend contract + conversation digest + chat pipeline DAG).

## v4.0.1 - 2026-06-20

`v4.0.1` simplifies deployment by making Docker the only path for the vLLM inference engine and fixing a skill-routing attribute bug.

- **Docker-only engine deployment**: removed host-binary mode from `run_vllm_engine.sh`. The engine now always runs inside a Docker container (`VLLM_ENGINE_CONTAINER` required in `.env`). Auto-escalates to `sudo docker` when needed.
- **Removed venv support**: deleted `--with-venv` flag from `quickstart.sh`, removed `.python-bin` marker file, cleaned up `.python-bin`/`.venv` references from `runtime_env.sh`, `.gitignore`, `README.md`, and `CONTRIBUTING.md`.
- **Bug fix**: skill routing referenced `request.session_id` (non-existent field on `ChatRequest`) — fixed to `request.conversation_id`.
- **CI**: all 336 tests pass. Engine test updated to verify Docker container-not-found error path.

## v3.4.0 - 2026-06-20

`v3.4.0` connects the capability plugin system to the deterministic workflow planner. Plugin steps are now **automatically injected** into execution plans when the query matches the plugin's routing pattern.

### Added

- **Plugin routing in deterministic planner**: `_plugin_steps_for()` method inspects the question and returns applicable plugin read-only + draft-write steps.
- **5 routing patterns**: meeting prep (booking prep), research mentoring (research + mentoring keywords), thesis review, course advising, paper feedback.
- **Safe fallback**: plugin steps are only injected if they exist in the step registry. Without plugins loaded, the planner behaves identically to v3.3.
- **`step_registry` constructor parameter**: `DeterministicWorkflowPlanner` now accepts an optional merged registry.
- **18 plugin step reason strings** added to the planner's explanation mapping.
- **7 new tests** covering all 5 routing patterns, ordering guarantees, safe fallback, and risk-level upgrade. Total: 308 tests.

## v3.3.1 - 2026-06-20

`v3.3.1` ships 5 real, enabled capability plugin packs covering core academic workflows.

### Changed

- **5 real capability plugin packs** (all `enabled: true`) replace the previous example manifests:
  - `research_mentoring`: research direction matching, reading methodology retrieval, research plan drafting
  - `meeting_prep`: team schedule lookup, blocker memory retrieval, meeting agenda drafting
  - `thesis_review`: paper digest retrieval, review checklist generation, review comments drafting
  - `course_advising`: courseware index retrieval, teaching resources, course plan drafting
  - `paper_feedback`: writing rubric retrieval, structured critique generation, revision notes drafting
- Registry now merges **36 total steps** (18 core + 18 plugin)
- 29 tests cover all plugin manifests including collision and trace-key validation

## v3.3.0 - 2026-06-20

`v3.3.0` delivers V3.3 Faculty-Specific Capability Plugins and replaces the hardcoded changelog with a data-driven API.

### Added

- **Capability Plugin System** (`capability_plugins.py`): manifest-driven plugin architecture with `CapabilityPluginManifest`, `CapabilityPluginRegistry`, validation, compatibility checks, and step registry merging.
- **Two example plugin manifests** shipped in `data/capability_plugins/`:
  - `course_advising.json`: syllabus lookup + prerequisite check steps
  - `paper_feedback.json`: rubric retrieval + structured critique + revision draft steps
- **`GET /changelog` endpoint**: serves release notes from `data/changelog.json` (no auth required).
- **`GET /capabilities` endpoint**: returns plugin statuses for the operations console (admin auth required).
- **`capability_plugin_dir` and `changelog_path`** added to `AppSettings`.
- **24 tests** covering manifest loading, compatibility, validation, registry merging, and real manifest loading.

### Changed

- Changelog modal now fetches from `/changelog` API instead of hardcoded `CHANGELOG_DATA` in `app.js`.
- Release notes content simplified and moved to `data/changelog.json`.
- Plugin manifests are disabled by default (`"enabled": false`); enable via manifest edit when ready.

## v3.2.0 - 2026-06-20

`v3.2.0` adds a user-facing version changelog modal and completes a full ROADMAP audit.

### Added

- Clickable version badge (bottom-right) now opens a "版本更新日志" modal showing concise highlights for each release.
- Changelog modal with clean, scrollable layout and accent-styled version tags.

### Changed

- ROADMAP full audit: V3.0 (Read-Only Planner) all 9 items marked complete, V3.1 (LLM-Assisted Planner) marked as implemented, V3.2 (Guarded Side-Effect Planning) marked as implemented (all 6 write steps live with `side_effect="draft_write"`), V3 Immediate Backlog all 8 items marked complete.
- Version badge text updated from stale `v3.0.1` to current `v3.2.0`.

## v3.1.1 - 2026-06-20

`v3.1.1` is a targeted fix for the evidence/support panel rendering and adds a manual retry button for failed inference.

### Added

- Retry button ("重试") appears on error chat bubbles when inference fails. Clicking it restores the original question to the input and re-submits automatically. Uses event delegation on `chatStream` and a module-level `lastFailedQuestion` tracker.

### Fixed

- Knowledge base content in the "回答依据" (answer evidence) panel now renders with proper markdown formatting (headers, tables, bold, lists) instead of a single collapsed line of raw text.
- `cleanAnswerBasisDetail` preserves markdown structure and truncates at ~600 chars (line boundary) instead of collapsing all whitespace into a single line.
- `buildAnswerBasisItemHtml` now uses `formatMessageContent()` for the detail field (same markdown renderer as chat messages) and wraps it in `<div>` instead of `<p>`.
- Added scrollable max-height (240px) and compact typography for basis detail cards so long knowledge entries don't overflow the card.

## v3.1.0 - 2026-06-20

`v3.1.0` is the retrieval and workflow modernization release. It replaces the BM25 knowledge backend with SageVDB/SageANNS, adds Tavily-powered web search, migrates the chat workflow to a parallel SAGE DataStream DAG, and introduces markdown table rendering and external knowledge ingestion.

### Added

- Integrated Tavily as primary web search engine with Bing as fallback, including conversational filler stripping from search queries and search result count surfaced in UI capability chips.
- Added markdown table rendering to the chat frontend (`| col | col |` syntax now renders as proper HTML tables with styled headers and borders).
- Added external PDF/article knowledge ingestion pipeline (first entry: S. Keshav's "How to Read a Paper" three-pass methodology).
- Added SageVDB and SageANNS version chips to the Powered By footer, using a unified `_resolve_source_version()` helper that resolves versions from pip metadata, module import, or pyproject.toml.
- Added auto-detect model name from connected LLM `/v1/models` endpoint — no more hardcoded model display names.
- Created `.env.template` with all environment variables documented and secrets replaced by `<placeholder>` markers.
- Added `tools/start_all_services.sh` orchestration script for multi-service startup.
- Persisted lucky question history to localStorage for cross-session continuity.

### Changed

- Migrated knowledge backend from BM25 lexical search to SageVDB/SageANNS vector search with reranking, significantly improving retrieval quality for large knowledge corpora.
- Rewired the chat workflow as a SAGE `DataStream` DAG instead of a 13-stage linear chain. Memory and knowledge retrieval now run in parallel through a 2-way `connect`/`comap` join, and post-answer side-effect stages fan out through a 4-way parallel join. The `workflow_trace` contract (canonical key order and statuses) is preserved via deterministic post-processing normalization.
- Upgraded Neuromem integration to `isage-neuromem>=0.2.1.12` with numpy BM25 as the default backend path.
- Consolidated operational scripts into `quickstart.sh` + `manage.sh` (CI uses consolidated entry points per two-script root policy).
- Migrated homepage hosting to GitHub Pages; tunnel/site-proxy is now optional.
- Widened chat content area to reduce side whitespace in the UI.
- Bumped `isage` to `0.3.2.4` and fixed version constraint definitions.
- Migrated conversation memory databases to updated schema.

### Fixed

- `PipelineCompiler._normalize_outputs` no longer fragments arbitrary iterable results from `Map`/`CoMap` transformations — flattens only when the caller opts in (`flatten=True`), fixing `Map` outputs that return Pydantic `BaseModel` instances.
- Fixed all 28 ruff lint errors across the codebase.
- Fixed web search running regardless of clarification or planner skip paths.
- Auto-linked SageVDB shared libs on bootstrap — no manual `ldconfig` or symlink step required.
- Prevented tests from downloading embedding models from the network.
- Fixed duplicate CI workflow content and added `--no-siblings` flag for CI isolation.
- Consolidated `retrieve_knowledge` traces for cleaner workflow visibility.

### Validation

- `PYTHONPATH=src:../SAGE/src pytest tests/ -q`
- `node --check src/sage_faculty_twin/web/app.js`
- `ruff check src/ tests/`
- Live smoke tests against `qwen32b` with SageVDB knowledge backend and Tavily web search.

## v3.0.0 - 2026-06-10

`v3.0.0` marks the first governed planning release (`V3.0: Read-Only Planner Preview`).

### Added

- Added admin replay report API `GET /workflow/replay` with deterministic planner scenario summary.
- Added operations-console Workflow Replay quality board with pass/fail summary, scenario highlights,
  and step chips for quick operator diagnosis.

### Changed

- Promoted package/app version metadata to `3.0.0` and updated frontend cache-busting tokens.
- Updated in-app bottom-right version badge to `v3.0.0`.
- Hardened V3 planner boundary test to avoid host-dependent Neuromem/FAISS embedding initialization,
  keeping regression checks stable in offline environments.

### Validation

- `node --check src/sage_faculty_twin/web/app.js`
- Live admin smoke validation for `/workflow/replay` (planner version, policy version, and scenario pass summary).

## v2.0.2 - 2026-06-10

`v2.0.2` is a quick stabilization patch focused on pre-v3 release hygiene and
runtime visibility.

### Fixed

- Updated frontend cache-busting so both CSS and JS assets ship with the same
  fresh release token, reducing stale-browser UI behavior after deploy.

### Changed

- Added a subtle bottom-right in-app version badge (`v2.0.2`) so operators can
  verify the running UI build without opening developer tools.
- Bumped package version metadata to `2.0.2` and exported
  `sage_faculty_twin.__version__` for runtime/version checks.

## v2.0.1 - 2026-05-29

`v2.0.1` is the pre-v3 stabilization baseline. It keeps the `v2` operations-console scope but
captures the production hardening that landed after the initial `v2.0.0` tag.

### Fixed

- Restored mobile first-open identity selection visibility and prevented first-login modal overlap.
- Hardened critical chat workflows for local Qwen2.5-32B, including deterministic booking routing,
  exact tutorial retrieval, and Chinese relative-time booking such as `明天下午三点`.
- Corrected footer acknowledgements and public links for SAGE, vLLM-HUST, and NeuroMem.

### Changed

- Refined the chat frontend with clearer context labels, folded runtime status, compact workflow
  capability chips, and a more explicit processing state.
- Updated the default owner style profile so current research directions are answered consistently
  before older historical database or stream-processing background.
- Expanded the V3 roadmap with governed planning candidates, architecture shape, step-registry
  constraints, and acceptance criteria.

### Validation

- `PYTHONPATH=src:../SAGE/src:../sageVDB:../neuromem pytest tests/test_agentic_workflow.py tests/test_llm_client.py tests/test_knowledge_base.py tests/test_persona.py -q`
- `node --check src/sage_faculty_twin/web/app.js`
- Live local smoke tests against `qwen32b` and `neuromem` for SAGE/ICML, research direction,
  database lab, Tutorial 7, and Chinese-time booking scenarios.

## v2.0.0 - 2026-05-27

`v2.0.0` is the operations-console release for Sage Mate. It moves the app from a
student-facing faculty twin with admin panels into a daily operations workbench for running,
reviewing, and improving the service.

### Added

- Operations overview and workbench APIs for admin-authenticated service review.
- Unified operations task queue covering pending bookings, knowledge gaps, escalations,
  follow-ups, and anonymous suggestions.
- Persistent operations task state overlays with status, assignee, note, and update timestamp.
- Student operations profiles derived from NeuroMem conversation and profile memory.
- Knowledge-gap draft workflow for turning repeated or unresolved question clusters into reviewable
  knowledge entries.
- Satisfaction metrics covering positive rate, unresolved rate, human-handoff rate, feedback
  coverage, reason summaries, and daily trend points.
- Chinese admin operations-console UI sections for task handling, booking review, student profiles,
  satisfaction, knowledge gaps, escalations, follow-ups, and suggestions.
- Operations-console documentation and runtime-data guidance for ignored task-state storage.

### Changed

- Real calendar-provider sync is no longer a `v2` blocker. The supported deployment default is local
  weekly availability plus admin approval, with provider sync left as a future optional integration
  for environments that expose an approved API.
- Roadmap language now treats `v3` as the future governed dynamic-workflow planning release, after
  `v2` operations and observability stabilize.

### Validation

- `PYTHONPATH=src pytest tests/test_operations_overview.py`
- Related backend and full-suite validation were run during the `v2` operations-console workstream.
- `node --check src/sage_faculty_twin/web/app.js`
- `ruff check` on touched backend and test files
- `git diff --check`

## v1.0.1

Maintenance release after the first public baseline.

## v1.0.0

First public repository baseline for the personal academic faculty twin.
