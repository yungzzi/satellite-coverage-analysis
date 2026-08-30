# -*- coding: utf-8 -*-
r"""区域逐星逐网格机会底座自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_regional_opportunities.py

本脚本只验第二阶段的数据底座，不执行 ILP，也不修改旧的单点覆盖路径：

1. 新 SimpleCone 网格矩阵的 any/mean 与旧多边形聚合结果逐时刻一致；
2. 分块大小改变不改变状态结果，稀疏窗口只保存非空卫星—网格组合；
3. Rectangular 与 SAR 的向量化区域判定分别和已验证复杂路径一致；
4. Targeted 指向被明确拒绝，避免把互斥逐点指向误报成城市同时覆盖。
5. 已有粗窗口可按需做 1 秒级边界二分精化，且不会改变窗口数量或网格维度。
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import coverage_analysis as cov
import regional_opportunities as ro
import satellite_loader as sl
import sensor_geometry as sg
from region_grid import build_clipped_grid, load_city_boundary


START = "2026-08-08 00:00:00"
END = "2026-08-09 00:00:00"


def fixture():
    ts = sl.load.timescale(builtin=True)
    tle_path = ROOT / "data" / "test_data" / "jilin1_all.tle"
    records = sl.parse_tle_text(tle_path.read_text(encoding="utf-8"), ts=ts)
    if not records:
        raise AssertionError("测试 TLE 没有解析出卫星。")
    boundary = load_city_boundary(ROOT / "data" / "geo" / "330100.json", adcode="330100")
    grid = build_clipped_grid(boundary)
    return ts, records[0], grid


def test_simple_matches_old_polygon_aggregate() -> None:
    ts, sat, grid = fixture()
    cfg = {"fov_half_angle_deg": 30.0, "min_elevation_deg": 0.0, "coarse_step_seconds": 60}
    dt_list = ro._half_open_times(ro._as_utc(START), ro._as_utc(END), 60)
    times = ts.from_datetimes(dt_list)
    states = ro.compute_satellite_grid_state(sat, grid, times, dt_list, cfg)
    lats = np.asarray([cell.representative_lat for cell in grid.cells])
    lons = np.asarray([cell.representative_lon for cell in grid.cells])
    old_covered, old_ratio = cov.compute_covered_series(
        sat, times, dt_list, {"mode": "polygon", "lats": lats, "lons": lons}, cfg
    )
    assert np.array_equal(states.any(axis=1), old_covered)
    assert np.allclose(states.mean(axis=1), old_ratio)
    print("[通过] SimpleCone 与旧多边形聚合逐时刻一致")


def test_chunking_and_sparse_windows() -> None:
    ts, sat, grid = fixture()
    cfg = {"fov_half_angle_deg": 30.0, "min_elevation_deg": 0.0, "coarse_step_seconds": 60}
    dt_list = ro._half_open_times(ro._as_utc(START), ro._as_utc(END), 60)
    times = ts.from_datetimes(dt_list)
    full = ro.compute_satellite_grid_state(sat, grid, times, dt_list, cfg)
    chunked = np.concatenate(
        [
            ro.compute_satellite_grid_state(
                sat, grid, times, dt_list, cfg, cell_start=start, cell_stop=min(start + 73, len(grid.cells))
            )
            for start in range(0, len(grid.cells), 73)
        ],
        axis=1,
    )
    assert np.array_equal(full, chunked)
    store = ro.compute_regional_opportunities(
        [sat], grid, START, END, cfg, cell_chunk_size=73, timescale=ts
    )
    assert store.n_nonempty_pairs <= len(grid.cells)
    assert store.n_windows >= store.n_nonempty_pairs
    for window in store.iter_windows():
        assert window.start_utc < window.end_utc
        assert window.duration_s == (window.end_utc - window.start_utc).total_seconds()
    print(
        f"[通过] 分块结果一致；稀疏存储 {store.n_nonempty_pairs} 个非空组合、"
        f"{store.n_windows} 个窗口"
    )


def test_complex_profiles_match_existing_path() -> None:
    ts, sat, grid = fixture()
    dt_list = ro._half_open_times(
        ro._as_utc("2026-08-08 21:30:00"), ro._as_utc("2026-08-08 22:30:00"), 60
    )
    times = ts.from_datetimes(dt_list)
    lats = np.asarray([cell.representative_lat for cell in grid.cells[:32]])
    lons = np.asarray([cell.representative_lon for cell in grid.cells[:32]])
    region = {"mode": "polygon", "lats": lats, "lons": lons}
    for cfg in (
        {"sensor_type": "Rectangular", "sensor_params": [45.0, 45.0], "min_elevation_deg": 0.0},
        {"sensor_type": "SAR", "sensor_params": [12.0, 70.0, 30.0, 30.0], "min_elevation_deg": 0.0},
    ):
        states = ro.compute_satellite_grid_state(
            sat, grid, times, dt_list, cfg, cell_start=0, cell_stop=32
        )
        old_covered, old_ratio = sg.compute_covered_series_complex(sat, times, dt_list, region, cfg)
        assert np.array_equal(states.any(axis=1), old_covered)
        assert np.allclose(states.mean(axis=1), old_ratio)
    print("[通过] Rectangular、SAR 与既有复杂路径逐时刻一致")


def test_targeted_is_rejected() -> None:
    ts, sat, grid = fixture()
    dt_list = ro._half_open_times(ro._as_utc(START), ro._as_utc(END), 60)
    times = ts.from_datetimes(dt_list)
    cfg = {"sensor_type": "SimpleCone", "pointing": {"type": "Targeted"}}
    try:
        ro.compute_satellite_grid_state(sat, grid, times, dt_list, cfg, cell_start=0, cell_stop=1)
    except ValueError as exc:
        assert "Targeted" in str(exc)
    else:
        raise AssertionError("Targeted 指向没有被拒绝。")
    print("[通过] Targeted 指向明确拒绝，避免误报整市同时覆盖")


def test_window_boundary_refinement() -> None:
    """用一个真实窗口核对按需精化，不把全城 795 个窗口拖进本测试。"""

    ts, sat, grid = fixture()
    cfg = {"fov_half_angle_deg": 30.0, "min_elevation_deg": 0.0, "coarse_step_seconds": 60}
    coarse = ro.compute_regional_opportunities(
        [sat], grid, START, END, cfg, cell_chunk_size=128, timescale=ts
    )
    # 这颗测试星在整天内有窗口；只取第一个卫星—网格组合做物理精化。
    if not coarse.windows:
        raise AssertionError("测试时段没有生成可精化窗口。")
    (_sat_index, grid_index), windows = next(iter(coarse.windows.items()))
    original = windows[0]
    one_cell_grid = replace(grid, cells=(grid.cells[grid_index],))
    one_window = ro.AccessWindow(
        sat_index=0,
        sat_id=original.sat_id,
        sat_name=original.sat_name,
        grid_index=0,
        grid_id=one_cell_grid.cells[0].grid_id,
        start_utc=original.start_utc,
        end_utc=original.end_utc,
        duration_s=original.duration_s,
    )
    one_store = ro.OpportunityStore(
        satellites=(sat,),
        grid=one_cell_grid,
        start_utc=coarse.start_utc,
        end_utc=coarse.end_utc,
        detection_step_s=coarse.detection_step_s,
        decision_step_s=coarse.decision_step_s,
        windows={(0, 0): (one_window,)},
        metadata=dict(coarse.metadata),
    )
    refined = ro.refine_regional_opportunity_windows(
        one_store, refine_step_seconds=1.0, timescale=ts, cfg=cfg
    )
    assert refined.metadata["windows_refined"] is True
    assert refined.n_windows == 1
    result = refined.windows[(0, 0)][0]
    # 精化采用保守边界：进入不晚于粗起点，退出不晚于粗终点。
    assert result.start_utc <= original.start_utc
    assert result.end_utc <= original.end_utc
    assert result.end_utc > result.start_utc
    print("[通过] 区域窗口支持按需 1 秒边界精化")


def main() -> int:
    tests = [
        test_simple_matches_old_polygon_aggregate,
        test_chunking_and_sparse_windows,
        test_complex_profiles_match_existing_path,
        test_targeted_is_rejected,
        test_window_boundary_refinement,
    ]
    for test in tests:
        test()
    print(f"区域逐星逐网格机会自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
