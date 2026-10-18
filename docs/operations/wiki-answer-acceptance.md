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
