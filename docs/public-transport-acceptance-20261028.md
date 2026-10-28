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
