# Native workflow completion audit

## Authoritative current status

Production is c659c2f / SAGE bcfeb104 (source-block receipt below). Earlier dated
and candidate sections are historical; their present-tense statements do not
override this status. In particular citation link bases, retired research
shortcut removal, native inner-source parallelism, and transactional HTTP/Slack
admission limits of eight are deployed. Browser visual acceptance is complete
for the recorded public Wiki sample. Real Slack delivery is still unverified.

### Real Slack command acceptance: configuration repaired, identity gate remains

The user authorized the operator to send the exact Wiki acceptance question from
the native Slack client to Twin's DM. At 21:10 (Asia/Shanghai), Slack displayed
`app did not respond`; the production access log confirmed POST commands/twin 503.
The running process had signing secret and bot token but no workspace ID, so the
native Slack worker was never started. Secret-free auth.test checks confirmed
that the configured bot and workspace matched the visible Slack app.

Added a workspace-only systemd drop-in, then restarted the unchanged c659c2f
release. Before restart all three authenticated booking-status queries returned
zero, and durable work contained 88 done / one historical failed / no active tasks.
The unfiltered booking endpoint unexpectedly returned 500 because its batch
source item was None (end-of-stream); the candidate now wraps the optional filter
in an explicit query object and tests the real FlowNet path.

At 21:15 the same real slash command received Twin's account-binding instructions.
This proves command ingress and immediate Slack acknowledgement, **not** answer
generation or final delivery. Do not bypass ownership/link checks. User confirmation
for binding their verified Twin account is pending. The candidate also rejects
partial Slack configuration at startup rather than presenting an apparently healthy
but permanently disabled integration. 52 Slack/booking-pipeline tests passed
in 7.55s; Ruff passed. These code changes are not yet deployed.

### Explicit non-destructive retention policy

Canonical conversations, completed work, event replay and isolated legacy data
are retained without an automatic TTL. Neither completion nor age authorizes
deletion or reassignment. Quarantine remains outside serving paths. Request
capacity limits protect active admission, not disk capacity or total history.
No cleanup job, silent truncation, vacuum or archive removal is introduced.

SAGE's separate content-free SQLite observation helper is now under qualification;
it has been run read-only against production without replacing its runtime. That
snapshot reported 129 entries, 3,780,677 serialized payload bytes, 4,386,816 page
bytes, 475,136 reusable page bytes, 4,643,272 observed WAL bytes, and
2,108,203,995,136 filesystem-free bytes. These are distinct point-in-time metrics,
not an SLA, growth forecast, backup verification or a hard storage bound.

Continue to preserve data if space pressure emerges: report the pressure and
provision capacity or qualify an explicitly authorized lossless archive/restore
procedure. Do not delete canonical answers as a side effect of backpressure.
Automatic storage alerts and a measured long-term growth budget remain open.

### Remaining goal evidence

Booking candidate now replaces `_bookings` with a single SAGE SQLite destination
at `booking_state_path` (default runtime `data/bookings/state.sqlite3`). Its pure
transition checks conflicts and commits records plus OperationReceipts atomically.
Confirm/reject transitions also serialize against current state. The chat write
stage passes the server-captured owner/request digest; replay returns the original
receipt without undoing a later confirmation. Availability remains an explicitly
captured file-policy snapshot; advisory suggestions are not reservations.

42 booking/pipeline/admin-auth tests passed (14.11s). Expanded durability suite:
nine passed (1.98s), including real child-process exit before commit and after commit
before acknowledgement, independent connections racing, and confirm/reject races.
The isolated two-invariant qualifier now exits 0 (two knowledge attempts, one
document; accepted booking survives close/reopen). **This does not establish
end-to-end booking recovery:** conversational booking-plan capture, notification
outbox and external acknowledgement boundaries remain open. No production code
switch has been made. Before cutover, recheck live bookings and losslessly migrate
any records created since the earlier zero-record observation.

Knowledge-write candidate now pins SAGE a848792 and uses native
`OperationReceipts`: the document, immutable result, and knowledge-index intent
commit in one destination transaction. The graph receives the server-captured
DeliveryIntent; its verified-owner/request digest scopes the write identity.
The current admin knowledge parser is deterministic, not a model-generated plan.
Changed payload fails closed. Replaying after a later deletion returns the original
receipt without resurrecting the document. Unkeyed administrative writes remain
distinct operations; source names are not idempotency keys.

Isolated results: 33 catalog/index/receipt tests passed (29.25s); after adding
independent-connection race and real authorized-stage cases, 55 receipt/retrieval/
admin-auth tests passed (19.63s). The real HttpWorkflow fault qualifier now reports
two attempts and **one** destination document. Its overall exit remains **1**:
booking reconstruction still loses the accepted booking. This is partial repair,
not a release gate pass. Production remains unchanged. Knowledge receipts do not
provide cross-database ingress cancellation fencing or external email exactly-once.

**New verified correctness gap: pre-answer side effects.** Isolated qualification
against installed c659c2f used real HttpWorkflow retry and LocalKnowledgeStore,
injecting a failure after the first document commit and before canonical answer
commit. Two producer attempts created two destination documents for one request.
Separately a valid accepted booking disappeared when MeetingService was rebuilt
with the same settings: bookings are kept in `_bookings`, not durable storage.
These failures contradict broad crash-recovery completion despite the passing
canonical-answer replay tests. No production knowledge/booking/email was changed.

Reproduce with `tools/qualify_precommit_side_effects.py` under the installed Python
and source PYTHONPATH. It creates a temporary isolated runtime, outputs only
synthetic aggregate results, and intentionally exits 1 while invariants fail.
It is a fault qualifier, not a passing regression or a real-model quality result.

Repair requirements: propagate owner-scoped stable operation identity through
the single graph; atomically commit destination state and immutable operation
receipt using SAGE state transactions; replay the saved result before re-running
the effect; reject same identity with changed input; persist booking transitions
and protect slot conflicts across workers; keep email's external at-least-once
boundary explicit. Do not replace this with source-name deduplication, disabling
retries, or an in-memory receipt cache. Qualify write-before-answer crash/restart,
lost acknowledgement, concurrent claims, changed payload and authorization.

Also removed the unused `_run_stage_chain` direct executor after repository-wide
caller inspection. The one-shot SAGE pipelines still used by distinct admin
operations are not alternate chat paths and were not deleted by name alone.

- Actual Slack ingress, completed native task chain, external acknowledgement and
  user-visible answer on the deployed release; tests alone do not establish it.
- Storage growth/operational alert qualification and external side-effect limits.
- Final requirement-by-requirement source audit; historical matrices below must
  not be used as current completion claims. External delivery remains at-least-once.


## Explicit citation link bases (candidate, not deployed)

New citation cards and historical citation hydration now pass their source URL
to the shared Markdown renderer. Relative destinations resolve against that
explicit URL, never against Twin. Unsafe/missing bases leave original Markdown
readable without an invented clickable target. HTTP/HTTPS/mail destinations are
validated; executable protocols, control characters and URL credentials are denied.
The underlying stored source and answer text are unchanged. Tests cover parent,
root, fragment, bare-file and query links, entity escaping, historical base
propagation and unsafe bases. Multi-source answer bodies still need typed
per-source rendering provenance; a single global base is not a valid fix for
that remaining requirement. No production change for this candidate.

## Retired research shortcut removal (candidate, not deployed)

Removed the always-false curated-direction compatibility hook and every caller:
canned review text, classifier bypass, source clearing, synthetic methodology
basis card, and disabled selection terms in answer/skill routing. No replacement
shortcut was added. The research-review contract and source-plan path remain.
Regression now asserts the obsolete hook is absent, not merely disabled.
Research review, grounding, source selection and post-answer tests: 113 passed
in 21.80 seconds against installed production dependencies in an isolated tree.
Production remains d1ace34; broader frozen-release/deployment acceptance is separate.

## Deployed source-selection release: d1ace34 (2026-10-18)

Production app and Wiki sync now use frozen `d1ace34`, SAGE `b46054f`.
Final regression: **1596 passed, 31 existing skips**, 358.28 seconds, with the
existing identity-eval exclusion unchanged. Five frontend groups, lock validation
and changed HTTP/source-runtime lint checks passed. No model/GPU/credential change.

The initially failed `92a50ed` cutover was rolled back, fixed on main, qualified
in isolation, then replaced by this release. Production old-to-new replay now
returns the same canonical exchange/answer in 0.08 seconds, without regeneration.
Rollback configuration and prior releases remain recoverable; no database was
restored or history deleted. Wiki timer is active; preview service was stopped.

Authenticated Chrome acceptance repeated the original complete Chinese question:
public Wiki research directions plus ECPA/StateAxis roles and full links. It
completed in **11.1 seconds**, selected only the public Wiki overview (one source),
and no longer included the private-KB-labelled homepage record. AX and a fresh
screenshot verified readable Markdown, links and expandable support; process and
support were initially collapsed. This is one measured case, not a latency SLA.
Two fresh Mac-to-public-HTTPS research requests also passed independent full-source
catalog checks in 17.57 and 14.05 seconds, retaining labelled proposals and links.

Post-switch ownership check: 22 records / 35 profiles, zero unowned records or
profiles, no legacy import directory, SQLite integrity OK. This checks current
ownership, not a new legacy classification or automatic assignment. Broader goal
items remain open: real Slack delivery, general source-relative links, and an
explicit non-destructive storage-retention policy. Research proposals remain
non-authoritative and still require expert review.

## Source-selection release qualification (2026-10-18)

Update: `92a50ed` passed 1595 tests (31 skips), but its first production
cutover failed cross-version replay with HTTP 409. The application was rolled
back to `d3fa3fb` after active work drained; no data snapshot was restored.
The added optional source field changed raw ingress equality (old missing field
versus new null). `d1ace34` preserves the original admitted payload and only
normalizes that documented default for equality; explicit lists, other fields,
authority checks and journal conflict handling remain enforced. Thirteen HTTP
tests passed, including restart with both persisted request shapes and explicit
source-change rejection. An isolated real old-to-new HTTP restart returned the
identical canonical exchange and answer in 0.44 seconds. Final frozen regression
and renewed production/browser acceptance are still pending at this checkpoint.

Candidate `92a50ed` supersedes the adapter-only implementation notes below.
The frozen `b669fa2` run failed: 1518 passed, 31 skipped, four failures and
68 setup errors. Producer tests require the parent's Git pin, which an archive
does not contain. Passing the verified committed pin
`840ee59021be2152dc787cb7569407e534df06e1` through the existing test override
restored all 117 instance-control tests without skipping their checks.
Three classifier mocks lacked recent_session_context; the escalation test
inadvertently used an unconfigured external model. Corrected isolated fixtures
passed all 44 tests in those two modules. Frozen `f3516fe` subsequently passed
1590 tests with 31 existing skips in 353.41 seconds.

Real research QA then exposed a separate business-policy defect: the intent
normalizer treated any occurrence of experiment/实验 as coursework, disabling
research proposals. Removing those ambiguous triggers while retaining explicit
course signals passed 109 tests, including five new bilingual controls. The
real classifier now retains research plus Wiki-only selection; private-only,
combined sources, negation and Wiki-as-topic checks also passed.

The exact `92a50ed` isolated preview returned two research answers in 12.43 and
12.52 seconds. Full selected source bodies were independently checked against
the public catalog, links retained, and proposals separately labelled. These
proposals remain unverified suggestions, not proof of scientific quality or
experimental validity. Public delivery took 11.04 seconds; SSE completion and
cross-guest stream denial passed. Five frontend test groups passed. The final
full suite and authenticated browser gate are still pending; production remains
`d3fa3fb` at this checkpoint.

## Browser acceptance findings requiring follow-up (2026-10-18)

Chrome extension tab binding timed out after 15s, but native Chrome accessibility
and screenshots worked after raising the existing Twin window. Native typeText
dropped Chinese characters; that first malformed request is not quality evidence.
The second request was set with AX setValue, and its complete Chinese text was
verified before sending: ask public Wiki for IntelliStream directions and roles
of ECPA/StateAxis, preserving source links.

The logged-in browser returned in 27.4s. The answer was visibly readable, with
rendered headings/table/list, and process/support sections initially collapsed.
Expanding support worked. However, it selected both the correct public Wiki
overview and a private-KB-labelled personal-homepage record without a source URL.
This does not prove an authorization leak (the caller was logged in), but fails
the explicit public-Wiki source preference and reveals a missing provenance link.
Guest-only API checks did not cover this member-context case.

Source inspection confirms `KnowledgeSources.search` gates private access by
role only; there is no independent requested-source constraint except exact named
Wiki titles. Its Wiki/private search calls are also sequential. Existing native
parallel tests cover memory versus knowledge, not those two inner source lanes.
These requirements remain incomplete; adding a keyword-only answer filter would
not establish a typed retrieval constraint or native parallel execution contract.
Additionally, the malformed query exposed relative README links resolving under
the Twin host; qualify source-relative URL resolution separately.

### Source-selection contract: adapter qualification, not deployed

`KnowledgeSources` now accepts immutable validated `SourceSelection` on search,
public enumeration and full-record reads. Requested origins intersect existing
ACLs; requesting private never grants guest access. Empty selection means none,
unknown origins fail, and even a manager/super-admin cannot bypass Wiki-only
selection. Poison-store tests prove the unrequested private store is never called,
including hydration of previously retrieved private IDs. Twenty source tests
passed in 1.74s in an isolated candidate package using production dependencies.

This is the adapter contract only. Intent/request propagation, supplemental
retrieval and identity-floor consistency, native Wiki/private parallel execution,
full regression and authenticated browser revalidation are still required. No
production change or claim that the observed source-scope bug is fixed yet.

Typed propagation follow-up: ChatRequest and InteractionIntent now preserve
nullable origin arrays (empty differs from unspecified); classifier coercion and
JSON repair retain them. Intent can only narrow an explicit request selection.
The primary knowledge branch, method/comparison supplements and full-source
hydration receive the constraint; the public identity floor is suppressed when
Wiki is excluded. Ninety-nine classifier/evidence/branch tests passed in 8.59s.
The first two isolated runs failed because the copied tree lacked fixture/policy
assets; after copying tests, deps and release seeds, the same tests passed.

Production remains unchanged. Fast-intent bypass/fallback and other shortcut
retrieval entrypoints still require audit before claiming every natural-language
restriction is honored. Source-lane parallelism and authenticated real-model
browser acceptance are also outstanding; this is not a release qualification.

Fast-topic bypass follow-up: a retrieval intent without explicit source selection
now goes through the semantic classifier even if the client supports fast bypass.
Classifier failure raises `knowledge_source_selection_unresolved` before retrieval,
rather than using a topic-only fallback that loses the restriction. Explicitly
scoped requests and self-contained fast intents retain bypass. Three added tests
cover semantic selection, failure, and safe explicit bypass; the two old tests
that assumed unscoped fallback now explicitly select Wiki. The original run
failed those two assertions as expected; updated regression passed 78 in 48.29s.
Production is unchanged. Repository search also found `try_fast_answer` referenced
only by legacy tests, not production callers; remove obsolete alternate entrypoints
and their misleading comments as part of the single-workflow cleanup.

### Native inner-source parallelism candidate

Wiki and private source searches now run as two business operators on a persistent
SAGE flow, replacing the serial calls in KnowledgeSources. SAGE owns execution,
request accounting and shutdown; Twin does not introduce an execution thread pool.
Permission and requested-origin masks are computed before dispatch, and disabled
operators never touch their store. Rank interleaving remains deterministic despite
completion order. The graph inherits parent cancellation and the current request's
remaining time (or the configured chat timeout when no request deadline exists).
Service shutdown closes the source graph before closing the backing catalogs.

Barrier-based concurrent tests verify both source lanes overlap, distinct caller
results remain isolated, the endpoint persists, a failed branch cannot publish
partial success, and the endpoint recovers. A nested test runs this graph inside
the existing memory/knowledge flow and proves completion without deadlock.
The first attempt failed the SAGE DSL's required init_stream parameter name;
after correction 53 focused tests passed in 11.36s, followed by 23 source tests
including nesting in 2.38s. Broader business regression and release qualification
remain separate gates. No production cutover has occurred.

Final source plus agentic-business regression passed 79 tests in 47.04s after
deadline/lifecycle wiring. This does not substitute for the full frozen-release
suite or real authenticated browser acceptance.

### Obsolete answer-entry cleanup candidate

Removed `try_fast_answer` and `_build_lightweight_fact_response` after repository
search found no production caller. These were an obsolete response/delivery route,
not a second path to keep for compatibility. Structured policy matching tests now
call the pure policy-response helper directly; they do not claim production
execution/delivery. Native fact-question tests assert the obsolete methods are
absent and still require the workflow, durable exchange and memory commit.

The first broader regression exposed stale test doubles: duck-typed requests
lacked knowledge_sources, the fake intent classifier omitted the existing
recent_session_context argument, and the shutdown fixture omitted the new source
graph. Updated those doubles and asserted source-graph shutdown before catalogs;
the runtime's fail-closed source-selection rule is unchanged. The failed run
(24 failed, 100 passed, 5 skipped) is retained, pending the corrected rerun.

Corrected regression passed 124 tests with 5 existing skips in 31.38s. The
Starlette/httpx warning remains. No production deployment occurred for this cleanup.

## Current release: durable admission and tokenizer qualification (2026-10-18)

Production now runs Twin `d3fa3fb`, SAGE `b46054f`, and control-plane `a5928e8`.
The receipt in [work-capacity-acceptance.md](work-capacity-acceptance.md)
supersedes the historical deployment and unimplemented-capacity statements below.
HTTP and Slack ingress each have a persisted active-request limit of 8. This is
not a bound on every background namespace, storage bytes or retained history.
No history retention/deletion policy was introduced. Real Slack delivery and
browser visual confirmation remain separate, open acceptance gates.

### Installed-runtime retrieval deadline boundary

Five retrieval-runtime tests passed in 1.59s against production d3fa3fb source and
its frozen dependencies, in an isolated process. The added test holds an old
owner's knowledge reader past its deadline, verifies another owner's two branches
still execute on the same endpoint, releases the old reader, and requires an
actual terminal failure plus empty request buffers and clean subsequent outputs.
An initial test incorrectly caught the observer Future timeout; the corrected
test explicitly checks the future is terminal. This proves late-output isolation
for the exercised boundary, not forced interruption: SAGE deadlines are cooperative,
and uncooperative blocking I/O still needs its own bounded transport timeout.
No production process, document or runtime code changed for this qualification.

### Retrieval transport fault qualification

`test_retrieval_transport_timeout.py` passed two real loopback-socket tests in
3.40s using the installed d3fa3fb source/dependencies. Embedding and reranking
each used the configured one-second test timeout (all four httpx phases checked).
A server accepted the first request but held its response: the actual client
raised ReadTimeout, then a second call on the same client succeeded while the
old server handler remained blocked. Exactly two server requests were observed;
there was no hidden retry/fallback. Synthetic inputs only; no live model service,
production settings or documents were changed.

The production configuration schema defaults both timeouts to 60 seconds with
a validated 1–300 second range. That is a per-network-phase timeout, not an
absolute end-to-end deadline. These tests do not qualify slow trickling responses,
DNS resolution bounds, or aggregate latency across multiple model calls.

## Previous status: source-plan production acceptance (2026-10-18)

Production code is Twin `50306a7` with SAGE `f3ecd85`. The full receipt is
[`research-source-plan-acceptance-20261018.md`](../research-source-plan-acceptance-20261018.md).
Its production receipt supersedes the deployment and research-quality status
in the historical sections below. Clean regression passed 1557 tests with 31
existing skips; the existing identity-evaluation exclusion was unchanged.
Public research probes passed in 10.03s and 7.98s, independently comparing
complete selected source bodies with the read-only canonical Wiki catalog.
Advice is separately marked as non-source inference. This qualifies those
observed cases, not universal research reasoning correctness.

Post-cutover replay preserved the answer and exchange ID. A fresh Mac-origin
public request passed in 8.08s with terminal SSE and cross-guest denial. An
A100-origin TLS connection reset is retained as a failed probe, not a pass.
Real Slack delivery and public visual confirmation remain separate open gates.

### Scheduling audit: still open

Inspection of `WorkJournal`, `WorkService`, `AsyncWorkService` and the dispatcher
does not establish bounded durable admission. Worker concurrency is bounded,
but it is not a bound on pending persisted requests. An observer wait timeout
also is not proof of a capacity rejection. Do not describe this as qualified
queue backpressure.

Any capacity contract must be SAGE-owned and transactional with request admission:
concurrent processes must not over-admit; identical replay must remain available
at capacity; rejected admission must not partially publish destination state;
terminal completion must release capacity while retaining canonical results;
restart must reconstruct the same accounting. Existing admitted finalizers and
recovery must not be blocked by new-request limits. Prepared multi-slot commits
must obey the same rule as direct journal commits.

No retention/deletion policy has been deployed. Completed answers, replay records
and quarantined legacy data are not disposable queue entries. Capacity work must
not silently introduce a TTL or delete them. These are remaining requirements,
not claims about implemented behavior.

## Historical research-answer failure (resolved by the receipt above)

A public StateAxis review question requesting sourced facts, unknowns and
controlled experiments returned 503 in 7.94 seconds on `9ff9fa0`. An isolated
copy of the public Wiki reproduced source-plan rejection: the only candidate
was the generic first-month research guide; the model selected no source but
produced suggestions, correctly rejected by the existing contract. Candidate
tracing found this unrelated guide already present before intent filtering.
Query construction/retrieval ranking therefore remains under investigation.
Follow-up isolated tracing confirmed the original query was preserved. The
query profile classified every occurrence of experiment as a teaching document
request, even for a research review. The candidate defers that ambiguous rule
until research intent is known, while preserving explicit course context.
Six new Chinese/English intent checks plus existing knowledge/source tests passed
(66 passed, 5 existing skips). Correct system-overview evidence then returned,
but seven unrelated prompt-engineering chunks also survived and the real request
hit its 80-second observer deadline. Research QA remains unqualified; do not
deploy this candidate merely because the narrower retrieval tests pass.
A prompt-only clarification did not resolve it and was not deployed. Ordinary
Wiki QA acceptance below does not qualify research-review quality.

## Follow-up deployment: 9ff9fa0 (2026-10-18)

The immutable release now runs `9ff9fa0` with installed SAGE `790007b`, independently
installed with the frozen lock on Python 3.11. Clean-checkout regression passed:
1530 tests, 31 existing skips, 332.10 seconds; the existing identity-evaluation
exclusion is unchanged. Five frontend test groups and focused Ruff checks passed.
An earlier partial scratch checkout failed repository-contract tests because root
assets were missing and was not accepted as release evidence.

Both app and Wiki sync were switched to the new release without changing data
directories or model services. Local real QA passed in 5.60 seconds; canonical
replay after a real restart took 0.09 seconds with identical answer/exchange ID.
Public HTTPS real QA passed in 11.36 seconds, including complete source body,
terminal SSE and cross-guest denial. The first public probe returned 429; its body
was not retained, so its precise cause is not asserted. The acceptance client now
supports explicit application queue responses using the same cookie/request ID.
Wiki sync exited successfully and its timer was restored. These checks are not
a throughput benchmark or proof of public visual rendering/actual Slack delivery.

Rollback configuration is retained under the private operator directory
`/home/shuhao/source-audit-tests.ThER15/release-switch.P1uNEG`; prior release
`56f4281` remains available. Rollback must preserve new owned data and must not
remount quarantined legacy memory.

### Pre-deployment investigation record (superseded status)

Follow-up finding: the new concurrent-peer regression reproduced head-of-line
blocking in the installed release. A blocked KnowledgeReader Actor held its
instance lock while the healthy peer waited in `actors/invoker.py:148`.
The candidate replaces both state-free readers with SAGE native `stateless`
declarations, preserving request-local ports and the same bounded branch
scheduler. The same four retrieval tests then passed in 0.91 seconds. This is
isolated synthetic fault evidence, not a production latency benchmark.
Production still runs `56f4281` until expanded regression and cutover acceptance.
An expanded 24-test run initially had one HTTP observer-cancellation timeout;
that case passed alone in 1.81 seconds. Keep this timing failure visible and
investigate reproducibility before claiming the expanded suite is reliable.
The same complete group subsequently passed: 24 tests in 12.46 seconds; project
Ruff checks passed. This repeat does not erase the earlier cancellation failure.

The cancellation race is now reproduced deterministically in SAGE: cancellation
while a thread polls pending work was replaced by WorkWaitTimeoutError, which
the outer observer loop caught and retried. SAGE `790007b` converts only this
expected pending result before the cancellation-aware offload boundary; genuine
storage errors still surface. The new regression fails against the installed
old SAGE and passes with the fix. 21 SAGE async/cancellation tests and all 24
Twin integration checks passed (13.58 seconds for Twin). Twin pins that revision;
`uv lock --check` passed. Immutable-release/full-regression and deployment gates
remain open; the running production release has not yet been replaced.

This is a requirement audit, not a declaration of overall completion. Runtime
revision: `56f4281`; production evidence date: 2026-10-18. The detailed cutover
receipt is `native-cutover-20261018.md`.

Follow-up on the final installed release: 53 focused retrieval, knowledge-index,
projection recovery, post-answer delivery and Slack workflow tests passed in
39.74 seconds. These tests reconstruct stores and inject acknowledgement loss;
they are not represented as actual Slack delivery or production process kills.

An isolated final-release canary additionally used the actual production embedding
configuration on synthetic documents only. It verified add/update visibility,
absence of the old body after update, restart with `loaded_current`, deletion
absence, and six acknowledged index tasks. No production knowledge was modified.
The first harness attempt incorrectly expected raw private search hits to have
the later hydrated `source_content` field; the corrected harness checked the
small result excerpt and exact canonical document body separately.

The isolated final-release process-crash test passed in 63.19 seconds. A spawned
worker admitted the canonical answer and acquired the post-answer lease, then
was killed by its parent. A new runtime opened the same store and recovered
after the unchanged default 60-second lease expired (attempt 1 to attempt 2).
The canonical record, including full attachment content, remained identical;
answer_done and terminal complete each appeared once, and repeated submission
left the event stream unchanged. No production process was terminated. This
qualifies the pre-side-effect leased-worker boundary, not external exactly-once
effects or every possible crash point. The reusable test is
`tests/test_post_answer_process_recovery.py`.

| Requirement | Source and qualifying evidence | Remaining gate |
| --- | --- | --- |
| One SAGE execution contract | `chat_runtime.py`, `retrieval_runtime.py`, HTTP/Slack native work adapters; frozen release regression | Confirm actual Slack ingress/delivery on this release |
| Independent parallel authorized retrieval | `test_retrieval_runtime.py` checks persistent graph concurrency, request scopes, failed-branch recovery and cancellation; real Wiki/embedding/reranker requests passed | Controlled multi-source latency/fault check against the final installed environment |
| Atomic turn commit and replay | `test_post_answer_delivery.py` checks transaction rollback, owned result/event commit, recovered worker completion; production same-request replay retained answer and exchange ID after restart | None for the observed HTTP replay; do not infer external exactly-once delivery |
| Persistent background execution | Production journal audit plus isolated real worker-kill/recovery with the default lease, unchanged canonical record and unique delivery events | External side-effect crash boundaries must not be inferred from this test |
| Bounded scheduling and recovery | Native worker concurrency, failure and recovery tests; no durable admission bound established | Transactional cross-process admission and restart qualification; explicit non-destructive retention policy |
| Versioned knowledge updates | `test_knowledge_index_work.py` checks running worker updates, pending-intent recovery, failed projection, revision fences and config isolation; `test_knowledge_projection_recovery.py` checks deleted/latest records and damaged-index recovery; real-embedding isolated canary verified update/delete/restart and six completed native tasks | No production-document mutation needed; unchanged Wiki sync is not used alone as proof |
| Reliable delivery | Production/public HTTP, answer_done plus complete SSE, cross-guest denial, restart replay passed | Real Slack acknowledgement/delivery; public browser visual confirmation remains pending |
| Ownership migration | Final lossless grouped archive, old memory/traces isolated, old shared-store writer stopped; latest receipt snapshot has 8 owned records/14 profiles, zero unowned, integrity OK and no legacy-import directory | Counts are snapshots; keep quarantined records excluded and never auto-claim |
| Preserve quality and evidence | Source-constrained SAGE plan preserves complete sources; public research samples and multipart/link checks passed per current receipt | Public visual check; sampled proposals still require human scientific review |
| Submit changes to main | Deployed Twin `50306a7`, SAGE `f3ecd85`, production receipt `eaf42a0` verified on main | Subsequent changes must retain this rule |

No secrets, identities, private source bodies or raw journal payloads are included
in the journal audit. Task completion is observed state, not proof of every crash
boundary. Browser automation timeouts remain separate from website health: API
success does not prove rendering, and a control timeout does not prove a freeze.
# Source-block deployment receipt — c659c2f

Production now runs `/home/shuhao/sage-mate-releases/twin-native-c659c2f/source`,
PID 2295106, with its own frozen venv and SAGE bcfeb104. Rollback configuration
is retained in `switch-c659c2f` and the previous d1ace34 release remains intact.
Only the Twin app and Wiki release pointers changed; inference engines did not.

- Frozen Python regression: 1598 passed, 31 existing skips, 348.07 seconds.
  The existing `test_identity_eval.py` exclusion is unchanged. Five Node groups,
  Ruff on changed Python files, and offline uv lock check passed.
- Previous dd5b38c regression: 1597 passed, 31 skipped, one obsolete test calling
  the removed curated helper failed. c659c2f tests actual research retrieval and
  absence of that shortcut; it does not restore the retired production path.
- Frozen preview real research questions passed full-source comparison against
  an independent public catalog and exact answer-block/URL binding.
  Both questions also passed after cutover over Mac public HTTPS; first response
  completed in 14.68 seconds. Advice is explicitly non-source inference, not a
  claim that the proposed experiments or benefits have already been realized.
- Mac public HTTPS old-to-new replay: same exchange and answer, 2.51 seconds.
  Fresh source-bound answer: 11.92 seconds, HTTP/SSE blocks equal, stream completed,
  cross-guest access denied, same-request replay 2.51 seconds without regeneration.
- Authenticated Chrome: exact Chinese Wiki directions/ECPA/StateAxis question,
  10.8 seconds, one public Wiki source, full source text and links. Accessibility
  tree and actual screenshot show readable Markdown; Support is initially folded.
- Read-only live ownership audit: 27 records, 41 profiles, two source-bound records
  with exact answer binding, no unowned records/profiles, no legacy import folder,
  SQLite integrity OK. These are point-in-time counts, not retention guarantees.
- Preview stopped (MainPID 0); production and Wiki timer active. No legacy data
  deletion or reassignment. Broad workflow completion still includes separate
  real Slack acceptance and retention-policy work.

Network limitation: A100-to-public HTTPS acceptance failed during TLS handshake
before application admission; its health probe also timed out after 10 seconds.
Mac public HTTPS and remote loopback acceptance passed. The failed A100 probe is
not counted as an application acceptance success or declared repaired.

## Source-block integration details

SAGE bcfeb104 owns canonical, lossless source blocks. Twin transports those blocks,
validates their exact concatenation against the answer, commits them with memory
and delivery, and includes them in server transcripts. Each source block renders
relative URLs against its own provenance; inference blocks never inherit a base.
Legacy records without provenance remain readable without invented attribution.

Focused checks: 71 Python tests passed (source gate, memory reopen, HTTP workflow,
post-answer delivery and delivery gate), five Node test groups passed. New source
record/response parity check still requires frozen full regression. These are
candidate checks, not production acceptance. Initial expanded Python run used
incorrect unprefixed settings and failed eight tests attempting an unavailable
offline embedding model; corrected DIGITAL_TWIN settings passed. Ruff passed
after removing two stale imports left by the retired direction shortcut.

