"""Reviewed source ownership, not heuristic topic-based deletion."""
import json
from importlib.resources import files


def load_source_ownership():
    mapping = json.loads(files('sage_faculty_twin').joinpath('source_ownership.json').read_text(encoding='utf-8'))
    if not isinstance(mapping, dict) or any(
        not isinstance(source, str) or not isinstance(target, str)
        or not target.startswith('wiki:') or source.startswith('wiki:')
        for source, target in mapping.items()
    ):
        raise ValueError('Invalid canonical Wiki source ownership registry')
    return mapping


SOURCE_OWNERSHIP = load_source_ownership()
