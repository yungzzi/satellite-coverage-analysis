"""经真实 HTTP 验收解压包：六项操作、历史杭州三方案、持久化结果检查。"""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--recheck', action='store_true', help='重启服务后复查已有任务和结果')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('SAT_API_TOKEN'):
        headers['X-API-Key'] = os.environ['SAT_API_TOKEN']

    def call(path, body=None):
        request = urllib.request.Request(args.base_url.rstrip('/') + path, headers=headers,
                                         data=None if body is None else json.dumps(body).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    assert call('/readyz')['status'] == 'ready'
    if args.recheck:
        records = json.loads((args.output / 'acceptance.json').read_text(encoding='utf-8'))
        for item in records:
            job_id = item['job_id']
            assert call(f'/api/v1/jobs/{job_id}')['status'] == 'completed'
            old = json.loads((args.output / f"{item['case']}.json").read_text(encoding='utf-8'))
            assert call(f'/api/v1/jobs/{job_id}/result') == old
        print(f'Restart persistence passed: {len(records)} jobs')
        return
    quick = json.loads((args.package / 'integrations/siriagent/examples/coverage_quick.json').read_text(encoding='utf-8'))
    cases = [('quick-coverage', quick)]
    parameters = [dict(temporal_coverage_target_pct=0.01, area_target_pct=90),
                  dict(mrt_target_minutes=180, area_target_pct=90),
                  dict(n_budget=5, temporal_coverage_target_pct=0.01),
                  dict(n_budget=5, area_target_pct=90), dict(n_budget=5)]
    for i, fields in enumerate(parameters, 1):
        cases.append((f'quick-task{i}', quick | fields | {'operation': f'task{i}', 'method': 'greedy'}))
    for case in ('baseline', 'swath', 'swing'):
        body = json.loads((args.package / f'integrations/http/examples/hangzhou-{case}.json').read_text(encoding='utf-8'))
        cases.append((f'hangzhou-{case}', body))
    records = []
    for case, body in cases:
        begin = time.perf_counter()
        job = call('/api/v1/jobs', copy.deepcopy(body))
        deadline = time.monotonic() + 900
        while True:
            status = call(job['status_url'])
            if status['status'] == 'completed':
                break
            if status['status'] == 'failed':
                raise RuntimeError((case, status))
            if time.monotonic() > deadline:
                raise TimeoutError(case)
            time.sleep(0.5)
        result = call(job['result_url'])
        assert result['status'] == 'completed', case
        assert result['coverage_map']['geojson']['features'], case
        assert 'city_pass_window_table' in result and 'weak_areas' in result, case
        (args.output / f'{case}.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if case.startswith('hangzhou-'):
            # 与历史演示中已独立复现的公开汇总值比较，而不是读取当前引擎中间量。
            expected = {'hangzhou-baseline': (0.1455, 1), 'hangzhou-swath': (0.2108, 1), 'hangzhou-swing': (0.4751, 3)}[case]
            cards = result['summary_cards']
            assert math.isclose(cards['city_mean_temporal_coverage_pct'], expected[0], abs_tol=1e-6), (case, cards)
            assert len(result['city_pass_window_table']) == expected[1], case
        item = {'case': case, 'job_id': job['job_id'], 'operation': result['operation'],
                'feasible': result['feasible'], 'windows': len(result['city_pass_window_table']),
                'map_cells': len(result['coverage_map']['geojson']['features']),
                'elapsed_s': round(time.perf_counter() - begin, 3)}
        records.append(item)
        (args.output / 'acceptance.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(item, ensure_ascii=False), flush=True)
    print(f'HTTP acceptance passed: {len(records)} scenarios')


if __name__ == '__main__':
    main()
