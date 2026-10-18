# Native workflow completion audit

## Open research-answer quality gate

A public StateAxis review question requesting sourced facts, unknowns and
controlled experiments returned 503 in 7.94 seconds on `9ff9fa0`. An isolated
copy of the public Wiki reproduced source-plan rejection: the only candidate
was the generic first-month research guide; the model selected no source but
produced suggestions, correctly rejected by the existing contract. Candidate
tracing found this unrelated guide already present before intent filtering.
Query construction/retrieval ranking therefore remains under investigation.
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
| Bounded scheduling and recovery | Native WorkService/AsyncWorkService adapters and existing failure/capacity tests | Explicit matched admission/queue/retention qualification; no unbounded-throughput claim |
| Versioned knowledge updates | `test_knowledge_index_work.py` checks running worker updates, pending-intent recovery, failed projection, revision fences and config isolation; `test_knowledge_projection_recovery.py` checks deleted/latest records and damaged-index recovery; real-embedding isolated canary verified update/delete/restart and six completed native tasks | No production-document mutation needed; unchanged Wiki sync is not used alone as proof |
| Reliable delivery | Production/public HTTP, answer_done plus complete SSE, cross-guest denial, restart replay passed | Real Slack acknowledgement/delivery; public browser visual confirmation remains pending |
| Ownership migration | Final lossless grouped archive, old memory/traces isolated, old shared-store writer stopped; fresh live store has two owned records/four owned profiles and no legacy-import directory | Keep quarantined records excluded; do not auto-claim |
| Preserve quality and evidence | Source-constrained SAGE plan preserves complete sources; real multipart questions, links, citation selection and isolated browser history passed | Public visual check and requested research-inference quality cases |
| Submit changes to main | SAGE main `babd891`, Twin runtime main `56f4281`, cutover documentation main `d8d29d7`; clean source trees verified | Subsequent changes must retain this rule |

No secrets, identities, private source bodies or raw journal payloads are included
in the journal audit. Task completion is observed state, not proof of every crash
boundary. Browser automation timeouts remain separate from website health: API
success does not prove rendering, and a control timeout does not prove a freeze.
