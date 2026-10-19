# Back to latest

One compact control floats eight pixels above the composer when the reader is
away from the bottom. It does not add a row to history or enter session export.
New answer body text shows “有新内容”; successful completion shows “有新回答”.
Reasoning, progress and background evidence updates do not claim a new answer.
The polite live-region text changes only when the notice changes, not per token.

Neither noticing new content nor completing an answer navigates the reader.
Clicking the button transfers focus to the scroll region without scrolling,
then performs explicit instant navigation, clears the notice and resumes the
existing bottom-follow policy. That order matters: a PageUp animation racing
with focus restoration otherwise left the region behind the latest content.
No delay, second scroll controller, polling or persisted follow state was added.
Notices clear on session navigation, reaching bottom or returning to welcome.

Before-publish checks: 12 Node behavior suites and40 frontend contracts passed.
The production-render browser fixture kept scrollTop4340 through342 deltas and
final replacement, changing the notice without scrolling; explicit click alone
reached15659/max15659 and hid the control. Mid-generation PageUp→click resumed
follow through252 deltas (12979.5/max12980, within browser subpixel rounding).
Light/dark rendering passed. At390px viewport width the control's horizontal
bounds were147..243, height44, bottom760 versus composerTop768; it did not cover
the input. Temporary viewport override was reset.

Deploy frontend assets only, preserve backup and separate frontend revision
receipt, leave backend/model processes and existing conversations unchanged.

Publication: all four public frontend assets were byte-verified against main
61a2163; backend PID3876506 remained unchanged. Public browser acceptance is
not complete: Chrome returned a gray client area without an HTML accessibility
subtree after refresh, and an independent in-app public tab timed out twice.
The localhost production-render fixture still worked and its screenshot was
retained. This is not evidence of successful public UI interaction, nor enough
to attribute the gray page to the application or the computer-control surface.
Resume public interaction after the visible browser is available; no browser
restart, process kill, model restart or authentication change was attempted.
