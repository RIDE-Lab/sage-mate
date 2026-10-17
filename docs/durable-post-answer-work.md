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
   writes versioned NeuroMem profiles and computes follow-up suggestions/scoring.
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
