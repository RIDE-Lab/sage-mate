# Unowned legacy memory quarantine

Run `tools/quarantine_legacy_memory.py --source <snapshot-db> --destination <new-private-directory>`
as the service operator. The destination must be outside all serving, retrieval,
static-file and synchronization roots. It contains private full records, not a
public report. Directory permissions are 0700 and files are 0600.

The exporter makes a WAL-consistent SQLite backup and two disjoint, lossless JSON
partitions: `test-labelled` (identity fields contain test hints) and
`unknown-origin`. Test hints do not prove AI authorship. Body text alone never
classifies a record as a test. Attachments inherit their parent record's group.
Any existing owner requires separate review: the exporter refuses that input.
It neither assigns ownership nor changes the source database.

The manifest records counts and SHA-256 hashes. Preserve the original SQLite
snapshot as well as both partitions. Never import either partition into live
personalized retrieval, infer ownership from names/emails, or delete unknown
records as presumed tests.

An export alone does not isolate the currently running service. At cutover:

1. Stop admission and drain/stop the old app; preserve its unit configuration.
2. Refresh the final archive after writers stop, verify hashes and counts, and
   preserve the whole legacy memory directory, including disk-layout records,
   attachments and digests, outside the serving runtime.
3. Start the new owner-bound memory store empty, with no symlink or fallback to
   legacy snapshots. Preserve accounts and reviewed knowledge sources separately.
4. Check startup/restart, authenticated and guest ownership boundaries, retrieval,
   streaming, cancellation and actual browser delivery before declaring success.
5. Keep rollback material private. A rollback must not silently expose quarantined
   data again or discard new owned records written after cutover.

Do not run the legacy data importer on production before this boundary is in
place; it deliberately supports old disk layouts and can otherwise reintroduce
unowned records.
