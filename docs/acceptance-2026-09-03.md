# Sage Mate independent acceptance — 2026-09-03

Evidence class: **existing-server-probe**. This is not a throughput benchmark,
and HTTP success is not sufficient evidence of answer correctness.
Workstation is owned by a separate task and is not certified by this report.

## Frozen production identity

- Parent at audit start: `f082fb3`.
- Container: `c82ec990eafbffa19a1c92f3cc100799c181313a89f55344f9271fafadd3bc10`;
  started `2026-09-02T12:25:43.59205152Z`.
- Image: `sage-mate/vllm-ascend-hust:core-762f85b3-plugin-124826b8-native6-cann9.1`;
  ID/digest `sha256:e37db6660c24c9cde10b3076c1146fdaa4366b6426130555643d1c5b2c3e68f9`.
- OCI build-start timestamp `2026-09-02T12:10:47Z`; Docker image creation
  `2026-09-02T12:24:35.359159014Z` (different stages, not a receipt capture time).
- Core `762f85b311fbab0bcf8921dd216f5093cd58b9b8`, installed package
  `0.28.1rc1.dev319+g762f85b31.empty`.
- Ascend `124826b8c649e5680aa1c57d5504922c68c28ad3`, installed package
  `0.25.1rc1+hust.20260902.9`.
- Both resolve under `/usr/local/python3.12.13/lib/python3.12/site-packages/`;
  receipt `/opt/vllm-hust-runtime/runtime-stack.json`, schema
  `vllm-hust.runtime-receipt/v2`, immutable wheels. Lock, labels and package
  metadata agree. This does not imply tracing every imported file inside each
  already-running worker.
- GLM-4-32B-0414, TP4, physical NPU0–3, worker PIDs 22833–22836.
  Process arguments specify `FULL_DECODE_ONLY`; startup log reports
  `enforce_eager=False` and completed graph capture. No engine/proxy restart.
- The v0.23.0 official image is the filesystem/CANN base, **not active core**.

At the GitHub API audit cutoff, HUST core main was
`86ffadbd8d27d6b17c7053420254caa239158774`, Ascend main
`1d2f1f87a7449cd86fd6c2946174224ee81def52`. Each was 33 commits ahead of its
production pin, with production as merge base and zero reverse divergence.
Official core was `0e14198a63c03f899a10f3e782e88eca7f11265b`; official Ascend
had advanced to `51b895ffc2bd778b0470149ca826f5c37e010a0d` during the audit.
Production is a verified September 2 snapshot, not today's moving main.

## Verified fixes

1. Nested implicit grid tracks expanded beyond a 390px viewport even while
   the document reported scrollWidth=390. The reply extended to x398 and
   Support to x408. Zero-minimum grid tracks and wrapping keep children inside
   their message; tests check child rectangles, not just document width.
2. Deep/search checkboxes used `display:none`, excluding them from native
   keyboard navigation. Clipped native inputs retain Tab/Space and visible
   label focus rings.
3. Guest avatar JavaScript overwrote theme CSS with translucent black. Actual
   dark contrast was 1.02:1. Guest colors now come from action tokens.
4. Unselected mode borders had only about 2.2:1 dark contrast; opaque shared
   action borders replace the low-contrast border.
5. Mobile chat reserved a fixed 140px for a composer that can exceed210px
   when deep-mode feedback is visible. ResizeObserver now reserves measured
   height, including wrapped actions and expanding input.
6. At320px, onboarding's max-height combined with min-content sizing and
   visible overflow placed navigation beneath the composer. The card now
   scrolls within measured available height; real public next-step click passes.

## Real public checks

| Check | Result |
|---|---|
| Local health/models; direct non-stream completion | Passed; baseline explanation0.34s |
| Engine streaming | Passed; multiple nonempty content deltas, stop, `[DONE]` |
| Public ordinary identity question |4.3s; correct research identity;3 Support sources |
| Public deep request |8.5s; processing UI and response work, **content acceptance failed** below |
| Public cancel |HTTP200, cancelled=true, send button re-enabled |
| Request after cancel |HTTP200/model response6.15s; **grounding failed**, not counted as content pass |
| Day/night refresh persistence |Both persisted in sageMateTheme; real Firefox |
|390×844 width and content |No page overflow; descendant bounds tested; composer clearance fixed |
|320px onboarding |Real public next-step reaches2/2; regression passes |
| NPU4–7/statecentric |No operations; container040c81e6/start2026-09-03T01:44:19Z unchanged |

### Actual computed colors

Solid controls had opacity1, so their composed background is the listed RGB.
Border ratios conservatively list the smaller adjacent/inside value. Gradient
contrast is computed against **every stop**, not transparent backgroundColor.

| Control | Night ink/background | Day ink/background | Contrast night/day |
|---|---|---|---|
| Dice/token/workflow default |#d7e5fb/#122847 |#29496d/#f5f8fd |11.61 /8.70 |
| Unsupported mic |#9dafc9/#1b3154 |#536a86/#dce5f0 |5.83 /4.37 |
| Default action boundary |#7892b8 |#6d88a4 |4.65 /3.46 |
| Mic boundary |#7892b8 |#607894 |4.09 /3.58 |
| Default mode label |#c3d0e5/#0d1d35 |#3b506d/#ffffff |10.83 /8.22 |
| Checked mode |white; #3f4fd0→#6549e8→#0b7188 |same |minimum5.63 |
| Processing mode |white; #3f4fd0→#7c4dff→#0b7188 |same |minimum4.81 |

Checked/processing SVG stroke and label computed white. Unsupported mic has a
slash and explanatory title; it is not made invisible through group opacity.
This audit does **not** certify every possible hover/disabled state of every
component. Mode keyboard, default borders, real processing and shared theme
contracts were exercised separately.

## Failed / follow-up required

- **Deep answer completeness:** two real requests asking for three actionable
  checks ended in the risk section before any actions. Current configuration
  caps deep answers at256 completion tokens and deliberately disables
  continuation. The answer contract needs completeness-aware bounded generation
  or explicit truncation handling; do not blindly reintroduce unbounded retries.
- **Evidence relevance:** a generic fair-comparison query cited a recruitment
  document. A subsequent owner-research question returned zero Support and
  generic claims. A visible Support panel alone is not citation correctness.
  Retrieval/answer-grounding require separate regression and semantic repair;
  this UI patch does not alter knowledge visibility or retrieval semantics.
- **Transport variability:** first public health probe timed out20s; next
  succeeded3.33s. No sustained local health failure. Needs repeated network
  measurements before blaming inference.
- Cold restart, saturation/load, actual429/504 injection and all authenticated
  roles were not exercised against production. They are not marked passed.

## Evidence and publication

Real public screenshots are under `output/playwright/sage-audit-20260903/`:
`desktop-before.png`, `desktop-dark-answer.png`, `mobile-dark-deep.png`
(before), `desktop-dark-after.png`, `desktop-light-after.png`,
`mobile-light-landing-after.png`, `mobile-dark-processing-final.png`,
`mobile-dark-final.png`, `mobile-light-final.png`,
`mobile-320-onboarding-final.png`. Some completed-response screenshots
intentionally show content failures; do not use them as answer-quality passes.

The static endpoints `app.4222.js` / `styles.4222.css` serve current files with
no-store and Cloudflare BYPASS, so the patch is visible without an app/engine
restart. App API version remains4.6.37; the Git commit identifies this frontend
patch. Existing api.py/chat-streaming edits and runtime artifacts are excluded.

Browser infrastructure initially failed because cached Firefox1538 had a broken
lock path. Isolated verification uses installed Firefox1539 without changing
the production environment. Earlier failed runs are retained; the final test
results must be distinguished from those intermediate failures.

Final focused gates: **19 browser behavioral/theme tests passed** (90s),
**23 Python frontend contracts passed**; JavaScript syntax and `git diff --check`
passed. Stored status-page pixel baselines were not regenerated or included in
this pass; public screenshots above were inspected separately.
