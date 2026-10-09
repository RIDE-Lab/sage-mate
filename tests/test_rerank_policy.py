import pytest
from pydantic import ValidationError

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import _build_query_profile, _select_rerank_plan


def test_adaptive_rerank_bypasses_remote_for_strong_deterministic_match() -> None:
    settings = AppSettings(_env_file=None)

    plan = _select_rerank_plan(
        settings,
        available_candidates=40,
        retrieval_scores=[0.8, 0.7],
        deterministic_scores=[100.0, 1.0],
    )

    assert plan.candidate_count == 16
    assert plan.bypass_remote is True
    assert plan.reason == "strong_deterministic"


def test_adaptive_rerank_can_disable_deterministic_bypass_for_unstructured_query() -> None:
    settings = AppSettings(_env_file=None)

    plan = _select_rerank_plan(
        settings,
        available_candidates=40,
        retrieval_scores=[0.6, 0.58],
        deterministic_scores=[100.0, 98.0],
        allow_deterministic_bypass=False,
    )

    assert plan.bypass_remote is False
    assert plan.reason == "medium_confidence"
@pytest.mark.parametrize(
    ("retrieval_scores", "expected_count", "expected_reason"),
    [
        ([0.80, 0.70], 16, "high_confidence"),
        ([0.60, 0.58], 24, "medium_confidence"),
        ([0.30, 0.28], 48, "low_confidence"),
    ],
)
def test_adaptive_rerank_uses_confidence_tiers(
    retrieval_scores: list[float],
    expected_count: int,
    expected_reason: str,
) -> None:
    settings = AppSettings(_env_file=None)

    plan = _select_rerank_plan(
        settings,
        available_candidates=64,
        retrieval_scores=retrieval_scores,
        deterministic_scores=[0.0],
    )

    assert plan.candidate_count == expected_count
    assert plan.bypass_remote is False
    assert plan.reason == expected_reason


def test_adaptive_rerank_never_exceeds_available_candidates() -> None:
    settings = AppSettings(_env_file=None)

    plan = _select_rerank_plan(
        settings,
        available_candidates=7,
        retrieval_scores=[0.1],
        deterministic_scores=[0.0],
    )

    assert plan.candidate_count == 7


def test_rerank_candidate_tiers_must_be_ordered_and_bounded() -> None:
    with pytest.raises(ValidationError, match="candidate tiers"):
        AppSettings(
            _env_file=None,
            sagevdb_reranker_small_candidates=24,
            sagevdb_reranker_medium_candidates=16,
        )

    with pytest.raises(ValidationError, match="medium-confidence"):
        AppSettings(
            _env_file=None,
            sagevdb_reranker_medium_confidence_similarity=0.8,
            sagevdb_reranker_high_confidence_similarity=0.7,
        )


def test_query_profile_does_not_treat_research_projects_or_labs_as_coursework() -> None:
    research_project = _build_query_profile("VAMOS 项目当前研究目标和路线图是什么？")
    new_lab_member = _build_query_profile("实验室新生第一个月应该完成哪些事情？")
    course_project = _build_query_profile("大模型推理课程项目实验应该怎么完成？")

    assert research_project.topic_domains == frozenset({"research"})
    assert research_project.document_types == frozenset()
    assert research_project.research_focus == "overview"
    assert "teaching" not in new_lab_member.topic_domains
    assert new_lab_member.document_types == frozenset()
    assert "teaching" in course_project.topic_domains
    assert course_project.document_types == frozenset({"experiment"})
