# Automatic public web research

Twin delegates web decisions and observed execution to SAGE. It has no local
keyword/empty-hit web fallback policy. Identity comes from the verified ingress;
private-source ACLs remain enforced independently of model intent proposals.

The default is `web_mode=auto`. Stable explanations, local Wiki/private facts
and business operations do not blindly search when the local index is empty.
Current public facts and public-source requests can search even if older local
material exists. Advanced controls under Tools expose auto, never and always.
Always cannot override private/unknown disclosure, explicit source restrictions
or an operator-disabled network capability. Prompt templates never change these
user controls. A new browser preference key prevents an old unchecked checkbox
from silently disabling the new default auto behavior.

Only a verbatim public-subject span of the current question reaches the search
transport. No query is expanded using conversation history, retrieved knowledge,
private attachments or Slack channel history. Requests carrying attachments or
channel context conservatively stay offline. Unknown pronouns require a public
subject rather than disclosure of history. Semantic classification and common
secret/email guards are not a formal data-loss-prevention guarantee.

The classifier uses SAGE's native JSON field schema. JSON shape repair stays on
the same intent protocol/model with its existing thinking-off settings, rather
than accidentally using answer-generation thinking defaults. The user's answer
model, thinking preference and output budget are not changed by this feature.

The UI renders provider start/terminal receipts, not an optimistic graph stage:
searching, sources retrieved, no usable sources, failed or skipped. Source
retrieval is not proof of factual correctness. Full source URLs and UTC retrieval
times remain in the evidence section. Old answers without a receipt never gain
an invented search status or fresh timestamp. Search failure does not authorize
unsupported claims; the existing SAGE evidence workflow retains explicit gaps.
Authorized web snippets enter that same coverage/source-plan workflow alongside
local sources, with stable `web:N` identities and full URLs. Selection does not
promote a search snippet to the complete fetched article. Unselected candidates
remain separately available in a collapsed receipt, explicitly labelled as not
necessarily used or verified; they never become supporting citations by default.

SAGE owns one request deadline across official sources and provider fallbacks.
The HTTP and Tavily adapters pass remaining time, rather than the SDK's default
60-second timeout. Bounded read-only workers protect caller waiting from blocked
OS DNS. At most four external reads may remain outstanding; late results are
discarded. Timeout is not a claim that the underlying request has been forcibly
cancelled. No shared inference engine is restarted for this capability.

The deprecated wire boolean `web_search` is normalized at ingress to the same
mode contract when no `web_mode` is supplied. It does not select a second policy;
an explicit new mode takes precedence. The old operator auto-trigger setting is
removed. JSON and multipart requests both default to auto when neither is sent.
