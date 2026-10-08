# Sage Mate full conversation traces

Sage Mate persists a full experimental trace for every successful or failed `POST /chat` request, including requests that use `request_id` with the workflow-events SSE channel.

## Configuration

All deployment-specific locations and origins are supplied through configuration:

- `DIGITAL_TWIN_CONVERSATION_TRACE_DB`: primary SQLite database;
- `DIGITAL_TWIN_CONVERSATION_TRACE_ARCHIVE_DB`: optional independent archive database;
- `DIGITAL_TWIN_CONVERSATION_TRACE_RETENTION_DAYS`: retention period, default 365 days;
- `SAGE_MATE_PUBLIC_ORIGIN`: public application origin used by operator commands.

Example operator environment:

```bash
export TRACE_DB="${DIGITAL_TWIN_CONVERSATION_TRACE_DB:-/path/to/private-runtime/conversation-traces.sqlite3}"
export TRACE_ARCHIVE="${DIGITAL_TWIN_CONVERSATION_TRACE_ARCHIVE_DB:-/path/to/private-runtime/conversation-traces-archive.sqlite3}"
export SAGE_MATE_ORIGIN="${SAGE_MATE_PUBLIC_ORIGIN:-https://twin.example.com}"
```

The parent directories must have mode `0700`; SQLite, WAL and SHM files must have mode `0600`. Neither database may live in a Git checkout's tracked paths or a web server's static-content directory.

## Captured data

A trace includes the complete chat request, extracted attachment text, final structured response, workflow steps, streamed answer chunks, exact model request payloads, model responses, usage, timing, errors, request ID, conversation ID and authenticated student identity.

When a trace reaches a terminal state, Sage Mate copies its root and every ordered event into the configured archive. Startup reconciliation backfills missing or changed traces, so an interrupted archive write is repaired on the next application restart.

## Correlation

Every `/chat` response includes a random `X-Trace-Id` header. This server-generated ID is independent of the caller-supplied SSE `request_id` and is safe to use for correlation.

## Admin-only query URLs

These routes require an authenticated Sage Mate administrator session:

- `GET /admin/conversation-traces?limit=50`
- `GET /admin/conversation-traces?conversation_id=<CONVERSATION_ID>`
- `GET /admin/conversation-traces/stats`
- `GET /admin/conversation-traces/<TRACE_ID>`

```bash
curl -fsS -b '<COOKIE_JAR>' \
  "$SAGE_MATE_ORIGIN/admin/conversation-traces?limit=20" | jq

curl -fsS -b '<COOKIE_JAR>' \
  "$SAGE_MATE_ORIGIN/admin/conversation-traces/<TRACE_ID>" | jq
```

Do not publish trace exports or commit them to Git. Anonymous callers must receive HTTP 401/403. Public monitoring endpoints may expose aggregate traffic statistics only and must never read or serve either trace database.
