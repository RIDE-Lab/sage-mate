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
  tokenizer endpoint. `application`: operator choice to use application-level
  analysis, either because native thinking is unsupported or because this
  endpoint's native latency/answer behavior has failed acceptance. These
  declarations belong to the current endpoint deployment; revalidate them when
  changing models.
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
The Chat Completions and Responses endpoints have independent support. When
Responses support has not been verified, budgeted native requests use Chat
Completions on the same model endpoint; normal traffic keeps the configured
Responses API. Set `DIGITAL_TWIN_LLM_RESPONSES_THINKING_BUDGET_SUPPORTED=true`
only after verifying that Responses enforces the budget in token usage, not
merely that it accepts the field. A rejected budget request fails explicitly;
the client never retries without the budget.

The output ceiling is not the model's context window. The current defaults are
16,384 tokens for a deep answer and 32,768 tokens for the combined native
reasoning/answer reservation (subject to the configured output ceiling). An
explicit deep web request uses `/chat/stream`, which sends keepalives and the
same validated final response as `/chat` under a 240-second server deadline;
ordinary `/chat` requests keep their shorter deadline. A length-limited deep
stream can continue once only when it has visible answer text and enough time
remains. A reasoning-only response never counts as an answer.

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

## A100 Qwen3.5 deep-budget acceptance — 2026-10-15

The served Qwen3.5 model and provider were unchanged. With native thinking but
without `thinking_token_budget` in the request, a research question spent about
200 seconds generating 32,768 output tokens with no visible answer. A short,
non-thinking repair then produced text. Direct engine verification subsequently
showed that this A100 engine **does** support `thinking_token_budget`; the Twin
client had left its separate support flag disabled, so the native request was
unbounded within the overall output ceiling. The measured failure was therefore
not evidence that the model or engine cannot support bounded native thinking.

An interim application-level mode completed the same question in about 9 seconds
and a public `/chat/stream` request in about 8.7 seconds, but was replaced on
user request. A direct native-engine test with an 8,192-token thinking budget
used 5,006 reasoning tokens, generated a final answer, and finished normally.
The app then explicitly selected native mode and enabled the verified budget
parameter for this A100 endpoint. Two public `/chat/stream` probes returned
validated answers; one workflow trace explicitly recorded “模型原生思考” for both
prompt assembly and answer generation. A later protocol-level probe revealed
that the production Responses endpoint accepted but ignored this budget: with
`thinking_token_budget=64` and `max_output_tokens=256`, all 256 output tokens
were reasoning and no answer was produced. Those earlier public probes proved
native mode, **not** budget enforcement. The client was repaired to route only
budgeted native requests over the verified Chat Completions endpoint and to
fail closed on errors. The research answer's specificity still needs quality
review. Do not infer that raising the overall ceiling improves quality or
latency from these tests.

The production Twin base URL points at the authenticated SAGE control plane,
not directly at the A100 engine. Its router was extended to proxy Chat
Completions under the same key admission, model selection, hostname scope, and
request metrics. After that deployment, a public research probe completed with
native-thinking workflow steps. The durable trace recorded
`thinking_token_budget=8192`, `max_tokens=24576`, 8,191 reasoning tokens and
8,352 total completion tokens, so the engine enforced the separate budget and
returned visible text. A normal public chat still traversed Responses and
completed. This proves transport and budget correctness, **not** answer quality:
the research answer was only 259 characters and was too superficial.
