# Durable post-answer work

Twin composes SAGE `WorkJournal`/`WorkService` with a restricted application
handler. Inline and background HTTP delivery use the same journal and worker;
there is no separate best-effort post-answer executor.

## Commit and recovery

1. Twin prepares the validated answer's conversation-memory record without writes.
2. Twin captures a versioned `PostAnswerTask`, excluding transient Slack channel
   context, runtime ports and credentials. It includes the full allowed evidence,
   profile/calendar snapshots and evaluation time.
3. SAGE atomically commits canonical NeuroMem memory, the answer and post-answer
   task intent in `conversation_memory_dir/neural_state.sqlite3`. The memory
   success trace is published only after acknowledgement. Its duration is unknown
   (`null`), since the immutable response is prepared before the transaction.
4. SAGE owns bounded dispatch, leases, retries and result publication. The handler
   writes versioned NeuroMem profiles and replay-safe artifact drafts, then
   computes follow-up suggestions/scoring and refreshes a provenance-aware digest
   when the captured task requests it. Only the restricted digest projection may
   use a model under saved policy; there is no generic model, booking or notification port.
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
durations for measured stages, immediate follow-up memory visibility and shared
delivery semantics. Service-level failure injection covers journal SQL rollback,
lost commit acknowledgement and restart recovery without upstream chat replay.

This component is not yet a complete recoverable HTTP delivery protocol. A client
cannot yet resume the saved result after process restart through a new authorized
endpoint. Recovered scoring results remain in the journal; the existing in-process
operational telemetry projection is not a durable aggregate. Its event-id
deduplication applies only within the retained telemetry window.

### Trusted HTTP identity prerequisite

The HTTP chat entry resolves one immutable `ChatIdentity` before tracing or
workflow execution. A bearer token takes precedence over browser cookies; its
stored role and token ID never imply a personal account. A valid user session
binds the account ID, name and normalized email from server-side account data;
a conflicting request email is rejected with 403. Anonymous/expired sessions
and role-only API tokens cannot write a caller-supplied personal email into the
chat record. Public profile hints remain non-privileged. JSON and multipart
requests use the same boundary. Raw credentials are not part of this contract.

This is identity binding, **not yet conversation ownership enforcement**. Names
and conversation IDs must not become authorization keys. Anonymous contact
details cannot double as verified history ownership; contact/booking flows need
an explicit separate policy. Slack uses its existing verified account-link path,
not HTTP bearer identity. Ownership checks for retrieval, compression, workflow
events and cancellation, and an authorized resumable result endpoint remain
required before deployment of the full delivery design.

### Owner-scoped canonical memory contract (adapter cutover pending)

Conversation and profile records can now carry an immutable `owner_id` scope
(`account:`, `api_token:`, `guest:` or `slack:` plus an opaque subject). Canonical
conversation enumeration, recent-turn reads, short/long-term retrieval and
artifact retrieval require exact scope equality. Index filters are an efficiency
hint, not the security boundary: returned IDs are checked against canonical
records even if the index ignores all filters. Profiles use the owner rather
than display name/email as identity; their IDs hash the complete owner string in
a namespace legacy slug IDs cannot produce. Punctuation normalization is not
used for owned IDs. Task capture rejects mismatched request/record ownership.

Missing ownership remains explicitly **unassigned**, not a wildcard and not
automatically inherited from matching names, emails or conversation UUIDs.
Legacy payloads retain their representation so existing durable fingerprints
are not rewritten. Unassigned/internal reads cannot see assigned records; a
scoped read cannot see unassigned records. Administrative inventory remains a
separate privileged internal operation.

This contract is not yet activated for HTTP traffic: `ChatIdentity.bind` clears
any client-provided owner field. The coordinated cutover must establish durable
guest identity, pass trusted scope through every service/history/digest path,
and handle legacy ownership explicitly. Until then, the old unassigned-data
behavior remains; no end-to-end ownership or deployment claim is made. Do not
expose journal recovery on this intermediate schema alone.

Owned service requests now propagate their scope through recent-session context,
meta recall and saved profile snapshots. Digests use a separate owner-derived
namespace plus the original conversation ID, so identical conversation IDs
cannot share a source frontier or text across owners. Preparation rejects
mixed-owner records and publication rejects a plan from another scope. Missing
ownership stays in the unassigned namespace, never a wildcard.

Both manual digest tasks and automatic post-answer projection carry scope through
the same durable worker port. A restart does not infer identity from the current
request, display name or email. Tests exercise same-ID multi-owner publication,
reopen, rejected cross-owner plans and an actual owned answer with recent context,
profile capture and automatic digest. Transport activation and owner-bound
history/events/cancellation remain pending; these internal service tests do not
establish online isolation.

Memory and answer/task intent share one transaction. A lost acknowledgement may
leave a committed turn even though the caller receives an error; workers recover
that saved intent, but client retry identity/resumption is not yet implemented.
Journal retention/admission policies remain separate acceptance items. No
production cutover is implied by repository tests or a merge to main.

## Same-store commit integration

`PostAnswerRuntime(state=memory.state_store, ...)` borrows the memory owner's
SAGE store; shutdown drains its worker but does not close borrowed state.
`submit_turn(task, memory)` rejects a different store before writes, takes one
detached admission snapshot for both the memory record and task, and admits a
NeuroMem prepared batch (including attachments and training receipt) through
SAGE WorkService's atomic destination-mutation API. The old direct prepared-
journal callback path has been removed. SAGE owns transaction/admission/startup.

The memory adapter holds its write lock through commit and read-view publication.
Local derivation reads take this lock and decode committed canonical NeuroMem
records, not a possibly stale cache. This also handles a committed transaction
whose acknowledgement was lost before local publication. Replay still validates
the journal intent and does not repeat training. Non-durable collection modes
reject combined admission rather than falling back to separate writes.

The HTTP answer workflow calls this combined boundary in both delivery modes.
An unexpected legacy `post_answer_work.sqlite3` blocks runtime creation with
`legacy_work_journal_requires_offline_migration`; it is never ignored, deleted or
automatically replayed alongside the new store. Offline migration is required
for such installations. The inspected deployment has no file at its configured
legacy path. Atomicity is same-store, not cross-file or external-effect atomicity.

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

`prepare_memory` captures a typed artifact intent rather than creating a
draft. The post-answer task stores this intent; the SAGE worker derives its
identity from the committed source record and invokes the replay-safe destination.
A task without a source record cannot contain an artifact intent. There is no
inline artifact-creation path. A missing destination or write failure leaves the
task retryable; it does not publish a completed artifact trace. Tests gate the
background destination to verify that the intent is durable before the response
returns and the completion event follows the actual write. A failure later in the
handler may replay creation, but cannot duplicate the draft or undo a review.

## Digest work

Automatic tasks capture a `DigestIntent` only for a persisted source exchange.
It contains threshold, target size and generation mode, not prompts or arbitrary
owner IDs. The handler derives conversation identity from the validated record.
It refreshes the current canonical conversation projection through NeuroMem's
optimistic source-coverage contract; it does not regenerate the user's answer.
The source snapshot is taken when work runs, so it may include later committed
turns. Replays select only still-uncovered IDs. A failed generation or stale
publication raises and remains retryable, without publishing task completion.

Manual refresh captures `DigestWorkTask` and uses the same journal, bounded
WorkService and handler port. It cannot execute profiles, scoring, booking or
chat. The API waits off the event loop; timeout does not cancel saved work.
Manual and automatic updates no longer have a separate synchronous executor.
Configured generation mode is captured before commit; later setting changes do
not rewrite admitted work. Legacy tasks without digest intent do not acquire
new side effects merely because the worker was upgraded.

Capacity-aware semantic generation, authenticated delivery recovery and
production deployment remain separate acceptance gates.
