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
