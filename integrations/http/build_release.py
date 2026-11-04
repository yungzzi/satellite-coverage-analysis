"""从干净的已提交源码生成独立 HTTP 后端、OpenAPI、哈希清单和 ZIP。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[2]
NAME = 'satellite-coverage-backend'
ENGINE = ['coverage_analysis', 'sensor_geometry', 'region_grid', 'regional_opportunities',
          'regional_metrics', 'regional_selection', 'satellite_loader', 'siriagent_catalog']
FILES = ['Dockerfile', 'compose.yaml', '.dockerignore', 'requirements-api.lock',
         'scripts/api_smoke.py', 'scripts/accept_backend_release.py', 'integrations/http/verify_package.py',
         'integrations/http/README_交付使用.md', 'integrations/http/RELEASE_NOTES.md',
         'integrations/http/DATA_NOTICE.md',
         'docs/current/计算后端API部署与验收.md', 'docs/current/计算后端API验证记录.md',
         'integrations/siriagent/examples/coverage_quick.json',
         'integrations/siriagent/examples/task2_quick.json']
PATTERNS = ['src/coverage_api/*.py', 'src/siriagent_skill/*.py',
            'data/geo/*.json', 'data/geo/metadata/zhejiang_boundaries.json',
            'data/platform/siriagent_110_catalog.json', 'data/test_data/*.tle',
            'integrations/http/examples/*.json']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def build(output, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?', version):
        raise ValueError('版本应形如 0.1.0-rc.1')
    if git('status', '--porcelain', '--untracked-files=normal'):
        raise RuntimeError('先提交源码再构建，避免交付包与 source_git_commit 不一致')
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    folder_name = f'{NAME}-{version}'
    package = output / folder_name
    archive = output / f'{folder_name}.zip'
    if package.exists() or archive.exists():
        raise FileExistsError('拒绝覆盖已有交付版本，请使用新的输出目录或版本')
    paths = set(FILES + [f'src/{name}.py' for name in ENGINE])
    for pattern in PATTERNS:
        matches = list(ROOT.glob(pattern))
        if not matches:
            raise FileNotFoundError(pattern)
        paths.update(p.relative_to(ROOT).as_posix() for p in matches if p.is_file())
    with tempfile.TemporaryDirectory(prefix='.backend-release-', dir=output) as temp:
        staging = Path(temp) / folder_name
        staging.mkdir()
        for relative in sorted(paths):
            # 只复制白名单中的受版本控制文件；不会带出密钥、缓存和本机结果。
            git('ls-files', '--error-unmatch', relative)
            source = ROOT / relative
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        shutil.copyfile(staging / 'integrations/http/README_交付使用.md', staging / 'README.md')
        shutil.copyfile(staging / 'integrations/http/RELEASE_NOTES.md', staging / 'RELEASE_NOTES.md')
        # 与在线 /docs 使用同一模型。导入只生成 schema，不启动 lifespan/计算 worker。
        env = os.environ.copy()
        env.update(PYTHONPATH=str(staging / 'src'), SAT_PROJECT_ROOT=str(staging),
                   SAT_STATE_DIR=str(staging / 'output/api'), PYTHONIOENCODING='utf-8')
        code = "import json; from coverage_api.app import app; print(json.dumps(app.openapi(), ensure_ascii=False, indent=2))"
        schema = subprocess.check_output([sys.executable, '-c', code], cwd=staging, env=env)
        (staging / 'openapi.json').write_bytes(schema)
        from_schema = json.loads(schema)
        example_path = staging / 'examples'
        example_path.mkdir()
        examples = from_schema['paths']['/api/v1/jobs']['post']['requestBody']['content']['application/json']['examples']
        for name, example in examples.items():
            (example_path / f'{name}.json').write_text(
                json.dumps(example['value'], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        metadata = {'package_name': NAME, 'package_version': version,
                    'source_git_commit': git('rev-parse', 'HEAD'),
                    'api_schema_version': from_schema['info']['version'],
                    'validated_runtime_target': 'Python 3.12 Linux/amd64; Windows Python 3.13',
                    'distribution': 'source, frozen data, documentation; no image or environment',
                    'files': {p.relative_to(staging).as_posix(): digest(p)
                              for p in sorted(staging.rglob('*')) if p.is_file()}}
        (staging / 'VERSION.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        checksums = {p.relative_to(staging).as_posix(): digest(p)
                     for p in sorted(staging.rglob('*')) if p.is_file()}
        (staging / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in checksums.items()), encoding='utf-8')
        # 固定 ZIP 时间及排序，避免仅因文件时间戳不同而产生另一份包。
        with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(staging.rglob('*')):
                if path.is_file():
                    info = zipfile.ZipInfo(f'{folder_name}/{path.relative_to(staging).as_posix()}', (2026, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    bundle.writestr(info, path.read_bytes())
        staging.rename(package)
    checksum_path = output / f'{folder_name}.zip.sha256'
    checksum_path.write_text(f'{digest(archive)}  {archive.name}\n', encoding='utf-8')
    return {'folder': str(package), 'zip': str(archive), 'bytes': archive.stat().st_size,
            'sha256': digest(archive), 'source_git_commit': metadata['source_git_commit'],
            'files': len(checksums), 'examples': len(examples)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/backend')
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.version), ensure_ascii=False, indent=2))
