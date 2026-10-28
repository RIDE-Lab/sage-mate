# Client retry and lost acknowledgements — 2026-10-28

## Contract

A retry must retain the original request ID, authenticated owner, conversation,
payload and attachments. `/chat` and `/chat/stream` accept `Idempotency-Key` or
`request_id`; disagreement or reuse with changed input is rejected with 409.
A different ID represents a new operation. Legacy clients omitting an ID cannot
receive deduplication across independently submitted requests. Authentication is
checked again on replay; a key is not an access capability.

The browser's manual and queued retries retain the same operation identity. A
manual retry reuses the original message instead of appending a second question.
Changing account or conversation prevents that retry. Retry contexts are in-memory;
a page reload does not recreate an unavailable retry context with a fresh ID.

SAGE `ExternalEffects` durably claims an invocation before calling a destination
without an idempotency API. Completed receipts replay their result. An incomplete
claim is never automatically reclaimed, including after process death. This is
at-most-once invocation, not a promise of exactly-once delivery: a crash between
the claim and the send can mean nothing was sent. An accepted send whose receipt
was lost remains **unknown**, requiring destination-side evidence to reconcile.
Never clear a claim or assign a new ID just because it timed out.

Twin uses this shared primitive for answer production, booking email and follow-up
email. Previously attempted pre-upgrade answer/booking notification jobs without
an effect claim are also treated as uncertain, rather than replayed blindly.
Follow-up email uses booking + action type when available, preserving one logical
intent even across duplicate queue entries. Repeating a booking decision does not
reset an existing follow-up to queued or overwrite its payload.

Model HTTP read/write timeouts are not retried: the server might already be
generating. Only connect/pool timeouts, before request transmission, qualify for
transport retry. Existing explicit answer-repair/validation operations remain
separate workflow operations; they are not transport retries.

## Real transport fault injection

Run in an A100 network namespace with **loopback only**, no external interface or
route, clean environment, temporary data, a synthetic answer producer and an SMTP
sink accepting only synthetic data. No live inference or mail service is used.
Tests execute the actual API, journal, persistence and notification workers.

`tests/test_retry_transport_acceptance.py` covers:

- Buffered HTTP and SSE: proxy reads the accepted response, then drops the client
  acknowledgement. Four concurrent retries and four retries after reopening the
  persisted service return one exchange; answer producer invocation count is 1.
- Changed payload or conflicting query/header key returns 409.
- Proposal response lost: retry restores the same draft; changed input returns 409.
  No booking or email exists before confirmation.
- Confirmation response lost: concurrent retries before/after reopening produce
  one booking ID. SMTP accepts DATA then closes before its final 250; one accepted
  notification remains unknown and is not resent.
- Follow-up SMTP acknowledgement loss, reopening, and re-queuing the same logical
  intent: one accepted message, visible unknown status, no second send.
- Actual model HTTP endpoint receives a request but delays its response past the
  read timeout: exactly one request for both buffered and streaming calls, even
  with retry count set to 3.
- Kill an isolated worker after its model endpoint accepts the request, before
  the result is persisted. Recover the same expired journal lease in a new worker:
  zero additional producer calls, terminal `answer_outcome_unknown`.

The old booking notification source was separately exercised against the same
SMTP fault: **5 accepted copies** for one notification. The fixed worker accepts
**1**, stops automatic retries and reports uncertain delivery.

Generic SAGE tests also inject acknowledgement loss when committing the claim and
when committing the result; reconnecting persistent stores never repeats an
uncertain external invocation. A completed durable result is replayable even if
its commit acknowledgement was lost.

## Recorded results

Final combined Python transport/durability regression: **148 passed**, one
dependency warning (33.69 s). Browser retry/booking state tests: **5 CJS test files
passed**. Follow-up integration checks: **3 passed**. These counts are not a
statistical reliability estimate.

## Reproduction

Use the release-compatible Python environment with Twin `src` and SAGE `src` on
`PYTHONPATH`, offline model discovery, BM25 test indexes, and the checked-in public
workflow policy seed. Execute inside an isolated loopback-only network namespace:

```sh
python -m pytest tests/test_retry_transport_acceptance.py tests/test_http_workflow.py \
  tests/test_http_workflow_integration.py tests/test_booking_notifications.py \
  tests/test_booking_confirmation.py tests/test_booking_durability.py \
  tests/test_llm_client.py ../SAGE/src/tests/test_external_effect.py \
  ../SAGE/src/tests/test_operation_receipts.py ../SAGE/src/tests/test_action_confirmation.py -q
node --test tests/chat*retry*.test.cjs tests/booking*.test.cjs
```

This is controlled transport/process-recovery acceptance, not a production fault
injection, real-user browser study, SMTP inbox-delivery claim or model-quality test.
