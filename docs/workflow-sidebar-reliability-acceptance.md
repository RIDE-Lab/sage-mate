# Workflow sidebar and answer reliability acceptance

Status: implementation and isolated backend acceptance complete; browser and
production cutover pending. This document does not certify the public release.

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

The companion SAGE main change is `2687cb0d`; production remains on the earlier pair until the following gates pass.

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

## Remaining release gates

Production has not switched. Public Chrome navigation fails with
ERR_CONNECTION_CLOSED; a Mac curl GET succeeds directly, but the configured
system HTTP proxy (127.0.0.1:7897) completes CONNECT then loses TLS. The user authorized an exact-domain DIRECT rule for twin.sage.org.ai. The active
Clash profile rule override was backed up and updated, preserving all other rules;
after reactivation the same proxy GET returned 200 in 1.78 s. Chrome extension
control subsequently timed out, independently of the restored HTTP route. Public member acceptance,
final narrow visual/keyboard acceptance and pinned production cutover remain.

The generic answer-level “依据待核对” disclaimer was removed. Full evidence
records stay in Details; an actual missing source span produces a concrete
“尚缺资料” notice naming that span. Execution errors and consent controls are
still independently visible. Current frontend regression: 25 suites passed.
