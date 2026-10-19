# Natural, role-specific starter questions

The four roles now have separate pools of six authored questions: public visitors
(research and orientation), undergraduates (concepts and first experiments),
writing students (selection, argument and revision), and lab members (hypotheses,
controls and systems evaluation). Availability matching uses public catalog topic
metadata, not catalog-authored prompt text. Catalog outages retain a meaningful
role-specific pool. Unknown roles fall back to the public visitor pool.

Visible starter text is the exact question put into the composer. It contains no
mandatory “依据 Wiki” prefix or citation instruction. The public catalog and guest
lucky-question endpoint also use natural wording. Retrieval permissions and source
verification stay inside the verified SAGE workflow: selecting a UI role never
grants private-source access; authorized source content still requires an audited
source-bound answer. No model, budget or source-audit gate was relaxed.

Regression coverage includes all four pools with/without catalog metadata,
identity-change races, exact click text, ignored catalog prompt injection,
source-grounded routing and rejection of tampered answers. Live qualification uses
unmodified natural questions against an isolated public Wiki snapshot, without
private accounts or Slack/email/calendar side effects. Release acceptance must
record focused/full regression and real-answer checks separately from rendered
browser acceptance and production cutover.

## Qualification and rollout

Code release `c536b6f69a6876b60de0f3632796429efc7b38aa` passed13 Node suites,
123 focused cases (1 existing skip), and the full clean-release regression:
1772 passed,31 skipped,0 failed (1803 collected;1 existing Starlette warning).
The first archive-based run lacked the pinned dev-hub submodule; its missing
deployment-receipt script was reproduced explicitly. Restoring the unchanged
production pin made all9 receipt cases pass; the entire suite was then rerun on
the clean Git release, rather than counting a partial retry as a full pass.

Four unmodified natural questions retrieved relevant public Wiki sources and
preserved full source links/answer blocks. Candidate browser acceptance covered
the visitor pool, exact click text, completed answer, Markdown/table and collapsed
support. The real catalog supports six questions for each of the four roles.

Production was switched to this code release only after those gates passed.
Application/background readiness and Slack readiness are healthy; runtime paths,
credentials, models, budgets and dependency pins were preserved. Public HTML,
JavaScript and CSS match the committed bytes. A public natural Prefill/Decode
request completed in19.73s with Wiki evidence, full links and a normal finish.
Public Chrome additionally showed the signed-in lab-member pool and exact
click-to-input text. One old tab and the in-app browser still showed the prior
gray/timeout behavior; a fresh Chrome tab rendered the new page and actual
generation normally. This release does not claim to fix that separate old-tab
issue.

The public browser test also exposed a content-coverage limitation: the original
RAG evaluation starter completed safely with a declared evidence gap, so it is
not counted as a complete quality pass. The pool replaces it with “RAG 系统可以从
哪些环节改进检索质量？”, which asks about the methods actually covered by the public
article. This is content curation, not a retrieval override, relaxed audit, or a
claim that the missing evaluation material has been repaired.

The corrected question passed isolated live QA in17.16s and public live QA in
68.48s, with normal completion, the relevant Wiki source, full links, source
blocks and no declared coverage gap. These are individual latencies, not a speed
benchmark. Final code release `e3333071827ef6e741ef963ea1b5fd6c2ef753cf` is active;
its delta from the full-regression baseline contains only the reviewed frontend,
its Node regression and this document. All13 Node suites passed on the final
remote release. Public assets match that revision. Final Chrome refresh/new-chat
acceptance visibly showed the corrected RAG question alongside the two other
lab-member questions, with readable dark-mode rendering. Both app and Slack
readiness remained healthy. No temporary branch or pull request was created.
