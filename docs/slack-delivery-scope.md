# Slack delivery scope

## One native multipart path

Twin pins SAGE `4c4abffed927925856daf6ac468789dc391d587f` with its declared
`delivery-markdown` extra. SAGE parses and plans the complete answer, checks
rendered content, and produces bounded native Markdown frames. Twin specifies
only Slack's transport limit, literal control tokens and trusted destination
policy; it contains no Markdown converter, splitter or second retry worker.

The answer task commits a versioned `DeliveryPlan` as its native result before
acknowledgement. Admission captures notice/failure plans before commit as well.
Every delivery uses `DeliverySequence` under the existing journal/task lease.
Retries restore immutable payloads and skip confirmed receipts; they do not
regenerate, rerender, or repartition the answer. External acceptance with no
local receipt can still duplicate a part after failure: this is at-least-once,
not Slack exactly-once. Receipt metadata contains no answer body or credentials.

Each part rechecks current authorization and the configured workspace, and
validates the stored destination against verified ingress. Member answers stay
member-protected; pre-captured nonsensitive notices can reach an unbound or
revoked requester without exposing answer content. Revocation during multipart
delivery stops the private remainder and records failure; the generic failure
notice is a separate, pre-captured settled task, not a replacement answer.

The only final-answer transport body is `markdown_text`, never combined with
`text` or `blocks`. Literal Slack control strings are code rather than mentions.
Question markup is isolated from answer markup, answer indentation is retained,
and the source-title footer is not limited to the first two entries.

## Cutover and history

The root journal result marks workflow version 3. Startup rejects any
unfinished legacy-format work without mutating it. Before promotion, drain the
old worker and recheck zero old pending/running requests under quiescent writers.
An earlier zero-count snapshot is not a substitute for this cutover check.
Already completed/terminal historical records remain readable and do not get a
new formatter, finalizer or resend. The old single-text send path and automatic
old-graph upgrade are removed; no runtime legacy sender is retained.

## Recipient and display constraints

Twin currently authorizes Slack model requests using the requester's verified
member identity. Retrieval can therefore contain private knowledge or personal
context. A channel mention does **not** authorize disclosing that answer to the
channel, even if the channel itself is private.

The transport uses `chat.postEphemeral` for commands and channel mentions, with
the verified workspace, original channel and requesting user. A signed message
event explicitly identifying a one-to-one Bot IM captures `conversation_type=im`
and uses `chat.postMessage` **in that original private conversation** instead,
preserving normal DM persistence across sessions. It does not create a new DM.
Generic message kinds, channel-ID prefixes, caller-supplied extra fields and the
legacy privacy hint do not attest a private conversation. Both methods use the
same captured plan and native sequence; no public-channel fallback exists.
Historical ingress `private_reply=False` is
retained as journal data, not as authority to widen the audience. Neither the
model nor a caller-supplied owner ID chooses a destination or recipient.

Ingress thread identity is retained in the original immutable request payload,
but is not passed as a delivery `thread_ts`. Slack only displays ephemeral thread
replies in an already active thread; a first mention does not establish one.
Replies therefore appear privately at the same channel's top level, without a
public placeholder, a different channel, or a new direct message.

An accepted Slack receipt is not proof of display or reading. Slack documents
that ephemeral messages require an active user and are not retained across
sessions. The canonical Twin answer and owner-scoped work status remain separate
from that transport acknowledgement. Do not treat ephemeral delivery as a durable
archive or silently switch to a public reply when it fails.

The selected method, private visibility and membership policy are part of the
captured plan identity. Persistent DM receipts require `ts` and the exact original
channel; ephemeral receipts require `message_ts`. Neither method tries the other
method's response fields as a success fallback. Reported truncation is failure.

## Input integrity

The signed Events API `authorizations` envelope identifies installed Bot users;
it is not the sender's identity or a member-role grant. Only those verified
leading Bot invocations and their single ordinary separator are removed. Other
people's mentions, line breaks, indentation and trailing whitespace remain in
the model question. Slash-command questions also retain their original bytes.
Malformed/unverified Bot metadata is rejected before command/binding admission,
rather than guessing a Bot name or storing a misparsed binding credential.
Caller/member authorization remains a separate current-account check.

Reference: [Slack Events API envelope](https://docs.slack.dev/apis/events-api/).

Reference: [Slack chat.postEphemeral](https://docs.slack.dev/reference/methods/chat.postEphemeral/).

Qualification covers signed requests for all three transports, current member
role resolution, and reconstructed workers resuming previously admitted mentions
with a legacy false privacy flag in public/private/IM destinations after an
initial send failure. No real Slack message is sent by these tests. This change
alone does not qualify Markdown rendering, multipart delivery or production
promotion of the unified workflow.

Prior audience-only source qualification: all five Slack test files passed (95 cases, 13.60 seconds),
with Ruff and diff checks clean. Tests additionally assert delivery omits the
possibly inactive `thread_ts`. The existing Starlette test-client deprecation
warning remains. This is isolated source qualification, not a frozen deployment
or proof of receipt/display in a real Slack client.

Prior v2 multipart source qualification: 200 related checks passed (32.27 seconds),
covering native worker/SQLite reopen without formatter/model calls, confirmed
prefix preservation, mid-sequence revocation, legacy drain barriers and terminal
history replay (including absent optional defaults), typed recipient/policy
binding, signed native-Markdown requests and complete long-answer/source links.
Malformed or missing ephemeral `message_ts`, wrong-method receipts, non-boolean
success, and reported truncation cannot become accepted delivery. Startup, HTTP,
identity, dependency/bootstrap and service-script checks passed in the same gate;
Ruff, shell syntax, diff and frozen-lock checks passed. The existing Starlette
deprecation warning remains. Frozen complete-suite and actual Slack display
acceptance are still required before production promotion.

Current v3 source qualification: 231 related checks passed (34.72 seconds),
including verified private-DM persistence, channel-mention anti-override, method-
specific receipt/channel validation, old terminal DM metadata replay, exact input
whitespace/person preservation, and malformed Bot-authorization rejection before
binding admission. The Git-pin verifier now accepts valid extras syntax while
still rejecting short pins, registry fallback, wrong repositories and stale
commits. Ruff, supported-Python lock verification, shell and frozen-lock checks
pass. The previous immutable v2 frozen run had 1699 passes, 31 unchanged skips and
one verifier-parser failure; that failed report is retained, not release evidence.
A new frozen complete run and real client acceptance remain required.

Immutable v3 qualification (Twin `c0cae2b`, SAGE `4c4abffe`): the completed
JUnit report contains 1757 cases: 1726 passed, 31 unchanged skips, no failures
or errors (470.248 seconds). The historical identity-evaluation exclusion and
file-descriptor limit were unchanged. The previous red report remains retained.
The same release's isolated HTTP preview returned full Wiki source blocks in
9.63 seconds; native execution/atomic task acknowledgement, owner isolation and
actual service-restart canonical replay (0.24 seconds) passed. Cancellation
returned 499 with two sealed destination databases, no canonical answer and
successful replay (0.43 seconds). Served JavaScript matches the frozen artifact.
These are not evidence of real Slack client receipt/rendering or production
promotion: both remain explicit gates.

Independent knowledge qualification is reproducible with the release interpreter
and configured environment (no secrets in arguments or dotenv loading):

```bash
python tools/qualify_knowledge_incremental.py --parent-dir /path/to/acceptance-artifacts
```

The tool creates a fresh synthetic corpus and uses real configured OpenAI-wire
embeddings and SageVDB across three OS processes: seed, update/delete, then reopen
and search. It checks revision 4, four completed native index tasks, removed
document exclusion, canonical body, unchanged committed generation on reopen and
no cold-start synchronization. All three phases passed against the candidate's
installed dependencies and actual embedding configuration. The tool never opens
the production corpus or changes services; it retains its own audit artifacts.
This qualifies knowledge persistence/incremental processing, not Slack rendering.

The subsequent legacy-chat-shortcut retirement runtime `e86c5c0` has its own
frozen result: 1726 passed, 31 unchanged skips, no failures/errors, exit0 in
458.29 seconds. Exact-version isolated full Wiki/native task/owner/asset checks,
actual answer restart replay, cancellation restart replay and the real-embedding
knowledge qualifier passed again. These do not close the outstanding real Slack
Markdown/multipart client-display or browser visual gates. Production remains
unchanged; no real Slack message was sent during this qualification.
