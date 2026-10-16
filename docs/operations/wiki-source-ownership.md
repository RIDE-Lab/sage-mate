# Wiki is the maintained source

Wiki-backed records in the retrieval store are derived indexes, not a second
editable copy. Correct the Wiki page and synchronize it; do not maintain parallel
paper-writing/private-materials versions of an already migrated guide.

`source_ownership.json` declares individually reviewed source-to-Wiki ownership.
It is deliberately not a keyword or similarity threshold: shared research topics
do not justify deleting independent papers, course lectures or private evidence.

Legacy upserts return the existing canonical Wiki record without overwriting its
content, metadata or visibility. If the canonical record is absent, imports fail
instead of silently dropping knowledge or recreating obsolete copies. Direct add
and update paths reject retired identities. The paper-writing importer explicitly
skips migrated guides before reading their old files.

Reviewed cleanup retires the English and Chinese revision-lessons copies. Private
project-specific build commands remain recoverable in the archived original KB,
not duplicated in current general writing guidance. Independent research-topic
material and lectures are retained. Wiki guides preserve course metadata and their
explicitly reviewed public visibility so they remain answerable after cleanup.
