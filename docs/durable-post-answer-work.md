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
