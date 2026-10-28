# Question-led answers and separate source basis

The native SAGE evidence workflow now selects sources, drafts question-led
paragraphs, reviews every sentence against complete source text, and checks the
complete original question against the resulting answer. One explicit correction
branch may rewrite a rejected answer; it receives a fresh audit. A second failure
is terminal and never silently falls back to pasting documents.

Twin supplies the existing model and permission-aware retrieval adapters. It does
not own a second workflow. The body is a canonical `answer` block. Full source
records and their original links remain in the existing collapsed answer basis.
Historical source blocks continue to render with their original provenance; new
prose never inherits a source's relative-link base.

Receipts bind the question, authorized source snapshot, answer and reviews.
Sentence coverage, exact source IDs/ranges and stale/mutated receipts are checked
by the runtime. Semantic entailment remains model assessed, not a proof of truth.
The auditor must distinguish omission from negative facts and source-scoped
uncertainty from assertions about the world. Substantive statements cannot be
exempted with the model's `nonfactual` label. Numbered list prefixes stay attached
to their sentences during audit segmentation.

No model, provider, token limit, identity permissions or inference settings change.
Release acceptance includes the original inference question plus synthetic
positive/negative entailment cases, genuine browser rendering and source expansion.

Follow-up references now receive the complete prior turns captured for the same
authenticated owner and conversation. History intake no longer depends on a
small set of phrases such as “刚才” or “这个”. SAGE marks these inputs as
conversation evidence: they support an explanation of what the prior answer
said, not independent truth of a prior hypothesis or benchmark claim. Only
selected dialogue is shown in the collapsed basis, explicitly labeled as not
an external fact source. Rolling summaries are not used as verbatim evidence.

Dialogue-only answer turns also enter the SAGE evidence workflow even when Wiki retrieval is empty. Repair feedback is constructed by SAGE and includes exact rejected sentences, not ambiguous paragraph indices; this does not increase retries or bypass the final review.

## Follow-up acceptance (2026-10-28)

- Owner/session-scoped complete prior turns reach SAGE without a keyword gate; prior assertions do not become external facts. Ordinary advisory handling remains intact.
- Supplemental retrieval and adapter grouping may order the same immutable records differently. Receipt checks compare exact source identities/content while SAGE still validates its original bound snapshot.
- Replay of the original “推论／建议（非来源事实）新增了什么解释” with its original previous answer completed with conversation evidence and no coverage gap. This does not reconstruct the precise unlogged rejected sentence from the historical production failure.
- A captured unrelated unsupported introductory sentence was actually replaced by the correction call and passed fresh audit. Numeric proposal-versus-measurement positive/negative checks passed.
- A browser follow-up without the referenced section now reports a source gap instead of an execution failure. This remains a conservative missing-evidence answer, not a claim of universal question-answering success.
- Verification: SAGE 99 protocol/runtime tests; Twin full run 1837 passed, 31 skipped, 5 failures investigated (advisory routing, two guest transports, background memory fixture, dependency pin). Affected suites passed after fixes: 111 cases plus the corrected source-order test and lock check in the final 40-case run. Storage persistence 28 passed with the C++ index required by those tests. Tests use isolated state and deterministic embeddings; real-model checks use the unchanged configured deployment.
