# Durable admission integration (not deployed)

Twin pins SAGE `d00ee1e`, which supplies transactional active-request admission.
No capacity policy is automatically configured at startup. Production remains
on the previous source-plan release pending full release qualification and an
offline policy installation with all relevant namespace writers stopped.

HTTP admission rejection returns 429 with `X-Sage-Error-Code:
work_capacity_exhausted`, Retry-After, and an explicit not-enqueued message.
The newly issued guest cookie is preserved. The streaming transport carries the
same code/status in its terminal error frame; HTTP200 stream establishment is
not successful admission. An admitted observer timeout remains the separate
queued response, with its original request identity.

The browser preserves the request ID during bounded retries. After retries are
exhausted, a known capacity rejection does not keep pretending that a durable
answer is pending. Existing queued requests retain their delivery observer.
Slack command/event and binding admission return retryable 503, not a success
acknowledgement or a promise that an answer will arrive. No outbound Slack call
is made by the rejection path.

Isolated A100 source-overlay qualification: 79 HTTP/Slack/atomic post-answer and
binding integration tests passed in19.18s, including REST and SSE rejection,
guest-cookie preservation and four Slack transport/binding combinations. Five
frontend Node groups passed, including both queued and rejected retry exhaustion.
Ruff, diff-check and offline lock-check passed. The existing Starlette/httpx
deprecation warning remains. These tests are not a frozen clean-release result,
production overload experiment or real Slack delivery acceptance.

An additional35 HTTP/Slack workflow and knowledge-index tests passed in30.82s.
The HTTP producer test installs the actual SAGE policy before startup, holds one
active request at capacity, verifies a second admission is absent from the journal,
and retains duplicate/replay and cancelled-observer behavior.

Initial dependency fetch failed with an HTTP2 framing error. Retrying through
the existing local proxy with HTTP1.1 resolved the exact requested SAGE commit;
no dependency/model fallback was used.

Remaining: full frozen release regression; measure/select ingress capacity;
exercise overload/recovery end-to-end in an isolated service; deploy during a
quiescent maintenance boundary. Do not independently quota downstream finalizers
in a way that prevents already-admitted requests from completing. No TTL,
canonical history deletion or quarantine changes are part of this integration.

## Frozen candidate and real-model gate (2026-10-18)

Independent CPython3.11 release f8f96ec installed147 frozen packages with exact
SAGEd00ee1e (no source overlay). Full regression:1562passed,1failed,31existing
skips in336.11s, with the existing identity-evaluation exclusion unchanged.
The failure was an unintended `code: null` field on ordinary SSE errors. The
fix emits the optional code only when present; the old exact assertion is kept.
Five frontend groups passed on that release.

The isolated real-model capacity1 canary did observe a truthful second-request
429 rejection, but the first request returned413, so the complete canary FAILED.
Question: new members' first-month research steps with complete source links.
Diagnostic-only isolated tracing showed102024source-plan characters: the907-char
onboarding guide, a2937-char research-method page, a2665-char public index and
five unrelated17489–17995-char Prompt Engineering chunks. Upstream returned400
classified as prompt_capacity. The diagnostic regex captured no numeric token
count, so no exact required-context number is claimed.

Read-only inspection of the running control-plane release
`native-budget-router-20261015` confirmed admission estimates complete JSON UTF8
bytes/2, not actual tokenizer counts. This may contribute to conservative
rejection; actual tokenizer comparison is still needed. Its deployed directory
has no Git metadata, so a commit identity has not been inferred from its name.

The clean preview and temporary diagnostic services are stopped (MainPID0).
Production app/router/model services were not changed. Do not deploy on this
failed QA gate or solve it by truncating sources, lowering output budgets, or
substituting an easier question. Relevance and tokenizer-aware admission remain
to be resolved before repeating this same end-to-end canary.
