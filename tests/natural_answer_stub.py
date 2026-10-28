"""Deterministic protocol fixture; not an entailment quality test."""
import json
from sage.runtime.natural_answer import NATURAL_ANSWER_PROMPT, NATURAL_AUDIT_PROMPT

def response(system, payload):
    data = json.loads(payload)
    if system == NATURAL_ANSWER_PROMPT:
        return json.dumps({"digest": data["digest"], "paragraphs": [s["content"] for s in data["sources"]]})
    if system == NATURAL_AUDIT_PROMPT:
        return json.dumps({"digest": data["digest"], "units": [{"id": u["id"], "verdict": "supported",
            "evidence": [{"source_id": data["sources"][0]["id"], "line_ids": [0]}]} for u in data["units"]]})
    return None
