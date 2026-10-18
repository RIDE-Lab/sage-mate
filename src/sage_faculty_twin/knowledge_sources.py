"""Read-only retrieval across public Wiki and an independently managed private KB."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal

from .config import AppSettings
from .source_ownership import SOURCE_OWNERSHIP
from .models import KnowledgeSearchHit


KnowledgeOrigin = Literal['wiki', 'private']


@dataclass(frozen=True)
class SourceSelection:
    """Requested evidence origins, independent of the caller's authorization.

    Empty means no source is requested, never 'use defaults'. This is a business
    constraint, not a permission grant or a runtime execution policy.
    """
    origins: frozenset[KnowledgeOrigin] = frozenset({'wiki', 'private'})

    def __post_init__(self):
        if isinstance(self.origins, str):
            raise ValueError('source origins must be a collection')
        origins = frozenset(self.origins)
        if not origins <= {'wiki', 'private'}:
            raise ValueError('unknown knowledge source origin')
        object.__setattr__(self, 'origins', origins)

    def allows(self, origin: str) -> bool:
        return origin in self.origins


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

    def search(self, query, top_k=None, *, visitor_profile=None, admin_role=None,
               selection: SourceSelection = SourceSelection()):
        # An explicitly named public document is a source request, not an
        # invitation to pad top-k with unrelated semantic neighbours. Include
        # every stored chunk of each exact title, retaining its public ACL.
        titles = set(re.findall(r'《([^》]+)》', query))
        named = [d for d in self.public_documents(selection=selection)
                 if d.title.removeprefix('Wiki | ') in titles]
        if named or (titles and re.search(r'wiki', query, re.IGNORECASE)):
            named.sort(key=lambda d: (d.metadata.get('wiki_page_source', d.title), int(d.metadata.get('wiki_chunk', '1'))))
            return [KnowledgeSearchHit(document_id=d.document_id, title=d.title,
                source_name=d.source_name, excerpt=d.content, score=1.0,
                tags=d.tags, metadata=d.metadata) for d in named]
        limit = top_k or self.top_k
        lanes = []
        if self.wiki is not None and selection.allows('wiki'):
            lanes.append(('wiki', self.wiki.search(query, top_k=limit, visitor_profile='general_visitor')))
        if selection.allows('private') and self._private_allowed(visitor_profile, admin_role):
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

    def get_visible_document(self, document_id, *, visitor_profile=None, admin_role=None,
                             selection: SourceSelection = SourceSelection()):
        origin, separator, local_id = document_id.partition('::')
        if not separator or not selection.allows(origin):
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

    def public_documents(self, *, selection: SourceSelection = SourceSelection()):
        if self.wiki is None or not selection.allows('wiki'):
            return []
        return [d.model_copy(update={'document_id': f'wiki::{d.document_id}',
            'metadata': {**d.metadata, 'retrieval_source': 'wiki'}})
            for d in self.wiki.list_documents()
            if self.wiki.get_visible_document(d.document_id, visitor_profile='general_visitor') is not None]
