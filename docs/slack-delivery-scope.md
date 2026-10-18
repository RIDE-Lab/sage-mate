# Slack delivery scope

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
