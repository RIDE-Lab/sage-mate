"""Public synthetic end-to-end task-routing gates, using original failing query.

Saves full public answers for operator review. No authentication or identity
override; route and execution checks supplement, not replace, answer review.
"""
import argparse
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

CASES = (
    ("facts", "公开 Wiki 是否报告了 Twin 在 2026 年 10 月 18 日用 9973 个真实研究问题测得的准确率？请给出测量条件；如果没有这些资料请明确指出。"),
    ("research", "根据公开 Wiki，评价 StateAxis 的研究机会，并设计可证伪机制收益的对照实验。"),
    ("mixed", "先查公开 Wiki 对 StateAxis 已有性能测量的说明；如果缺资料，请为补齐证据设计对照实验。"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    rows = []
    with httpx.Client(trust_env=False, timeout=330) as client:
        for mode, question in CASES:
            request_id = 'task-purpose-synthetic-' + uuid4().hex
            start = time.monotonic()
            response = client.post(args.base_url.rstrip('/')+'/chat', params={'request_id': request_id},
                json={'student_name': '任务路由公开资料合成验收', 'question': question})
            data = response.json()
            answer = data.get('answer', '')
            traces = {item['key']: item for item in data.get('workflow_trace', [])}
            route = traces.get('adaptive_plan', {}).get('summary', '')
            intent = traces.get('interaction_understand', {}).get('detail', '')
            blocks = data.get('answer_blocks', [])
            prospective = any(block.get('kind') == 'inference' for block in blocks)
            planned = any(e.get('operator_key') == 'plan-experiments' and e.get('state') == 'completed'
                          for e in data.get('execution_events', []))
            coverage = data.get('evidence_coverage') or {}
            checks = {
                'http_ok': response.status_code == 200,
                'normal_finish': data.get('finish_reason') == 'stop',
                'canonical_blocks': bool(blocks) and '\n\n'.join(b['markdown'] for b in blocks).strip() == answer,
                'wiki_only': bool(data.get('knowledge_hits')) and all(str(h.get('source_name', '')).startswith('wiki:') for h in data.get('knowledge_hits', [])),
                'full_source_link': 'https://lab.sage.org.ai/sage-wiki/docs/' in answer,
                'semantic_task': f"任务目的：{'facts' if mode == 'facts' else 'research'}" in intent,
                'correct_branch': ('执行路线：research' in route) == (mode != 'facts'),
                'prospective_only_when_requested': prospective == (mode != 'facts'),
                'native_experiment_execution_matches_task': planned == (mode != 'facts'),
            }
            if mode == 'facts':
                checks['original_missing_evidence_retained'] = bool(coverage.get('missing_parts'))
                checks['no_experiment_plan_prose'] = '匹配对照' not in answer and '建议实验' not in answer
            row = {'mode': mode, 'question': question, 'request_id': request_id,
                   'status': response.status_code, 'seconds': round(time.monotonic()-start, 2),
                   'checks': checks, 'response': data}
            rows.append(row)
            print(json.dumps({k:v for k,v in row.items() if k!='response'}, ensure_ascii=False), flush=True)
    with args.output.open('x') as output:
        json.dump(rows, output, ensure_ascii=False, indent=2)
    if not all(all(row['checks'].values()) for row in rows):
        raise SystemExit('Task routing qualification failed; inspect original answers and execution receipts')


if __name__ == '__main__':
    main()
