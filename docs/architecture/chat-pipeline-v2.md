# Sage Mate Chat Pipeline v2

## Why this upgrade exists

The current chat path has named stages, but it does not have enforceable module
boundaries. A mutable context crosses every stage, semantic policy is repeated
in multiple modules, and several fast paths return a `ChatResponse` without
entering the staged pipeline. The result is local optimizations that preserve
their own tests while violating system-level behavior.

Pipeline v2 is an incremental replacement, not a second permanent pipeline.
Each migration step must remove or adapt an old path before the next step lands.

## Non-negotiable invariants

1. The original user question is immutable. Guidance is structured context and
   never replaces the question.
2. One interaction-policy component owns action, decision mode, retrieval scope,
   and escalation decisions.
3. Retrieval returns typed evidence; prompt builders cannot manufacture source
   identities or owner facts.
4. Full, compact, retry, skill, and deterministic answers all produce an
   `AnswerCandidate`. None is publicly deliverable by construction.
5. Only the delivery gate can produce a `DeliveredChatResponse`.
6. Public transports accept only delivered responses. Raw model tokens remain
   private diagnostics until candidate validation completes.
7. Runtime identity and topology come from validated settings. Source code does
   not contain machine, model, owner, NPU, port, or domain assumptions.
8. Every execution path must pass the same contract and invariant suite.

## Target dependency direction

```text
api / slack / benchmark adapters
             |
             v
       chat application
             |
    +--------+---------+
    |        |         |
 policy  retrieval  generation
    |        |         |
    +--------+---------+
             |
      answer validation
             |
        delivery gate
             |
       public transports

models/config  <- imported by application modules
stores/clients <- injected behind ports; never import the application
```

The API parses and renders transport data only. It does not decide intent,
select prompts, validate answer quality, or publish model candidates.

## Stage contracts

| Stage | Input | Output | May mutate external state |
|---|---|---|---|
| Intake | transport request | `ChatIntake` | no |
| Policy | `ChatIntake` | `InteractionDecision` | no |
| Evidence | intake + decision | `EvidenceBundle` | no |
| Prompt | intake + decision + evidence | `PromptEnvelope` | no |
| Generate | prompt | `AnswerCandidate` | no |
| Validate/retry | candidate + evidence + policy | `ValidatedAnswer` | no |
| Commit effects | validated answer + authorized plan | effect receipts | yes |
| Deliver | validated answer + receipts | `DeliveredChatResponse` | no |

Post-answer analytics and memory writes consume an immutable delivered snapshot.
They cannot alter the answer that was already validated.

## Migration map

1. Add the delivery boundary at the common service exit and require the SSE
   broker to accept only `DeliveredChatResponse`.
2. Extract answer candidate generation, retry, and validation from
   `FacultyTwinWorkflowSupport`.
3. Replace the mutable routing fields with `InteractionDecision`; consolidate
   the deterministic and model-assisted classifiers behind one policy service.
4. Extract retrieval and prompt composition with typed evidence and shared
   prompt invariants.
5. Replace both linear and DAG chat executors with one orchestrator. Parallelism
   becomes an internal execution strategy, not a second semantic path.
6. Move post-answer effects behind explicit ports and delete compatibility
   adapters after the full-path contract suite is green.

## Compatibility and rollback

- `/chat`, SSE event names, `ChatResponse`, stored records, and environment
  settings remain backward compatible during migration.
- Each phase is independently revertible and does not restart the inference
  engine unless its external contract changes.
- No long-lived v1/v2 feature flag is introduced. Temporary adapters are removed
  in the same phase that migrates their final caller.

## Current migration state

Both FlowNet and in-process execution now consume the same critical-stage
registry and enter the same post-answer/delivery logic. Retrieval is sequential
for now: the former DAG fan-out allowed two branches to mutate one shared
`ChatWorkflowContext`. Parallel retrieval may return only after those stages
produce independent immutable results that can be merged explicitly.
