"""Exercise the original natural RAG question; never alter retrieval or auth policy.

This is a coverage/protocol smoke gate, not a semantic benchmark. Review the
saved answer and cited source as well. No private account or API key is needed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

QUESTION = '评测 RAG 系统时，怎样同时检查效果和性能？'
SOURCE = 'wiki:tech-notes/retrieval-augmented-generation'
SOURCE_URL = 'https://lab.sage.org.ai/sage-wiki/docs/tech-notes/retrieval-augmented-generation'


def coverage_checks(status: int, data: dict) -> dict[str, bool]:
    answer = data.get('answer', '')
    lower = answer.lower()
    hits = data.get('knowledge_hits', [])
    blocks = data.get('answer_blocks', [])
    return {
        'http_ok': status == 200,
        'normal_finish': data.get('finish_reason') == 'stop',
        'rag_source_retrieved': any(hit.get('source_name') == SOURCE for hit in hits),
        'public_sources_only': bool(hits) and all(str(hit.get('source_name', '')).startswith('wiki:') for hit in hits),
        'complete_source_url': SOURCE_URL in answer,
        'canonical_source_blocks': bool(blocks) and '\n\n'.join(block.get('markdown', '') for block in blocks).strip() == answer,
        'retrieval_and_answer_quality': 'recall@k' in lower and '忠实' in answer and '引用' in answer,
        'full_path_performance': 'p95' in lower and 'ttft' in lower and '吞吐' in answer and '校验' in answer,
        'joint_quality_slo': '联合有效吞吐' in answer and 'slo' in lower,
        'controlled_comparison': '配对' in answer and '语料快照' in answer,
        'failure_accounting': '超时' in answer and '总请求数' in answer,
        'no_coverage_gap': '仍待补充的依据' not in answer,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    request_id = 'rag-evaluation-qa-' + uuid4().hex
    body = {'student_name': 'RAG原题公开资料合成验收', 'question': QUESTION}
    start = time.monotonic()
    with httpx.Client(timeout=330, trust_env=False) as client:
        response = client.post(args.base_url.rstrip('/') + '/chat',
                               params={'request_id': request_id}, json=body)
        data = response.json()
    record = {'request_id': request_id, 'question': QUESTION, 'status': response.status_code,
              'seconds': round(time.monotonic() - start, 2), 'model': data.get('used_model'),
              'checks': coverage_checks(response.status_code, data),
              'answer': data.get('answer'), 'answer_blocks': data.get('answer_blocks'),
              'sources': [hit.get('source_name') for hit in data.get('knowledge_hits', [])]}
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(record, output, ensure_ascii=False, indent=2)
    print(json.dumps({key: value for key, value in record.items()
                      if key not in {'answer', 'answer_blocks'}}, ensure_ascii=False), flush=True)
    if not all(record['checks'].values()):
        raise SystemExit('Original RAG question did not pass all coverage/protocol gates')


if __name__ == '__main__':
    main()
