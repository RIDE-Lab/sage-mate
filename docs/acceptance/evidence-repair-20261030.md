# Evidence repair acceptance — 2026-10-30

Status: code regression passed; original-question semantic acceptance **not passed**.
Do not deploy this candidate on the strength of tests or model-reported success.

## Changes

- The API worker and user-visible timing select the existing extended execution
  deadline for required native source verification independently of the user's
  deep-generation toggle. The model, token limits and audit requirements remain.
- One publication-grade full-source audit reviews each unchanged draft. A bound
  revision reruns the complete audit, completeness check and scope challenge.
- SAGE freezes question interpretation before examining sources, checks declared
  source relationships, and adds a source-independent generality veto. These
  model judgments are proposals, not proof; every receipt is bound and validated.
- Structured JSON generation neutralizes repetition penalties on required syntax.
- Retrieval no longer treats an unqualified experiment or a subject shared with
  a course as a request for course documents. Explicit teaching and numbered
  experiment requests retain their behavior and every source retains its ACL.
- Bound answer edits cannot duplicate unaffected or newly inserted sentences.

## Observations

Combined isolated regression: **553 passed, 5 skipped**, 104.98 seconds. After
formatting/unused-import cleanup: 75 affected checks passed. Modified Twin Python
files and new SAGE modules passed their respective Ruff checks; JS syntax passed.
The pinned SAGE dependency and lock source agree.

The real native auditor rejected all five negative controls: universal method
necessity, blanket rejection of alternatives, extrapolation of a workload result,
confusing absent displayed measurements with absent measurements, and reversal
of a conditional latency comparison. It accepted attributed proposals, scoped
measurements and a correct conditional comparison. This controlled result is not
a general false-positive/false-negative estimate.

Same original questions, member scope, isolated production-catalog snapshot:

| Question | Observed result | Independent acceptance |
| --- | --- | --- |
| 怎样区分系统优化的真实收益和实验干扰？ | Returned in 156.5 s; model checks passed, but generalized RAG-specific evaluation requirements and repeated the conclusion | Failed |
| KV Cache 迁移和重计算，什么时候各自更划算？ | Returned in 89.4 s with an explicit evidence gap, after refusing to treat a narrow recomputation source as the complete comparison | Failed: no substantive complete answer |

The Wiki catalog payload hash matched production. In an instrumented lookup,
`推理系统 Benchmark 指南` ranked first before filtering, then disappeared under
the old inferred teaching policy. With the fix it remains first. This does not
prove all natural-language queries rank correctly; the general-method question
still selects overly specialized material.

## Remaining blockers

1. Source/answer relevance and scope must pass both original questions. A valid
   receipt or an evidence-gap notice is not sufficient acceptance.
2. At the time of testing, A100 and A6000-1 could not reach public DNS or establish
   TCP connections to current Tavily addresses. A100 also failed direct Bing and
   Cloudflare DNS HTTPS connections. Bypassing DNS only for diagnostic curl did
   not help. Its internal gateway neighbor remained reachable. This identifies
   a shared external-connectivity symptom, not the exact gateway cause. No shared
   network settings were changed.
3. Production cutover and public-browser acceptance remain pending. No GPU,
   engine, router, or production Twin service was restarted for these checks.
