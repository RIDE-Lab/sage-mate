# Conversation digest provenance

Twin's digest destination now uses NeuroMem `DurableDigest` through SAGE atomic
SQLite state. There is no JSON write/cache path and no count-based frontier.
Automatic and manual updates use the same preparation/publication method.
Preparation enumerates a detached chronological snapshot of all canonical
conversation exchanges, including complete answers, attachments and source
metadata. Stable memory IDs and payload fingerprints determine coverage, so
backlogs beyond 64 turns and late-arriving records are not silently omitted.

Generation receives the previous digest and all new payloads. Model failure
does not advance coverage. Concurrent publication against an outdated base
fails and requires regeneration, rather than overwriting the winner. An exact
plan/output retry has a deterministic operation identity. Automatic and manual
intents now execute through SAGE WorkService. Generation failure or stale-base
publication keeps the task retryable; restart recovers admitted tasks without
re-running the chat. Generation can repeat after an uncertain acknowledgement,
but persisted coverage prevents repeating the destination update. The task
refreshes current canonical sources at execution time, not a fixed enqueue-time
snapshot. The remaining exchange-to-task admission gap needs the whole-turn
atomic commit cutover.

With `context_digest_llm_enabled=false`, the default remains free of extra model
calls but now **retains full content**, including the previous state. This is
lossless accumulation, not semantic compression. The API and UI identify it as
such; `turns_incorporated` counts consumed sources and `turns_compressed` is zero.
With generation enabled, the character target is advisory and input/output
strings are not sliced; the old 256-token generation override is removed.
Semantic fidelity, finite context admission and throughput still need end-to-end
qualification. Lossless accumulation is not a scalable substitute for scheduled
semantic compression and must not be advertised as a latency optimization.

Old digest JSON files remain untouched for recovery/audit but are not read into
the active prompt or written again. Their cumulative counts cannot establish
source provenance, and their text may already have lost earlier facts. Rebuild
from canonical exchanges before a production cutover; retain the old files for
manual review if source records are missing. No fabricated IDs/count-based
coverage or lossy-summary migration is attempted. New state lives in
`<context_digest_dir>/digests.sqlite3`, with managed connection shutdown.

The tests exercise actual service methods and persistence: 100 automatic turns,
100-turn initial backlog, late sources, repeated/manual updates, reopen,
legacy-count rejection, complete nested Markdown, stale-generation conflict,
complete model input/output and failed generation without false coverage.
Mock model tests prove transport inputs, not semantic output quality. Worker
tests additionally cover restart recovery, failure after destination commit,
captured-policy stability, nonblocking answer delivery and completion ordering.
Deployment rebuild, capacity-aware generation and browser acceptance remain
separate rollout gates. Source snapshots currently use the local memory
read view; multi-process refresh and paginated large-history access are pending.
