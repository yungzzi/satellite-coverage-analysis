"""无需第三方依赖：在任意当前目录核验解压包的 SHA256SUMS。"""
import argparse
import hashlib
from pathlib import Path, PurePosixPath


def verify(root):
    root = root.resolve()
    expected = set()
    for line in (root / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
        sha, name = line.split('  ', 1)
        relative = PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or name in expected:
            raise ValueError(f'非法/重复清单路径：{name}')
        expected.add(name)
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f'缺失或越界文件：{name}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
            raise ValueError(f'哈希不一致：{name}')
    print(f'Package integrity passed: {len(expected)} files')
    # 安装后会产生 .venv/__pycache__/output，故不把运行产物算成校验失败。
    return len(expected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    verify(parser.parse_args().root)
