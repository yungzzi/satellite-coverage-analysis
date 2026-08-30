# -*- coding: utf-8 -*-
r"""区域指标层自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_regional_metrics.py

先用两个等面积网格构造可手算的小例子，再用真实杭州边界和测试 TLE 串起第二阶段
机会窗口，确认本模块只消费窗口、不重复传播或调用选星器。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import box
from shapely.ops import transform
from pyproj import CRS, Transformer

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import regional_metrics as rm
import regional_opportunities as ro
import satellite_loader as sl
from region_grid import (
    CityBoundary,
    WGS84_CRS,
    ZHEJIANG_EQUAL_AREA_CRS,
    _hash_geometry,
    build_clipped_grid,
    load_city_boundary,
)


def tiny_grid():
    projected = CRS.from_user_input(ZHEJIANG_EQUAL_AREA_CRS)
    wgs84 = CRS.from_user_input(WGS84_CRS)
    to_wgs84 = Transformer.from_crs(projected, wgs84, always_xy=True)
    geometry = transform(to_wgs84.transform, box(0.0, 0.0, 10_000.0, 5_000.0))
    boundary = CityBoundary(
        adcode="test",
        city_name="测试区域",
        geometry_source=geometry,
        boundary_hash=_hash_geometry(geometry),
        source_crs=WGS84_CRS,
    )
    return build_clipped_grid(boundary, grid_size_m=5_000.0)


def synthetic_store() -> ro.OpportunityStore:
    grid = tiny_grid()
    assert len(grid.cells) == 2
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=1)

    def window(sat_index, grid_index, start_minute, end_minute):
        begin = start + timedelta(minutes=start_minute)
        finish = start + timedelta(minutes=end_minute)
        cell = grid.cells[grid_index]
        return ro.AccessWindow(
            sat_index=sat_index,
            sat_id=100 + sat_index,
            sat_name=f"测试星{sat_index}",
            grid_index=grid_index,
            grid_id=cell.grid_id,
            start_utc=begin,
            end_utc=finish,
            duration_s=(finish - begin).total_seconds(),
        )

    windows = {
        # 网格 0：两颗星的窗口 [0,20)、[10,40) 合并为 [0,40)。
        (0, 0): (window(0, 0, 0, 20),),
        (1, 0): (window(1, 0, 10, 40),),
        # 网格 1：只有 [10,30)。
        (0, 1): (window(0, 1, 10, 30),),
    }
    return ro.OpportunityStore(
        satellites=("测试星0", "测试星1"),
        grid=grid,
        start_utc=start,
        end_utc=end,
        detection_step_s=60,
        decision_step_s=60,
        windows=windows,
    )


def test_union_gaps_and_area_weighting() -> None:
    result = rm.compute_regional_metrics(
        synthetic_store(),
        temporal_coverage_target_pct=30.0,
        mrt_target_s=30 * 60.0,
        area_coverage_target_pct=50.0,
    )
    first, second = result.grid_metrics
    assert np.isclose(first.temporal_coverage_pct, 100.0 * 40.0 / 60.0)
    assert np.isclose(second.temporal_coverage_pct, 100.0 * 20.0 / 60.0)
    assert np.isclose(first.mrt_s, 20.0 * 60.0)
    assert np.isclose(second.mrt_s, 30.0 * 60.0)
    assert first.qualified is True and second.qualified is True
    assert np.isclose(result.city.qualified_area_pct, 100.0)
    assert np.isclose(result.city.area_weighted_temporal_coverage_pct, 50.0)
    assert np.isclose(result.city.average_observable_duration_s, 30.0 * 60.0)
    # 两个等面积网格同时覆盖的是 [10,30)，即 20 分钟 / 1 小时。
    assert np.isclose(result.city.full_area_coverage_time_pct, 100.0 / 3.0)
    # 面积阈值为 50% 时，[0,10)、[10,30)、[30,40) 都达标，共 40 分钟。
    assert np.isclose(result.city.area_coverage_time_pct, 200.0 / 3.0)
    print("[通过] 窗口并集、首尾空窗、MRT/ART、面积加权和达标面积")


def test_k_overlap_and_empty_selection() -> None:
    store = synthetic_store()
    k2 = rm.compute_regional_metrics(store, min_simultaneous=2)
    assert np.isclose(k2.grid_metrics[0].temporal_coverage_pct, 100.0 / 6.0)
    assert np.isclose(k2.grid_metrics[1].temporal_coverage_pct, 0.0)
    assert np.isclose(k2.city.full_area_coverage_time_pct, 0.0)
    empty = rm.compute_regional_metrics(store, selected_sat_indices=[])
    assert all(item.temporal_coverage_pct == 0.0 for item in empty.grid_metrics)
    assert all(np.isclose(item.mrt_s, 3600.0) for item in empty.grid_metrics)
    assert np.isclose(empty.city.area_weighted_temporal_coverage_pct, 0.0)
    print("[通过] K=2 重叠扫描和空选星集合")


def test_real_opportunity_store_can_be_consumed() -> None:
    ts = sl.load.timescale(builtin=True)
    tle_path = ROOT / "data" / "test_data" / "jilin1_all.tle"
    sat = sl.parse_tle_text(tle_path.read_text(encoding="utf-8"), ts=ts)[0]
    grid = build_clipped_grid(
        load_city_boundary(ROOT / "data" / "geo" / "330100.json", adcode="330100")
    )
    cfg = {"fov_half_angle_deg": 30.0, "min_elevation_deg": 0.0, "coarse_step_seconds": 60}
    store = ro.compute_regional_opportunities(
        [sat], grid, "2026-08-08 00:00:00", "2026-08-08 01:00:00", cfg,
        cell_chunk_size=128, timescale=ts,
    )
    result = rm.compute_regional_metrics(store)
    assert len(result.grid_metrics) == len(grid.cells)
    assert 0.0 <= result.city.area_weighted_temporal_coverage_pct <= 100.0
    assert result.city.average_observable_duration_s >= 0.0
    print(f"[通过] 真实杭州机会窗口可消费（{len(grid.cells)} 个网格）")


def main() -> int:
    tests = [
        test_union_gaps_and_area_weighting,
        test_k_overlap_and_empty_selection,
        test_real_opportunity_store_can_be_consumed,
    ]
    for test in tests:
        test()
    print(f"区域指标自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
