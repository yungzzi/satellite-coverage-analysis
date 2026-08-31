# -*- coding: utf-8 -*-
r"""同机 Skill 的参数提示、五任务分派和真实调用链自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_siriagent_skill.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from siriagent_skill.facade import run_selection_task
from siriagent_skill.runner import run_skill
from siriagent_skill.schemas import normalize_request
from validate_regional_metrics import synthetic_store


def test_describe_and_missing_parameters() -> None:
    skill_file = ROOT / ".agents" / "skills" / "satellite-coverage-selection" / "SKILL.md"
    skill_text = skill_file.read_text(encoding="utf-8")
    assert skill_text.startswith("---\n")
    assert "\nname: satellite-coverage-selection\n" in skill_text
    assert "\ndescription:" in skill_text.split("---", 2)[1]
    for example_name in ("example-coverage-quick.json", "example-task2-quick.json"):
        example_path = skill_file.parent / "references" / example_name
        assert isinstance(json.loads(example_path.read_text(encoding="utf-8")), dict)

    described = run_skill({"action": "describe"}, root=ROOT)
    assert described["status"] == "ok"
    assert len(described["tasks"]) == 6
    assert described["mcp_required"] is False
    assert described["fastapi_required"] is False
    assert described["parameters"]["profile_slug"]["default"] == (
        "siriagent_catalog_swath_v1"
    )
    assert described["parameters"]["tle_source"]["default"] == (
        "siriagent_110_snapshot"
    )
    assert {item["adcode"] for item in described["available_cities"]} >= {
        "330000", "330100", "330200", "330300", "330400", "330500",
        "330600", "330700", "330800", "330900", "331000", "331100",
    }
    described_task5 = run_skill(
        {"action": "describe", "describe_operation": "task5"}, root=ROOT
    )
    assert described_task5["parameters"]["method"]["allowed"] == ["greedy"]

    missing = run_skill({"operation": "task2"}, root=ROOT)
    assert missing["status"] == "needs_input"
    assert missing["missing_fields"] == ["city_adcode", "mrt_target_minutes"]
    assert "mrt_target_minutes" in missing["parameter_hints"]

    invalid = run_skill(
        {"operation": "coverage_only", "city_adcode": "330100", "min_simultaneous": 4},
        root=ROOT,
    )
    assert invalid["status"] == "invalid_input"
    invalid_task5_method = run_skill(
        {"operation": "task5", "city_adcode": "330100", "n_budget": 1, "method": "ilp"},
        root=ROOT,
    )
    assert invalid_task5_method["status"] == "invalid_input"
    invalid_targeted = run_skill(
        {
            "operation": "coverage_only",
            "city_adcode": "330100",
            "sensor_profile": {
                "pointing": {"type": "Targeted", "target_lat": 30.2, "target_lon": 120.1}
            },
        },
        root=ROOT,
    )
    assert invalid_targeted["status"] == "invalid_input"
    default_request = normalize_request({"operation": "coverage_only", "city": "宁波"})
    assert default_request.city_adcode == "330200"
    assert default_request.profile_slug == "siriagent_catalog_swath_v1"
    assert default_request.tle_source == "siriagent_110_snapshot"
    print("[通过] describe、任务参数提示、缺参和 K 范围校验")


def _request(operation: str, **values):
    payload = {
        "operation": operation,
        "city_adcode": "330100",
        "method": "greedy",
        "allow_area_fallback": False,
        **values,
    }
    return normalize_request(payload)


def test_all_five_tasks_dispatch() -> None:
    store = synthetic_store()
    requests = [
        _request("task1", temporal_coverage_target_pct=30.0),
        _request("task2", mrt_target_minutes=30.0),
        _request("task3", n_budget=1, temporal_coverage_target_pct=30.0),
        _request("task4", n_budget=1, area_target_pct=50.0),
        _request("task5", n_budget=1),
    ]
    for request in requests:
        outcome = run_selection_task(request, store)
        assert outcome.response["objective"] == request.operation
        assert isinstance(outcome.selected_sat_indices, tuple)
        assert outcome.metrics.min_simultaneous == 1
        assert outcome.optimality
    k3 = normalize_request(
        {
            "operation": "task3",
            "city_adcode": "330100",
            "n_budget": 1,
            "temporal_coverage_target_pct": 10,
            "min_simultaneous": 3,
        }
    )
    assert k3.min_simultaneous == 3
    print("[通过] 五项任务全部映射到现有区域选星入口，K=3 可进入 Schema")


def test_real_city_direct_skill() -> None:
    common = {
        "city_adcode": "330100",
        "start_utc": "2026-08-08T00:00:00Z",
        "analysis_horizon_h": 8,
        "profile_slug": "optical_rectangular_validation_v1",
        "grid_size_km": 30,
        "detection_step_s": 60,
        "decision_step_s": 60,
        "debug": False,
    }
    result = run_skill({"operation": "coverage_only", **common}, root=ROOT)
    assert result["status"] == "completed", result
    assert result["operation"] == "coverage_only"
    assert result["input_summary"]["candidate_satellite_count"] == 58
    assert result["city"]["grid_cell_count"] > 0
    assert result["summary_cards"]["satellite_count"] == 58
    assert result["coverage_map"]["geojson"] is None
    assert result["debug_artifacts"] is None
    assert isinstance(result["satellite_table"], list)
    assert isinstance(result["city_pass_window_table"], list)
    assert isinstance(result["weak_areas"], list)
    assert result["satellite_table"]
    assert result["city_pass_window_table"]
    first_window = result["city_pass_window_table"][0]
    assert first_window["start_utc"] < first_window["end_utc"]
    assert first_window["duration_minutes"] > 0.0
    advanced = run_skill(
        {
            "operation": "coverage_only",
            "city_adcode": "330100",
            "satellite_ids": [40961],
            "start_utc": "2026-08-08T00:00:00Z",
            "analysis_horizon_h": 1,
            "grid_size_km": 30,
            "detection_step_s": 30,
            "sensor_profile": {
                "sensor_type": "Rectangular",
                "sensor_params": [45, 45],
                "min_elevation_deg": 0,
                "attitude": {"profile": 0},
            },
            "satellite_profile_overrides": [
                {"norad_id": 40961, "sensor_type": "SimpleCone", "sensor_params": [5]}
            ],
        },
        root=ROOT,
    )
    assert advanced["status"] == "completed", advanced
    assert advanced["input_summary"]["advanced_mode"] is True
    assert advanced["profile"]["mode"] == "advanced"
    assert advanced["profile"]["effective_config"]["sat_sensor_overrides"][0]["norad"] == 40961
    targeted = run_skill(
        {
            "operation": "coverage_only",
            "city_adcode": "330100",
            "satellite_ids": [40961],
            "start_utc": "2026-08-08T00:00:00Z",
            "analysis_horizon_h": 1,
            "grid_size_km": 30,
            "detection_step_s": 30,
            "sensor_profile": {
                "sensor_type": "Rectangular",
                "sensor_params": [45, 45],
                "pointing": {"type": "Targeted"},
                "attitude": {"profile": 0},
            },
        },
        root=ROOT,
    )
    assert targeted["status"] == "completed", targeted
    assert targeted["profile"]["targeting"]["definition"] == (
        "area_weighted_representative_point_centroid"
    )
    selected = run_skill(
        {"operation": "task5", "n_budget": 1, "method": "greedy", **common},
        root=ROOT,
    )
    assert selected["status"] == "completed", selected
    assert selected["operation"] == "task_5_min_area_weighted_art"
    assert selected["selection"]["objective"] == "task_5_min_area_weighted_art"
    assert selected["metrics_satellite_scope"] == "selected_satellites"
    print(
        "[通过] 杭州真实边界＋真实 TLE 通过同机 Skill 完成覆盖计算："
        f"{result['city']['grid_cell_count']} 格，{result['timings_s']['total']} 秒"
    )


def main() -> int:
    tests = [
        test_describe_and_missing_parameters,
        test_all_five_tasks_dispatch,
        test_real_city_direct_skill,
    ]
    for test in tests:
        test()
    print(f"SIRIAgent/Codex 同机 Skill 自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
