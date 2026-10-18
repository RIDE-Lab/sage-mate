# Research source-plan qualification

Candidate (not yet production): SAGE `33079b9aff5d972d86c2eece85df5fe5211fb871`.

## Root causes and fixes

1. Research requests mentioning experiments were classified as course experiments
   (`62ea44f`, already on main). Explicit course context remains supported.
2. A shared collection label (Wiki) admitted unrelated long Prompt Engineering
   chunks. SAGE now selects candidates by discriminating named subjects over
   full authorized text; no system-name allowlist, excerpt truncation or token-cap
   reduction. Multiple requested subjects retain their respective candidates.
3. A deterministic gateway HTTP400 capacity rejection was retried as a temporary
   workflow failure. Typed safe SAGE rejection codes now survive the worker
   boundary in HTTP and Slack. Provider error bodies are not persisted in those
   codes. Transient 429/5xx remain retryable.
4. Research suggestions confused performance falsification with functional
   safety. The source-plan instruction now separates proposed hypotheses,
   correctness, performance, controls and evidence boundaries.

## Isolated real-model observations

Two public Wiki research questions (StateAxis review; ECPA/StateAxis verification)
returned HTTP200 in 10.59s and 8.32s after the final prompt update. Both selected
the full systems research overview, including all Markdown and complete links.
The second answer explicitly stated that this source cannot establish overall
validation, and that missing published measurements do not imply no experiments.
Suggested fault injection and conflict-isolation tests were labelled non-source
inferences. No performance numbers or paper acceptance claims were invented.

Earlier 7.85s/7.16s responses were not accepted for research quality because a
suggestion conflated absent speedup with falsification of a safety claim.

Focused results: 111 error/client/workflow tests passed; 86 retrieval/source-plan
tests passed with 5 existing skips; 38 SAGE classification/relevance/source-plan
tests passed. Full-suite and clean locked-release acceptance remain required.
An initial new fixture used the private namespace for Wiki records and correctly
failed the source-separation guard; the fixture now uses the Wiki namespace.

These are isolated API observations, not new browser/Slack visual acceptance or
a production deployment claim. The live gateway's exact tokenizer estimate has
not been audited; this change does not claim to fix its estimation policy.

## Expanded qualification

The `source_content` field is intentionally excluded from public API serialization.
The original API harness used a vacuous conditional check; that check is withdrawn.
The corrected harness loads the trusted public Wiki catalog snapshot read-only
and compares each selected record's complete content against the answer. Both
research questions pass this independent comparison. Their 1.91s/8.87s repeat
latencies mix cache/reuse with generation and are not a cold-speed benchmark.

Clean locked e5b7492: 1552 passed, 31 existing skips, 339.37s. A separate scratch
run including 21 SAGE tests passed1573/31skips in331.79s. Five Node groups passed.
False-premise probe refused to infer that experiments never happened; the four
stage onboarding guide returned complete with links. An additional explicit
falsification request omitted suggestions because its action was unrecognized.
Follow-up main4b6e9f6 fixes that request type; 49 focused tests passed. A new clean
release is undergoing full regression and real-question qualification before
deployment. The earlier successful two-question sample did not establish this
broader request-coverage gate.

The 4b6e9f6 clean suite passed1555/31skips in332.98s, but the new real comparison
question exposed a second path: per-subject supplements reintroduced generic
Wiki candidates and an unconstrained model emitted an extra closing brace.
The final candidate applies the same SAGE full-source relevance policy to those
supplements using the immutable authority snapshot, and sends SAGE's strict
source-plan JSON schema through Chat Completions or Responses as appropriate.
Validation remains strict; there is no JSON repair or alternate free-text path.
The comparison then passed in13.65s with separate safety/performance proposals;
onboarding passed in4.27s. Focused155passed/5skips and native authority/wire tests
26passed. SAGE schema commit0ac1e92 is pinned for the final clean qualification.
