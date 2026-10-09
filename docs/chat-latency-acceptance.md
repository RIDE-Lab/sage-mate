# Chat latency acceptance

`tools/benchmark_chat_latency_acceptance.py` is the reproducible acceptance
gate for the public chat critical path. The deployment origin is always
provided explicitly; the tool does not embed a host, port, model, or device
mapping.

```bash
python tools/benchmark_chat_latency_acceptance.py \
  --base-url "$SAGE_MATE_ACCEPTANCE_BASE_URL" \
  --runs 10 \
  --mixed-concurrency 4 \
  --output artifacts/chat-latency-acceptance.json
```

The acceptance client uses HTTP/2, matching the browser-to-Cloudflare public
path instead of attributing HTTP/1.1 connection churn to the application.
The JSON receipt also snapshots the public health metadata for the exact run,
including app/model/runtime versions, knowledge-document count, engine image,
and declared NPU device mapping.

The three fixed workloads represent grounded simple Q&A, a non-deep complex
research comparison, and an explicit deep-research request. The gate checks:

- p95 limits of 3, 30, and 60 seconds respectively;
- HTTP success and visible knowledge/web references for every warm run;
- request-stage reconciliation within 5%, with no unexplained gap above two
  seconds;
- per-request LLM calls, retry count, TTFT, cache hit/miss, and model duration;
- bounded mixed-load admission, where either a successful response or a 429
  carrying a numeric `Retry-After` is valid.

The separate socket-level cancellation regression uses a deliberately slow
local HTTP server. It asserts that a request blocked before response headers
exits within five seconds after cancellation, rather than merely returning
from its asyncio wrapper while the worker continues in the background.

The browser workflow SSE and the OpenAI proxy stream both emit one-second
keepalives. This is a cancellation liveness contract, not only a Cloudflare
idle-timeout workaround: each proxy boundary must touch its downstream socket
often enough to propagate a disconnect and close the upstream model stream
within the five-second reclamation target.
