"""Deterministic protocol fixture; not an entailment quality test."""
import json
from sage.runtime.natural_answer import NATURAL_ANSWER_PROMPT, NATURAL_AUDIT_PROMPT, NATURAL_CORRECTION_PROMPT

def response(system, payload):
    data = json.loads(payload)
    if system == NATURAL_CORRECTION_PROMPT:
        return json.dumps({"digest": data["digest"], "replacements": {str(u["id"]): None for u in data["failed_units"]},
            "additional_paragraphs": [s["content"] for s in data["sources"]] if data["missing_requirement_ids"] else []})
    if system == NATURAL_ANSWER_PROMPT:
        return json.dumps({"digest": data["digest"], "paragraphs": [s["content"] for s in data["sources"]]})
    if system == NATURAL_AUDIT_PROMPT:
        return json.dumps({"digest": data["digest"], "units": [{"id": u["id"], "verdict": "supported",
            "checked_sources": [{"source_id": data["sources"][0]["id"], "block_ids": [data["sources"][0]["blocks"][0]["id"]]}]} for u in data["units"]]})
    return None
