# Durable post-answer work

Twin composes SAGE `WorkJournal`/`WorkService` with a restricted application
handler. Inline and background HTTP delivery use the same journal and worker;
there is no separate best-effort post-answer executor.

## Commit and recovery

1. The validated answer's conversation-memory record is committed before delivery.
2. Twin captures a versioned `PostAnswerTask`, excluding transient Slack channel
   context, runtime ports and credentials. It includes the full allowed evidence,
   profile/calendar snapshots and evaluation time.
3. SAGE atomically commits the answer and post-answer task intent to
   `conversation_memory_dir/post_answer_work.sqlite3`.
4. SAGE owns bounded dispatch, leases, retries and result publication. The handler
   writes versioned NeuroMem profiles and replay-safe artifact drafts, then
   computes follow-up suggestions/scoring.
   It cannot call a model, create a booking or send a notification.
5. Async observers publish only committed task results. Startup starts recovery
   even when model cache warmup is disabled. Shutdown drains workers, permits
   observers to read results, then closes storage.

Profile destination writes are idempotent and revision-ordered; SAGE delivery is
at least once, not exactly-once handler execution. Attempt timings can differ on
replay; the captured answer and semantic inputs cannot. A failed handler retains
its task for retry. A result-observation timeout does not cancel durable work or
claim that the task succeeded.

## Acceptance and remaining boundaries

Tests cover saved-task recovery without upstream chat replay, retry after profile
commit, preserved answer/model identity, captured score skip decisions, real trace
durations, immediate follow-up memory visibility and shared delivery semantics.

This component is not yet a complete recoverable HTTP delivery protocol. A client
cannot yet resume the saved result after process restart through a new authorized
endpoint. Recovered scoring results remain in the journal; the existing in-process
operational telemetry projection is not a durable aggregate. Its event-id
deduplication applies only within the retained telemetry window.

The conversation-memory commit and answer/task journal commit are separate
transactions. A crash between them can leave a stored exchange without a task;
no successful response is returned before the second commit. Cross-store atomic
turn recovery and journal retention/admission policies remain separate acceptance
items. No production cutover is implied by repository tests or a merge to main.

## Same-store commit integration

The memory storage boundary now supports
`persist_exchange(record, work_commit=prepared_work_commit)`. It composes a
NeuroMem prepared batch (including complete attachments and the training receipt)
with the SAGE journal mutation in the memory state runtime's single transaction.
The read view/history is published only after commit. Replay of an already
visible exchange still validates and commits the supplied journal intent; it
does not bypass that intent or repeat training. Non-durable collection modes
reject this operation instead of silently falling back to separate writes.

The HTTP workflow does **not yet call this combined boundary**. Its existing
post-answer journal is in a separate file. Service cutover must reconcile task
capture, journal ownership/migration, artifact drafts and digest updates before
claiming whole-turn atomicity. The combined boundary tests use the same SAGE
state runtime for memory and journal; they do not establish cross-file atomicity.

New canonical attachment records preserve full accepted input text, including
Markdown whitespace. The stored field is still named `text_excerpt` for the
existing record schema; old already-truncated records are not reconstructible
from these records alone. History response validation no longer rejects generated
answers merely because they exceed 12000 characters.

## Replay-safe artifact draft destination

Artifact drafts now use SAGE atomic state in the same
`conversation_memory_dir/neural_state.sqlite3` database, under their own slot.
Creation and review transitions use one canonical state source, not JSON plus
an in-memory cache. A source memory ID identifies one immutable draft input;
replaying the same input returns its current draft/review status. Changed input
under that identity fails closed. Calls without a source memory ID remain manual
new creations and must not be used as replayable worker operations.

Legacy JSON drafts are imported in one atomic update with content receipts,
preserving draft IDs, content and review status. Only after that commit are source
files moved to `artifact_memory_draft_dir/legacy-imported/`. The archive is for
recovery/audit, not a second write/read path. Repeated imports cannot reset later
reviews. Duplicate source identities or changed imported files stop migration
for explicit resolution rather than silently choosing a record. Back up both the
state database and legacy archive before production rollout.

This destination stores the draft collection and receipts in one atomic record;
large-queue latency/retention is not yet qualified.

`persist_memory` now captures a typed artifact intent rather than creating a
draft. The post-answer task stores this intent; the SAGE worker derives its
identity from the committed source record and invokes the replay-safe destination.
A task without a source record cannot contain an artifact intent. There is no
inline artifact-creation path. A missing destination or write failure leaves the
task retryable; it does not publish a completed artifact trace. Tests gate the
background destination to verify that the intent is durable before the response
returns and the completion event follows the actual write. A failure later in the
handler may replay creation, but cannot duplicate the draft or undo a review.

The whole-turn atomic switch, digest handling and production deployment remain
separate acceptance gates.
