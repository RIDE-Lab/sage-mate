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

## Remaining release gates

Chrome native control reports a locked Mac; the in-app browser control also
failed to create a usable session. Desktop/narrow light/dark appearance, real
keyboard interaction and public original-question browser acceptance are not yet
verified. No production cutover should be inferred from unit tests or these
isolated model runs. After browser acceptance, deploy the pinned Twin and SAGE
main commits together, restart only Twin, and verify the public asset versions
and original question again.
