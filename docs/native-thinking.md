# Native thinking first

Deep mode uses the served model's native reasoning when supported. The application
still retrieves authorized evidence and validates the final answer/Support; those
safety and grounding stages are not replaced by reasoning. A non-reasoning model
retains the application-level analytical answer prompt. Curated answer shortcuts
must not intercept a supported native-deep request.

## Capability and portable configuration

- `DIGITAL_TWIN_LLM_THINKING_MODE=auto` (default): inspect the server-rendered chat
  template using two CPU-only `/tokenize` requests, with thinking off and on.
  Results are cached per served model in the client; no per-question model probe.
- `DIGITAL_TWIN_LLM_TOKENIZE_URL`: optional trusted URL when the OpenAI proxy does
  not expose `/tokenize`. It receives the same authentication header as the LLM.
  Do not configure an unrelated host. Without an override, the URL is derived
  from the configured LLM base URL, not a machine-specific port in code.
- `native`: operator-verified enable_thinking protocol, for deployments without a
  tokenizer endpoint. `application`: explicit declaration that native thinking
  is unsupported. These declarations belong to the current endpoint deployment;
  revalidate them when changing models.
- An unavailable/unknown capability is **not** evidence of unsupported reasoning:
  deep generation reports a configuration failure instead of silently downgrading.
  Other reasoning protocols require a verified adapter; no model-name allowlist.
- `DIGITAL_TWIN_LLM_REASONING_EFFORT` defaults to `medium` (low/medium/high/xhigh;
  choose a level supported by the deployed template) and is
  passed through chat-template kwargs only for native thinking.
- Optional `DIGITAL_TWIN_LLM_THINKING_TEMPERATURE`, `LLM_THINKING_TOP_P` and
  `LLM_THINKING_TOP_K` (all with the DIGITAL_TWIN_ prefix) carry model-card sampling
  recommendations in deployment configuration. Native reasoning uses neutral
  frequency/presence/repetition penalties instead of inheriting prose penalties.

The client sends `enable_thinking=true` explicitly for native-deep and `false`
for normal/application synthesis, overriding a server default of false. For an
always-reasoning template, the probe records that the switch is not available;
normal mode cannot remove a capability intrinsic to the checkpoint.

## Budgets, privacy and failure behavior

Native max_tokens reserves the configured deep answer budget plus
`DIGITAL_TWIN_THINKING_TOKEN_BUDGET`, capped by the existing output limit. This
reservation is not a promise that the model will obey a reasoning-only cap.
The optional vLLM `thinking_token_budget` extension is sent only when
`DIGITAL_TWIN_LLM_THINKING_BUDGET_SUPPORTED=true` was explicitly verified. Native
reasoning support alone does not imply that extension is enabled on the server.

Final output uses content, never reasoning/reasoning_content as a fallback.
Streaming similarly separates reasoning from answer tokens. Reasoning-only or
token-limited responses are not successful final answers. Bounded quality repair
retains the native mode; it does not silently replace it with application thinking.
Cancellation and the request deadline remain in force. Cache reuse separates
models and thinking/decoding modes.

The workflow trace identifies “模型原生思考” or “应用层深度分析”, without exposing
private reasoning. Unit tests: `tests/test_native_thinking.py`,
`tests/test_deep_thinking_policy.py`, `tests/test_llm_client.py`; transport/cancel
regressions also cover `tests/test_chat_streaming.py` and
`tests/test_llm_request_cancellation.py`.

Comparison grounding keeps bounded evidence for each named subject under the
same visitor/admin permissions. Public repository and system documentation count
as research evidence; they must not be discarded merely for lacking paper tags.
This became visible during native-deep acceptance when a two-system comparison
incorrectly retained only one paper excerpt.

## Live acceptance — 2026-09-03

- Served model: `Qwen/Qwen3.8-27B`; auto detection returned native=true,
  switchable=true from the engine's template. Direct native completion returned
  122 reasoning tokens and a separate correct final answer; normal mode returned
  zero reasoning tokens. Graph-mode engine verification passed on physical NPU0–3.
- Public browser: checked deep mode, submitted the two-system comparison,
  observed a complete answer with both systems and two actions plus three Support
  cards. The workflow identified native thinking. Approximately45s on this case:
  **not** a latency acceptance pass. A subsequent small arithmetic question
  returned391 in approximately10s in the same browser conversation, but omitted
  the requested extra verification sentence; full instruction-following remains
  a separate quality issue, not evidence to disable native thinking.
- The first public deep probe failed qualitative review despite HTTP200. It
  retained only a paper source, mischaracterized the systems, and drifted later
  in its output. Scope filtering, comparison coverage and native sampling were
  corrected before the second browser probe. Background Wiki wording and claims
  about paper mechanisms still need source-by-source review; no claim that all
  knowledge or model answers have been validated.
- Regression union:250passed,5skipped (optional BAAI/bge-small-zh-v1.5 embedding
  fixtures absent; no model downloaded by tests). Frontend contracts:26passed.
  Ruff, JS syntax and diff whitespace checks passed.
- Browser artifacts under `output/playwright/`:
  `native-thinking-public-desktop.png` (Support and checked control),
  `native-thinking-public-pending.png`, `native-thinking-public-390.png`.
- Only the app was reloaded. No engine/proxy restart for this policy change.
  Original `api.py` and `tests/test_chat_streaming.py` changes were retained and
  excluded from the policy commit. Secrets and machine-local endpoint settings
  stay in the ignored `.env`.
