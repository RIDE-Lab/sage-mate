"""Read-only retrieval across public Wiki and an independently managed private KB."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Literal
from threading import Lock
import weakref

from .config import AppSettings
from .source_ownership import SOURCE_OWNERSHIP
from .models import KnowledgeSearchHit
from .request_context import request_remaining_seconds


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
    def __init__(self, private, wiki=None, *, top_k=5, timeout=120):
        self.private, self.wiki, self.top_k = private, wiki, top_k
        self._timeout = timeout
        self._runtime = None
        self._runtime_lock = Lock()
        self._finalizer = None
        self._closed = False

    def _get_runtime(self):
        from .knowledge_source_runtime import KnowledgeSourceRuntime
        with self._runtime_lock:
            if self._closed:
                raise RuntimeError('knowledge_sources_closed')
            if self._runtime is None:
                self._runtime = KnowledgeSourceRuntime()
                self._finalizer = weakref.finalize(self, self._runtime.close)
            return self._runtime

    def close(self):
        with self._runtime_lock:
            self._closed = True
            if self._finalizer is not None and self._finalizer.alive:
                self._finalizer()

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
        with self._runtime_lock:
            if self._closed:
                raise RuntimeError('knowledge_sources_closed')
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
        wiki_allowed = self.wiki is not None and selection.allows('wiki')
        private_allowed = selection.allows('private') and self._private_allowed(visitor_profile, admin_role)
        if not wiki_allowed and not private_allowed:
            return []
        remaining = request_remaining_seconds()
        results = self._get_runtime().search(wiki=self.wiki, private=self.private,
            timeout=remaining if remaining is not None else self._timeout,
            request={'query': query, 'limit': limit, 'wiki': wiki_allowed, 'private': private_allowed,
                     'visitor_profile': visitor_profile, 'admin_role': admin_role})
        lanes = []
        if wiki_allowed:
            lanes.append(('wiki', results['wiki']))
        if private_allowed:
            lanes.append(('private', [hit for hit in results['private']
                if not (hit.source_name or '').startswith('wiki:') and hit.source_name not in SOURCE_OWNERSHIP]))
        # Rank-position interleaving avoids comparing incompatible score scales
        # across independently indexed sources. Preserve per-hit diagnostic score.
        from sage.runtime.evidence_relevance import interleave_source_candidates
        return list(interleave_source_candidates(
            [[self._label(hit, origin) for hit in hits] for origin, hits in lanes], limit))

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
