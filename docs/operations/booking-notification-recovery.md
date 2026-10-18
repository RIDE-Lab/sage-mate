# Booking notification recovery

Booking state, operation result receipt and immutable notification intent commit
together through SAGE OperationReceipts and WorkJournal. The resident WorkService
dispatches notifications after the transaction. The API reports `queued`, not
`sent`; a recipient field is not proof of delivery. Replay returns the original
result and must not recreate a notification or revert a later booking decision.

The former synchronous booking-mail attachment path is removed. Application
startup recovers durable notification work; orderly shutdown drains its worker
before closing the booking state store. Source-of-truth booking state remains
independent from email transport success.

Recipient consistency is checked before transport invocation. A changed admin
recipient or inconsistent student recipient fails closed with a sanitized code.
Transient transport errors retry up to five attempts. SMTP acceptance followed by
acknowledgement loss can still duplicate mail: this is at-least-once delivery,
not external exactly-once delivery. No production SMTP canary is authorized by
these synthetic tests.

## Deployment gate: legacy receipts

Do not deploy this candidate over nonempty pre-outbox operation receipts without
an explicit migration. Old receipts declare only booking destinations; the new
atomic destination set also declares work admission. SAGE correctly rejects a
changed destination set on replay. Do not weaken that check, delete receipts,
rerun producers or silently reissue historical email. Historical SMTP outcome is
unknown and must be preserved explicitly until reconciled. A migration must retain
the original canonical response, original receipt evidence and current booking
state, and must not admit a historical notification automatically.

The operator tool `tools/migrate_booking_notifications.py STATE_PATH` reports only
schema counts by default. After stopping the application and taking a recoverable
SQLite backup, run the same command with `--apply`; it refuses while the user
systemd application unit is active. Keep all writers stopped through the migration
and release switch. The tool uses SAGE's atomic audited destination migration,
retains original response/digest/current booking state and records historical SMTP
outcomes as unknown, without queuing historical mail. Unknown schemas fail closed.
Interrupted multi-record migration can be repeated; do not restore an older store
over newer application writes. Replays are handled by the one current receipt path,
not by a legacy producer or a runtime compatibility branch.

Cross-database ingress lease fencing remains a separate qualification. Passing
local receipt tests does not complete the overall native workflow migration.

## Captured conversational booking decisions

The conversational booking draft uses the same SAGE persistent state runtime as
the calendar and notification journal. The former process-local conversation
dictionary is removed. Draft slots are scoped by verified owner and conversation;
unattributed requests cannot capture a draft or discover one by name/email.
HTTP/Slack adapters continue to capture and reauthorize the actual channel owner.

Before an effect, SAGE OperationReceipts atomically stores the updated draft and
an immutable decision for the owner-scoped delivery identity. The decision holds
missing fields or the complete typed booking request, including resolved dates.
Explicit topic updates may replace a draft topic; suggestions/defaults only fill
an empty value inside the transaction. Contact/time-only follow-ups preserve an
already captured topic, including when another turn updated it concurrently.
Retry validates the original request digest and reuses that decision without
reparsing fields. If a client omits a conversation ID, the conversation identity
is derived from the delivery identity so the same request survives restart.

Calendar commit still uses its single canonical receipt/outbox path. A failure
between calendar commit and draft settlement recovers by replaying the same
booking identity, then settling the draft. Settlement has its own SAGE receipt
and only updates a draft still owned by that turn; an older retry cannot erase
a newer draft. A conflict clears only the captured time preference, retaining
contact and topic. No external notification is sent from these transactions.

Draft/plan results are private state. There is no import or automatic claiming of
old in-memory/unattributed drafts. This change does not add a second scheduler,
an application retry worker, or a cross-database cancellation guarantee. Production
promotion still requires the frozen-release regression and recovery gates below.

## Candidate evidence

15 notification/durability tests passed, including abrupt process death on both
sides of commit, reopen recovery, retry, atomic admission failure and destination
validation before transport. All 26 admin API tests passed with isolated booking
stores and actual background notification completion checks. Agentic workflow
tests previously passed56 and lifecycle tests25. The frozen `eef22b7` release
with SAGE `d256189e` completed 1,627 tests with 31 skips in 405.95 seconds.
These skips are not successful qualifications; the run also excluded the existing
`test_identity_eval.py` suite. This is component/regression evidence, not proof
of production SMTP delivery or of the remaining conversational-plan guarantees.

The subsequent `ea724d3` release includes identity-specific public Wiki starters
and additional migration CLI/frontend tests. In an isolated preview, a real
public Wiki request completed in 9.15 seconds on `eef22b7`, with full source
blocks, a terminal SSE completion frame and cross-guest isolation. After stopping
that preview and starting `ea724d3` against the same synthetic runtime, readiness
was checked before replaying the same request: replay took 0.15 seconds and
preserved the exchange ID, complete answer and source blocks. No production
application or inference engine was restarted for this qualification.

The final `ea724d3` regression completed with 1,631 passes, 31 skips and one
failure: two independent booking connections could fail at their first SQLite
WAL negotiation with `database is locked`. SAGE `b5abaa80` fixes that startup race
with a bounded retry of SQLite lock codes and connection cleanup on failed
initialization; Twin pins that exact dependency. Deterministic tests fail on the
old package and pass with the repair, including an actual held database lock,
deadline exhaustion and eight independent cold connections with 80 updates.
The related SAGE runtime suite passed 202 tests, and Twin's booking, notification
and migration group passed 21 tests against the repaired source. The frozen
`cbcb10b` release installed the pinned SAGE package into its own environment;
four deterministic initialization checks also passed against that installed
package, without a source-path override. Its complete regression passed
1,633 tests with the same 31 skips in 358.70 seconds and the same existing
`test_identity_eval.py` exclusion. No additional tests were skipped to clear
the startup failure.

The initial collection attempt for `ea724d3`
selected an unavailable `cpp` memory index because the isolated test index was
not specified. The corrected test run explicitly uses an isolated runtime,
local knowledge, hash embeddings and BM25 memory indexing. This neither changes
production indexing nor qualifies the unavailable backend. Do not deploy an old
frontend when promoting the backend: the release must retain `ea724d3` or newer
identity-specific Wiki question pools.

## Production acceptance for the SQLite repair

Production promoted the complete `cbcb10b` release, retaining the question-pool,
workflow-status and dark/light workflow-theme repairs. The cutover verified no
active work and no bookings, stopped the Wiki sync timer and application, then
backed up both affected SQLite databases through the backup API and checked
their integrity. Offline receipt migration reported zero legacy/current receipts
and zero migrated records. No historical notifications were admitted.

After the application reported ready, a public HTTPS request completed before
the switch was replayed on the new release in 2.12 seconds, retaining its exchange
ID, answer and source blocks. A fresh public Wiki request completed in 10.14
seconds; full sources, terminal SSE completion and cross-guest isolation passed,
and same-request replay took 0.89 seconds. The Wiki timer was restored and the
installed SAGE commit was verified as `b5abaa80`. Private state backups remain on
the service host. No inference engine was restarted for this deployment.

These checks qualify this startup repair and release transition. Conversational
plan capture, cross-store cancellation fencing and the remaining native workflow
requirements still need their own implementation and acceptance.
