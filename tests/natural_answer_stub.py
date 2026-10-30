"""Deterministic protocol fixture; not an entailment quality test."""
import json
from sage.runtime.natural_answer import NATURAL_ANSWER_PROMPT, NATURAL_AUDIT_PROMPT, NATURAL_CORRECTION_PROMPT

def response(system, payload):
    data = json.loads(payload)
    from sage.runtime.coverage_relation import RELATION_REVIEW_PROMPT
    if system == RELATION_REVIEW_PROMPT:
        return json.dumps({"digest":data["digest"],"decisions":{key:{"reason":"fixture relation", "direct":True} for key in data["source_relations"]}})
    from sage.runtime.evidence_question import QUESTION_RELATION_PROMPT
    if system == QUESTION_RELATION_PROMPT:
        return json.dumps({"digest":data["digest"],"requested_relation":data["question"]})
    from sage.runtime.scope_audit import SCOPE_AUDIT_PROMPT
    if system == SCOPE_AUDIT_PROMPT:
        return json.dumps({'digest': data['digest'], 'units': {str(u['id']): None for u in data['units']}})
    if system == NATURAL_CORRECTION_PROMPT:
        return json.dumps({"digest": data["digest"], "replacements": {str(u["id"]): None for u in data["failed_units"]},
            "additional_paragraphs": [s["content"] for s in data["sources"]] if data["missing_requirement_ids"] else []})
    if system == NATURAL_ANSWER_PROMPT:
        return json.dumps({"digest": data["digest"], "paragraphs": [s["content"] for s in data["sources"]]})
    if system == NATURAL_AUDIT_PROMPT:
        return json.dumps({"digest": data["digest"], "units": [{"id": u["id"], "verdict": "supported",
            "checked_sources": [{"source_id": data["sources"][0]["id"], "block_ids": [data["sources"][0]["blocks"][0]["id"]]}]} for u in data["units"]]})
    return None


def coverage(data, *, first_only=False):
    return json.dumps({'digest': data['digest'], 'requested_relation': data.get('requested_relation') or 'fixture relation', 'source_assessments': {
        source['source_id']: {'studied_subject': 'fixture', 'established_relation': 'fixture relation', 'selection': {'role': 'direct',
            'requirements': {r['requirement_id']: {p['id']: True for p in source['paragraphs'] if not first_only or index == 0} for r in data['requirements']}}}
        for index, source in enumerate(data['sources'])}})
