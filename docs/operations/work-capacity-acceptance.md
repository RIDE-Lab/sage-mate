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
