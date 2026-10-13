"""Crash-safe SageVDB SageANNS snapshots for the knowledge index.

SageANNS exposes whole-index ``save``/``load`` rather than the mutable vector
API used by ``PersistentSageVDB``.  This wrapper only manages generations and
compatibility; SageVDB itself serializes and restores every vector and its
metadata.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import uuid4


logger = logging.getLogger(__name__)
_GENERATION = re.compile(r"[0-9a-f]{32}\Z")
_INDEX_FILES = ("index", "index.sage_anns_adapter.json")


def fingerprint(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SageANNSCheckpoint:
    """Validate and atomically publish immutable SageVDB index generations."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.generations = root / "generations"

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / ".lock").open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def load(
        self, database: object, *, compatibility: dict[str, object], documents: str
    ) -> str | None:
        current = self.root / "CURRENT"
        try:
            preferred = (
                current.read_text(encoding="ascii").strip() if current.is_file() else ""
            )
        except (OSError, UnicodeError):
            preferred = ""
        candidates = [preferred] if _GENERATION.fullmatch(preferred) else []
        if self.generations.is_dir():
            candidates.extend(
                path.name
                for path in sorted(
                    self.generations.iterdir(),
                    key=lambda item: item.stat().st_mtime,
                    reverse=True,
                )
                if path.is_dir()
                and _GENERATION.fullmatch(path.name)
                and path.name != preferred
            )
        for generation in candidates:
            directory = self.generations / generation
            try:
                manifest = json.loads(
                    (directory / "manifest.json").read_text(encoding="utf-8")
                )
                if manifest.get("version") != 1:
                    continue
                if (
                    manifest.get("compatibility") != compatibility
                    or manifest.get("documents") != documents
                ):
                    continue
                checksums = manifest.get("sha256")
                if not isinstance(checksums, dict) or set(checksums) != set(
                    _INDEX_FILES
                ):
                    continue
                if any(
                    _file_hash(directory / name) != checksums[name]
                    for name in _INDEX_FILES
                ):
                    continue
                database.load(str(directory / "index"))
                if generation != preferred:
                    self._publish_current(generation)
                return generation
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                RuntimeError,
                KeyError,
            ) as exc:
                logger.warning(
                    "Ignoring invalid SageVDB SageANNS generation %s: %s",
                    generation,
                    exc,
                )
        return None

    def save(
        self, database: object, *, compatibility: dict[str, object], documents: str
    ) -> str:
        self.generations.mkdir(parents=True, exist_ok=True)
        generation = uuid4().hex
        directory = self.generations / generation
        directory.mkdir()
        database.save(str(directory / "index"))
        checksums = {name: _file_hash(directory / name) for name in _INDEX_FILES}
        manifest = {
            "version": 1,
            "compatibility": compatibility,
            "documents": documents,
            "sha256": checksums,
        }
        (directory / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        for name in (*_INDEX_FILES, "manifest.json"):
            with (directory / name).open("rb") as saved:
                os.fsync(saved.fileno())
        self._publish_current(generation)
        return generation

    def _publish_current(self, generation: str) -> None:
        temporary = self.root / f".CURRENT-{uuid4().hex}"
        with temporary.open("w", encoding="ascii") as pointer:
            pointer.write(generation + "\n")
            pointer.flush()
            os.fsync(pointer.fileno())
        os.replace(temporary, self.root / "CURRENT")
        descriptor = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
