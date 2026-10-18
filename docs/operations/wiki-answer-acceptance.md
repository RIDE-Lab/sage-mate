# Wiki answer release gate

Full stored source records must go through the common grounded synthesis path.
Display excerpts are not complete evidence. The legacy short-fact renderer must
not replace synthesis when complete source records are available. Markdown links
and lists must not be sentence-split or cut at a character limit.

The production acceptance configuration uses `DIGITAL_TWIN_RETRIEVAL_TOP_K=8`.
The earlier value 3 returned only navigation/contribution pages for the broad
research-directions question; 8 also recalled the actual systems overview.
This is a recall setting, not a relevance or correctness guarantee. Do not
interpret every returned source as support for every claim.

Run these against the real embedding/reranker/model stack:

- Ask for IntelliStream research directions based on public Wiki. Verify all
  three directions against the systems overview, with a complete source URL.
- Ask for all four steps and acceptance conditions in the new-member research
  guide. Verify every step and nested list, without forcing the user to re-ask.
- Ask how ECPA/StateAxis relate to the three directions. Preserve the source's
  uncertainty; "ongoing research" does not prove an absence of deployments,
  benchmarks, or results. Do not add an unsolicited experiment proposal.
- Check new and restored conversations in a browser, including full citations,
  clickable URLs, default-collapsed evidence and nested lists.

Record end-to-end latency separately from correctness. An HTTP 200, normal SSE
termination, or a fast answer alone does not pass the gate. Keep the old-memory
quarantine and public/private retrieval boundaries intact during validation.

## Source-audit integration

Full-source answers are buffered until `sage.runtime.source_audit` validates a
complete per-unit audit. The audit sees the same authorized full evidence, with
titles and public URLs; it cannot fetch additional private documents. The cache
namespace binds the exact question, answer and sources. Malformed audits and
transport errors never count as approval. Unsupported statements may use the
existing single repair, followed by a fresh audit; a prior repair exhausts that
allowance. The render stage runs this gate before response construction and the
subsequent durable memory/result commit. Raw audit transcripts are suppressed
from trace sinks; successful checks expose only aggregate workflow metadata.

Auditing uses neutral repetition penalties because repeated JSON keys and exact
source quotations are required. Normal answer decoding remains unchanged. This
does not reduce evidence or the configured output cap. Protocol tests and seeded
semantic tests are distinct from full Wiki/model/browser acceptance: a model
auditor is not a proof of truth. Requested hypothetical advice and mixed factual/
proposal answers still need explicit real-model qualification before rollout.

For loopback HTTP preview probes, preserve the guest cookie across retries in
the test client. Production cookies remain Secure; ordinary HTTP clients do not
send them back automatically. Reuse one request ID with the same identity and
input when querying queued work, rather than admitting a fresh task each time.
