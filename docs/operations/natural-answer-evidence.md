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
