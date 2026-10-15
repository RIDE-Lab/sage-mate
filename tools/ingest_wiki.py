"""Ingest sage-wiki Docusaurus content into the faculty-twin knowledge base.

Parses markdown files from the sage-wiki repo, extracts frontmatter metadata
and inter-page links, and creates/updates KB entries with link graph metadata.

Usage:
    cd /home/shuhao/sage-mate
    PYTHONPATH=src python tools/ingest_wiki.py [--wiki-dir /home/shuhao/sage-wiki]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate

_WIKI_BASE_URL = "https://lab.sage.org.ai/sage-wiki"
_MAX_CONTENT_LEN = 18000

# Category → KB tags mapping
_CATEGORY_TAGS: dict[str, list[str]] = {
    "achievements": ["wiki", "achievement", "audience:public"],
    "tutorials": ["wiki", "tutorial", "audience:public"],
    "tech-notes": ["wiki", "tech-note", "audience:lab_member"],
    "standards": ["wiki", "standard", "team-norm", "audience:lab_member"],
    "resources": ["wiki", "resource", "audience:public"],
    "industry-docs": ["wiki", "industry", "audience:lab_member"],
}


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Extract YAML frontmatter and body from a markdown file."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end < 0:
        return {}, text
    fm_text = text[3:end].strip()
    body = text[end + 3:].strip()

    meta: dict[str, str] = {}
    for line in fm_text.split("\n"):
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip("'\"")
    return meta, body


def _extract_wiki_links(body: str, current_file: Path, wiki_docs_dir: Path) -> list[str]:
    """Extract relative wiki links and resolve them to source_names.

    Returns a list of source_names like ``wiki:tutorials/some-page``.
    """
    links: list[str] = []
    # Match [text](../path/to/page.md) or [text](./page.md) or [text](page.md)
    for match in re.finditer(r'\[([^\]]*)\]\(([^)]+\.md(?:#[^)]*)?)', body):
        href = match.group(2).split("#")[0]  # strip anchor
        if href.startswith("http://") or href.startswith("https://"):
            continue  # skip external links
        # Resolve relative path
        target = (current_file.parent / href).resolve()
        try:
            rel = target.relative_to(wiki_docs_dir)
        except ValueError:
            continue
        # Convert to source_name: wiki:category/page-name
        parts = rel.with_suffix("").parts
        source_name = "wiki:" + "/".join(parts)
        links.append(source_name)
    return links


def _wiki_source_name(rel_path: Path) -> str:
    """Convert a wiki file's relative path to a source_name."""
    parts = rel_path.with_suffix("").parts
    return "wiki:" + "/".join(parts)


def _wiki_url(rel_path: Path) -> str:
    """Build the public URL for a wiki page."""
    parts = rel_path.with_suffix("").parts
    return f"{_WIKI_BASE_URL}/docs/{'/'.join(parts)}"


def main():
    parser = argparse.ArgumentParser(description="Ingest sage-wiki into KB")
    parser.add_argument("--wiki-dir", default="/home/shuhao/sage-wiki", help="Path to sage-wiki clone")
    parser.add_argument(
        "--changed-marker",
        type=Path,
        help="Write a marker after a successful index sync only when documents changed",
    )
    args = parser.parse_args()

    wiki_dir = Path(args.wiki_dir)
    docs_dir = wiki_dir / "docs"
    if not docs_dir.exists():
        print(f"ERROR: wiki docs directory not found: {docs_dir}")
        sys.exit(1)

    settings = AppSettings()
    store = LocalKnowledgeStore(settings)

    md_files = sorted(docs_dir.rglob("*.md")) + sorted(docs_dir.rglob("*.mdx"))
    # Skip index files (category landing pages)
    md_files = [f for f in md_files if f.name not in ("index.mdx", "index.md", "_category_.json")]
    print(f"Found {len(md_files)} wiki pages in {docs_dir}")

    created = updated = skipped = 0
    existing_by_source = {
        document.source_name: document for document in store.list_documents()
        if document.source_name
    }
    for md_file in md_files:
        text = md_file.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            skipped += 1
            continue

        frontmatter, body = _parse_frontmatter(text)
        rel_path = md_file.relative_to(docs_dir)

        # Determine category from path
        category = rel_path.parts[0] if len(rel_path.parts) > 1 else "general"
        tags = list(_CATEGORY_TAGS.get(category, ["wiki", "audience:public"]))

        # Extract and resolve wiki links
        wiki_links = _extract_wiki_links(body, md_file, docs_dir)

        # Build metadata
        title = frontmatter.get("title", md_file.stem.replace("-", " ").title())
        source_name = _wiki_source_name(rel_path)
        wiki_url = _wiki_url(rel_path)

        metadata: dict[str, str] = {
            "wiki_url": wiki_url,
            "wiki_category": category,
        }
        if wiki_links:
            metadata["linked_source_names"] = "|".join(wiki_links)
        if frontmatter.get("authors"):
            metadata["wiki_authors"] = frontmatter["authors"]
        if frontmatter.get("date"):
            metadata["wiki_date"] = frontmatter["date"]

        # Build content with URL header for citation
        content = f"[Wiki: {title}]({wiki_url})\n\n{body}"
        if len(content) > _MAX_CONTENT_LEN:
            content = content[:_MAX_CONTENT_LEN]

        payload = KnowledgeDocumentCreate(
            title=f"Wiki | {title}",
            content=content,
            tags=tags,
            source_name=source_name,
            metadata=metadata,
        )

        # Upsert: create or update
        previous = existing_by_source.get(source_name)
        record, is_new = store.upsert_document(payload, rebuild_indexes=False)
        existing_by_source[source_name] = record
        if is_new:
            created += 1
            print(f"  CREATED: {source_name} ({len(wiki_links)} links)")
        elif record != previous:
            updated += 1
            print(f"  UPDATED: {source_name} ({len(wiki_links)} links)")
        else:
            skipped += 1

    if created or updated:
        store.rebuild_indexes()
        if args.changed_marker is not None:
            args.changed_marker.write_text(f"{created + updated}\n", encoding="utf-8")

    print(f"\nDone: {created} created, {updated} updated, {skipped} skipped")
    print(f"Link graph: {len(store._link_graph)} nodes, {sum(len(v) for v in store._link_graph.values())} edges")


if __name__ == "__main__":
    main()
