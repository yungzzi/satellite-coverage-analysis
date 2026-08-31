# -*- coding: utf-8 -*-
"""卫星覆盖 Skill 的稳定启动器。

项目内使用时会根据脚本位置自动找到引擎；把 Skill 安装到全局目录后，也可以通过当前工作目录、
``--project-root`` 或 ``SATELLITE_COVERAGE_PROJECT_ROOT`` 指向引擎仓库。
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Iterable


SKILL_NAME = "satellite-coverage-selection"
PROJECT_ROOT_ENV = "SATELLITE_COVERAGE_PROJECT_ROOT"
REQUIRED_PROJECT_PATHS = (
    "src/siriagent_skill/runner.py",
    "src/regional_opportunities.py",
    "src/regional_metrics.py",
    "src/regional_selection.py",
    "data/geo/metadata/zhejiang_boundaries.json",
    "data/platform/siriagent_110_catalog.json",
    "data/test_data/siriagent_110_snapshot.tle",
)
REQUIRED_PYTHON_MODULES = (
    "numpy",
    "skyfield",
    "sgp4",
    "shapely",
    "pyproj",
    "pulp",
)


def _configure_utf8() -> None:
    """机器接口固定 UTF-8，避免 Windows 中文输出被默认代码页破坏。"""

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")


def _explicit_project_root(argv: list[str]) -> Path | None:
    for index, argument in enumerate(argv):
        if argument == "--project-root" and index + 1 < len(argv):
            return Path(argv[index + 1]).expanduser().resolve()
        if argument.startswith("--project-root="):
            return Path(argument.split("=", 1)[1]).expanduser().resolve()
    return None


def _walk_up(path: Path) -> Iterable[Path]:
    resolved = path.expanduser().resolve()
    if resolved.is_file():
        resolved = resolved.parent
    yield resolved
    yield from resolved.parents


def _looks_like_project_root(path: Path) -> bool:
    return all((path / relative).is_file() for relative in REQUIRED_PROJECT_PATHS[:4])


def _locate_project_root(argv: list[str]) -> tuple[Path | None, str | None, list[str]]:
    """按显式参数、环境变量、Skill 所在位置、当前目录依次寻找引擎仓库。"""

    explicit = _explicit_project_root(argv)
    if explicit is not None:
        return (
            (explicit, "--project-root", [str(explicit)])
            if _looks_like_project_root(explicit)
            else (None, "--project-root", [str(explicit)])
        )
    environment = os.environ.get(PROJECT_ROOT_ENV, "").strip()
    if environment:
        configured = Path(environment).expanduser().resolve()
        return (
            (configured, PROJECT_ROOT_ENV, [str(configured)])
            if _looks_like_project_root(configured)
            else (None, PROJECT_ROOT_ENV, [str(configured)])
        )
    candidates: list[tuple[Path, str]] = []
    candidates.extend((path, "skill_location") for path in _walk_up(Path(__file__)))
    candidates.extend((path, "current_working_directory") for path in _walk_up(Path.cwd()))

    searched: list[str] = []
    seen: set[str] = set()
    for candidate, source in candidates:
        key = str(candidate).casefold()
        if key in seen:
            continue
        seen.add(key)
        searched.append(str(candidate))
        if _looks_like_project_root(candidate):
            return candidate, source, searched
    return None, None, searched


def _module_checks(project_root: Path) -> dict[str, bool]:
    source = str(project_root / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    return {
        module: importlib.util.find_spec(module) is not None
        for module in REQUIRED_PYTHON_MODULES
    }


def _cbc_available() -> bool:
    try:
        import pulp

        return bool(pulp.PULP_CBC_CMD(msg=False).available())
    except Exception:
        return False


def _self_check(
    project_root: Path | None,
    source: str | None,
    searched: list[str],
) -> dict[str, object]:
    if project_root is None:
        return {
            "status": "invalid_environment",
            "skill": SKILL_NAME,
            "message": "没有找到卫星覆盖引擎项目根目录。",
            "project_root_source": source,
            "searched_paths": searched,
            "hints": [
                "在卫星覆盖性分析项目目录中运行本脚本。",
                f"或设置环境变量 {PROJECT_ROOT_ENV}。",
                "或传入 --project-root <项目根目录>。",
            ],
        }

    project_files = {
        relative: (project_root / relative).is_file()
        for relative in REQUIRED_PROJECT_PATHS
    }
    python_modules = _module_checks(project_root)
    cbc_available = _cbc_available()
    ready = all(project_files.values()) and all(python_modules.values()) and cbc_available
    hints: list[str] = []
    if not all(project_files.values()):
        hints.append("项目文件不完整；请使用包含 src/ 与 data/ 的完整引擎仓库。")
    if not all(python_modules.values()):
        missing = [name for name, available in python_modules.items() if not available]
        hints.append(f"缺少 Python 依赖：{', '.join(missing)}；请安装项目 requirements.txt。")
    if not cbc_available:
        hints.append("PuLP 的 CBC 求解器不可用；贪心仍可运行，但 ILP 任务不能完整验收。")
    return {
        "status": "ok" if ready else "invalid_environment",
        "skill": SKILL_NAME,
        "project_root": str(project_root),
        "project_root_source": source,
        "python_executable": sys.executable,
        "checks": {
            "project_files": project_files,
            "python_modules": python_modules,
            "cbc_solver": cbc_available,
        },
        "ready_for_direct_agent_use": ready,
        "hints": hints,
    }


def _print_json(payload: dict[str, object], pretty: bool) -> None:
    print(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2 if pretty else None,
            allow_nan=False,
        )
    )


def main() -> int:
    _configure_utf8()
    argv = list(sys.argv[1:])
    project_root, root_source, searched = _locate_project_root(argv)
    if "--self-check" in argv:
        result = _self_check(project_root, root_source, searched)
        _print_json(result, pretty="--pretty" in argv)
        return 0 if result["status"] == "ok" else 1
    if project_root is None:
        result = _self_check(None, root_source, searched)
        _print_json(result, pretty="--pretty" in argv)
        return 1

    source = str(project_root / "src")
    if source not in sys.path:
        sys.path.insert(0, source)
    from siriagent_skill.runner import main as engine_main

    return engine_main(["--project-root", str(project_root), *argv])



if __name__ == "__main__":
    raise SystemExit(main())
