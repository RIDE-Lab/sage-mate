# Adaptive evidence workflow acceptance — 2026-10-19

## Deployed source

Twin application: `5d20654f7b479ee08e2472b2e3cb08503335f243`.
SAGE SDK: `4ec3ecb0b701df4bdf1f9c9e69ef24faf64c4b7f`.
Both source revisions are on their repositories' `main` branches. This receipt
is a subsequent documentation commit, not the running application's revision.

## Implemented contract

SAGE captures an immutable plan from the question and verified capabilities.
Booking, writes, ordinary dialogue, source-constrained answers and research
experiments take actual conditional branches; hiding a frontend node does not
implement authorization or routing. Wiki and private knowledge remain separate
retrieval sources. Guest tests assert that denied private readers never start.

The SAGE evidence subflow selects relevant evidence from a complete authorized
candidate snapshot, checks coverage with exact source-local line witnesses,
conditionally performs one supplemental retrieval round, rechecks coverage,
composes source-backed facts and separately labelled research proposals, and
verifies provenance and canonical answer blocks before publication. It replaces
the previous source-answer path rather than running a second quality pipeline.
Uncovered premises remain explicit unknowns, not negative factual claims.

Research plans have typed shared conditions and replayable event declarations.
Runtime rendering supplies paired sampling, falsification, deterministic-numeric
and fixed-observation-window conditions. These checks do **not** prove the source
is true, that semantic coverage is correct, that a proposed mechanism exists,
or that the proposed experiment is feasible. Experimental settings are proposals,
not descriptions of existing implementations or measured results.

Twin renders one compact native network. Entry/return shapes come from SAGE
metadata. Declared inactive branches remain dim; routed edges are highlighted;
only running nodes animate. Explicit child invocations use a distinct edge type,
without invented return dependencies. Missing historical topology stays partial.
Answer delivery, node completion and durable background confirmation are distinct.

## Qualification evidence

| Gate | Evidence and scope |
| --- | --- |
| Exact final application regression | 1,765 passed, 31 skipped, one dependency deprecation warning; 506.54 seconds. No identity-matrix exclusion. Independently collected test IDs match every JUnit case. |
| Dependency provenance | Locked SDK revisions checked; 205 installed SAGE Python files byte-match the exact Git revision. |
| SAGE regression | Exact pinned SDK: 643 passed, one skipped. Application qualification additionally runs against this pinned SDK. |
| Frontend regression | All nine Node test groups pass. A parallel-lane test rejects the old opaque-ID-dependent ordering and passes rank-ordered placement. |
| Isolated real model QA | Guide, roles, absence-inference boundary and research questions all return HTTP 200 with canonical blocks, exact source IDs, complete selected source bodies/URLs, coverage/final verification and no private reader. Guide/roles/boundary/research took 11.443/8.747/16.147/25.790 seconds in that run; these are observations, not controlled speedup claims. |
| Final layout change scope | Relative to the qualified real-QA application `3f842da9`, only frontend layout code and its Node test changed. No backend, dependency, model, provider or budget changes. |
| Isolated browser layout | Light/dark previews contain one map and distinguished input/output; at 722px width the 38-node fixture is 154.35px high with no horizontal overflow. This is visual-fixture evidence, not model evidence. |
| Recovery | Graceful isolated restart preserved ten requests and fifteen task receipts, with answer hashes/attempts unchanged. Browser history restored the existing answer without regeneration. |
| Public browser QA before final layout-only switch | Chrome guide answered in 20.5 seconds with full Wiki links, 35 declared / 26 executed nodes and durable background confirmation. Research answered in 27.6 seconds, 50 declared / 42 executed nodes, with actual supplemental retrieval, experiment planning and final verification. User independently confirmed the complete research answer was visible. |
| Production switch | Drained active work; twelve consistent SQLite backups passed integrity checks. App and Wiki release pointers switched together; readiness ready, Slack readiness verified. Credentials, model/provider/output settings and private/public data paths unchanged. |
| Public deployed bytes | HTML, JavaScript and both CSS assets match the final deployed Git revision byte-for-byte; checked from Mac after switch. |
| Legacy isolation | Test-labelled and unknown-origin archives retain matching manifests/hashes, private permissions, no serving references and no automatic ownership claims. |

## Remaining acceptance and diagnostics

The final layout revision is deployed and public health is ready. A fresh public
page load rendered the role-specific Wiki-oriented welcome screen after the
switch. Restoring this turn's research conversation and capturing the final
layout after that refresh remain open; do not mark the full goal complete yet.

Browser tab-binding observations timed out, and some native screenshots retained
older frames despite newer accessibility content. User confirmation plus native
Chrome observations established that the research answer was actually visible.
The latest coordinate action subsequently reported no available windows. These
tool observations are not proof of an application rendering defect or its fix.

A100 public HTTP transfer probes also saw intermittent partial-body timeouts;
origin transfers were fast and complete. Mac downloaded and verified all public
assets. One gzip/identity comparison differed, but that single variable-network
sample does not establish compression as the root cause. No cache, TLS, security,
proxy or timeout protections were weakened to obtain acceptance.

The rollback procedure restores release pointers only. It must never overwrite
new user data with a pre-switch database backup.
