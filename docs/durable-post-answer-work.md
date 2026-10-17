# Durable post-answer work

## SageVDB dependency qualification

The base dependency pins `c1b38311109fbe65dfe7ef203be7938231587c54`, which
provides `VersionedDocumentCatalog`, revision-fenced index generations and
`rebuild_from_catalog` and revision-qualified read-only `refresh`. An exact Git archive was built offline on A100 after
Git transport/DNS prevented network builds; no shared Git/network configuration
was changed. The CPython 3.11 Linux x86-64 wheel SHA-256 is
`d7113cce263c3cb3a923f71045a13789c5511654197a18e50071bcf5d126fdad`.
Installed module hashes match the source commit, exports resolve from the scratch
venv's site-packages, and all 58 catalog/native-persistence/recovery/refresh tests passed
without source overlays. That venv exposes system packages, so this is not a
clean-room dependency-resolution test. The version number remains 0.2.0.12;
commit and wheel hash, not the version string alone, identify this qualification.
The installed-package qualification does not imply a production cutover.

## Canonical knowledge cutover

All knowledge backends now use SageVDB's `VersionedDocumentCatalog` for canonical
documents. The local, NeuroMem and native/Sage-ANNS indexes remain derived views,
not alternate document authorities. `KnowledgeCatalog` is a schema adapter over
SageVDB/SAGE, not a new transaction or worker implementation. Every mutation
atomically commits document versions/tombstones and a SAGE `knowledge_index`
intent. Competing revisions fail rather than silently overwrite concurrent edits.
Public Wiki and private knowledge retain separate directories and namespaces.

For a legacy directory `/runtime/knowledge`, canonical state is stored in the
sibling `/runtime/.knowledge.knowledge-state.sqlite3`, including its SQLite WAL
and SHM sidecars while open. Keep this database with the dataset during backup,
restore or relocation; the legacy directory alone is no longer a complete backup.
Use a consistent SQLite backup or stopped writers, not a live database-only copy.
Removing rebuildable JSON/index directories does not remove canonical records.
Removing or replacing the canonical database causes reads/writes to fail, never to acknowledge
data saved only in an unlinked database handle.

Legacy JSON import records content-hash receipts in the same transaction as the
documents and indexing intent, then moves originals to `legacy-imported/` for
recovery/audit. Repeated import cannot overwrite newer canonical edits; changed
legacy inputs or conflicting IDs fail explicitly. New writes never create JSON.
Imports obey the same Wiki/private source-ownership checks as new writes; a
misclassified legacy source is retained for review, not silently admitted.
Source-name deduplication creates tombstones without deleting archived originals.
Reads refresh canonical revisions and invalidate obsolete search caches; returned
records are detached. No-op upserts create neither new revisions nor tasks.

Index intents are consumed by a bounded SAGE WorkService (one worker per catalog).
Twin starts both private and Wiki consumers even when model warmup is disabled.
The domain handler reads current canonical data, checks task namespace/revision,
and acknowledges only after the projection reaches at least the required revision.
Failures retain retryable work; an old intent cannot restore an old document body.
After activation, new body/intent transactions use WorkService's admission lock.
Shutdown stops producers/retrieval first, drains index workers, then closes their
state. Drain timeout leaves state available to active work and permits close retry;
new admissions are rejected. Standalone callers may explicitly start indexing and
must close the store. A weak finalizer uses SAGE's nonblocking `retire` contract:
even when the last owner reference is released inside a projector, it does not
join its own unfinished task. SAGE completes the acknowledgement and drains the
dispatcher before releasing storage. Retirement failure retains resources and
does not claim a clean shutdown. This is SAGE scheduling, not an application
polling/executor loop.

Foreground freshness checks, synchronous rebuild flags and startup reconstruction
still remain; avoiding duplicated rebuilds and migrating the foreground wait
boundary without holding the mutation lock are the next cutover steps.
When another worker has already published a sufficiently new native generation,
the caller now refreshes its process-local handle directly from that generation;
it does not invoke embedding or reconstruct the index. Missing, behind or damaged
generations do not count as fresh. Explicit task targeting for different projection
configurations now uses SAGE's pre-claim selector: an active native writer records
the configuration fingerprint with its atomic intent, and only a matching worker
may lease it. Results identify the actual projection. Unmatched tasks remain
pending with no lease/attempt churn. A refresh request can be durably admitted
without editing canonical data; requesting an uncommitted revision is rejected.
Legacy/standalone intents without a fingerprint remain generic recovery work,
not proof of any particular configuration's freshness. All consumers sharing a
journal must be upgraded before targeted producers are deployed; an older worker
without selection must not run concurrently against that journal. Replacing the
remaining foreground build path with unlocked targeted waits is still pending.
Native SageVDB projections now use canonical revisions
and an isolated `canonical-v1/<configuration-sha256>/` generation root. Backend,
embedding model/dimension, retrieval format, source namespace and a hash of the
canonical database location select the configuration fingerprint,
so an older process cannot overwrite a different configuration's index. All such
indexes derive from the same canonical document authority. Old unversioned generations stay
offline and are never an alternate live read path. Open/sync failures invoke
SageVDB's reconstruction from the current catalog; namespace/regression conflicts
remain errors, and embedding failures do not publish a successful generation.
The indexed revision comes from the committed index, not a potentially newer
document-cache revision. Sage-ANNS/NeuroMem projection scheduling and version
contracts still need qualification during worker integration. This
intermediate repository state is not approved for production rollout. Catalog
snapshot/receipt retention and API-level retry identity also remain open.

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

Names and conversation IDs are not authorization keys. Anonymous contact
details cannot double as verified history ownership; contact/booking flows need
an explicit separate policy. Slack uses its existing verified account-link path,
not HTTP bearer identity; its owner adapter is still pending. Workflow events,
cancellation and an authorized resumable result endpoint remain
required before deployment of the full delivery design.

### Owner-scoped canonical memory and HTTP binding

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

HTTP chat overwrites client owner claims with the resolved account, token or guest
subject. Authenticated history requires both account owner and matching email;
unassigned legacy records are preserved but not automatically claimed or exposed
through that route. Explicit reviewed migration remains a rollout gate. Do not
expose journal recovery on this intermediate schema alone.

Guests receive a random 256-bit opaque credential in a Secure, HttpOnly,
SameSite=Lax cookie. Only its SHA-256 lookup key, random subject and absolute
expiry are persisted in SAGE state; raw credentials are never persisted or
included in work payloads. Authentication is read-only and does not extend
expiry. Reopening the store preserves identity; an invalid/expired credential
creates a new identity, without transferring old ownership. No development
default signing secret is used. HTTPS is required for browser cookie continuity.

The existing user-session bootstrap, direct JSON/multipart chat and streaming
chat establish this identity. Streaming does so before response headers and
reuses it inside the worker request; cookies cannot be attached after streaming
begins. Credential-bearing responses are no-store. Manual compression also
uses the trusted scope, not a client UUID as an access capability. Guest issuance
capacity and expired-state retention still need qualification before deployment.

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
profile capture and automatic digest. HTTPS transport tests verify cookie
delivery/continuity and isolation across clients. Event/cancel ownership,
Slack cutover and online acceptance remain pending.

Feedback resolves the trusted HTTP owner and checks the canonical source record
before any analytics or knowledge writeback. Missing, foreign and unassigned
records return the same 404 to scoped callers. The checked record is passed into
knowledge writeback rather than re-read from a stale cache. Canonical lookup
supports commits made by another store instance. Feedback now borrows the same
SAGE state runtime and uses an atomic update, preserving the original creation
time inside the transaction. Reads use detached canonical state, not a local
cache. Legacy JSON is imported with content-hash receipts and archived only after
commit; replay cannot overwrite subsequent edits, and changed legacy inputs fail
explicitly. New feedback never writes JSON. Tests cover rollback, lost commit
acknowledgement, independent SQLite connections, concurrent writers and interrupted
archival. The collection is still a full snapshot: bounded retention/indexed
enumeration remains a scalability gate. Positive-feedback knowledge writeback is
still a separate synchronous effect, not a durable replay-safe intent; feedback
and knowledge are **not** an atomic unit yet. No online migration is implied.

Feedback knowledge candidates now require an explicit per-source URL in the saved
answer; title/token overlap and an unrelated hit do not authorize another source.
All cited hits are considered, not only the first two. Candidates contain the
complete stored search-result title, snippet and URL, never the private question,
answer or owner/exchange identifier. They are explicitly labelled as snippets,
not full-page evidence. A hash of URL/title/snippet identifies each immutable
candidate version; repeat feedback preserves curator edits and review decisions,
and changed source material creates another review candidate. The display title
may be shortened to the UI limit, but the full title remains in evidence content.
Existing legacy candidates are not rewritten or deleted automatically; they need
an audited privacy/migration review. This policy is not a claim of cross-process
atomicity for the existing knowledge JSON store. Canonical SAGE knowledge state,
replay-safe projections and atomic feedback/task admission remain required.

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
