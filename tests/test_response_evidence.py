from dataclasses import replace
from datetime import UTC, datetime
import json
from types import SimpleNamespace

import pytest
from sage.runtime.evidence_coverage import (
    InvalidEvidenceCoverage, prepare_coverage, question_requirements, validate_coverage,
)
from sage.runtime.source_answer_plan import PlanSource
from sage_faculty_twin.models import ChatResponse, EvidenceCoverageView
from sage_faculty_twin.memory_store import ConversationMemoryRecord


def work():
    question = '系统有什么功能？性能测量数据在哪里？'
    request = prepare_coverage(question, question_requirements(question),
        (PlanSource('s', 'public', 'https://example.org/full', '功能说明。'),))
    assessment = validate_coverage(request, json.dumps({'digest':request.digest,
        'items':[{'requirement_id':item.requirement_id,'witnesses':[]} for item in request.requirements]}))
    return SimpleNamespace(request=request, assessment=assessment)


def test_display_projects_validated_sage_receipt_without_reassessing():
    actual = work()
    view = EvidenceCoverageView.from_work(actual)
    assert view.request_digest == actual.request.digest
    assert view.requirement_count == len(actual.request.requirements)
    assert ''.join(view.missing_parts) == actual.request.question
    assert EvidenceCoverageView.from_work(None) is None
    forged = SimpleNamespace(request=actual.request, assessment=replace(actual.assessment, digest='0' * 64))
    with pytest.raises(InvalidEvidenceCoverage):
        EvidenceCoverageView.from_work(forged)


def test_evidence_coverage_survives_canonical_memory_roundtrip_and_legacy_is_unknown():
    view = EvidenceCoverageView.from_work(work())
    record = ConversationMemoryRecord(memory_id='e',conversation_id='c',student_name='test',
        student_email=None,course_context=None,question='question',answer='canonical unchanged',
        workflow_action='answer',interaction_domain='research',knowledge_hit_count=1,
        booking_summary=None,created_at=datetime.now(UTC),owner_id='guest:test',evidence_coverage=view)
    restored = ConversationMemoryRecord.from_dict(record.to_dict())
    assert restored.evidence_coverage == view
    assert restored.answer == record.answer
    legacy = record.to_dict()
    del legacy['evidence_coverage']
    assert ConversationMemoryRecord.from_dict(legacy).evidence_coverage is None
    response = ChatResponse(answer=record.answer,owner_name='test',used_model='fixture',evidence_coverage=view)
    assert response.model_dump()['evidence_coverage']['missing_parts'] == view.missing_parts
