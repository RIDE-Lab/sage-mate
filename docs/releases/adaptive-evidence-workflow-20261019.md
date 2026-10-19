# Adaptive evidence workflow acceptance — 2026-10-19

## Deployed source

Twin application: `e890ba8d2ca8f8746cd8e985d931a2ebf89ba029`.
SAGE SDK: `4ec3ecb0b701df4bdf1f9c9e69ef24faf64c4b7f`.
NeuroMem: `9374c3465022e8d02235a5cf470898d26209570b`.
All source revisions are on their repositories' `main` branches. This receipt
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
Running routed edges have directional CSS streams, with distinct child-call
coloring and a reduced-motion alternative. Completed history does not pretend
to be running. The session export button downloads all displayed question and
answer bodies locally as Markdown, without exporting hidden reasoning or other
sessions. Legacy HTML snapshots retain semantic structure rather than claiming
byte-identical historical Markdown.

## Qualification evidence

| Gate | Evidence and scope |
| --- | --- |
| Exact final application regression | 1,767 passed, 31 skipped, one dependency deprecation warning; 513.35 seconds. No identity-matrix exclusion. Independently collected test IDs match every JUnit case. |
| Dependency provenance | Locked SDK revisions checked; 205 installed SAGE Python files byte-match the exact Git revision. |
| SAGE regression | Exact pinned SDK: 643 passed, one skipped. Application qualification additionally runs against this pinned SDK. |
| Frontend regression | All ten Node test groups pass, including long multi-round Unicode/code/link export, legacy Markdown reconstruction, pending/empty handling, and no network call during export. |
| Isolated real model QA | Exact final candidate guide, roles, absence-inference boundary and research questions all return HTTP 200 with canonical blocks, exact source IDs, complete selected source bodies/URLs, coverage/final verification and no private reader. Guide/roles/boundary/research took 11.064/18.742/20.702/28.778 seconds; these are observations, not controlled speedup claims. All four answers match the isolated process's trace store, with revision/runtime/PID binding. |
| Backend and dependency scope | Public readiness now reads native lifecycle state instead of full admin analytics. NeuroMem ANN stores canonical record references, not duplicate text/profile payloads. Fresh full regression and real QA were run; old frontend-only qualification was not reused for these changes. Models, provider and output budgets unchanged. |
| Isolated browser layout | Light/dark previews contain one map and distinguished input/output; at 722px width the 38-node fixture is 154.35px high with no horizontal overflow. This is visual-fixture evidence, not model evidence. |
| Recovery | Graceful isolated restart preserved ten requests and fifteen task receipts, with answer hashes/attempts unchanged. Browser history restored the existing answer without regeneration. |
| Public browser QA before final layout-only switch | Chrome guide answered in 20.5 seconds with full Wiki links, 35 declared / 26 executed nodes and durable background confirmation. Research answered in 27.6 seconds, 50 declared / 42 executed nodes, with actual supplemental retrieval, experiment planning and final verification. User independently confirmed the complete research answer was visible. |
| Production switch | Drained active work; twelve consistent SQLite backups passed integrity checks. App and Wiki release pointers switched together; readiness ready, Slack readiness verified. Credentials, model/provider/output settings and private/public data paths unchanged. |
| Public deployed bytes | HTML, JavaScript and both CSS assets match the final deployed Git revision byte-for-byte; checked from Mac after switch. |
| Legacy isolation | Test-labelled and unknown-origin archives retain matching manifests/hashes, private permissions, no serving references and no automatic ownership claims. |
| Long-profile recovery | 154 related tests pass, including real ANN long content and durable restart. Two consecutive constructors on consistent private production-memory copies preserve all 85 canonical profile payloads. Original databases are not modified by that qualification. |
| Final public browser | After refresh, Chrome restored the existing research answer without regeneration and enabled Export. Native Save completed an 8,290-byte historical-session Markdown download. A second real public Wiki question completed in 27.4 seconds; the resulting 12,047-byte download contains both questions and answers, tables, full source URLs and the final answer tail. Light/dark screenshots show the deployed export control and native graph. New execution was observed live; its completed graph has 55 declared nodes and 42 observed executions, with durable post-answer confirmation. |
| Motion evidence boundary | Isolated browser computed CSS offsets changed over time for real native started-event fixtures, and completed fixtures have no stream overlay. Public browser showed real in-progress native execution and the final routed map. No public animation frame-rate or rendering-performance claim is made. |

## Release incidents and diagnostic boundaries

The final revision is deployed; public HTTP readiness is ready and Slack's
independent workspace verification is verified. History restoration, final
light/dark capture, a new production question and actual production multi-round
Markdown downloads are now observed, rather than inferred from HTTP success.

An initial deployment attempt exited while the existing systemd stop job was
still within its 90-second allowance: the helper had waited only 60 seconds.
The helper now uses nonblocking stop plus bounded state observation, and records
stop intent before waiting so an exception cannot abandon recovery. No manual
kill or stop-timeout override was used for the production unit.

Restarting the old application then exposed a separate latent defect: full
profiles duplicated in ANN metadata exceeded SageVDB's 10KB field protection.
NeuroMem now projects only the canonical ID into ANN metadata and retains full
text and metadata in Collection storage. The protection was not increased and
no record was truncated, discarded or switched to a fallback backend. The
stopped service was recovered only after exact new regression and real QA,
twelve consistent SQLite integrity backups, and private-copy restart checks.
Existing configuration-file hashes, model/provider/budget settings, model
credentials and data paths were preserved; databases were never rolled back.

Browser tab-binding observations timed out, and some native screenshots retained
older frames despite newer accessibility content. User confirmation plus native
Chrome observations established that the research answer was actually visible.
Some coordinate actions also reported no available windows. A later uninterrupted
unlocked session completed the real native Chrome workflow and download checks.
The earlier tool failures are not proof of an application rendering defect or
of its root cause being fixed.

A100 public HTTP transfer probes also saw intermittent partial-body timeouts;
origin transfers were fast and complete. Mac downloaded and verified all public
assets. One gzip/identity comparison differed, but that single variable-network
sample does not establish compression as the root cause. No cache, TLS, security,
proxy or timeout protections were weakened to obtain acceptance.

The rollback procedure restores release pointers only. It must never overwrite
new user data with a pre-switch database backup.
