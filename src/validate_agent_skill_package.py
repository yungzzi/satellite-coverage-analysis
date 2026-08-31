# -*- coding: utf-8 -*-
"""验证项目 Skill 能被另一套本地 Agent 稳定发现和启动。"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / ".agents" / "skills" / "satellite-coverage-selection"
RUNNER = SKILL / "scripts" / "run_skill.py"
OPENAI_YAML = SKILL / "agents" / "openai.yaml"


def _run(
    runner: Path,
    *arguments: str,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-B", str(runner), *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(
            f"启动器 stdout 不是单一 JSON：{completed.stdout!r}；stderr={completed.stderr!r}"
        ) from exc
    return completed.returncode, payload


def test_project_skill_metadata() -> None:
    assert (SKILL / "SKILL.md").is_file()
    assert (SKILL / "references" / "input-output-schema.md").is_file()
    assert (SKILL / "references" / "runtime-and-portability.md").is_file()
    text = OPENAI_YAML.read_text(encoding="utf-8")
    assert "display_name:" in text
    assert "$satellite-coverage-selection" in text
    assert "allow_implicit_invocation: true" in text
    print("[通过] Skill 说明、运行参考和 Agent UI 元数据齐全")


def test_project_local_discovery() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        cwd = Path(temporary)
        code, result = _run(RUNNER, "--self-check", cwd=cwd)
        assert code == 0
        assert result["status"] == "ok"
        assert result["ready_for_direct_agent_use"] is True
        assert result["project_root_source"] == "skill_location"

        code, described = _run(RUNNER, "--describe", cwd=cwd)
        assert code == 0
        assert described["status"] == "ok"
        assert len(described["tasks"]) == 6
    print("[通过] 从项目外工作目录调用项目内 Skill，仍能自动发现引擎并描述六项任务")


def test_global_skill_handoff() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        temporary_root = Path(temporary)
        copied_runner = (
            temporary_root
            / ".codex"
            / "skills"
            / "satellite-coverage-selection"
            / "scripts"
            / "run_skill.py"
        )
        copied_runner.parent.mkdir(parents=True)
        shutil.copy2(RUNNER, copied_runner)

        # 只复制 Skill 启动器、不提供引擎时，必须明确失败，不能错误声称可运行。
        code, missing = _run(copied_runner, "--self-check", cwd=temporary_root)
        assert code == 1
        assert missing["status"] == "invalid_environment"

        # 全局安装后可通过显式项目根目录或环境变量连接同一份引擎。
        code, explicit = _run(
            copied_runner,
            "--self-check",
            "--project-root",
            str(ROOT),
            cwd=temporary_root,
        )
        assert code == 0
        assert explicit["ready_for_direct_agent_use"] is True
        assert explicit["project_root_source"] == "--project-root"

        environment = os.environ.copy()
        environment["SATELLITE_COVERAGE_PROJECT_ROOT"] = str(ROOT)
        code, described = _run(
            copied_runner,
            "--describe",
            cwd=temporary_root,
            environment=environment,
        )
        assert code == 0
        assert described["status"] == "ok"
        assert described["skill"] == "satellite-coverage-selection"

        code, completed = _run(
            copied_runner,
            "--input-file",
            str(SKILL / "references" / "example-coverage-quick.json"),
            cwd=temporary_root,
            environment=environment,
        )
        assert code == 0
        assert completed["status"] == "completed"
        assert completed["city"]["adcode"] == "330100"
        assert completed["city_pass_window_table"]
    print("[通过] 全局 Skill 能明确识别缺失引擎，并可用参数/环境变量连接项目")


def main() -> int:
    tests = (
        test_project_skill_metadata,
        test_project_local_discovery,
        test_global_skill_handoff,
    )
    for test in tests:
        test()
    print(f"Agent Skill 包装自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
