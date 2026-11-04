"""标准库 HTTP 冒烟验收：提交真实任务、轮询并保存原始 JSON。"""
import argparse
import json
import os
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:8000')
    parser.add_argument('--input', type=Path, default=Path('integrations/siriagent/examples/coverage_quick.json'))
    parser.add_argument('--output', type=Path, default=Path('output/api-smoke-result.json'))
    parser.add_argument('--timeout', type=int, default=300)
    args = parser.parse_args()
    headers = {'Content-Type': 'application/json'}
    if os.environ.get('SAT_API_TOKEN'):
        headers['X-API-Key'] = os.environ['SAT_API_TOKEN']

    def call(path, data=None):
        request = urllib.request.Request(args.base_url.rstrip('/') + path, headers=headers,
                                         data=json.dumps(data).encode() if data is not None else None)
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    assert call('/readyz')['status'] == 'ready'
    job = call('/api/v1/jobs', json.loads(args.input.read_text(encoding='utf-8-sig')))
    print('job_id:', job['job_id'], flush=True)
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        status = call(job['status_url'])
        if status['status'] == 'failed':
            raise RuntimeError(status['error'])
        if status['status'] == 'completed':
            result = call(job['result_url'])
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'status': result['status'], 'operation': result['operation'],
                              'feasible': result['feasible'], 'windows': len(result['city_pass_window_table']),
                              'map_cells': len(result['coverage_map']['geojson']['features']),
                              'timings_s': result['timings_s']}, ensure_ascii=False))
            return
        time.sleep(0.5)
    raise TimeoutError('客户端等待超时；服务端任务不一定已停止，可按 job_id 查询。')


if __name__ == '__main__':
    main()
