# Twin / WS shared visual language

Twin borrows Workstation's clear state differentiation; Workstation borrows
Twin's quiet reading space. Both keep their own task-specific hierarchy.

Twin changes: lighter starter borders, teal hover/focus/link tokens readable in
both themes, quieter rounded source/detail surfaces, slightly more breathing room
in answer text, and a versioned stylesheet URL. Execution colors and failure
states are not recolored; no answer, intent, source, identity or backend changes.
No new dependency or second UI framework is introduced.

Acceptance: 43 static frontend/contrast tests passed. Tests ran without repository
runtime bootstrap because the Mac checkout lacks backend dependencies; this is
explicitly not a new full backend regression. Both theme previews were inspected,
including the 390px Twin layout. A first screenshot immediately after viewport
change was stale; subsequent screenshot showed the responsive layout correctly.
The hidden sidebar remains offscreen, not a visible overlay.

Local preview uses static assets only; it does not access credentials, private
knowledge or user conversations. It is not live production acceptance. Deploy and
verify the public stylesheet and old/new answer rendering before calling this live.

## Production frontend qualification, 2026-10-20

Frontend assets from630a36359f23ac3013b0022624a08c10e03a1219 are live. Only
index.html and minimal.1009.css differ from the running backend112e924; every
Python file was compared unchanged. This is an explicitly recorded two-asset
overlay, not a claim that the backend was replaced. App PID remained1058851;
no app, model, gateway or Wiki sync service was restarted. Old asset bytes are
retained in the deployment backup for atomic restoration.

46 frontend/contrast checks and14 Node interaction groups pass. Tests used an
isolated test package path and explicit Node PATH on Mac, and the existing
qualified Python environment on A100. No new full backend regression is claimed.
Public stylesheet SHA256:
`ca0d140bf1f662acb40b923e25b79d566172c9ea0913ecd4ad152a583be7e265`.

Real public Chrome question about ECPA/StateAxis completed in13.9s (page timing),
showed the original Wiki table, complete links, coverage qualification and
collapsed advanced workflow. Both light and dark answer/link surfaces were
visually checked; no question text, permission or retrieval behavior was changed.
This is visual/delivery qualification, not a new human usefulness study.

Rollback restores the saved two asset files without restarting the app; the
private deployment receipt includes the guarded rollback command. Avoid blindly
checking out the backend repository, which intentionally has this tracked static
overlay and must retain its existing runtime/dependency qualification.

## Minimal interaction release, 2026-10-20

Latest frontend revision on main is
`faa266e8d416216c4c31b5e112ff180471bc50d3`, deployed06:13:05Z. Application
backend remains112e924 and PID1058851 is unchanged. The explicit overlay now
contains app.js, index.html and minimal.1009.css; all Python files are identical
to the running qualified backend. No new workflow, routing or permission path.

Source/knowledge cards use one compact icon/title/count/show row instead of the
large English-kicker card. Full evidence and links remain available on expansion;
missing-evidence warnings do not get compacted or hidden. Advanced workflow
remains folded. Feedback is a quiet helpful/not-helpful row; the explanation
form appears only on request. Cancel clears its prompt and returns keyboard
focus; Escape collapses sources and returns focus to their toggle.

Real public old-history acceptance caught a saved-HTML difference: new answers
were compact but old source cards retained the old markup. The faa266e migration
changes only evidence controls during history hydration, retaining original
answer, source body, links, expansion state and provenance. It does not regenerate
or reassign old conversations, or disguise source gaps.

46 frontend/contrast/contract checks and15 Node interaction groups pass on Mac
and A100. The Node extractor recognizes both ordinary and async function
boundaries; a previously detected harness failure was fixed and rerun explicitly,
not ignored behind a later passing command. No full backend regression claimed.

Native Chrome acceptance included a real public-Wiki ECPA/StateAxis answer
(12.3s candidate page timing), full source expansion, Escape focus restoration,
feedback open/cancel without submitting feedback, and390x844 light/dark reading.
Final candidate restored older saved HTML and verified the migration. Public
Chrome restored the13:02 old answer from the history rail without re-asking,
showed the compact source control, expanded its complete source and collapsed
with Escape. Actual Markdown download completed; the saved file retained the
question, original answer and complete public links. Refresh may show the welcome
view; select the retained conversation in history rather than assuming data loss.
The final public version also answered a new ECPA/StateAxis follow-up in19.0s
(page timing), kept its source and advanced graph folded, and passed feedback
open/cancel focus restoration without submitting feedback.

Public app.js SHA256:
`da4e32de53580e171cc1a4bf9654f0b9b2b7913a961f676d706a7ffbac5c15f0`.
Public stylesheet SHA256:
`1876686c39d23619a6c2c08fa1d2f3f1800e1251134acacc0f570006cde2a754`.
Asset URLs carry the new twin-ws-layout-history-20261020 revision.

Rollback uses the guarded private deployment helper and saved previous-web
bytes, restoring the three frontend files without an app restart. The immediate
previous1334654 overlay and original630a363 overlay are both retained.
Limitations: anonymous restricted local-code/hardware requests return403 and
unavailable historical feedback can return404; these were not masked by the UI
release. Existing original answers and historical feedback-state text are not
rewritten. Browser checks are not a new human usefulness study.
