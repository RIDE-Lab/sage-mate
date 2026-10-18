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

The root journal result marks the new workflow version. Startup rejects any
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

The transport sends all replies through `chat.postEphemeral`, with the verified
workspace, original channel and requesting user. The same invariant applies to
commands, direct-message events, mentions, notices, recovery and retries. No
public-message fallback exists. Historical ingress `private_reply=False` is
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

Multipart source qualification: 200 related checks passed (32.27 seconds),
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
