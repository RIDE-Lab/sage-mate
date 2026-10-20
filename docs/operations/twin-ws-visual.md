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
