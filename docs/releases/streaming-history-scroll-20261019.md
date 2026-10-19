# Streaming history reading

The prior completed-history correction did not qualify streaming behavior.
The production final-answer renderer still assigned scrollTop unconditionally;
the new regression reproduced 600→5900 when the reader was viewing history.
The legacy progress renderer had the same assignment, while token deltas had
no common follow policy.

Eight rendering entry points now capture scroll state before synchronous DOM
mutation and restore it in finally. A reader within four pixels of the bottom
follows growth. Otherwise the first visible message retains its visual offset,
including when an earlier evidence card grows; native browser anchoring is not
double-counted. Returning to the bottom resumes follow automatically. Only an
explicit new question or initial history navigation changes reading position.
Initial restore-to-top now happens before awaited citation hydration, not after.

Coverage includes final and error answers, text and reasoning deltas, pending
render/stage/progress, native workflow updates and background evidence refresh.
No polling, global mutation observer, persisted scroll mode or second workflow.

Qualification before publishing: all 12 Node suites and 39 frontend contracts
passed. Browser fixture uses 12 synthetic previous rounds and real production
render functions: top3960 stayed unchanged as 202 more deltas arrived; final
replacement after528 deltas retained top5990. Updating earlier evidence kept
history question11 at exactly -58.203125px while scrollTop compensated for its
new height. Returning to the bottom resumed follow through348 deltas and final
render (15741/15741). These are synthetic fixture checks, not live model output.

Publish frontend only with backed-up assets and a separate frontend revision
receipt. Backend, model processes and existing history are not migrated.

Public acceptance: frontend15546de was byte-verified for HTML, JavaScript and
both stylesheets. Chrome restored the existing two-round public QA session;
while a new, verified full-Chinese Wiki research question was generating, the
reader scrolled to the middle of the first answer. The new answer completed in
31.0 seconds and the viewport retained the same older text and visual offset.
The submit control changed from stop to send without navigating to the bottom.
Backend PID3876506 remained unchanged. A preceding automation attempt lost
Chinese characters through native typing; it is not intended-question quality
evidence. The accepted attempt used paste and checked input before submission.
