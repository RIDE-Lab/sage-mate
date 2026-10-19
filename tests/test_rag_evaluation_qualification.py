"""Do not mistake transport success or a citation for a complete RAG answer."""
import pytest
from tools.qualify_rag_evaluation import SOURCE, SOURCE_URL, coverage_checks


def response(answer):
    return {'answer': answer, 'answer_blocks': [{'markdown': answer}],
            'finish_reason': 'stop', 'knowledge_hits': [{'source_name': SOURCE}]}


def covered_text():
    # Gate fixture only, not a production answer, prompt or source override.
    return SOURCE_URL + '\nRecall@k 忠实度 引用 p95 TTFT 吞吐 校验 联合有效吞吐 SLO 配对 语料快照 超时 总请求数'


def test_transport_and_link_alone_do_not_pass():
    checks = coverage_checks(200, response(SOURCE_URL))
    assert checks['http_ok'] and checks['complete_source_url']
    assert not checks['retrieval_and_answer_quality']
    assert not checks['full_path_performance']
    assert not all(checks.values())


@pytest.mark.parametrize('change', ['quality', 'performance', 'gap', 'length', 'private', 'blocks'])
def test_gate_rejects_incomplete_or_invalid_answer(change):
    text = covered_text()
    if change == 'quality':
        text = text.replace('Recall@k', '')
    elif change == 'performance':
        text = text.replace('TTFT', '')
    elif change == 'gap':
        text += '\n仍待补充的依据'
    data = response(text)
    if change == 'length':
        data['finish_reason'] = 'length'
    elif change == 'private':
        data['knowledge_hits'].append({'source_name': 'private:test'})
    elif change == 'blocks':
        data['answer_blocks'] = []
    assert not all(coverage_checks(200, data).values())


def test_gate_accepts_full_protocol_coverage_but_is_not_a_semantic_score():
    assert all(coverage_checks(200, response(covered_text())).values())
