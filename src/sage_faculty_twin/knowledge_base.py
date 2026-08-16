from __future__ import annotations

import hashlib
import math
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .config import AppSettings
from .models import (
    KnowledgeDocumentCreate,
    KnowledgeDocumentRecord,
    KnowledgeSearchHit,
)


class HashingTextEmbedder:
    def __init__(self, settings: AppSettings, np_module) -> None:
        self._settings = settings
        self._np = np_module
        self.dimension = settings.sagevdb_dimension

    def encode(self, text: str):
        vector = self._np.zeros(self.dimension, dtype=self._np.float32)
        token_counts: dict[str, int] = {}
        for token in _tokenize_text(text):
            token_counts[token] = token_counts.get(token, 0) + 1

        for token, count in token_counts.items():
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            hashed = int.from_bytes(digest, byteorder="big", signed=False)
            index = hashed % self.dimension
            sign = 1.0 if ((hashed >> 8) & 1) == 0 else -1.0
            vector[index] += sign * (1.0 + math.log1p(count))

        norm = float(self._np.linalg.norm(vector))
        if norm > 0.0:
            vector /= norm
        return vector


class SentenceTransformerTextEmbedder:
    def __init__(self, settings: AppSettings, np_module) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sagevdb embedding backend 'sentence-transformers' requires the sentence-transformers package. "
                "Install with: python -m pip install -e .[vdb]"
            ) from exc

        self._np = np_module
        self._model_name = settings.sagevdb_embedding_model
        self._model = SentenceTransformer(self._model_name)
        self.dimension = self._model.get_sentence_embedding_dimension()
        if not self.dimension:
            raise RuntimeError(
                f"Embedding model '{self._model_name}' did not report an embedding dimension."
            )

    def encode(self, text: str):
        vector = self._model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        array = self._np.asarray(vector, dtype=self._np.float32)
        if array.ndim != 1:
            array = array.reshape(-1)
        if int(array.shape[0]) != int(self.dimension):
            raise RuntimeError(
                f"Embedding dimension mismatch: model returned {array.shape[0]}, expected {self.dimension}."
            )
        return array


class NeuromemBgeEmbedder:
    """Sentence-transformers wrapper used by the neuromem 'faiss' index branch.

    Reads ``settings.neuromem_embedding_model`` (default BAAI/bge-small-zh-v1.5,
    dim=512) and produces L2-normalised float32 vectors suitable for cosine
    similarity in faiss-cpu's IndexFlatIP.
    """

    def __init__(self, settings: AppSettings, np_module) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "DIGITAL_TWIN_NEUROMEM_INDEX_TYPE=faiss requires the sentence-transformers package. "
                "Install with: python -m pip install sentence-transformers"
            ) from exc

        self._np = np_module
        self._model_name = settings.neuromem_embedding_model
        self._model = SentenceTransformer(self._model_name)
        reported = self._model.get_sentence_embedding_dimension()
        if not reported:
            raise RuntimeError(
                f"Embedding model '{self._model_name}' did not report a dimension."
            )
        self.dimension = int(reported)

    def encode(self, text: str):
        vector = self._model.encode(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
        )
        array = self._np.asarray(vector, dtype=self._np.float32)
        if array.ndim != 1:
            array = array.reshape(-1)
        if int(array.shape[0]) != self.dimension:
            raise RuntimeError(
                f"Neuromem embedder dimension mismatch: returned {array.shape[0]}, expected {self.dimension}."
            )
        return array


class LocalKnowledgeStore:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._base_dir = settings.knowledge_base_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._documents: dict[str, KnowledgeDocumentRecord] = {}
        self._backend = settings.knowledge_backend.lower()
        self._sagevdb = None
        self._neuromem_collection = None
        raw_index_type = (settings.neuromem_index_type or "auto").strip().lower()
        if raw_index_type == "auto":
            # Prefer faiss (dense retrieval) when sentence-transformers is available;
            # fall back to segment otherwise because current neuromem builds do not
            # always ship a bm25 index implementation.
            try:
                import sentence_transformers  # noqa: F401
                raw_index_type = "faiss"
            except ImportError:
                raw_index_type = "segment"
        self._neuromem_index_type = raw_index_type
        self._neuromem_embedder = None
        self._np = None
        self._text_embedder = None
        self._document_id_to_vector_id: dict[str, int] = {}
        self._search_cache: OrderedDict[
            str, tuple[float, list[KnowledgeSearchHit]]
        ] = OrderedDict()
        self._search_cache_lock = threading.Lock()
        # Wiki-link retrieval: adjacency list mapping document_id → list of
        # linked document_ids.  Built from ``metadata["linked_source_names"]``
        # at load time.  See wiki-link-retrieval repo for research context.
        self._link_graph: dict[str, list[str]] = {}
        self._link_expansion_enabled = True
        self._load_documents_from_disk()
        self._rebuild_link_graph()
        if self._backend == "sagevdb":
            self._initialize_sagevdb()
        elif self._backend == "neuromem":
            self._initialize_neuromem()

    def add_document(
        self,
        payload: KnowledgeDocumentCreate,
        *,
        rebuild_indexes: bool = True,
    ) -> KnowledgeDocumentRecord:
        record = KnowledgeDocumentRecord(
            document_id=str(uuid4()),
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            source_name=payload.source_name,
            metadata=_normalize_knowledge_metadata(payload),
            created_at=datetime.now(UTC),
        )
        target_path = self._base_dir / f"{record.document_id}.json"
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._base_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(record.model_dump_json(indent=2), encoding="utf-8")
        self._documents[record.document_id] = record
        if self._backend == "sagevdb":
            self._add_to_sagevdb(record, rebuild_index=rebuild_indexes)
        elif self._backend == "neuromem" and rebuild_indexes:
            self._add_to_neuromem(record)
        self._rebuild_link_graph()
        self._clear_search_cache()
        return record

    def upsert_document(
        self,
        payload: KnowledgeDocumentCreate,
        *,
        rebuild_indexes: bool = True,
    ) -> tuple[KnowledgeDocumentRecord, bool]:
        existing = self._find_document_by_source_name(payload.source_name)
        if existing is None:
            return self.add_document(payload, rebuild_indexes=rebuild_indexes), True

        normalized_metadata = _normalize_knowledge_metadata(payload)
        if (
            existing.title == payload.title
            and existing.content == payload.content
            and existing.tags == payload.tags
            and existing.source_name == payload.source_name
            and existing.metadata == normalized_metadata
        ):
            return existing, False

        updated_record = KnowledgeDocumentRecord(
            document_id=existing.document_id,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            source_name=payload.source_name,
            metadata=normalized_metadata,
            created_at=existing.created_at,
        )
        target_path = self._base_dir / f"{updated_record.document_id}.json"
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._base_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated_record.model_dump_json(indent=2), encoding="utf-8")
        self._documents[updated_record.document_id] = updated_record
        self._remove_duplicate_documents_for_source(
            payload.source_name,
            keep_document_id=updated_record.document_id,
        )
        if rebuild_indexes:
            self._rebuild_backend_indexes()
        self._rebuild_link_graph()
        self._clear_search_cache()
        return updated_record, False

    def update_document(
        self,
        document_id: str,
        payload: KnowledgeDocumentCreate,
        *,
        rebuild_indexes: bool = True,
    ) -> KnowledgeDocumentRecord:
        existing = self._documents.get(document_id)
        if existing is None:
            raise KeyError(document_id)

        updated_record = KnowledgeDocumentRecord(
            document_id=existing.document_id,
            title=payload.title,
            content=payload.content,
            tags=payload.tags,
            source_name=payload.source_name,
            metadata=_normalize_knowledge_metadata(payload),
            created_at=existing.created_at,
        )
        target_path = self._base_dir / f"{updated_record.document_id}.json"
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._base_dir.mkdir(parents=True, exist_ok=True)
        target_path.write_text(updated_record.model_dump_json(indent=2), encoding="utf-8")
        self._documents[updated_record.document_id] = updated_record
        self._remove_duplicate_documents_for_source(
            payload.source_name,
            keep_document_id=updated_record.document_id,
            persist=True,
        )
        if rebuild_indexes:
            self._rebuild_backend_indexes()
        self._rebuild_link_graph()
        self._clear_search_cache()
        return updated_record

    def list_documents(self) -> list[KnowledgeDocumentRecord]:
        return sorted(self._documents.values(), key=lambda item: item.created_at)

    def get_document(self, document_id: str) -> KnowledgeDocumentRecord | None:
        return self._documents.get(document_id)

    def delete_documents(
        self,
        document_ids: list[str],
        *,
        rebuild_indexes: bool = True,
    ) -> int:
        removed = 0
        for document_id in {item for item in document_ids if item}:
            record = self._documents.pop(document_id, None)
            if record is None:
                continue
            target_path = self._base_dir / f"{document_id}.json"
            if target_path.exists():
                target_path.unlink()
            self._document_id_to_vector_id.pop(document_id, None)
            removed += 1

        if removed and rebuild_indexes:
            self._rebuild_backend_indexes()
        if removed:
            self._rebuild_link_graph()
            self._clear_search_cache()
        return removed

    def rebuild_indexes(self) -> None:
        self._rebuild_backend_indexes()
        self._clear_search_cache()

    def search(
        self,
        query: str,
        top_k: int | None = None,
        *,
        visitor_profile: str | None = None,
        admin_role: str | None = None,
    ) -> list[KnowledgeSearchHit]:
        cache_key = self._search_cache_key(query, top_k, visitor_profile, admin_role)
        cached = self._get_cached_search(cache_key)
        if cached is not None:
            return cached

        if self._backend == "sagevdb":
            hits = self._search_sagevdb(
                query,
                top_k,
                visitor_profile=visitor_profile,
                admin_role=admin_role,
            )
            self._store_cached_search(cache_key, hits)
            return hits
        if self._backend == "neuromem":
            hits = self._search_neuromem(
                query,
                top_k,
                visitor_profile=visitor_profile,
                admin_role=admin_role,
            )
            self._store_cached_search(cache_key, hits)
            return hits

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        query_profile = _build_query_profile(
            query,
            visitor_profile=visitor_profile,
            admin_role=admin_role,
        )

        scored_hits: list[KnowledgeSearchHit] = []
        for document in self.list_documents():
            if not _document_is_visible_to_requester(
                document,
                query_profile.visitor_profile,
                query_profile.admin_role,
            ):
                continue
            score = self._score_document(document, query_tokens, query_profile)
            if score <= 0:
                continue
            scored_hits.append(
                KnowledgeSearchHit(
                    document_id=document.document_id,
                    title=document.title,
                    excerpt=self._build_excerpt(document.content, query_tokens),
                    score=score,
                    tags=document.tags,
                    source_name=document.source_name,
                    metadata=document.metadata,
                )
            )

        limit = top_k or self._settings.retrieval_top_k
        reranked = sorted(scored_hits, key=lambda item: item.score, reverse=True)
        # Wiki-link retrieval: 1-hop expansion after reranking.
        if self._link_expansion_enabled:
            reranked = self._expand_hits_with_links(reranked, query_tokens, query_profile)
            reranked.sort(key=lambda item: item.score, reverse=True)
        hits = self._finalize_hits(reranked, query_profile, limit)
        self._store_cached_search(cache_key, hits)
        return hits

    def _search_cache_key(
        self,
        query: str,
        top_k: int | None,
        visitor_profile: str | None,
        admin_role: str | None,
    ) -> str:
        normalized_query = " ".join(query.split())
        return "\n".join(
            (
                self._backend,
                str(top_k or self._settings.retrieval_top_k),
                visitor_profile or "",
                admin_role or "",
                normalized_query,
            )
        )

    def _get_cached_search(self, cache_key: str) -> list[KnowledgeSearchHit] | None:
        ttl_seconds = int(self._settings.knowledge_search_cache_ttl_seconds)
        max_entries = int(self._settings.knowledge_search_cache_max_entries)
        if ttl_seconds <= 0 or max_entries <= 0:
            return None

        now = time.time()
        with self._search_cache_lock:
            self._evict_expired_search_cache_locked(now)
            cached = self._search_cache.get(cache_key)
            if cached is None:
                return None
            expires_at, hits = cached
            if expires_at <= now:
                self._search_cache.pop(cache_key, None)
                return None
            self._search_cache.move_to_end(cache_key)
            return [hit.model_copy(deep=True) for hit in hits]

    def _store_cached_search(self, cache_key: str, hits: list[KnowledgeSearchHit]) -> None:
        ttl_seconds = int(self._settings.knowledge_search_cache_ttl_seconds)
        max_entries = int(self._settings.knowledge_search_cache_max_entries)
        if ttl_seconds <= 0 or max_entries <= 0:
            return

        now = time.time()
        with self._search_cache_lock:
            self._evict_expired_search_cache_locked(now)
            self._search_cache[cache_key] = (
                now + ttl_seconds,
                [hit.model_copy(deep=True) for hit in hits],
            )
            self._search_cache.move_to_end(cache_key)
            while len(self._search_cache) > max_entries:
                self._search_cache.popitem(last=False)

    def _evict_expired_search_cache_locked(self, now: float) -> None:
        expired_keys = [
            key for key, (expires_at, _) in self._search_cache.items() if expires_at <= now
        ]
        for key in expired_keys:
            self._search_cache.pop(key, None)

    def _clear_search_cache(self) -> None:
        with self._search_cache_lock:
            self._search_cache.clear()

    def count_documents(self) -> int:
        return len(self._documents)

    def indexed_document_count(self) -> int:
        """Return how many documents are represented by the active search index."""

        if self._backend == "sagevdb":
            return len(self._document_id_to_vector_id)
        if self._backend == "neuromem":
            if self._neuromem_collection is None:
                return 0
            search_index = self._neuromem_collection.indexes.get("search")
            if search_index is None:
                return 0
            for attribute in ("data_to_segment", "id_to_idx", "data_ids"):
                entries = getattr(search_index, attribute, None)
                if entries is not None:
                    try:
                        return len(entries)
                    except TypeError:
                        continue
            return 0
        return self.count_documents()

    def index_is_complete(self) -> bool:
        return self.indexed_document_count() == self.count_documents()

    def backend_name(self) -> str:
        return self._backend

    def _load_documents_from_disk(self) -> None:
        for path in sorted(self._base_dir.glob("*.json")):
            document = KnowledgeDocumentRecord.model_validate_json(path.read_text(encoding="utf-8"))
            if not document.metadata:
                document = document.model_copy(
                    update={
                        "metadata": _infer_knowledge_metadata(
                            title=document.title,
                            tags=document.tags,
                            source_name=document.source_name,
                        )
                    }
                )
            self._documents[document.document_id] = document
        self._deduplicate_loaded_documents()

    def _find_document_by_source_name(
        self, source_name: str | None
    ) -> KnowledgeDocumentRecord | None:
        if not source_name:
            return None
        matches = self._find_documents_by_source_name(source_name)
        return matches[0] if matches else None

    def _find_documents_by_source_name(
        self, source_name: str | None
    ) -> list[KnowledgeDocumentRecord]:
        if not source_name:
            return []
        matches = [
            document for document in self._documents.values() if document.source_name == source_name
        ]
        return sorted(matches, key=lambda item: item.created_at, reverse=True)

    def _deduplicate_loaded_documents(self) -> None:
        source_names = {
            document.source_name for document in self._documents.values() if document.source_name
        }
        for source_name in source_names:
            self._remove_duplicate_documents_for_source(source_name, persist=True)

    def _remove_duplicate_documents_for_source(
        self,
        source_name: str | None,
        *,
        keep_document_id: str | None = None,
        persist: bool = False,
    ) -> int:
        matches = self._find_documents_by_source_name(source_name)
        if len(matches) <= 1:
            return 0

        canonical_document_id = keep_document_id or matches[0].document_id
        removed = 0
        for document in matches:
            if document.document_id == canonical_document_id:
                continue
            self._documents.pop(document.document_id, None)
            target_path = self._base_dir / f"{document.document_id}.json"
            if persist and target_path.exists():
                target_path.unlink()
            removed += 1
        return removed

    def _rebuild_backend_indexes(self) -> None:
        if self._backend == "sagevdb":
            self._initialize_sagevdb()
        elif self._backend == "neuromem":
            self._initialize_neuromem()

    def _normalize_sagevdb_backend(self) -> str:
        return self._settings.sagevdb_backend.strip().lower().replace("_", "-")

    def _uses_sagevdb_anns_backend(self) -> bool:
        return self._normalize_sagevdb_backend() in {"sage-anns", "sageanns", "anns"}

    def _document_metadata(self, document: KnowledgeDocumentRecord) -> dict[str, str]:
        return {
            "document_id": document.document_id,
            "title": document.title,
            "tags": "|".join(document.tags),
            "source_name": document.source_name or "",
        }

    def _initialize_sagevdb(self) -> None:
        try:
            import numpy as np
            from sagevdb import DatabaseConfig, DistanceMetric, IndexType, create_database
        except ImportError as exc:
            raise RuntimeError(
                "DIGITAL_TWIN_KNOWLEDGE_BACKEND is set to 'sagevdb' but sagevdb is not available. "
                "Install isage-vdb or expose the local sageVDB checkout on PYTHONPATH."
            ) from exc

        self._np = np
        self._text_embedder = self._build_text_embedder(np)
        cfg = DatabaseConfig(int(self._text_embedder.dimension))
        backend = self._normalize_sagevdb_backend()
        cfg.metric = (
            DistanceMetric.INNER_PRODUCT
            if self._uses_sagevdb_anns_backend()
            else DistanceMetric.COSINE
        )
        cfg.index_type = IndexType.FLAT

        database_kwargs = {"backend": backend}
        if self._uses_sagevdb_anns_backend():
            algorithm = self._settings.sagevdb_anns_algorithm.strip()
            if not algorithm:
                raise RuntimeError(
                    "DIGITAL_TWIN_SAGEVDB_BACKEND is set to 'sage-anns' but "
                    "DIGITAL_TWIN_SAGEVDB_ANNS_ALGORITHM is empty."
                )
            database_kwargs["algorithm"] = algorithm

        try:
            self._sagevdb = create_database(cfg, **database_kwargs)
        except ImportError as exc:
            if self._uses_sagevdb_anns_backend():
                raise RuntimeError(
                    "DIGITAL_TWIN_SAGEVDB_BACKEND is set to 'sage-anns' but isage-anns is not available. "
                    "Install with: python -m pip install -e .[vdb-anns]"
                ) from exc
            raise
        except ValueError as exc:
            raise RuntimeError(f"Failed to initialize sagevdb backend '{backend}': {exc}") from exc

        self._document_id_to_vector_id.clear()
        if self._uses_sagevdb_anns_backend():
            self._rebuild_sagevdb_anns_index()
            return

        for document in self.list_documents():
            self._add_to_sagevdb(document, rebuild_index=False)
        self._sagevdb.build_index()

    def embedding_backend_name(self) -> str:
        if self._backend != "sagevdb":
            if self._backend == "neuromem":
                if self._neuromem_index_type == "faiss":
                    return f"faiss:{self._settings.neuromem_embedding_model}"
                return self._neuromem_index_type
            return "none"
        return self._settings.sagevdb_embedding_backend.lower()

    def _initialize_neuromem(self) -> None:
        try:
            from sage.neuromem import UnifiedCollection
        except ImportError as exc:
            raise RuntimeError(
                "DIGITAL_TWIN_KNOWLEDGE_BACKEND is set to 'neuromem' but isage-neuromem is not available. "
                "Install with: python -m pip install -e ."
            ) from exc

        collection_name = f"{self._base_dir.name}-owner-materials"
        self._neuromem_collection = UnifiedCollection(collection_name)
        if self._neuromem_index_type == "faiss":
            try:
                import numpy as np
            except ImportError as exc:
                raise RuntimeError(
                    "neuromem 'faiss' index requires numpy."
                ) from exc
            self._np = np
            self._neuromem_embedder = NeuromemBgeEmbedder(self._settings, np)
            dim = int(self._neuromem_embedder.dimension)
            self._neuromem_collection.add_index(
                "search",
                "faiss",
                {"dim": dim, "metric": "cosine"},
            )
            self._batch_index_documents_with_faiss()
            return

        self._neuromem_collection.add_index(
            "search",
            self._neuromem_index_type,
            (
                {"backend": "numpy", "csc_backend": "numpy"}
                if self._neuromem_index_type == "bm25"
                else {}
            ),
        )
        if self._build_neuromem_search_index_batch():
            return
        for document in self.list_documents():
            self._add_to_neuromem(document)

    def _batch_index_documents_with_faiss(self) -> None:
        """Encode all documents in a single batch and add them to the FAISS index.

        Sequential per-document encoding is ~50x slower than batched encode
        when the model runs on CPU, so we precompute every vector up-front and
        feed each (data_id, text, metadata-with-vector) triple to the index.
        """
        if (
            self._neuromem_collection is None
            or self._neuromem_embedder is None
        ):
            return
        documents = self.list_documents()
        if not documents:
            return
        texts = [
            self._expand_retrieval_text(self._compose_retrieval_text(document))
            for document in documents
        ]
        # Single batched forward pass; sentence-transformers picks a sensible
        # default batch size internally.
        batch_vectors = self._neuromem_embedder._model.encode(
            texts,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        np = self._np
        for document, text, vector in zip(documents, texts, batch_vectors):
            array = np.asarray(vector, dtype=np.float32).reshape(-1)
            metadata: dict[str, object] = {
                "document_id": document.document_id,
                "title": document.title,
                "tags": document.tags,
                "source_name": document.source_name or "",
                "vector": array.tolist(),
            }
            self._neuromem_collection.insert(text, metadata, index_names=["search"])

    def _build_neuromem_search_index_batch(self) -> bool:
        if self._neuromem_collection is None:
            return False

        search_index = self._neuromem_collection.indexes.get("search")
        if search_index is None or not hasattr(search_index, "_rebuild_index"):
            return False

        texts: list[str] = []
        data_ids: list[str] = []
        for document in self.list_documents():
            text = self._expand_retrieval_text(self._compose_retrieval_text(document))
            data_id = self._neuromem_collection.insert(
                text,
                {
                    "document_id": document.document_id,
                    "title": document.title,
                    "tags": document.tags,
                    "source_name": document.source_name or "",
                },
                index_names=[],
            )
            texts.append(text)
            data_ids.append(data_id)

        search_index.texts = texts
        search_index.id_to_idx = {data_id: index for index, data_id in enumerate(data_ids)}
        search_index.idx_to_id = {index: data_id for index, data_id in enumerate(data_ids)}
        search_index._rebuild_index()
        return True

    def _add_to_neuromem(self, document: KnowledgeDocumentRecord) -> None:
        if self._neuromem_collection is None:
            return

        text = self._expand_retrieval_text(self._compose_retrieval_text(document))
        metadata: dict[str, object] = {
            "document_id": document.document_id,
            "title": document.title,
            "tags": document.tags,
            "source_name": document.source_name or "",
        }
        if self._neuromem_index_type == "faiss" and self._neuromem_embedder is not None:
            metadata["vector"] = self._neuromem_embedder.encode(text).tolist()
        self._neuromem_collection.insert(
            text,
            metadata,
            index_names=["search"],
        )

    def _search_neuromem(
        self,
        query: str,
        top_k: int | None = None,
        *,
        visitor_profile: str | None = None,
        admin_role: str | None = None,
    ) -> list[KnowledgeSearchHit]:
        if self._neuromem_collection is None or not self._documents:
            return []

        limit = top_k or self._settings.retrieval_top_k
        expanded_query = self._expand_retrieval_text(query)
        if self._neuromem_index_type == "faiss" and self._neuromem_embedder is not None:
            query_payload = self._neuromem_embedder.encode(expanded_query).tolist()
        else:
            query_payload = expanded_query
        results = self._neuromem_collection.retrieve(
            "search", query_payload, top_k=max(limit * 5, limit + 8)
        )
        query_tokens = self._tokenize(query)
        query_profile = _build_query_profile(
            query,
            visitor_profile=visitor_profile,
            admin_role=admin_role,
        )

        hits: list[KnowledgeSearchHit] = []
        seen_document_ids: set[str] = set()
        for rank, result in enumerate(results, start=1):
            metadata = dict(result.get("metadata") or {})
            document_id = metadata.get("document_id")
            if not document_id:
                continue
            document = self._documents.get(str(document_id))
            if document is None:
                continue
            if not _document_is_visible_to_requester(
                document,
                query_profile.visitor_profile,
                query_profile.admin_role,
            ):
                continue
            if document.document_id in seen_document_ids:
                continue
            seen_document_ids.add(document.document_id)
            hits.append(
                KnowledgeSearchHit(
                    document_id=document.document_id,
                    title=document.title,
                    excerpt=self._build_excerpt(document.content, query_tokens),
                    score=1.0 / float(rank),
                    tags=document.tags,
                    source_name=document.source_name,
                    metadata=document.metadata,
                )
            )

        lexical_hits: list[KnowledgeSearchHit] = []
        for document in self.list_documents():
            if not _document_is_visible_to_requester(
                document,
                query_profile.visitor_profile,
                query_profile.admin_role,
            ):
                continue
            if document.document_id in seen_document_ids:
                continue
            lexical_score = self._score_document(document, query_tokens, query_profile)
            if lexical_score <= 0:
                continue
            lexical_hits.append(
                KnowledgeSearchHit(
                    document_id=document.document_id,
                    title=document.title,
                    excerpt=self._build_excerpt(document.content, query_tokens),
                    score=lexical_score,
                    tags=document.tags,
                    source_name=document.source_name,
                    metadata=document.metadata,
                )
            )
        hits.extend(
            sorted(lexical_hits, key=lambda item: item.score, reverse=True)[
                : max(limit * 3, limit + 5)
            ]
        )

        reranked = sorted(
            hits,
            key=lambda item: (
                self._score_document(
                    self._documents[item.document_id], query_tokens, query_profile
                ),
                item.score,
            ),
            reverse=True,
        )
        # Wiki-link retrieval: 1-hop expansion after reranking.
        if self._link_expansion_enabled:
            reranked = self._expand_hits_with_links(reranked, query_tokens, query_profile)
            reranked.sort(key=lambda item: item.score, reverse=True)
        return self._finalize_hits(reranked, query_profile, limit)

    def _build_text_embedder(self, np_module):
        embedding_backend = self._settings.sagevdb_embedding_backend.lower()
        if embedding_backend == "hash":
            return HashingTextEmbedder(self._settings, np_module)
        if embedding_backend == "sentence-transformers":
            return SentenceTransformerTextEmbedder(self._settings, np_module)
        raise RuntimeError(
            "Unsupported sagevdb embedding backend. Use 'sentence-transformers' or 'hash'."
        )

    def _rebuild_sagevdb_anns_index(self) -> None:
        if self._sagevdb is None or self._np is None:
            return

        documents = self.list_documents()
        if not documents:
            return

        vectors = []
        metadata_batch = []
        for vector_id, document in enumerate(documents):
            vectors.append(self._embed_text(self._compose_retrieval_text(document)))
            metadata_batch.append(self._document_metadata(document))
            self._document_id_to_vector_id[document.document_id] = int(vector_id)

        self._sagevdb.build_index(self._np.stack(vectors), metadata=metadata_batch)

    def _add_to_sagevdb(
        self, document: KnowledgeDocumentRecord, rebuild_index: bool = True
    ) -> None:
        if self._sagevdb is None:
            return

        if self._uses_sagevdb_anns_backend():
            if rebuild_index:
                self._initialize_sagevdb()
            return

        vector = self._embed_text(self._compose_retrieval_text(document))
        vector_id = self._sagevdb.add(vector.tolist())
        self._document_id_to_vector_id[document.document_id] = int(vector_id)
        self._sagevdb.set_metadata(int(vector_id), self._document_metadata(document))
        if rebuild_index:
            self._sagevdb.build_index()

    def _search_sagevdb(
        self,
        query: str,
        top_k: int | None = None,
        *,
        visitor_profile: str | None = None,
        admin_role: str | None = None,
    ) -> list[KnowledgeSearchHit]:
        if self._sagevdb is None or self._np is None or not self._documents:
            return []

        limit = top_k or self._settings.retrieval_top_k
        query_vector = self._embed_text(query)
        if self._uses_sagevdb_anns_backend():
            results = self._sagevdb.search(
                query_vector,
                k=max(self.count_documents(), 1),
                include_metadata=True,
            )
        else:
            from sagevdb import SearchParams, search_numpy

            results = search_numpy(
                self._sagevdb,
                query_vector,
                SearchParams(k=max(self._sagevdb.size(), 1)),
            )

        query_tokens = self._tokenize(query)
        query_profile = _build_query_profile(
            query,
            visitor_profile=visitor_profile,
            admin_role=admin_role,
        )
        hits: list[KnowledgeSearchHit] = []
        for result in results:
            if self._uses_sagevdb_anns_backend():
                metadata = dict(getattr(result, "metadata", {}) or {})
            else:
                metadata = dict(self._sagevdb.get_metadata(int(result.id)))
            document_id = metadata.get("document_id")
            if not document_id:
                continue
            document = self._documents.get(str(document_id))
            if document is None:
                continue
            if not _document_is_visible_to_requester(
                document, query_profile.visitor_profile, query_profile.admin_role
            ):
                continue
            # Re-rank using the same token-overlap + tag-boost scoring as
            # the local backend.  sagevdb provides candidate recall; precise
            # ordering is delegated to _score_document which handles Chinese
            # text and tag relevance far better than hash cosine similarity.
            rerank_score = self._score_document(document, query_tokens, query_profile)
            if rerank_score <= 0:
                continue
            hits.append(
                KnowledgeSearchHit(
                    document_id=document.document_id,
                    title=document.title,
                    excerpt=self._build_excerpt(document.content, query_tokens),
                    score=rerank_score,
                    tags=document.tags,
                    source_name=document.source_name,
                    metadata=document.metadata,
                )
            )
        hits.sort(key=lambda h: h.score, reverse=True)
        # Wiki-link retrieval: 1-hop expansion after reranking.
        if self._link_expansion_enabled:
            hits = self._expand_hits_with_links(hits, query_tokens, query_profile)
            hits.sort(key=lambda h: h.score, reverse=True)
        return self._finalize_hits(hits, query_profile, limit)

    def _score_document(
        self,
        document: KnowledgeDocumentRecord,
        query_tokens: set[str],
        query_profile: "QueryProfile",
    ) -> float:
        tag_tokens = {tag.lower() for tag in document.tags}

        title_overlap = _span_overlap_score(document.title, query_tokens)
        content_overlap = _span_overlap_score(document.content, query_tokens)
        tag_overlap = len(query_tokens & tag_tokens)

        base_score = float((title_overlap * 3) + (tag_overlap * 2) + content_overlap)
        raw = (
            base_score
            + self._document_intent_boost(document, query_profile)
            + self._document_visitor_profile_boost(document, query_profile)
            + self._document_course_scope_boost(document, query_profile)
            + self._document_feedback_web_review_boost(document, query_profile)
        )
        if raw < 1.5 and query_profile.topic_domains & frozenset({"research", "general"}):
            if tag_tokens & {"profile", "overview"}:
                return max(raw, 1.5)
        return raw

    def _document_feedback_web_review_boost(
        self,
        document: KnowledgeDocumentRecord,
        query_profile: "QueryProfile",
    ) -> float:
        if query_profile.admin_role:
            return 0.0

        source_name = str(document.source_name or "").lower()
        tags = {str(tag).lower() for tag in document.tags}
        if not (source_name.startswith("feedback-web:") or "feedback-web" in tags):
            return 0.0

        review_status = str(document.review_status or "pending").strip().lower()
        if review_status == "approved":
            return 0.0
        if review_status == "stale":
            return -8.5
        return -4.5

    def _document_course_scope_boost(
        self,
        document: KnowledgeDocumentRecord,
        query_profile: "QueryProfile",
    ) -> float:
        if not query_profile.course_ids:
            return 0.0
        document_course_ids = _document_course_ids(document)
        if not document_course_ids:
            return 0.0
        if document_course_ids & query_profile.course_ids:
            return 10.0
        return -9.0

    def _document_visitor_profile_boost(
        self,
        document: KnowledgeDocumentRecord,
        query_profile: "QueryProfile",
    ) -> float:
        visitor_profile = query_profile.visitor_profile
        if not visitor_profile:
            return 0.0

        document_tags = {tag.lower() for tag in document.tags}
        prefers_teaching = bool(
            query_profile.document_types or "teaching" in query_profile.topic_domains
        )
        prefers_research = bool(
            query_profile.research_focus or "research" in query_profile.topic_domains
        )
        prefers_meeting = "meeting" in query_profile.topic_domains

        if visitor_profile == "hust_undergraduate":
            if document_tags & _TEACHING_RELATED_TAGS:
                boost = 5.0
                if document_tags & {"lecture", "tutorial", "experiment"}:
                    boost += 2.0
                return boost
            if not prefers_research and document_tags & _RESEARCH_DOCUMENT_TAGS:
                return -2.0
            return 0.0

        if visitor_profile == "paper_writing_student":
            if _is_paper_writing_document(document):
                return 8.0
            if document_tags & _TEACHING_RELATED_TAGS:
                return 3.0
            if not prefers_research and document_tags & _RESEARCH_DOCUMENT_TAGS:
                return -3.0
            return 0.0

        if visitor_profile == "lab_member":
            boost = 0.0
            if document_tags & (_RESEARCH_DOCUMENT_TAGS | _MEETING_DOCUMENT_TAGS):
                boost += 4.5
            if document_tags & {"profile", "overview"}:
                boost += 1.5
            if (
                not prefers_teaching
                and not prefers_meeting
                and document_tags & _TEACHING_RELATED_TAGS
            ):
                boost -= 2.5
            return boost

        if visitor_profile == "general_visitor":
            if document_tags & {"profile", "overview"}:
                return 4.0
            if not prefers_teaching and document_tags & _RESEARCH_DOCUMENT_TAGS:
                return 2.0
            if (
                not prefers_teaching
                and not prefers_research
                and not prefers_meeting
                and document_tags & _TEACHING_RELATED_TAGS
            ):
                return -2.5

        return 0.0

    def _build_excerpt(self, content: str, query_tokens: set[str]) -> str:
        compact = " ".join(content.split())
        lowercase = compact.lower()
        hit_index = min(
            (lowercase.find(token) for token in query_tokens if token in lowercase), default=-1
        )
        if hit_index == -1:
            return compact[:220]
        start = max(hit_index - 60, 0)
        end = min(hit_index + 160, len(compact))
        return compact[start:end]

    def _tokenize(self, text: str) -> set[str]:
        return _tokenize_text(_expand_query_synonyms(text))

    def _compose_retrieval_text(self, document: KnowledgeDocumentRecord) -> str:
        title = ((document.title + " ") * 3).strip()
        tags = " ".join(document.tags + document.tags)
        tag_aliases = " ".join(_expand_document_aliases(document))
        metadata_text = " ".join(str(value) for value in document.metadata.values())
        source_tokens = _normalize_retrieval_text(document.source_name or "")
        return f"{title} {tags} {tag_aliases} {metadata_text} {source_tokens} {document.content}".strip()

    def _document_intent_boost(
        self,
        document: KnowledgeDocumentRecord,
        query_profile: "QueryProfile",
    ) -> float:
        if (
            not query_profile.document_types
            and not query_profile.ordinal_numbers
            and not query_profile.topic_domains
        ):
            return 0.0

        document_tags = {tag.lower() for tag in document.tags}
        document_types = {tag for tag in document_tags if tag in _TEACHING_DOCUMENT_TYPES}
        boost = 0.0
        if query_profile.document_types:
            if document_types & query_profile.document_types:
                boost += 6.0
            elif document_types:
                boost -= 5.0
            else:
                boost -= 2.0

        if query_profile.ordinal_numbers:
            haystacks = [document.title, document.source_name or "", document.content[:400]]
            if any(
                _document_mentions_ordinal(haystack, query_profile.ordinal_numbers)
                for haystack in haystacks
            ):
                boost += 4.0
                if document_types & query_profile.document_types:
                    boost += 2.0
            elif query_profile.document_types or document_types:
                boost -= 2.0

        if "research" in query_profile.topic_domains:
            if document_tags & _RESEARCH_DOCUMENT_TAGS:
                boost += 8.0
            elif document_tags & _TEACHING_RELATED_TAGS:
                boost -= 8.0

        if query_profile.research_focus == "paper":
            if "paper-digest" in document_tags:
                boost += 7.0
            elif "overview" in document_tags:
                boost += 3.0
            elif "profile" in document_tags:
                boost -= 3.0
        elif query_profile.research_focus == "overview":
            if "overview" in document_tags:
                boost += 6.0
            elif "paper-digest" in document_tags:
                boost += 2.0
            elif "profile" in document_tags:
                boost += 1.0

        if "meeting" in query_profile.topic_domains:
            if document_tags & _MEETING_DOCUMENT_TAGS:
                boost += 8.0
            elif document_tags & _TEACHING_RELATED_TAGS:
                boost -= 8.0

        if "teaching" in query_profile.topic_domains:
            if document_tags & _TEACHING_RELATED_TAGS:
                boost += 3.0
            elif document_tags & _RESEARCH_DOCUMENT_TAGS:
                boost -= 2.0

        if query_profile.prefers_intro_parts and document_tags & _TEACHING_RELATED_TAGS:
            part_index = _extract_document_part_index(document)
            if part_index == 1:
                boost += 5.0
            elif part_index == 2:
                boost += 1.5
            elif part_index and part_index >= 3:
                boost -= min(4.0, float(part_index - 2) * 1.5)

        return boost

    def _finalize_hits(
        self,
        hits: list[KnowledgeSearchHit],
        query_profile: "QueryProfile",
        limit: int,
    ) -> list[KnowledgeSearchHit]:
        visible_hits = [
            hit
            for hit in hits
            if hit.document_id in self._documents
            and _document_is_visible_to_requester(
                self._documents[hit.document_id],
                query_profile.visitor_profile,
                query_profile.admin_role,
            )
        ]
        if not visible_hits:
            return []
        if not query_profile.document_types and not query_profile.topic_domains:
            return _dedupe_hits_by_source_group(visible_hits, limit)

        aligned_hits = [
            hit
            for hit in visible_hits
            if self._document_intent_boost(self._documents[hit.document_id], query_profile) >= 0.0
        ]
        if query_profile.ordinal_numbers and "teaching" in query_profile.topic_domains:
            ordinal_hits = [
                hit
                for hit in aligned_hits
                if _hit_matches_ordinal_query(
                    hit,
                    self._documents[hit.document_id],
                    query_profile.ordinal_numbers,
                    query_profile.document_types,
                )
            ]
            if ordinal_hits:
                aligned_hits = ordinal_hits
        if query_profile.course_ids and "teaching" in query_profile.topic_domains:
            course_hits = [
                hit
                for hit in aligned_hits
                if _document_course_ids(self._documents[hit.document_id]) & query_profile.course_ids
            ]
            if course_hits:
                aligned_hits = course_hits
        if query_profile.research_focus == "paper":
            paper_focused_hits = [
                hit
                for hit in aligned_hits
                if {tag.lower() for tag in hit.tags} & {"paper-digest", "overview", "publication"}
            ]
            if paper_focused_hits:
                aligned_hits = paper_focused_hits
            if query_profile.named_entities:
                entity_matched_hits = [
                    hit
                    for hit in aligned_hits
                    if _hit_matches_named_entities(
                        hit, self._documents[hit.document_id], query_profile.named_entities
                    )
                ]
                if entity_matched_hits:
                    aligned_hits = entity_matched_hits
        if aligned_hits:
            return _dedupe_hits_by_source_group(aligned_hits, limit)
        return _dedupe_hits_by_source_group(visible_hits, limit)

    # ── Wiki-link retrieval: link graph construction + expansion ────────

    # Tags that are too generic to signal topical relatedness.
    _GENERIC_TAGS: frozenset[str] = frozenset({
        "wiki", "homepage", "private-materials", "publication",
        "paper-list", "pdf-download", "news", "config",
    })
    # Tags that ARE meaningful for auto-linking across documents.
    _TOPIC_TAG_PREFIXES: frozenset[str] = frozenset({
        "inference", "vllm", "kv", "npu", "ascend", "sage", "neuromem",
        "rag", "batch", "quantiz", "specul", "prefix", "attention",
        "tokeniz", "fine-tun", "prompt", "distributed", "memory",
        "orca", "distserve", "ragcache", "flexgen", "powerinfer",
        "course:", "stream-", "multicore", "graph", "gpu", "llm",
        "transactional-", "compression", "approximate-", "continual-",
        "system", "cluster", "vamos", "paper-writing", "advising",
        "lecture:", "material:", "experiment", "tutorial",
    })
    # Manual cross-references: source_name → list of source_names to link.
    # Bridges non-wiki documents (lectures, project docs) into the wiki graph.
    _MANUAL_CROSS_REFS: dict[str, list[str]] = {
        "private-materials:lecture-ecnu-inference-infra": [
            "wiki:tech-notes/kv-cache-optimization",
            "wiki:tech-notes/npu-memory-management",
            "wiki:tech-notes/distributed-inference-patterns",
            "wiki:tutorials/llm-inference-basics",
            "wiki:tech-notes/continuous-batching-notes",
            "wiki:tech-notes/retrieval-augmented-generation",
        ],
        "private-materials:project-4-implementation-plan": [
            "wiki:tech-notes/kv-cache-optimization",
            "wiki:tech-notes/npu-memory-management",
            "wiki:tech-notes/distributed-inference-patterns",
            "wiki:tech-notes/continuous-batching-notes",
        ],
        "private-materials:industry-talk-inference": [
            "wiki:tech-notes/kv-cache-optimization",
            "wiki:tech-notes/npu-memory-management",
            "wiki:tech-notes/distributed-inference-patterns",
            "wiki:achievements/sage-system-overview",
        ],
        "private-materials:academic-presentation": [
            "wiki:tech-notes/kv-cache-optimization",
            "wiki:tech-notes/npu-memory-management",
            "wiki:tech-notes/retrieval-augmented-generation",
            "wiki:achievements/sage-system-overview",
        ],
    }
    # Concept keywords → related wiki pages.
    # Scanned against document title + source_name to create links even
    # for docs that have no topic tags at all (e.g. paper-page docs).
    _CONCEPT_TO_WIKI: dict[str, list[str]] = {
        "kv cache":       ["wiki:tech-notes/kv-cache-optimization", "wiki:tech-notes/kv"],
        "kv_cache":       ["wiki:tech-notes/kv-cache-optimization", "wiki:tech-notes/kv"],
        "kv-cache":       ["wiki:tech-notes/kv-cache-optimization", "wiki:tech-notes/kv"],
        "prefix caching": ["wiki:tech-notes/kv-cache-optimization", "wiki:tech-notes/kv"],
        "npu":            ["wiki:tech-notes/npu-memory-management", "wiki:tech-notes/npu", "wiki:tutorials/ascend-npu-setup"],
        "ascend":         ["wiki:tech-notes/npu-memory-management", "wiki:tech-notes/npu", "wiki:tutorials/ascend-npu-setup"],
        "continuous batching": ["wiki:tech-notes/continuous-batching-notes", "wiki:tech-notes/batch"],
        "batch scheduling":    ["wiki:tech-notes/continuous-batching-notes", "wiki:tech-notes/batch"],
        "distributed inference": ["wiki:tech-notes/distributed-inference-patterns"],
        "tensor parallel":  ["wiki:tech-notes/distributed-inference-patterns"],
        "pipeline parallel": ["wiki:tech-notes/distributed-inference-patterns"],
        "rag":              ["wiki:tech-notes/retrieval-augmented-generation"],
        "retrieval augmented": ["wiki:tech-notes/retrieval-augmented-generation"],
        "llm inference":    ["wiki:tutorials/llm-inference-basics", "wiki:tutorials/inference"],
        "llm serving":      ["wiki:tutorials/llm-inference-basics", "wiki:tutorials/inference"],
        "inference serving": ["wiki:tutorials/llm-inference-basics", "wiki:tutorials/inference", "wiki:resources/inference-benchmark-guide"],
        "inference engine": ["wiki:tutorials/llm-inference-basics", "wiki:tutorials/inference"],
        "sage":             ["wiki:achievements/sage-system-overview"],
        "prompt engineering": ["wiki:tutorials/prompt-engineering-guide", "wiki:industry-docs/prompt-engineering-cb-cli"],
        "benchmark":        ["wiki:resources/inference-benchmark-guide"],
        "quantiz":          ["wiki:tutorials/inference"],
        "speculative decoding": ["wiki:tutorials/inference"],
        "attention":        ["wiki:tutorials/llm-inference-basics"],
        "vllm":             ["wiki:tutorials/llm-inference-basics", "wiki:resources/tools-and-frameworks"],
        "gpu":              ["wiki:tutorials/inference"],
        "memory management": ["wiki:tech-notes/kv-cache-optimization", "wiki:tech-notes/npu-memory-management"],
        "oom":              ["wiki:tech-notes/npu-memory-management", "wiki:tech-notes/npu"],
    }

    def _is_topic_tag(self, tag: str) -> bool:
        """Return True if *tag* is meaningful enough for auto-linking."""
        lower = tag.lower()
        if lower in self._GENERIC_TAGS:
            return False
        if lower.startswith("audience:"):
            return False
        # Explicit topic prefixes
        for prefix in self._TOPIC_TAG_PREFIXES:
            if lower.startswith(prefix) or prefix in lower:
                return True
        # Wiki category tags
        if lower.startswith("wiki:"):
            return False
        return False

    def _rebuild_link_graph(self) -> None:
        """Build bidirectional adjacency list from document metadata.

        Four edge sources are combined:

        1. **Explicit wiki links** — ``metadata["linked_source_names"]``
           extracted at ingest time from markdown cross-references.
        2. **Manual cross-references** — ``_MANUAL_CROSS_REFS`` maps
           key non-wiki documents (lectures, project docs) to related
           wiki pages.
        3. **Tag-based auto-linking** — documents sharing ≥ 1 topic
           tag are linked, bridging the gap between isolated non-wiki
           content and the wiki link graph.
        4. **Concept-keyword linking** — scan document titles and
           source names for key concepts (KV Cache, NPU, RAG, …) and
           link matching documents to the corresponding wiki pages.
           This is the most important bridge for documents that have
           no topic tags (e.g. paper-page, homepage docs).

        Edges are bidirectional: if A links to B, both A→B and B→A are
        added so that expansion can reach documents that are only link
        targets (no outbound links of their own).
        """
        source_to_id: dict[str, str] = {
            doc.source_name: doc_id
            for doc_id, doc in self._documents.items()
            if doc.source_name
        }
        graph: dict[str, list[str]] = {}

        def _add_edge(src: str, dst: str, *, cap: int = 15) -> None:
            if src == dst:
                return
            adj = graph.get(src)
            if adj is None:
                graph[src] = [dst]
            elif dst not in adj and len(adj) < cap:
                adj.append(dst)

        # ── 1. Explicit wiki links ──────────────────────────────────
        for doc_id, doc in self._documents.items():
            linked_sources = (doc.metadata or {}).get("linked_source_names", "")
            if not linked_sources:
                continue
            if isinstance(linked_sources, list):
                source_values = (str(item) for item in linked_sources)
            else:
                source_values = str(linked_sources).split("|")
            for src_name in source_values:
                src_name = src_name.strip()
                if src_name and src_name in source_to_id:
                    target_id = source_to_id[src_name]
                    _add_edge(doc_id, target_id)
                    _add_edge(target_id, doc_id)

        # ── 2. Manual cross-references ──────────────────────────────
        for source_name, target_names in self._MANUAL_CROSS_REFS.items():
            src_id = source_to_id.get(source_name)
            if src_id is None:
                continue
            for target_name in target_names:
                dst_id = source_to_id.get(target_name)
                if dst_id is not None:
                    _add_edge(src_id, dst_id)
                    _add_edge(dst_id, src_id)

        # ── 3. Tag-based auto-linking ───────────────────────────────
        # Build inverted index: topic_tag → list of doc_ids
        tag_index: dict[str, list[str]] = {}
        for doc_id, doc in self._documents.items():
            topic_tags = {t for t in doc.tags if self._is_topic_tag(t)}
            for tag in topic_tags:
                tag_index.setdefault(tag, []).append(doc_id)

        # For each doc, find others sharing ≥ 1 topic tag
        for doc_id, doc in self._documents.items():
            doc_topic_tags = {t for t in doc.tags if self._is_topic_tag(t)}
            if len(doc_topic_tags) < 1:
                continue
            # Count shared tags with every candidate
            candidate_shared: dict[str, int] = {}
            for tag in doc_topic_tags:
                for other_id in tag_index.get(tag, []):
                    if other_id != doc_id:
                        candidate_shared[other_id] = candidate_shared.get(other_id, 0) + 1
            # Link to candidates sharing ≥ 1 topic tag
            for other_id, shared_count in sorted(
                candidate_shared.items(), key=lambda x: -x[1]
            ):
                if shared_count < 1:
                    break
                _add_edge(doc_id, other_id, cap=12)
                _add_edge(other_id, doc_id, cap=12)

        # ── 4. Concept-keyword linking ──────────────────────────────
        # Scan document title + source_name for concept keywords and
        # link to matching wiki pages.  This catches documents that have
        # no topic tags but whose titles clearly indicate the subject
        # (e.g. paper pages, lecture slides, project docs).
        for doc_id, doc in self._documents.items():
            if (doc.source_name or "").startswith("wiki:"):
                continue  # wiki pages already have explicit links
            haystack = f"{(doc.title or '')} {(doc.source_name or '')}".lower()
            for keyword, wiki_sources in self._CONCEPT_TO_WIKI.items():
                if keyword.lower() in haystack:
                    for wiki_src in wiki_sources:
                        wiki_id = source_to_id.get(wiki_src)
                        if wiki_id is not None:
                            _add_edge(doc_id, wiki_id, cap=12)
                            _add_edge(wiki_id, doc_id, cap=12)

        self._link_graph = graph

    def _expand_hits_with_links(
        self,
        hits: list[KnowledgeSearchHit],
        query_tokens: set[str],
        query_profile: "QueryProfile",
        *,
        max_expansion: int = 8,
    ) -> list[KnowledgeSearchHit]:
        """Post-retrieval 1-hop link expansion.

        For each hit in the result set, follow outgoing links in the
        adjacency list and inject linked documents that are not already
        present.  Expanded documents receive a fraction of the parent
        hit's score so they rank below the primary result but above
        unrelated documents.
        """
        if not self._link_graph or not hits:
            return hits

        seen_ids = {h.document_id for h in hits}
        expanded: list[KnowledgeSearchHit] = list(hits)

        for hit in hits:
            if len(expanded) - len(hits) >= max_expansion:
                break
            neighbors = self._link_graph.get(hit.document_id, [])
            for neighbor_id in neighbors:
                if neighbor_id in seen_ids:
                    continue
                if len(expanded) - len(hits) >= max_expansion:
                    break
                doc = self._documents.get(neighbor_id)
                if doc is None:
                    continue
                if not _document_is_visible_to_requester(
                    doc, query_profile.visitor_profile, query_profile.admin_role
                ):
                    continue
                # Linked docs get a fraction of the parent hit's score
                link_score = max(hit.score * 0.6, 1.5)
                expanded.append(
                    KnowledgeSearchHit(
                        document_id=doc.document_id,
                        title=doc.title,
                        excerpt=self._build_excerpt(doc.content, query_tokens),
                        score=link_score,
                        tags=doc.tags,
                        source_name=doc.source_name,
                        metadata=doc.metadata,
                    )
                )
                seen_ids.add(neighbor_id)

        return expanded

    def _expand_retrieval_text(self, text: str) -> str:
        tokens = sorted(self._tokenize(text))
        if not tokens:
            return text
        return f"{text} {' '.join(tokens)}".strip()

    def _embed_text(self, text: str):
        if self._np is None or self._text_embedder is None:
            raise RuntimeError("sagevdb backend is not initialized")
        return self._text_embedder.encode(text)


def _tokenize_text(text: str) -> set[str]:
    tokens: set[str] = {
        token.lower() for token in re.findall(r"[A-Za-z0-9_]+", text) if len(token) > 1
    }
    for span in re.findall(r"[\u4e00-\u9fff]+", text):
        if not span:
            continue
        tokens.add(span)
        if len(span) == 1:
            continue
        tokens.update(span)
        for width in (2, 3):
            if len(span) < width:
                continue
            for index in range(len(span) - width + 1):
                tokens.add(span[index : index + width])
    return tokens


def _span_overlap_score(text: str, query_tokens: set[str]) -> float:
    """Compute overlap score using maximal span deduplication.

    Instead of counting every overlapping n-gram token independently (which
    inflates scores for Chinese text due to abundant bigrams), this function
    identifies *maximal* matching spans and scores them by length squared,
    rewarding longer, more specific matches over short, generic ones.
    """
    # Collect all matching spans as (start, end) tuples.
    matching_spans: list[tuple[int, int]] = []

    for match in re.finditer(r"[A-Za-z0-9_]+", text):
        token = match.group().lower()
        if len(token) > 1 and token in query_tokens:
            matching_spans.append((match.start(), match.end()))

    for char_span in re.finditer(r"[\u4e00-\u9fff]+", text):
        s = char_span.group()
        base = char_span.start()
        for width in (3, 2, 1):
            if len(s) < width:
                continue
            for index in range(len(s) - width + 1):
                token = s[index : index + width]
                if token in query_tokens:
                    matching_spans.append((base + index, base + index + width))

    if not matching_spans:
        return 0.0

    # Sort by start position, then by length descending.
    matching_spans.sort(key=lambda span: (span[0], -(span[1] - span[0])))

    # Greedily select non-overlapping maximal spans.
    selected: list[tuple[int, int]] = []
    consumed_end = -1
    for start, end in matching_spans:
        if start >= consumed_end:
            selected.append((start, end))
            consumed_end = end

    # Score: length squared per span, but deduplicated by matched text.
    # A document that mentions "SAGE" 130 times is not 130× more relevant
    # than one that mentions it once — each unique matched token contributes
    # at most once.  This prevents repetition-heavy documents from dominating
    # relevance ranking and ensures index/reference documents that cover many
    # different query terms score competitively.
    seen_matched_text: set[str] = set()
    score = 0.0
    for start, end in selected:
        matched = text[start:end].lower()
        if matched not in seen_matched_text:
            score += (end - start) ** 2
            seen_matched_text.add(matched)
    return score


@dataclass(frozen=True)
class QueryProfile:
    document_types: frozenset[str]
    ordinal_numbers: frozenset[str]
    topic_domains: frozenset[str]
    course_ids: frozenset[str] = frozenset()
    research_focus: str | None = None
    prefers_intro_parts: bool = False
    named_entities: frozenset[str] = frozenset()
    visitor_profile: str | None = None
    admin_role: str | None = None


_TEACHING_DOCUMENT_TYPES = {"tutorial", "lecture", "experiment"}
_TEACHING_RELATED_TAGS = {
    "teaching",
    "courseware",
    "tutorial",
    "lecture",
    "experiment",
    "resources",
    "pdf",
}
_RESEARCH_DOCUMENT_TAGS = {"research", "publication", "paper-digest", "overview", "profile"}
_MEETING_DOCUMENT_TAGS = {"meeting", "preparation", "policy", "qa", "course"}
_VISITOR_PROFILE_ALLOWED_AUDIENCES = {
    None: frozenset({"public"}),
    "general_visitor": frozenset({"public"}),
    "hust_undergraduate": frozenset({"public", "undergraduate"}),
    "paper_writing_student": frozenset({"public", "graduate"}),
    "lab_member": frozenset({"public", "undergraduate", "graduate", "lab_member"}),
}
_ADMIN_ROLE_ALLOWED_AUDIENCES = {
    None: frozenset(),
    "manager": frozenset({"public", "undergraduate", "graduate", "lab_member", "manager"}),
    "super_admin": frozenset(
        {"public", "undergraduate", "graduate", "lab_member", "manager", "admin"}
    ),
}
_AUDIENCE_ALIASES = {
    "public": {"all", "any", "general", "guest", "open", "public", "visitor"},
    "undergraduate": {"hust_undergraduate", "ug", "undergrad", "undergraduate"},
    "graduate": {
        "grad",
        "graduate",
        "paper_writing",
        "paper_writing_student",
        "paper-writing-student",
        "postgraduate",
    },
    "lab_member": {"group", "internal", "lab_member", "lab-member", "member", "team"},
    "manager": {"manager", "management", "staff_manager"},
    "admin": {"admin", "administrator", "super_admin", "super-admin"},
}
_COURSE_ALIAS_MAP = {
    "llm-inference": (
        "大模型推理基础设施",
        "大模型推理",
        "推理基础设施",
        "推理系统",
        "llm inference",
        "inference engines",
        "inference infrastructure",
        "kv cache",
        "prefill",
        "decode",
    ),
    "paper-writing": (
        "研究生论文写作",
        "论文写作课程",
        "毕业论文",
        "发表高水平论文",
        "paper writing",
        "thesis writing",
    ),
    "database-lab": (
        "数据库实验课",
        "数据库实验",
        "数据库 lab",
        "database lab",
        "database experiment",
        "db lab",
    ),
}
_TEACHING_ALIAS_MAP = {
    "tutorial": ("tutorial", "教程", "习题", "练习", "workshop"),
    "lecture": ("lecture", "slides", "讲义", "课程讲解", "课件"),
    "experiment": ("experiment", "lab", "实验", "project", "项目"),
    "pdf": ("pdf", "课件正文", "讲义正文"),
    "research": ("research", "研究", "研究方向", "研究主题"),
    "publication": ("publication", "paper", "论文", "成果"),
    "paper-digest": ("paper digest", "论文提炼", "摘要"),
    "overview": ("overview", "研究主线", "研究总览"),
    "profile": ("profile", "主页资料", "个人简介"),
    "meeting": ("meeting", "预约", "office hour", "沟通"),
    "preparation": ("preparation", "准备", "提前准备", "材料"),
    "policy": ("policy", "建议", "要求", "规范"),
    "team-policy": ("课题组制度", "组内制度", "绩效", "报销", "执行规则"),
    "performance": ("绩效", "有效贡献", "贡献认定", "计入绩效", "自评"),
    "knowledge-boundary": ("知识边界", "可见性", "互联网公开", "对外发布"),
    "qa": ("qa", "答疑", "提问", "问题"),
    "course:llm-inference": (
        "大模型推理基础设施",
        "大模型推理",
        "推理系统",
        "kv cache",
        "prefill",
        "decode",
    ),
    "course:paper-writing": ("研究生论文写作", "论文写作", "毕业论文", "发表高水平论文"),
    "course:database-lab": (
        "数据库实验课",
        "数据库实验",
        "database lab",
        "database experiment",
        "db lab",
    ),
    "identity:teacher": ("主课老师", "教师", "课程"),
    "identity:pi": ("课题组", "负责人", "导师", "PI"),
    "domain:teaching": ("教学", "课程", "研究生课程"),
    "domain:research-group": ("课题组", "研究团队", "组内"),
    "material:lecture": ("lecture", "讲义", "第几讲"),
    "material:tutorial": ("tutorial", "教程", "习题"),
    "material:experiment": ("experiment", "实验", "项目"),
    "material:pdf": ("pdf", "课件正文"),
}


# -- Query-level synonym expansion ------------------------------------------
# Bidirectional synonym groups for core domain concepts.  When any term in a
# group is found in the search query, all other terms in the same group are
# appended so that the tokeniser (and downstream scorer) can match documents
# that use the canonical form.  This fixes retrieval failures when students
# use informal or colloquial phrasings (e.g. "KV部分复用" instead of
# "KV Cache partial reuse").
_QUERY_SYNONYM_GROUPS: list[frozenset[str]] = [
    frozenset({
        "计入绩效", "纳入绩效", "有效贡献", "贡献认定", "可纳入的工作",
    }),
    frozenset({
        "发布到互联网", "互联网公开", "公开发布", "对外公开", "是否可以发布",
    }),
    # KV Cache family
    frozenset({
        "KV Cache", "KV缓存", "KV cache", "kv cache",
        "键值缓存", "KV部分复用", "KV复用", "key-value cache",
        "key value cache", "KV重用",
    }),
    # Prefix caching / prefix reuse
    frozenset({
        "prefix cache", "prefix caching", "前缀缓存", "前缀复用",
        "prefix reuse", "前缀共享", "prefix sharing",
    }),
    # GPU memory / VRAM management
    frozenset({
        "GPU memory", "GPU内存", "GPU显存", "显存管理", "显存优化",
        "VRAM", "vram", "GPU memory management", "显存分配",
    }),
    # Ascend NPU
    frozenset({
        "Ascend NPU", "NPU", "昇腾", "Ascend", "ascend npu",
        "华为昇腾", "昇腾NPU", "Ascend芯片",
    }),
    # Batch scheduling
    frozenset({
        "batch scheduling", "批调度", "动态批处理", "continuous batching",
        "continuous batch", "连续批处理", "iteration-level scheduling",
    }),
    # Speculative decoding
    frozenset({
        "speculative decoding", "投机解码", "投机采样",
        "speculative sampling", "draft model",
    }),
    # Model quantization
    frozenset({
        "quantization", "量化", "INT8", "int8", "INT4", "int4",
        "FP16", "fp16", "BF16", "bf16", "模型量化", "权重量化",
    }),
    # Retrieval-augmented generation
    frozenset({
        "RAG", "rag", "retrieval augmented generation",
        "检索增强生成", "检索增强", "retrieval-augmented",
    }),
    # Prompt engineering
    frozenset({
        "prompt engineering", "提示工程", "prompt设计", "提示词设计",
        "prompt优化", "提示词优化", "prompt tuning",
    }),
    # Fine-tuning
    frozenset({
        "fine-tuning", "fine tuning", "finetuning", "微调",
        "LoRA", "lora", "QLoRA", "qlora", "参数高效微调",
        "parameter-efficient fine-tuning", "PEFT", "peft",
    }),
    # Distributed inference
    frozenset({
        "tensor parallel", "张量并行", "tensor parallelism", "TP",
        "pipeline parallel", "流水线并行", "pipeline parallelism", "PP",
        "模型并行", "model parallelism", "分布式推理",
    }),
    # Attention mechanism
    frozenset({
        "attention", "注意力机制", "self-attention", "自注意力",
        "multi-head attention", "多头注意力", "MHA", "GQA", "MQA",
        "grouped-query attention", "分组查询注意力",
    }),
    # Token / tokenizer
    frozenset({
        "token", "tokenization", "分词", "tokenizer", "词元",
        "BPE", "bpe", "byte pair encoding", "子词",
    }),
]


def _expand_query_synonyms(text: str) -> str:
    """Append synonym terms to the query text for improved recall.

    For each synonym group, if any member appears in *text* (case-insensitive
    for ASCII terms), all other members are appended.  This ensures the
    downstream tokeniser produces tokens for canonical forms even when the
    student uses an informal or colloquial phrasing.
    """
    lower = text.lower()
    expansions: list[str] = []
    for group in _QUERY_SYNONYM_GROUPS:
        matched = False
        for term in group:
            if term.lower() in lower:
                matched = True
                break
        if matched:
            for term in group:
                if term.lower() not in lower:
                    expansions.append(term)
    if expansions:
        return f"{text} {' '.join(expansions)}".strip()
    return text


def _build_query_profile(
    query: str,
    visitor_profile: str | None = None,
    admin_role: str | None = None,
) -> QueryProfile:
    lowered = query.lower()
    document_types: set[str] = set()
    topic_domains: set[str] = set()
    course_ids = set(_infer_course_ids(query))
    research_focus: str | None = None
    named_entities = _extract_named_entities(query)
    if "tutorial" in lowered or "教程" in query or "习题" in query or "练习" in query:
        document_types.add("tutorial")
        topic_domains.add("teaching")
    if (
        "lecture" in lowered
        or "讲义" in query
        or "课件" in query
        or re.search(r"第\s*\d+\s*讲", query)
    ):
        document_types.add("lecture")
        topic_domains.add("teaching")
    project_is_teaching = (
        ("project" in lowered or "项目" in query)
        and any(
            marker in lowered
            for marker in (
                "course",
                "课程",
                "教学",
                "实验",
                "作业",
                "tutorial",
                "lecture",
                "workshop",
            )
        )
    )
    if (
        "experiment" in lowered
        or "lab" in lowered
        or "实验" in query
        or project_is_teaching
    ):
        document_types.add("experiment")
        topic_domains.add("teaching")

    if course_ids:
        topic_domains.add("teaching")

    research_markers = (
        "研究主线",
        "研究方向",
        "主要研究",
        "研究什么",
        "做什么研究",
        "研究板块",
        "科研",
        "flowrag",
        "libamm",
        "publication",
        "publications",
        "research",
    )
    # “研究生课程/教学/招生” describes the audience, not a research-paper
    # intent.  Only treat a bare 研究 occurrence outside 研究生 as research.
    query_without_graduate = query.replace("研究生", "")
    has_research_marker = (
        any(marker in lowered for marker in research_markers)
        or any(marker in query for marker in research_markers)
        or "研究" in query_without_graduate
    )
    if has_research_marker:
        topic_domains.add("research")
        if any(
            marker in query
            for marker in ("研究主线", "研究方向", "主要研究", "研究什么", "研究板块")
        ):
            research_focus = "overview"
        elif "论文" in query or _has_named_research_entity(query):
            research_focus = "paper"
    elif (
        "teaching" not in topic_domains
        and re.search(r"[A-Za-z][A-Za-z0-9_-]{3,}", query)
        and any(phrase in query for phrase in ("做什么", "是什么", "主要做什么"))
    ):
        topic_domains.add("research")
        research_focus = "paper"

    meeting_markers = (
        "meeting",
        "office hour",
        "office-hour",
        "agenda",
        "blocker",
        "预约",
        "约时间",
        "联系老师",
        "联系导师",
        "准备什么",
        "提前准备",
    )
    if any(marker in lowered for marker in meeting_markers) or any(
        marker in query for marker in meeting_markers
    ):
        topic_domains.add("meeting")
    elif "准备" in query and any(role in query for role in ("老师", "导师")):
        topic_domains.add("meeting")

    prefers_intro_parts = "teaching" in topic_domains and any(
        phrase in query for phrase in ("讲什么", "讲了什么", "主要内容", "内容是什么", "介绍什么")
    )

    return QueryProfile(
        document_types=frozenset(document_types),
        ordinal_numbers=frozenset(re.findall(r"\d+", query)),
        topic_domains=frozenset(topic_domains),
        course_ids=frozenset(course_ids),
        research_focus=research_focus,
        prefers_intro_parts=prefers_intro_parts,
        named_entities=frozenset(named_entities),
        visitor_profile=visitor_profile,
        admin_role=admin_role,
    )


def _infer_course_ids(text: str) -> tuple[str, ...]:
    lowered = text.lower()
    course_ids: list[str] = []
    for course_id, aliases in _COURSE_ALIAS_MAP.items():
        if any(alias.lower() in lowered for alias in aliases):
            course_ids.append(course_id)
    return tuple(dict.fromkeys(course_ids))


def _normalize_knowledge_metadata(payload: KnowledgeDocumentCreate) -> dict[str, str]:
    inferred = _infer_knowledge_metadata(
        title=payload.title,
        tags=payload.tags,
        source_name=payload.source_name,
    )
    explicit = {
        str(key).strip(): str(value).strip()
        for key, value in payload.metadata.items()
        if str(key).strip() and str(value).strip()
    }
    return {**inferred, **explicit}


def _infer_knowledge_metadata(
    *,
    title: str,
    tags: list[str],
    source_name: str | None,
) -> dict[str, str]:
    metadata: dict[str, str] = {}
    normalized_tags = [tag.lower() for tag in tags]
    tag_set = set(normalized_tags)

    for tag in normalized_tags:
        if ":" not in tag:
            continue
        key, value = tag.split(":", 1)
        if key == "identity":
            metadata["identity"] = value
        elif key == "domain":
            metadata["domain"] = value
        elif key == "audience":
            metadata["audience"] = value
        elif key == "course":
            metadata["course_id"] = value
        elif key == "material":
            metadata["material_type"] = value
        elif key in {"lecture", "tutorial", "experiment"}:
            metadata["ordinal_type"] = key
            metadata["ordinal"] = value

    if "domain" not in metadata:
        if tag_set & _TEACHING_RELATED_TAGS:
            metadata["domain"] = "teaching"
        elif tag_set & _RESEARCH_DOCUMENT_TAGS:
            metadata["domain"] = "research"
        elif tag_set & _MEETING_DOCUMENT_TAGS:
            metadata["domain"] = "meeting"

    if "identity" not in metadata:
        if metadata.get("domain") == "teaching":
            metadata["identity"] = "teacher"
        elif metadata.get("domain") == "research":
            metadata["identity"] = "pi"
        elif "profile" in tag_set:
            metadata["identity"] = "public-profile"

    if "material_type" not in metadata:
        for candidate in (
            "tutorial",
            "lecture",
            "experiment",
            "pdf",
            "paper-digest",
            "overview",
            "profile",
        ):
            if candidate in tag_set:
                metadata["material_type"] = candidate
                break

    for course_id in _infer_course_ids(f"{title} {source_name or ''}"):
        metadata.setdefault("course_id", course_id)

    if source_name:
        metadata["source_kind"] = source_name.split(":", 1)[0] if ":" in source_name else "manual"

    return metadata


def _document_course_ids(document: KnowledgeDocumentRecord) -> frozenset[str]:
    course_ids = {
        tag.split(":", 1)[1].lower()
        for tag in document.tags
        if tag.lower().startswith("course:") and ":" in tag
    }
    if document.metadata.get("course_id"):
        course_ids.add(document.metadata["course_id"].lower())
    haystack = f"{document.title} {document.source_name or ''}".lower()
    for course_id, aliases in _COURSE_ALIAS_MAP.items():
        if any(alias.lower() in haystack for alias in aliases):
            course_ids.add(course_id)
    if "intro-to-llm-inference-engines" in haystack:
        course_ids.add("llm-inference")
    if "graduate-paper-writing-course" in haystack:
        course_ids.add("paper-writing")
    if "database-lab" in haystack or "database-experiment" in haystack:
        course_ids.add("database-lab")
    return frozenset(course_ids)


def _normalize_audience_label(value: object | None) -> str | None:
    if not value:
        return None
    normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    for canonical, aliases in _AUDIENCE_ALIASES.items():
        if normalized == canonical or normalized in aliases:
            return canonical
    return normalized or None


def _document_visibility_audiences(document: KnowledgeDocumentRecord) -> frozenset[str]:
    # Explicit runtime visibility is authoritative. Combining it with stale
    # tags could otherwise widen a team-only document back to public access.
    metadata_visibility = _normalize_audience_label(document.metadata.get("visibility"))
    if metadata_visibility:
        return frozenset({metadata_visibility})

    audiences: set[str] = set()
    metadata_audience = _normalize_audience_label(document.metadata.get("audience"))
    if metadata_audience:
        audiences.add(metadata_audience)
    for tag in document.tags:
        lowered = tag.lower()
        if not lowered.startswith("audience:"):
            continue
        audience = _normalize_audience_label(lowered.split(":", 1)[1])
        if audience:
            audiences.add(audience)
    return frozenset(audiences)


def _allowed_audiences_for_requester(
    visitor_profile: str | None,
    admin_role: str | None = None,
) -> frozenset[str]:
    normalized_profile = visitor_profile.strip() if visitor_profile else None
    normalized_role = admin_role.strip() if admin_role else None
    profile_audiences = _VISITOR_PROFILE_ALLOWED_AUDIENCES.get(
        normalized_profile,
        _VISITOR_PROFILE_ALLOWED_AUDIENCES[None],
    )
    role_audiences = _ADMIN_ROLE_ALLOWED_AUDIENCES.get(
        normalized_role,
        _ADMIN_ROLE_ALLOWED_AUDIENCES[None],
    )
    return frozenset(profile_audiences | role_audiences)


_SENSITIVE_SOURCE_PREFIXES = (
    "workspace/",
    "private-materials:",
)
_SENSITIVE_TAG_INDICATORS = frozenset({
    "proposal",
    "roadmap",
})
_SENSITIVE_SOURCE_PATH_KEYWORDS = frozenset({
    "proposal",
    "roadmap",
    "基金",
    "loa ",
    "loa_",
    "loa.",
    "letter of award",
    "letter of accept",
    "thesis_proposal",
})


def _infer_default_audience(document: "KnowledgeDocumentRecord") -> str | None:
    """Infer a default audience for documents that lack explicit audience tags.

    Returns ``"lab_member"`` for documents from internal workspace or
    private-materials sources, or for documents whose source path or tags
    suggest sensitive content (proposals, roadmaps, award letters, etc.).
    Returns ``None`` for all other documents, meaning they remain publicly
    visible (the original allow-by-default behaviour for public content).
    """
    source = (document.source_name or "").lower()
    # 1. Workspace and private-materials sources are always internal
    for prefix in _SENSITIVE_SOURCE_PREFIXES:
        if source.startswith(prefix.lower()):
            return "lab_member"
    # 2. Tag-based heuristic: proposal/roadmap tags without explicit audience
    lower_tags = {t.lower() for t in document.tags}
    if lower_tags & _SENSITIVE_TAG_INDICATORS:
        return "lab_member"
    # 3. Source-path keyword heuristic (award letters, fund docs, etc.)
    for keyword in _SENSITIVE_SOURCE_PATH_KEYWORDS:
        if keyword in source:
            return "lab_member"
    return None


def _document_is_visible_to_requester(
    document: "KnowledgeDocumentRecord",
    visitor_profile: str | None,
    admin_role: str | None = None,
) -> bool:
    document_audiences = _document_visibility_audiences(document)
    if not document_audiences:
        # No explicit audience: check if the document matches sensitive
        # source patterns that should be restricted by default.
        inferred = _infer_default_audience(document)
        if inferred is not None:
            document_audiences = frozenset({inferred})
        else:
            return True  # genuinely public content
    return bool(document_audiences & _allowed_audiences_for_requester(visitor_profile, admin_role))


def _is_paper_writing_document(document: KnowledgeDocumentRecord) -> bool:
    document_tags = {tag.lower() for tag in document.tags}
    haystacks = (document.title, document.source_name or "", document.content[:400])
    matches_topic = any(
        "论文写作" in haystack or "paper-writing" in haystack.lower() for haystack in haystacks
    )
    if not matches_topic:
        return False
    return bool(document_tags & _TEACHING_RELATED_TAGS) or "graduate-paper-writing-course" in (
        document.source_name or ""
    )


def _has_named_research_entity(query: str) -> bool:
    return bool(re.search(r"[A-Za-z][A-Za-z0-9_-]{3,}", query))


def _extract_named_entities(query: str) -> tuple[str, ...]:
    entities: list[str] = []
    for match in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", query):
        if match.islower():
            continue
        entities.append(match.lower())
    return tuple(dict.fromkeys(entities))


def _expand_document_aliases(document: KnowledgeDocumentRecord) -> tuple[str, ...]:
    aliases: list[str] = []
    for tag in document.tags:
        aliases.extend(_TEACHING_ALIAS_MAP.get(tag.lower(), ()))
    return tuple(dict.fromkeys(aliases))


def _normalize_retrieval_text(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", text).strip()


def _document_mentions_ordinal(text: str, ordinal_numbers: frozenset[str]) -> bool:
    if not text or not ordinal_numbers:
        return False

    normalized = text.lower()
    for raw_number in ordinal_numbers:
        number = str(int(raw_number))
        variants = {
            number,
            f"{int(raw_number):02d}",
            f"第 {number} 讲",
            f"第{number}讲",
            f"tutorial {number}",
            f"tutorial {int(raw_number):02d}",
            f"实验 {number}",
            f"实验{number}",
            f"experiment {number}",
        }
        if any(variant in normalized for variant in variants):
            return True
    return False


def _extract_document_part_index(document: KnowledgeDocumentRecord) -> int | None:
    for text in (document.title, document.source_name or ""):
        if not text:
            continue
        for pattern in (r"第\s*(\d+)\s*部分", r"part-(\d+)"):
            match = re.search(pattern, text, re.IGNORECASE)
            if match is not None:
                return int(match.group(1))
    return None


def _hit_matches_ordinal_query(
    hit: KnowledgeSearchHit,
    document: KnowledgeDocumentRecord,
    ordinal_numbers: frozenset[str],
    document_types: frozenset[str],
) -> bool:
    haystacks = [(hit.title or "").lower(), (hit.source_name or "").lower()]
    for raw_number in ordinal_numbers:
        number = int(raw_number)
        padded = f"{number:02d}"
        patterns: list[str] = []
        if not document_types or "lecture" in document_types:
            patterns.extend(
                [
                    rf"第\s*0?{number}\s*讲",
                    rf"lecture[-_\s]?0?{number}\b",
                    rf"第{padded}讲",
                ]
            )
        if not document_types or "tutorial" in document_types:
            patterns.extend(
                [
                    rf"tutorial[-_\s]?0?{number}\b",
                    rf"tutorial\s*{padded}\b",
                ]
            )
        if not document_types or "experiment" in document_types:
            patterns.extend(
                [
                    rf"实验\s*0?{number}\b",
                    rf"experiment[-_\s]?0?{number}\b",
                ]
            )
        if any(
            re.search(pattern, haystack, re.IGNORECASE)
            for pattern in patterns
            for haystack in haystacks
        ):
            return True
    return False


def _hit_matches_named_entities(
    hit: KnowledgeSearchHit,
    document: KnowledgeDocumentRecord,
    named_entities: frozenset[str],
) -> bool:
    if not named_entities:
        return False
    haystacks = [hit.title.lower(), (hit.source_name or "").lower(), document.content[:500].lower()]
    return any(any(entity in haystack for haystack in haystacks) for entity in named_entities)


def _dedupe_hits_by_source_group(
    hits: list[KnowledgeSearchHit], limit: int
) -> list[KnowledgeSearchHit]:
    deduped_hits: list[KnowledgeSearchHit] = []
    seen_source_groups: set[str] = set()
    for hit in hits:
        source_group = _canonical_hit_group(hit)
        if source_group in seen_source_groups:
            continue
        seen_source_groups.add(source_group)
        deduped_hits.append(hit)
        if len(deduped_hits) >= limit:
            break
    return deduped_hits


def _canonical_hit_group(hit: KnowledgeSearchHit) -> str:
    source_group = _canonical_source_group(hit.source_name, hit.document_id)
    if source_group.startswith("knowledge-gap:"):
        normalized_title = re.sub(r"\s+", " ", hit.title.strip().lower())
        if normalized_title:
            return f"knowledge-gap-title:{normalized_title}"
    return source_group


def _canonical_source_group(source_name: str | None, fallback_id: str) -> str:
    if not source_name:
        return fallback_id
    return re.sub(r"::part-\d+$", "", source_name)
