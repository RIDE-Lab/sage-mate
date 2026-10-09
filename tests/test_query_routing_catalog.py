from sage_faculty_twin.query_routing_catalog import (
    SYSTEM_PROJECT_MARKERS,
    contains_marker,
    is_explicit_other_teacher_query,
    is_owner_identity_query,
)


def test_owner_aliases_are_centralized_and_exclude_other_teacher_requests() -> None:
    assert is_owner_identity_query("张书豪老师是谁？")
    assert is_owner_identity_query("张老师简介")
    assert is_explicit_other_teacher_query("另一个张老师是谁？")
    assert not is_owner_identity_query("另一个张老师是谁？")


def test_system_project_marker_catalog_covers_natural_phrasings() -> None:
    assert contains_marker("课题组目前有哪些开源项目？", SYSTEM_PROJECT_MARKERS)
    assert contains_marker("请介绍一下系统建设", SYSTEM_PROJECT_MARKERS)
    assert not contains_marker("这篇论文的实验结果如何？", SYSTEM_PROJECT_MARKERS)
