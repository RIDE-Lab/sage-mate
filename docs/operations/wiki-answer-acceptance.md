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

## Source-constrained answer plan

Full-source facts use `sage.runtime.source_answer_plan`, not free factual
generation followed by a model judging its own paraphrase. The model selects
authorized source IDs; SAGE renders the selected documents verbatim, including
complete paragraphs, qualifications, Markdown and links. Version 1 selects whole
documents to avoid dropping a qualifier, reference definition or later list item.
It may be longer than a synthesis; it must not silently truncate.

The plan digest binds question, full authorized source snapshot and inference
policy. Strict JSON rejects unknown/duplicate IDs, extra fields and stale plans.
Final rendering is independently rechecked before response construction and
durable commit. Only selected sources appear in answer basis; other retrieval
hits are not presented as support. Empty selection states insufficient evidence,
not that the fact does not exist. The raw internal transcript is never streamed.

Research-review requests may have a separately labelled inference/advice section;
ordinary Wiki fact questions disallow it. Verbatim provenance does not establish
source truth, relevance or coverage: test those with real questions separately.
The same SAGE workflow is used for generation, delivery and persistence; there is
no semantic-audit fallback or second legacy Wiki pipeline.

Isolated live acceptance on 2026-10-18: systems overview 6.06s, new-member guide
3.95s; all three routes, both cross-cutting systems, four steps and completion
conditions preserved. Browser guide 5.2s, complete Markdown and default-collapsed
single-source basis; reload/history restored without re-asking. Initial footer
autolink syntax was rendered as literal text; changed to explicit Markdown link
and browser recheck passed (5.3s, clickable full source, all four steps). An
open-ended research-directions question also selected the correct overview (7.8s).
These observations alone are not production
cutover or frozen-release acceptance.

Exhausted quality repairs are terminal in both HTTP and Slack durable workflows;
they must not be retried as transport failures. Public HTTP status remains 503.
Repeated URL destinations are excluded from prose repetition checks, while
actual repeated sentences remain invalid.

The superseded semantic-auditor qualification **did not pass**: both bulk and independently
focused model audits have incorrectly approved an inference from an interpretation
warning to product maturity. A separate multilingual NLI control also falsely
entailed missing experiments from absent displayed data. Neither extra prompting,
long native reasoning nor combining model scores is an accepted replacement for
full-source negative controls. Do not deploy based on protocol tests or short
synthetic cases alone; preserve these negative controls during further work.

For loopback HTTP preview probes, preserve the guest cookie across retries in
the test client. Production cookies remain Secure; ordinary HTTP clients do not
send them back automatically. Reuse one request ID with the same identity and
input when querying queued work, rather than admitting a fresh task each time.
