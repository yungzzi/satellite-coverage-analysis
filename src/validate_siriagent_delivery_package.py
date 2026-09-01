# -*- coding: utf-8 -*-
"""从包外验证 SIRIAgent 最小交付目录确实能够独立运行。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BUILDER = PROJECT_ROOT / "integrations" / "siriagent" / "build_package.py"


def _command(*args: object, cwd: Path, timeout: int = 180) -> dict:
    completed = subprocess.run(
        [sys.executable, *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"命令失败（{completed.returncode}）：{' '.join(map(str, args))}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AssertionError(f"stdout 不是单一 JSON：\n{completed.stdout}") from exc


def main() -> int:
    checks: list[str] = []
    with tempfile.TemporaryDirectory(prefix="siriagent-delivery-check-") as raw_temp:
        temp = Path(raw_temp)
        package = temp / "satellite_coverage_selection"
        build = _command(BUILDER, "--output", package, cwd=PROJECT_ROOT)
        assert build["status"] == "built"
        required = [
            "skill.md",
            "requirements-skill.txt",
            "VERSION.json",
            "scripts/run_satellite_coverage_selection.py",
            "engine/src/siriagent_skill/runner.py",
            "engine/data/geo/330100.json",
            "engine/data/test_data/siriagent_110_snapshot.tle",
        ]
        assert all((package / relative).is_file() for relative in required)
        skill_text = (package / "skill.md").read_text(encoding="utf-8")
        operation_question = skill_text.split("  - id: operation", 1)[1].split(
            "  - id: city_adcode", 1
        )[0]
        assert "default:" not in operation_question
        assert "coverage_only" not in operation_question
        assert operation_question.count("      - value: task_") == 5
        assert "MRT" not in operation_question and "ART" not in operation_question
        assert "90%" in operation_question and "真实面积" in operation_question
        forbidden = ["loader_app.py", "atk_parallel", "output", "tle_cache"]
        package_files = [path.relative_to(package).as_posix() for path in package.rglob("*")]
        assert not any(any(word in relative for word in forbidden) for relative in package_files)
        checks.append("交付目录与瘦身边界")

        launcher = package / "scripts" / "run_satellite_coverage_selection.py"
        health = _command(launcher, "--self-check", cwd=temp)
        assert health["ready"] is True
        assert health["cbc_available"] is True
        checks.append("Python 依赖与 ILP/CBC")

        description = _command(launcher, "--describe", cwd=temp)["result"]
        operations = {item["operation"] for item in description["tasks"]}
        assert operations == {
            "coverage_only",
            "task_1_min_satellites_temporal",
            "task_2_min_satellites_mrt",
            "task_3_max_qualified_area",
            "task_4_min_area_quantile_mrt",
            "task_5_min_area_weighted_art",
        }
        checks.append("覆盖分析与五项选星注册能力")

        default_behavior = _command(
            launcher,
            "--input-json",
            '{"city":"宁波"}',
            cwd=temp,
        )["result"]
        assert default_behavior["status"] == "needs_input"
        assert default_behavior["operation"] is None
        assert default_behavior["missing_fields"] == ["operation"]
        assert len(default_behavior["selection_task_choices"]) == 5
        assert all(
            item["operation"].startswith("task_")
            for item in default_behavior["selection_task_choices"]
        )
        assert "coverage_only" not in default_behavior["parameter_hints"]["operation"]["allowed"]

        coverage = _command(
            launcher,
            "--input-file",
            package / "examples" / "coverage_quick.json",
            cwd=temp,
        )
        coverage_result = coverage["result"]
        assert coverage_result["status"] == "completed"
        assert coverage_result["summary_cards"]
        assert "city_pass_window_table" in coverage_result
        assert "weak_areas" in coverage_result
        assert coverage["view_html"].startswith("<!doctype html>")
        checks.append("覆盖分析与人类可读输出")

        selection = _command(
            launcher,
            "--input-file",
            package / "examples" / "task2_quick.json",
            cwd=temp,
        )
        selection_result = selection["result"]
        assert selection_result["status"] == "completed"
        assert selection_result["selection"] is not None
        assert "feasible" in selection_result
        assert "optimality" in selection_result
        assert selection["view_html"].startswith("<!doctype html>")
        checks.append("任务②选星与平台返回包络")

    print(
        json.dumps(
            {"status": "passed", "checks": checks, "passed": len(checks), "total": 5},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
