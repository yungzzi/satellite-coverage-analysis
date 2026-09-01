# -*- coding: utf-8 -*-
"""生成可交给 SIRIAgent 研发的最小独立目录。

开发源仍只维护在 ``src/`` 与 ``integrations/siriagent/``。本脚本把运行必需的
代码和冻结数据复制到 ``dist/satellite_coverage_selection/``，避免手工复制漏文件，
也避免在 Git 中长期维护两份引擎源码。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


INTEGRATION_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = INTEGRATION_ROOT.parents[1]
MANIFEST_PATH = INTEGRATION_ROOT / "package_manifest.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "dist" / "satellite_coverage_selection"
MARKER_NAME = "VERSION.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_file(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(f"交付清单中的文件不存在：{source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _iter_data_files(patterns: Iterable[str]) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern in patterns:
        matches = sorted(path for path in PROJECT_ROOT.glob(pattern) if path.is_file())
        if not matches:
            raise FileNotFoundError(f"交付清单没有匹配到数据：{pattern}")
        for path in matches:
            files[path.relative_to(PROJECT_ROOT).as_posix()] = path
    return [files[key] for key in sorted(files)]


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _validate_existing_output(output: Path, package_name: str) -> None:
    """只允许替换由本脚本生成、且包名一致的目录。"""

    if not output.exists():
        return
    marker = output / MARKER_NAME
    if not output.is_dir() or not marker.is_file():
        raise RuntimeError(f"拒绝覆盖非本脚本生成的路径：{output}")
    try:
        metadata = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"交付目录标记不可读取，拒绝覆盖：{marker}") from exc
    if metadata.get("package_name") != package_name:
        raise RuntimeError(f"交付目录包名不一致，拒绝覆盖：{output}")


def build_package(output: Path = DEFAULT_OUTPUT) -> dict[str, object]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    package_name = str(manifest["package_name"])
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    _validate_existing_output(output, package_name)

    staging = Path(tempfile.mkdtemp(prefix=f".{package_name}-", dir=output.parent))
    copied: list[Path] = []
    try:
        for relative in (
            "skill.md",
            "requirements-skill.txt",
            "README_部署验收.md",
            "package_manifest.json",
        ):
            source = INTEGRATION_ROOT / relative
            destination = staging / relative
            _copy_file(source, destination)
            copied.append(destination)
        for folder in ("scripts", "examples"):
            for source in sorted((INTEGRATION_ROOT / folder).rglob("*")):
                if source.is_file() and "__pycache__" not in source.parts:
                    destination = staging / source.relative_to(INTEGRATION_ROOT)
                    _copy_file(source, destination)
                    copied.append(destination)

        engine_relatives = [Path(item) for item in manifest["engine_files"]]
        data_files = _iter_data_files(manifest["data_globs"])
        for relative in engine_relatives:
            source = PROJECT_ROOT / relative
            destination = staging / "engine" / relative
            _copy_file(source, destination)
            copied.append(destination)
        for source in data_files:
            relative = source.relative_to(PROJECT_ROOT)
            destination = staging / "engine" / relative
            _copy_file(source, destination)
            copied.append(destination)

        hashes = {
            path.relative_to(staging).as_posix(): _sha256(path)
            for path in sorted(copied)
        }
        version = {
            "schema_version": "siriagent_delivery_version_v1",
            "package_name": package_name,
            "package_version": manifest["package_version"],
            "built_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_git_commit": _git_commit(),
            "file_count": len(copied),
            "source_bytes": sum(path.stat().st_size for path in copied),
            "sha256": hashes,
            "excluded_runtime_features": manifest["excluded_runtime_features"],
        }
        (staging / MARKER_NAME).write_text(
            json.dumps(version, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if output.exists():
            shutil.rmtree(output)
        staging.replace(output)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise

    return {
        "status": "built",
        "output": str(output),
        "package_name": package_name,
        "package_version": manifest["package_version"],
        "file_count": version["file_count"],
        "source_bytes": version["source_bytes"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 SIRIAgent 独立交付包")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build_package(args.output), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

