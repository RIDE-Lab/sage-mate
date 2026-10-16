# Public Wiki and private knowledge

Wiki is a public source of truth, not an import into the private knowledge base.
`DIGITAL_TWIN_WIKI_INDEX_DIR` selects its derived search index; it must be disjoint
from `DIGITAL_TWIN_KNOWLEDGE_BASE_DIR` and its persistence directory. Configure the
same value for the app and Wiki sync service. No configured Wiki directory means
Wiki retrieval is disabled; the importer fails rather than falling back to private storage.

`tools/ingest_wiki.py` indexes the public repository only into that directory,
using the existing production embedding, reranker and SageVDB settings. Long
pages are losslessly partitioned into bounded index chunks, never summarized or
truncated. Each chunk retains its canonical public page URL. Both indexes have
independent persistence checkpoints.

The answer path retrieves public Wiki and, for authenticated lab members or
authorized administrators, private knowledge. Record-level ACLs still apply.
Results and full-body lookups carry `wiki::` or `private::` identifiers and
`retrieval_source` metadata. Rank-position interleaving does not compare scores
from different index populations. Private CRUD/admin APIs never mutate Wiki.
Wiki-owned source names and retired aliases are rejected by private writes.

## Migration order

1. Pause the Wiki sync timer and retain the current release/configuration.
2. Build and reopen the independent Wiki index with production retrieval settings.
   Verify canonical sources and full-text coverage, including the final chunk.
3. Deploy the source router and configure the app and sync service identically.
   Verify real public answers before removing any old private records. The router
   excludes legacy Wiki records from the private lane during this transition.
4. Prepare a private shadow snapshot without `wiki:` records, rebuild its index,
   reopen it and verify completeness. Check the live document manifest again.
5. Briefly stop the app, rename the original private store to a protected rollback
   directory, atomically activate the verified snapshot, and check readiness.
6. Run a no-change Wiki sync; verify private files stay unchanged and the app does
   not restart. Resume the timer. Retain the protected backup for rollback.

Health exposes `knowledge_documents`/`knowledge_index_complete` for the private
source and `wiki_documents`/`wiki_index_complete` for the independent Wiki index.
Wiki document count is a chunk count, not necessarily a page count.
