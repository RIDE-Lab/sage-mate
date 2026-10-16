# Retrieval configuration parity

The app and scheduled Wiki ingestion must load the same production retrieval
environment file, in the same precedence order after base settings. Model,
dimension, backend, endpoint and index paths must not drift. Test-only hash
embeddings must never be used to prepare a production semantic index.

On the managed deployment, both user units load the existing shared
`%h/.config/sage-mate/twin-pooling.env` after their base environment. Keep
credentials in that protected environment file, never in unit files or logs.
After changing unit drop-ins, run `systemctl --user daemon-reload`.
The managed-deployment drop-in is checked in under
`deploy/systemd/user/sage-mate-wiki-sync.service.d/zzz-retrieval-parity.conf`.
It requires the production file to exist and does not silently fall back to defaults.

`tools/sync_wiki_kb.sh` compares its effective retrieval settings with those
of the running Twin before opening the index. An absent app, unreadable process,
or mismatch fails closed. Diagnostics show field names only. This guard is
Linux/systemd-specific; standalone offline importers need explicit configuration
and must not point at a live production store without equivalent checks.

Acceptance: matching preflight; no-change ingestion skips all documents without
restart; a changed-document test on a private snapshot builds a compatible
checkpoint that the app can reopen without calling the embedding API again.
