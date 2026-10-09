from sage_faculty_twin.evidence_policy import (
    has_query_evidence,
    has_unsupported_source_quote,
    is_public_evidence_hit,
    is_research_hit,
    is_teaching_hit,
)
from sage_faculty_twin.models import KnowledgeSearchHit


def _hit(*, tags: list[str], title: str = "资料", excerpt: str = "推理系统") -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        document_id="test",
        title=title,
        excerpt=excerpt,
        score=1,
        tags=tags,
        source_name="test:source",
    )


def test_public_policy_rejects_private_audiences() -> None:
    assert is_public_evidence_hit(_hit(tags=["profile", "audience:public"]))
    assert not is_public_evidence_hit(_hit(tags=["profile", "audience:admin"]))


def test_domain_policy_classifies_research_and_teaching() -> None:
    assert is_research_hit(_hit(tags=["research"]))
    assert is_teaching_hit(_hit(tags=["courseware"]))
    assert not is_teaching_hit(_hit(tags=["research"]))


def test_query_evidence_requires_an_anchor() -> None:
    assert has_query_evidence("推理系统有哪些组件", _hit(tags=["research"], excerpt="推理系统包括调度"))
    assert not has_query_evidence("数据库课程如何报名", _hit(tags=["research"], excerpt="推理系统包括调度"))


def test_attributed_quote_must_exist_in_retrieved_text():
    assert has_unsupported_source_quote("依据：主页附件强调“只能使用100次梯度更新”。", ["推理调度与缓存设计"])
    assert not has_unsupported_source_quote("论文指出“缓存一致性影响性能”。", ["缓存一致性影响性能，需要实测。"])
    assert not has_unsupported_source_quote("建议固定‘baseline’。", [])
