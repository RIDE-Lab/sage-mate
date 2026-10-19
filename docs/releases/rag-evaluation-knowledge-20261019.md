# Fill the RAG evaluation knowledge gap, retain the original question

The original starter is restored verbatim: “评测 RAG 系统时，怎样同时检查效果和性能？”
The earlier replacement question is not an acceptable resolution of that gap.
The starter regression now requires the original question to remain available.

Canonical public knowledge changes live in `intellistream/sage-wiki` main:
`63dbac2` adds a reusable joint-evaluation protocol to the RAG article and a
benchmark cross-link. It covers versioned test sets, retrieval/answer/citation
quality, full-path timing, failure accounting, controlled ablations, joint quality
and SLO goodput, and reproducible artifacts. Primary references are linked in
the article. It explicitly does not report fabricated local measurements.
`bb8952f` supplies reviewed research/guide metadata for both articles; this is
semantic topic metadata, not a change of access permissions.

The first exact-question probe failed with HTTP200 and normal completion but no
sources: the research intent entered the evidence route, yet the public record
had only wiki/tech-note/public tags. The unchanged advisory filter rejected it.
A read-only reproduction of the real public record returned zero candidates
before reviewed topic tags and one after, with identical text and ACLs. The
ingestion regression exercises preservation of those topic tags and the original
question through the same filter. No document-name routing, question prefix,
model/provider/budget change, or relaxed source audit is used.

`tools/qualify_rag_evaluation.py` submits only the natural question and a synthetic
test label. It checks completion, actual public RAG source retrieval, full source
URL, canonical source blocks, quality/performance/control/failure coverage and
absence of an evidence-gap substitute. Its unit tests reject transport-only,
missing coverage, truncation, private sources and missing blocks. It is a smoke
gate, not a semantic benchmark; the saved answer and sources also require review.

Public Wiki publication, independent retrieval-index refresh, live-answer
coverage, frontend rendering and production release readiness are separate gates.
Results are recorded after observation; the old failed probe is retained.
