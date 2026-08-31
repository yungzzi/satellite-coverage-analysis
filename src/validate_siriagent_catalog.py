# -*- coding: utf-8 -*-
"""SIRIAgent 110 星目录、TLE、候选筛选和 Profile 映射自检。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import coverage_analysis as cov  # noqa: E402
import regional_opportunities as ro  # noqa: E402
import siriagent_catalog as sc  # noqa: E402
from siriagent_skill.facade import _load_satellites  # noqa: E402
from siriagent_skill.runner import run_skill  # noqa: E402
from siriagent_skill.schemas import normalize_request  # noqa: E402


def test_catalog_and_tle_integrity() -> None:
    catalog = sc.load_catalog(ROOT)
    rows = catalog["satellites"]
    quality = catalog["quality_summary"]
    assert len(rows) == 110
    assert quality["tle_matched_count"] == 110
    assert quality["sensor_class_counts"] == {
        "SAR": 3,
        "可见光": 101,
        "红外": 2,
        "高光谱": 4,
    }
    assert quality["platform_period_present_count"] == 31
    assert quality["platform_period_mismatch_gt_5_min_count"] == 27
    periods = [float(row["tle_orbit_period_min"]) for row in rows]
    assert 91.0 < min(periods) < max(periods) < 111.0
    print("[通过] 平台目录 110 颗、TLE 110/110；周期真值全部由 TLE 推导")


def test_candidate_filter_and_profile_completeness() -> None:
    catalog = sc.load_catalog(ROOT)
    default_rows = sc.select_entries(
        catalog,
        sensor_type="可见光",
        max_resolution_m=1.0,
    )
    assert len(default_rows) == 88

    all_rows = sc.select_entries(catalog, sensor_type="全部", max_resolution_m=10_000.0)
    ready, blocked = sc.build_catalog_overrides(all_rows)
    assert len(ready) == 107
    assert {item["norad_id"] for item in blocked} == {41727, 44312, 49495}
    assert all(item["sensor_type"] == "Rectangular" for item in ready)

    sar_row = sc.select_entries(
        catalog,
        sensor_type="SAR",
        max_resolution_m=1.0,
        satellite_ids=[41727],
    )
    ready_sar, blocked_sar = sc.build_catalog_overrides(
        sar_row,
        user_overrides=[
            {
                "norad_id": 41727,
                "sensor_type": "SAR",
                "sensor_params": [12.0, 70.0, 30.0, 30.0],
            }
        ],
    )
    assert len(ready_sar) == 1 and not blocked_sar
    assert ready_sar[0]["sensor_type"] == "SAR"
    print("[通过] 默认可见光≤1 m 为 88 颗；107 颗目录派生矩形，3 颗 SAR 严格缺参")


def test_rectangular_swath_is_cross_track_not_circle() -> None:
    altitude = np.asarray([500.0])
    cross_half = float(cov.swath_to_half_angle_deg(altitude, 60.0)[0])
    cross_angles = np.radians([cross_half * 0.8, cross_half * 1.2])
    u_s = np.zeros((1, 2, 3), dtype=float)
    u_s[0, :, 1] = np.sin(cross_angles)
    u_s[0, :, 2] = np.cos(cross_angles)
    covered = ro._complex_pattern_grid(
        u_s,
        np.full((1, 2), 45.0),
        np.zeros((1, 2, 3), dtype=float),
        np.asarray([[1.0, 0.0, 0.0]]),
        {
            "sensor_type": "Rectangular",
            "swath_km": 60.0,
            "along_track_half_angle_deg": 60.0,
        },
        altitude_km=altitude,
    )
    assert covered.tolist() == [[True, False]]
    print(f"[通过] 60 km 幅宽在 500 km 高度换算跨轨半角 {cross_half:.4f}°，按矩形判定")


def test_skill_catalog_defaults_and_sar_guard() -> None:
    request = normalize_request(
        {
            "operation": "coverage_only",
            "city": "宁波",
            "profile_slug": sc.CATALOG_PROFILE_SLUG,
        }
    )
    assert request.tle_source == "siriagent_110_snapshot"
    satellites, _, _, _, context = _load_satellites(request, ROOT)
    assert len(satellites) == 88
    assert context is not None and len(context["profile_overrides"]) == 88

    sar_error = run_skill(
        {
            "operation": "coverage_only",
            "city": "宁波",
            "profile_slug": sc.CATALOG_PROFILE_SLUG,
            "catalog_sensor_type": "SAR",
            "max_resolution_m": 2.0,
        },
        root=ROOT,
    )
    assert sar_error["status"] == "invalid_input"
    assert len(sar_error["blocked_satellites"]) == 3

    mismatched_tle = run_skill(
        {
            "operation": "coverage_only",
            "city": "宁波",
            "profile_slug": sc.CATALOG_PROFILE_SLUG,
            "tle_source": "jilin1_test",
        },
        root=ROOT,
    )
    assert mismatched_tle["status"] == "invalid_input"
    assert mismatched_tle["missing_tle_satellite_ids"]
    print("[通过] Skill 平台 Profile 自动配套 110 星 TLE；纯 SAR 缺参会在计算前拒绝")


def test_skill_catalog_end_to_end() -> None:
    result = run_skill(
        {
            "operation": "coverage_only",
            "city": "宁波",
            "start_utc": "2026-08-30T00:00:00Z",
            "analysis_horizon_h": 1,
            "grid_size_km": 30,
            "detection_step_s": 60,
            "decision_step_s": 60,
            "profile_slug": sc.CATALOG_PROFILE_SLUG,
        },
        root=ROOT,
    )
    assert result["status"] == "completed"
    assert result["input_summary"]["candidate_satellite_count"] == 88
    assert result["profile"]["catalog_mapping"]["generated_override_count"] == 88
    assert result["reproducibility"]["catalog_sha256"]
    assert result["satellite_table"]
    assert all(
        row["sensor_class"] == "可见光"
        and float(row["resolution_m"]) <= 1.0
        and float(row["swath_km"]) > 0.0
        for row in result["satellite_table"]
    )
    print("[通过] 宁波 1 小时快速端到端：88 颗候选、矩形幅宽、窗口和目录哈希完整")


def main() -> int:
    tests = [
        test_catalog_and_tle_integrity,
        test_candidate_filter_and_profile_completeness,
        test_rectangular_swath_is_cross_track_not_circle,
        test_skill_catalog_defaults_and_sar_guard,
        test_skill_catalog_end_to_end,
    ]
    for test in tests:
        test()
    print(f"SIRIAgent 平台目录自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
