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
