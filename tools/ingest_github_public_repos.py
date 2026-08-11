"""Sync public GitHub repository metadata into Sage Mate's knowledge base.

The organization list is configurable through ``SAGE_MATE_GITHUB_ORGS``;
this keeps deployment portable while making the default public research
organizations explicit. Only public repository metadata and README text are
ingested—no issues, pull requests, source files, or credentials.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate

DEFAULT_ORGS = ("intellistream", "SAGE-Research", "datasys")
MAX_README_CHARS = 16_000


def _api_get(path: str) -> object:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "sage-mate-public-sync"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com/{path.lstrip('/')}", headers=headers)
    with urlopen(request, timeout=20) as response:
        return json.load(response)


def _readme(org: str, repo: str) -> str:
    try:
        payload = _api_get(f"repos/{org}/{repo}/readme")
        if not isinstance(payload, dict):
            return ""
        encoded = str(payload.get("content") or "").replace("\n", "")
        if not encoded:
            return ""
        return base64.b64decode(encoded).decode("utf-8", errors="replace")[:MAX_README_CHARS]
    except (HTTPError, URLError, ValueError, KeyError):
        return ""


def sync_public_repositories(orgs: tuple[str, ...]) -> tuple[int, int, int]:
    store = LocalKnowledgeStore(AppSettings())
    created = updated = skipped = 0
    for org in orgs:
        try:
            repositories = _api_get(f"orgs/{org}/repos?type=public&per_page=100&sort=updated")
        except (HTTPError, URLError, ValueError):
            print(f"ERROR {org}: unable to list public repositories", file=sys.stderr)
            continue
        if not isinstance(repositories, list):
            continue
        for repository in repositories:
            if not isinstance(repository, dict) or repository.get("fork"):
                continue
            name = str(repository.get("name") or "").strip()
            if not name:
                continue
            full_name = f"{org}/{name}"
            readme = _readme(org, name)
            description = str(repository.get("description") or "").strip()
            content = (
                f"GitHub public repository: {full_name}\n"
                f"Description: {description or 'No public description provided.'}\n"
                f"URL: {repository.get('html_url') or ''}\n"
                f"Default branch: {repository.get('default_branch') or 'main'}\n"
                f"License: {((repository.get('license') or {}).get('spdx_id') or 'not specified')}\n"
                f"Topics: {', '.join(repository.get('topics') or []) or 'none'}\n\n"
                f"README:\n{readme or 'No public README available.'}"
            )
            payload = KnowledgeDocumentCreate(
                title=f"GitHub public repository | {full_name}",
                content=content[:MAX_README_CHARS + 1200],
                tags=["github", "public-repository", f"org:{org.lower()}", "audience:public"],
                source_name=f"github-public:{full_name}",
                metadata={
                    "visibility": "public",
                    "repository_url": str(repository.get("html_url") or ""),
                    "default_branch": str(repository.get("default_branch") or "main"),
                    "github_updated_at": str(repository.get("updated_at") or ""),
                    "synced_at": datetime.now(UTC).isoformat(),
                },
            )
            _, inserted = store.upsert_document(payload, rebuild_indexes=False)
            if inserted:
                created += 1
                print(f"CREATED {full_name}")
            else:
                updated += 1
                print(f"UPDATED {full_name}")
        store.rebuild_indexes()
    return created, updated, skipped


if __name__ == "__main__":
    configured = os.environ.get("SAGE_MATE_GITHUB_ORGS", "")
    organizations = tuple(item.strip() for item in configured.split(",") if item.strip()) or DEFAULT_ORGS
    result = sync_public_repositories(organizations)
    print(f"Done. created={result[0]} updated={result[1]} skipped={result[2]}")
