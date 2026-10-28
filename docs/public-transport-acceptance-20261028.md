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

## Cross-host egress comparison

At 04:30 UTC on 2026-10-28, A100, A6000-1 and A6000-5 ran the same direct,
TLS-verified HTTP/1.1 GET for `/app.js`, with identity encoding, a 12-second
whole-response deadline, and three interleaved attempts per address per host.
No proxy was used. All complete responses contained 574612 bytes and matched
the deployed asset SHA256 above.

| Host | 104.21.42.210 | 172.67.166.76 |
| --- | --- | --- |
| A100 | 3/3 complete, 1.948–2.638 s | 1/3 complete; two timeouts |
| A6000-1 | 3/3 complete, 1.684–2.721 s | 0/3 complete; three timeouts |
| A6000-5 | 3/3 complete, 1.559–2.815 s | 0/3 complete; three timeouts |

Seven of the eight failures had received HTTP 200 before stalling; one timed
out before response headers. This is intermittent destination-dependent
failure, not evidence that the address is permanently unreachable. Observed
CF-Ray suffixes differed consistently: HKG for the first address, LAX for the
second. All three machines used the same next-hop gateway and MTU 1500; bounded
tracepath on A100 and A6000-1 shared the first three responding hops. Later
tracepath no-replies do not establish where the HTTPS flow loses data.

The comparison rules out an explanation confined to the A100 machine. It
supports investigating the shared network/egress/upstream/edge path, but does
not prove a gateway restriction or identify a dropping device. Per-interface
RX error/missed and TX error/drop counters had zero deltas; aggregate RX drop
counters rose on all three busy hosts and cannot be attributed to these probes.
No shared network configuration, GPU workload or service was changed.

A second synchronized round reproduced 3/3 full responses at the first address
and 0/3 within 12 seconds at the second; failures occurred at TLS, response
headers, or partial body. An independent A6000-1 `/usr/bin/curl -q` run, with
proxy disabled and a 30-second deadline, completed both: 2.248 seconds versus
22.963 seconds. Earlier curl attempts also recorded TLS/TCP connection timeouts
(including one 30-second timeout to each address). Those failures are retained;
the first address is not promised to be permanently healthy. The problematic
path is intermittently slow/unreliable, not demonstrably blocked. Raising a
deadline can allow a request to finish but does not repair the network.
