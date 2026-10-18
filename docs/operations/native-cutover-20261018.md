# SAGE-native Twin cutover — 2026-10-18

## Released revisions

- Twin runtime: `56f4281220d99208e6b606ed2bc7e3f63a09e0d7` on main.
- SAGE: `babd891ece839a44fc69a0b29ce793af053a718c` on main.
- NeuroMem: `aef26791ecf8a30bc641ee586cb404bd637bb329`.
- SageVDB: `311d65e972fb3cb4e5a2904a4c499a51dafae38d`.
- Dev Hub gitlink: `840ee59021be2152dc787cb7569407e534df06e1`.

A clean checkout and independent `uv sync --frozen` environment were used.
Both the application and Wiki sync task use that checkout/interpreter; retrieval
top-k is 8. No model service was restarted or GPU allocation changed.

## Evidence

- Frozen release: 1,528 tests passed, 31 existing skips, 269.14 seconds.
  The existing identity-eval exclusion is unchanged, not a quality pass.
- Focused integration: 153 passed. Installed SAGE protocols: 45 passed.
  Five Node regression groups passed. Ruff and lock checks passed.
- Real Wiki overview/guide on the isolated final release: 4.03/3.42 seconds.
  Complete source content, all requested routes/roles/steps/criteria and URLs
  were checked, not merely HTTP status.
- Browser before cutover: full Markdown, default-collapsed source basis,
  clickable complete source link, and restored history without re-asking.
- Production local HTTP: 6.65 seconds, complete source answer, SSE answer_done
  followed by complete. A different guest could not access the event cursor.
- Production restart: startup from 16:18:10 to 16:18:15 CST. The same guest and
  request replayed the identical answer and exchange ID in 0.60 seconds.
- Public HTTPS: 8.21 seconds; complete answer, normal SSE termination and
  cross-guest isolation checks passed. Public health returned 200.
- Wiki sync exited 0 using the same release; timer restored active.

The first production index initialization took 340 seconds (16:11:15–16:16:55).
The subsequent five-second startup is restart-recovery evidence, not a claim
that cold initialization is fast or a controlled inference-speed benchmark.

## Legacy data boundary

After stopping writers, a WAL-consistent final archive was hash-verified. Test
labels do not prove AI authorship. The two preserved groups contain:

| Collection | Test-labelled | Unknown-origin |
| --- | ---: | ---: |
| Conversation entries, including attachments | 189 | 329 |
| Profiles | 76 | 58 |

The original database, full old memory directory and traces were moved outside
all serving/retrieval roots into private quarantine. Nothing was deleted or
automatically assigned to an owner. Identity accounts, Slack bindings and
reviewed knowledge sources were retained. A stale Twin process on port 55602
also held the production database and was stopped; the independent pooling
canary on 55603 was not changed.

After production tests, the new store contained two canonical records and four
profiles, all owner-bound; zero unowned entries and no legacy-import directory.
SQLite integrity passed. Rollback must never remount quarantined data or discard
new owned records. Archive paths and private contents are not public artifacts.

## Remaining visual check

Post-cutover public-page automation timed out in both accessibility and DOM
control, while public HTTPS/API/SSE checks succeeded. This does not establish
that the website froze. The earlier isolated browser checks remain valid, but
do not substitute for public-page visual confirmation. User visual confirmation
has been requested; do not report that remaining check as passed.

The failed semantic-auditor/NLI controls remain documented in
`wiki-answer-acceptance.md`; the source-constrained plan replaces that serving
gate rather than lowering its acceptance threshold.
