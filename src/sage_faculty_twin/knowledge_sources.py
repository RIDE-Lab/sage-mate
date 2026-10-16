"""Read-only retrieval across public Wiki and an independently managed private KB."""
from __future__ import annotations

from .config import AppSettings
from .source_ownership import SOURCE_OWNERSHIP


def wiki_index_settings(settings: AppSettings) -> AppSettings:
    path = settings.wiki_index_dir
    if path is None:
        raise ValueError('wiki_index_dir must be configured; Wiki never uses the private KB directory')
    private = settings.knowledge_base_dir.resolve()
    public = path.resolve()
    if public == private or private in public.parents or public in private.parents:
        raise ValueError('Wiki index and private KB directories must be disjoint')
    persistence = settings.knowledge_sagevdb_persistence_dir
    if persistence is not None:
        persistence = persistence.resolve()
        if public == persistence or public in persistence.parents or persistence in public.parents:
            raise ValueError('Wiki index and private persistence directories must be disjoint')
    return settings.model_copy(update={
        'knowledge_base_dir': path,
        'knowledge_sagevdb_persistence_dir': path / '.sagevdb-index',
        'knowledge_namespace': 'wiki',
    })


class KnowledgeSources:
    """No CRUD methods: editing a private record never edits a Wiki index."""
    def __init__(self, private, wiki=None, *, top_k=5):
        self.private, self.wiki, self.top_k = private, wiki, top_k

    @staticmethod
    def _private_allowed(visitor_profile, admin_role):
        return visitor_profile == 'lab_member' or admin_role in {'super_admin', 'manager'}

    @staticmethod
    def _label(hit, origin):
        return hit.model_copy(update={
            'document_id': f'{origin}::{hit.document_id}',
            'metadata': {**hit.metadata, 'retrieval_source': origin},
        })

    def search(self, query, top_k=None, *, visitor_profile=None, admin_role=None):
        limit = top_k or self.top_k
        lanes = []
        if self.wiki is not None:
            lanes.append(('wiki', self.wiki.search(query, top_k=limit, visitor_profile='general_visitor')))
        if self._private_allowed(visitor_profile, admin_role):
            lanes.append(('private', [hit for hit in self.private.search(query, top_k=limit,
                visitor_profile=visitor_profile, admin_role=admin_role)
                if not (hit.source_name or '').startswith('wiki:') and hit.source_name not in SOURCE_OWNERSHIP]))
        # Rank-position interleaving avoids comparing incompatible score scales
        # across independently indexed sources. Preserve per-hit diagnostic score.
        merged = []
        for rank in range(limit):
            for origin, hits in lanes:
                if rank < len(hits):
                    merged.append(self._label(hits[rank], origin))
        return merged[:limit]

    def get_visible_document(self, document_id, *, visitor_profile=None, admin_role=None):
        origin, separator, local_id = document_id.partition('::')
        if not separator:
            return None
        if origin == 'wiki' and self.wiki is not None:
            document = self.wiki.get_visible_document(local_id, visitor_profile='general_visitor')
        elif origin == 'private' and self._private_allowed(visitor_profile, admin_role):
            document = self.private.get_visible_document(local_id,
                visitor_profile=visitor_profile, admin_role=admin_role)
        else:
            return None
        if document is None:
            return None
        if origin == 'private' and ((document.source_name or '').startswith('wiki:') or document.source_name in SOURCE_OWNERSHIP):
            return None
        return document.model_copy(update={
            'document_id': document_id,
            'metadata': {**document.metadata, 'retrieval_source': origin},
        })

    def public_documents(self):
        if self.wiki is None:
            return []
        return [d.model_copy(update={'document_id': f'wiki::{d.document_id}',
            'metadata': {**d.metadata, 'retrieval_source': 'wiki'}})
            for d in self.wiki.list_documents()
            if self.wiki.get_visible_document(d.document_id, visitor_profile='general_visitor') is not None]
