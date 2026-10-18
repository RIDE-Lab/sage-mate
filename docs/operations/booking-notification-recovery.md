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

Conversational plan capture and cross-database ingress lease fencing are separate
remaining qualifications. Passing local receipt tests does not establish those
properties or complete the overall native workflow migration.

## Candidate evidence

15 notification/durability tests passed, including abrupt process death on both
sides of commit, reopen recovery, retry, atomic admission failure and destination
validation before transport. All 26 admin API tests passed with isolated booking
stores and actual background notification completion checks. Agentic workflow
tests previously passed56 and lifecycle tests25; the complete candidate suite is
still pending and must be rerun against the final frozen revision.
