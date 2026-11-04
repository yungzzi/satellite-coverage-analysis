"""将 api_smoke 保存的杭州三方案结果与历史演示快照对照，不修改原始文件。"""
import argparse
import json
import math
from pathlib import Path


def compare(a, b, path='result'):
    if isinstance(a, bool) or isinstance(b, bool):
        assert a == b, path
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        assert math.isclose(a, b, rel_tol=0, abs_tol=1e-6), (path, a, b)
    elif isinstance(a, dict) and isinstance(b, dict):
        assert a.keys() == b.keys(), path
        for key in a:
            compare(a[key], b[key], f'{path}.{key}')
    elif isinstance(a, list) and isinstance(b, list):
        assert len(a) == len(b), path
        for i, (x, y) in enumerate(zip(a, b)):
            compare(x, y, f'{path}[{i}]')
    else:
        assert a == b, (path, a, b)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference-dir', type=Path, required=True)
    parser.add_argument('--result-dir', type=Path, default=Path('output'))
    args = parser.parse_args()
    blocks = ['summary_cards', 'satellite_table', 'city_pass_window_table', 'weak_areas',
              'coverage_map', 'profile', 'reproducibility', 'city']
    for case in ['baseline', 'swath', 'swing']:
        old = json.loads((args.reference_dir / f'{case}.result.json').read_text(encoding='utf-8-sig'))
        new = json.loads((args.result_dir / f'api-hangzhou-{case}.json').read_text(encoding='utf-8-sig'))
        for key in blocks:
            compare(old[key], new[key], f'{case}.{key}')
        print(json.dumps({'case': case, 'matched_blocks': blocks, 'summary_cards': new['summary_cards']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
