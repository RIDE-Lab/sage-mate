# Answer completeness and Support repair — v4.6.38

Verified on 2026-09-03 against `https://twin.sage.org.ai/`. This is a targeted
acceptance record, not a claim that all possible questions are correct.

## Causes and changes

- Delivery parsed “每项用一句话” as one sentence for the whole answer. Per-item
  clauses are now separate from global limits; Markdown numbering is not a
  sentence boundary. Explicit list cardinality participates in answer checks.
- Deep generation required four sections with a 256-token cap. The configurable
  default and deployment setting are now 1024; explicit requested structure takes
  precedence over generic analysis sections. Structured non-deep answers also
  receive a complete-answer budget. This is a ceiling, not a minimum length.
- `finish_reason=length` was accepted and cached as success, including streaming.
  Incomplete output now fails explicitly; the workflow can make one grounded
  repair within the existing deadline. Failed repairs no longer silently return
  a generic template with unrelated citations. Cancellation propagates through
  continuation and skill transports instead of triggering another attempt.
- A speculative fact renderer mutated evidence even when its text would not be
  used. Constrained answers now bypass that mutation. Compact prompts cannot
  silently discard selected knowledge; repairs keep the selected source text.
- Scope filtering fell back to unrelated/excluded documents when empty. Empty
  now means no matching evidence. `pdf` is a format, not a teaching subject.
  Provenance metadata alone does not establish query relevance.
  Explicit document-purpose tags also apply when planner scopes are empty:
  a recruitment/job-opening record is not a research citation merely because
  the query shares the author's name or a technical keyword.
- Snippets previously selected the earliest matching token, often a title or
  author line. Bounded overlapping windows now select matching body passages.
- A caveat mentioning insufficient information could clear all valid Support.
  Only an actual unsupported-answer branch clears it now. The UI explicitly
  explains zero-source answers without fabricating a citation.
- Browser testing found another missing-Support route: `research_mentoring`
  skill execution discarded knowledge provenance. Both native and compatibility
  tool transports now carry typed `KnowledgeSearchHit` records through
  `SkillResult` and the shared Support builder. Only executed knowledge-tool
  results can supply provenance, not generated model text; metadata is allowlisted.
- Experimental validity instructions are separated from retrieved source text
  in the system role. Exact source quotations are checked against source text;
  this is not a general semantic-entailment validator.

## Verification

305 relevant Python tests passed; 15 skipped: five optional BGE embedding tests
(model not locally cached), ten runtime skill-manifest fixture tests (their
`data/skills` fixture directory absent). Built-in mocked skill/provenance tests
and the real deployed `research_mentoring` path passed. Ruff, JavaScript syntax
and `git diff --check` passed.

Real public requests, using the existing GLM-4-32B backend:

| Request | Observed result |
| --- | --- |
| Three fair-experiment checks, one sentence each | All three delivered; 3.5 s application time in the browser; honest zero-source Support notice |
| Long-context baseline and ablations, three checks | All three delivered; relevant KV-cache source passage; final public API repeat 9.1 s |
| Compare two inference schedulers, three actions | Complete actions, matched workloads, length strata and ablations; 6.4 s |
| Three short long-context checks | All three present; relevant source; 4.7 s |
| Owner research, two/three sentences | Correct inference/state/memory research with homepage Support; ordinary workflow and browser skill paths tested |
| Same long-context request with deep mode off | Complete structured answer, one model call, real Support; 16.5 s public wall time (14.8 s application) |
| Stop, then ask about owner research | Server cancellation returned `cancelled: true`; skill answer had two real sources (7.9 s uncached); exact-question repeat also retained both sources on a cache hit |
| Owner state-reuse paraphrase after final reload | Two sentences, public biography and inference survey only; no recruitment citation; 5.85 s application, one model call, zero cache hits |

The three deep API repeats used one model call each, not a fallback template.
The browser deep completion was also one real model call (5.9 s application).
Intermediate candidates failed semantic checks (fabricated numeric thresholds,
training/inference confusion, and instructions misattributed as source text);
they were not counted as passing. The final repeats corrected those observed
failures. Recommendations and background citations still require human judgment;
retrieval overlap alone is not proof of every generated claim.

One intermediate public request timed out reading response headers after 65 s.
At follow-up the engine had no running or queued requests. Subsequent bounded
requests and browser checks succeeded, but this does not prove the transient
public network/tunnel issue is eliminated. No proxy or tunnel changes were made.

## Visual evidence

Real Firefox/public-page screenshots under
`/home/shuhao/sage-mate/output/playwright/answer-grounding-20260903/`:

- `deep-pending-desktop.png`
- `deep-complete-desktop.png`
- `support-desktop.png`
- `support-mobile-dark.png`
- `no-source-mobile-dark.png`
- `owner-after-cancel-mobile.png`

Desktop Support is expanded and readable. At 390×844, document scroll width is
390 px, long source text wraps, and the composer does not cover the last source
card. Both cited and explicitly uncited results remain distinguishable. A
programmatic source navigation exposed an additional clearance issue: physical
bottom padding alone does not affect `scrollIntoView`. Matching dynamic
`scroll-padding-bottom` now reserves the composer when navigating to sources.
Final public-page measurement after programmatic navigation: source bottom
607 px, composer top 619 px; 12 px clearance, 390 px document width.
Pixel inspection also found a long source filename widening the implicit grid
to 320 px inside a 274 px container, clipping text despite the page having no
horizontal scroll. A zero-minimum grid track and breakable source identifiers
fix this. The final public-page repeat has no overflowing Support descendants;
the final mobile screenshot was replaced after this correction.
Initial browser automation mistakes (clicking the clipped native checkbox instead of
its label/keyboard, ambiguous `.message-body`, capturing before final render)
were corrected; those attempts are not visual acceptance evidence.

## Operations and scope

Only `sage-mate-app.service` was reloaded, through the existing managed startup
script. Each reload followed an idle-engine check. No knowledge records or
indices were rewritten: the app reload cleared query/answer caches and loaded
the new query/snippet/provenance behavior. The local `.env` change is solely
`DIGITAL_TWIN_LLM_DEEP_ANSWER_MAX_TOKENS=1024`; no credentials are committed.
One browser reload hit HTTP 502 during the short app restart window; navigation
was repeated after app readiness. This deployment is not zero-downtime.

Public HTML references `styles.4222.css` and `app.4222.js`; both returned HTTP 200,
matched local SHA-256 bytes, and carried `no-store, no-cache, must-revalidate`.
App source/package/lock version is 4.6.38.

Engine remained container `c82ec990eafb`, started 2026-09-02T12:25:43Z, image
`sha256:e37db6660c24c9cde10b3076c1146fdaa4366b6426130555643d1c5b2c3e68f9`.
NPU0–3, GLM-4-32B-0414, engine/proxy/tunnel configuration unchanged. Protected
statecentric container `040c81e60426` retained its 2026-09-03T01:44:19Z start.
Workstation and NPU4–7 were not operated on.

Existing changes in `api.py`, `tests/test_chat_streaming.py`, and dev-hub remain
outside this commit. Tests ran against the preserved working tree; the user-owned
diffs were neither reverted nor included in the repair commit.
