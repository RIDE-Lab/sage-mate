# Workflow sidebar and answer reliability acceptance

Status: frontend e5878bf and SAGE 2687cb0d are deployed. Public member original-question acceptance FAILED; do not treat isolated runs or UI acceptance as end-to-end approval. The later paragraph-selection repair is committed but not deployed pending relevance acceptance.

## Failure and changes

For the original question, “怎样区分系统优化的真实收益和实验干扰？”, the
production execution record showed a coverage correction failing after about
60 seconds, followed by an outer attempt that hit the durable effect fence.
A scoped isolated replay captured `FlowRequestOutcomeError(ReadTimeout)`.
The fence was working; the outer retry incorrectly obscured a known terminal
native failure as an unknown execution outcome. Typed native failures now settle
without another producer attempt; genuine lost acknowledgements remain fenced.

Structured model work now consumes streaming transport internally. No unvalidated
JSON or private reasoning is forwarded to the answer stream. Empty internal
streams do not fall back to another buffered generation. Read-idle and total
request cancellation limits remain enforced.

The source repair route also had two reproducible failure mechanisms:

- Supplemental retrieval reused the original missing question without alternative
  terminology. SAGE now has an explicit, bounded `plan-retrieval-repair` node;
  it preserves the original query and allows at most three additional search
  hints. The adapter retains captured identity/source permissions. Queries are
  never treated as source evidence.
- The constrained decoder could select blank-only line witnesses. SAGE now
  constrains witness endpoints to nonblank original line numbers, preserving
  every source byte. Capacity-partition child mappings can receive one strictly
  validated correction against their own snapshot. Invalid corrections still
  fail closed; no source truncation, digest rebinding or weakened audit.

The browser candidate moves advanced execution details from answer cards into
one right-hand inspector. The selected answer, including history, remains the
source of the displayed record. Updates preserve disclosure/scroll state and do
not open the panel or scroll the conversation. Narrow screens use a bounded row
inside the page layout, rather than an overlay over the composer. Errors and
confirmation controls remain in the answer card.

## Isolated evidence

No shared model was restarted and no booking or email was sent. Temporary state
was separate from production. Test dependencies were installed only under the
isolated test directory.

- Same original question with a current public Wiki snapshot and a scoped
  relevant member document: two final complete executions, 31.5 s and 30.6 s.
  Both retrieved supplemental material, produced a natural answer on controls,
  measurement validity and confounding, and passed source and completeness
  checks. These are acceptance samples, not a latency distribution or a general
  failure-rate claim.
- An intentionally malformed first coverage response, followed by real model
  correction and complete answer audit, succeeded in 24.1 s against a complete
  public Benchmark guide. This controlled repair probe is distinct from normal
  retrieval acceptance.
- Early runs with zero retrieval hits or an evidence-gap-only result were NOT
  counted as successful original-question acceptance.
- Real loopback HTTP streaming test: incremental JSON takes longer than the
  configured read-idle timeout overall but succeeds while chunks arrive. Exactly
  one upstream invocation; a missing stream body never triggers buffered replay.
- Durable workflow tests: explicit timeout/validation/native failure survives
  restart with one producer attempt. Existing lost-receipt, booking and identity
  regression tests remain included.
- Browser-function regressions cover selected-history isolation, explicit panel
  opening, detached-history handling and preserving scrolling/fold state.

Regression totals: 228 Twin Python checks, 80 SAGE checks, and 25 JavaScript suites passed.

The deployed pair for the public check below is Twin `e5878bf` and SAGE `2687cb0d`. Earlier isolated results above used a smaller private catalog, so they do not establish full-catalog relevance.

## Browser follow-up (2026-10-29)

The unlocked Mac allowed real Chrome interaction. The initial horizontal graph
was rejected as unreadable in a sidebar; it is now one vertical native topology,
with input at the top and output at the bottom. Nodes and actual route/call edges
are preserved. Empty layout ranks are removed, labels stay horizontal, and the
inspector scrolls vertically independently of the conversation.

Real checks found and fixed two additional issues: business progress could replace
the pending native graph, and the old fixed mobile composer was covered by the
new detail row. Native events now retain the running leaf label, including while
waiting for a new event. The mobile composer participates in layout. Completed
answers move full evidence records into Details, while evidence-gap, error and
confirmation notices remain outside. At 1504 px the reading shell is 800 px and
centred near the viewport centre; the inspector is approximately 331 px.

The original question completed in guest browser runs (including a 26.4 s deep
run), with relevant prose but no verifiable attached source. These are UI tests,
NOT source-grounded acceptance. The scoped member runs above remain separate.
Old synthetic conversations were restored using the history UI. The 390 px
layout has no horizontal overflow, and composer and inspector rectangles no
longer overlap. Desktop light/dark rendering was observed. Browser viewport
emulation produced inconsistent screenshot scaling, so narrow visual acceptance
must not be inferred solely from those measurements.

## Public follow-up and remaining gates (2026-10-29)

The exact Twin-domain DIRECT rule restored Chrome access after the user's
approval. Native Chrome loaded the signed-in public page. Public HTML, JS and
CSS bytes matched main. The app-only cutover preserved inference/router/
workstation/advisory process IDs and all existing private state.

The public original question failed in about 201.9 seconds at
`assess-coverage-correction`. Durable answer attempt remained 1. Native Chrome
showed the actual failure; this is a website/backend failure, distinct from
occasional browser-control timeouts. Request ID:
`362a8d44-a692-4028-9ec6-c0571e58d420`.

An isolated production-catalog snapshot reproduced the root mechanism: model
coverage selected start line 3 and end line 1. The correction then exhausted
its output allowance after about 192.6 seconds (`IncompleteCompletionError`).
The previous schema constrained each endpoint separately but allowed reversed
pairs and an unlimited number of repeated witnesses.

The candidate SAGE repair (`60854a64`, requiring the paired Twin adapter change) now asks the model to select compact paragraph IDs
bound to the complete numbered source. The adapter decodes these to canonical
exact line ranges; runtime provenance/digest checks remain mandatory. Every
nonblank line remains selectable; sources are not truncated. Invalid IDs and
malformed output remain failures, not empty-coverage fallbacks. Cardinality is
bounded by the actual number of supplied paragraphs, not an answer token cap.
A prototype with full object schemas was rejected for excessive schema size.
The compact schema stays below 10 KB for 1,000 paragraphs.

A complete-catalog execution with ordered paragraph ranges passed execution in
21.7 seconds, but its answer narrowed a general experimental-method question
to compression optimization. It FAILED independent relevance acceptance.
An initial complementary-search experiment took 94.5 seconds and retained the
same problem; that ineffective workflow change was removed. The compact decoder
and precise incomplete-generation classification remain candidates, not a
production quality claim. Do not deploy them as a completed original-question
fix until relevance is independently accepted.

The generic answer-level “依据待核对 · 请在详情中查看缺口与来源” text is
removed in the deployed UI, including normalization of saved answer summaries.
Native public Chrome confirms evidence records inside right-hand Details, while
actual execution errors remain separately visible. Concrete missing-source
notices name the missing part. No model-derived source status is fabricated.

Real isolated 320 px light / 390 px dark checks found and fixed a header theme
button collision and composer wrapping. History restored without regeneration;
the inspector scrolled independently through the final output node. Explicit
Close works. Full public success-state, streaming/history and keyboard acceptance
remain incomplete because the original public answer failed.

Candidate regression: 180 selected Twin/SAGE tests passed. Unit tests use isolated
local knowledge and segment memory fixtures because the test host does not cache
the optional BGE model; production retrieval acceptance above used the actual
configured embedding/reranker and an isolated catalog snapshot. Test model stubs
now emit paragraph IDs. Notification tests assert one sender invocation and the
existing unknown-outcome fence; no real booking notification was sent.
