# Public response reads and browser acceptance

The A100 host can receive HTTP 200 and a partial body without completing a
response. On 2026-10-28, matched requests to the current DNS answers for Twin
completed at one Cloudflare address and stalled at the other. HTTP/1.1 versus
HTTP/2 and identity versus gzip did not remove the difference. TCP_MAXSEG 1200
and 536 did not repair it. A scoped header-only capture showed repeated SACK
gaps with a nonzero receive window and zero capture drops. This establishes an
address/path-dependent delivery failure, not which carrier device caused it.

`python tools/public_https_probe.py https://twin.sage.org.ai/app.js` checks full
bodies at freshly resolved addresses with original-host TLS verification. It
does not change host DNS, routes, MTU, service configuration or certificates.
Mixed results return nonzero and must be reported as degraded. The helper's
`EdgeHTTPSHandler` permits a single acceptance run to use a verified live DNS
answer; that is an explicit transport mitigation, not repair of the bad path.
Do not automatically replay a POST after a lost acknowledgement. Recover its
existing request identity through the durable work contract first.

The Chrome extension could list/open tabs and read their URLs, but page
snapshot/log commands timed out. Native Chrome accessibility could read and
operate the same public page. Therefore an extension timeout is not evidence
that Twin is blank or offline. Use the documented native control path and
record actual rendered answers; do not count API success as browser acceptance.

That real member-browser check exposed a separate error: a 384271-byte model
request was rejected as `auto_context_too_large` during coverage reassessment.
The UI incorrectly called every 413 an oversized attachment. Typed
`prompt_capacity` now has its own message; upload errors retain their own
details. SAGE's pinned coverage implementation can validate complete source
groups separately after an explicit capacity rejection. It validates child
digests, IDs and ranges before combining witnesses, keeps the full question
and every source, and still rejects a single document that cannot fit.

External transport health, source qualification and final answer relevance are
separate acceptance gates. A public-only replay is not evidence that the member
source scope passed. Never describe a failing gate as repaired merely because
another route, identity or smaller fixture passed.

## Deployed acceptance, 2026-10-28

Application release: Twin `0cf98d2bb4f964993eb800dfbc83b2eb44b1e3da`,
SAGE `faf3d97702060af71f58a6ca9c31029718c91042`.
Only the Twin application restarted; the inference engine, router, Workstation
and advisory service retained their process identities and protected settings.

- SAGE targeted suite: 132 passed. Twin HTTP/transport suite: 26 passed.
  The Node HTTP error-message regression also passed. These are targeted results,
  not a claim that the full application suite was rerun for this change.
- Public Chrome member question: `大模型生成一段回答，背后经历了哪些步骤？`.
  The rendered answer described Prefill and Decode rather than RAG, with the full
  Wiki tutorial URL behind the answer-basis disclosure. Server duration was
  54.007 seconds, finish reason `stop`, empty missing-parts list and no failed
  execution nodes. This request reproduced the 384271-byte capacity rejection;
  subsequent source-group requests of 161633 and 226793 bytes succeeded. This
  exercises the repair with the member source scope, rather than a smaller
  public-only fixture.
- Restored original conversation: `推论／建议（非来源事实）新增了什么解释`.
  The rendered answer explained the previous experimental-design additions in
  seven points. Server duration was 42.882 seconds, finish reason `stop`, empty
  missing-parts list and no failed nodes. The original long-context isolated
  replay also passed before deployment. Historical failed exchanges remain
  historical records; the successful new exchange does not overwrite them.
- While reading above the bottom, completion displayed the new-answer return
  button without pulling the viewport down. The tester explicitly clicked it.
  Refresh returned to the welcome page; reopening this conversation from history
  restored the complete answer with basis and advanced details collapsed.
- Post-deployment A100 public two-turn acceptance used the freshly verified
  working DNS answer. Original question: 25.2 seconds; contextual follow-up:
  31.2 seconds. Both completed with no failed nodes or missing coverage.

These single functional checks establish the observed acceptance behavior, not
latency distributions or a general model-quality benchmark.

## Remaining external faults

The same post-deployment full-body probe completed 574612 bytes at the working
address in 2.287 seconds. The other current DNS address returned HTTP 200 but
stalled after 135301 bytes and hit the 12-second deadline. The degraded path is
**not repaired**. Per-run address selection restores this acceptance operation,
not arbitrary A100 public clients. Further repair needs control or diagnosis of
the shared egress/carrier path; no global route, DNS or MTU was changed.

Chrome extension page commands remain unreliable. Native Chrome accessibility
and actual public-page rendering worked and were used for the browser checks.
No extension restart, login change, browser security bypass or user-session
reset was performed. The precise internal extension fault is not established.
