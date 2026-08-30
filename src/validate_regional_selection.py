# -*- coding: utf-8 -*-
r"""区域选星任务②自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_regional_selection.py

用两颗测试卫星的两网格手算窗口做全部子集枚举，核对：

* 100% 面积目标时最少卫星数为 2；
* 90% 面积目标时最少卫星数仍为 2（两个等面积网格，单星只有 50%）；
* 50% 面积目标时最少卫星数为 1，用来覆盖降级模式；
* 贪心+反向删除和 K=1 滑动窗口 ILP 与穷举最优值一致；
* 真实杭州机会窗口能够直接交给任务②，而不会重新传播轨道。
"""

from __future__ import annotations

import itertools
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import regional_metrics as rm
import regional_opportunities as ro
import regional_selection as rs
import satellite_loader as sl
from region_grid import build_clipped_grid, load_city_boundary
from validate_regional_metrics import synthetic_store


def brute_force_minimum(store, *, mrt_target_s: float, area_target_pct: float) -> int | None:
    """仅用于两颗卫星的小例子，枚举全部子集得到基准最优值。"""

    for size in range(store.n_satellites + 1):
        for subset in itertools.combinations(range(store.n_satellites), size):
            metrics = rm.compute_regional_metrics(
                store, subset, mrt_target_s=mrt_target_s
            )
            if (metrics.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
                return size
    return None


def test_small_exact_agreement() -> None:
    store = synthetic_store()
    for target, expected in ((100.0, 2), (90.0, 2), (50.0, 1)):
        optimum = brute_force_minimum(store, mrt_target_s=1_800.0, area_target_pct=target)
        assert optimum == expected
        greedy = rs.greedy_select_mrt(
            store, mrt_target_s=1_800.0, area_target_pct=target
        )
        assert greedy.feasible
        assert len(greedy.selected_sat_indices) == optimum
        ilp = rs.ilp_select_mrt(
            store, mrt_target_s=1_800.0, area_target_pct=target, time_limit_s=30
        )
        if ilp.status not in {"unsupported", "skipped"}:
            assert ilp.feasible
            assert len(ilp.selected_sat_indices) == optimum
            assert ilp.status == "proven_optimal"
    result = rs.run_regional_mrt_selection(
        store, mrt_target_s=1_800.0, area_target_pct=100.0, method="both", time_limit_s=30
    )
    assert result.recommended.feasible
    assert len(result.recommended.selected_sat_indices) == 2
    print("[通过] 两网格穷举、贪心和 ILP 的 100%/90%/50% 最少卫星数一致")


def test_empty_and_invalid_requests() -> None:
    store = synthetic_store()
    zero = rs.run_regional_mrt_selection(
        store, mrt_target_s=3_600.0, area_target_pct=100.0, method="greedy"
    )
    assert zero.recommended.feasible
    assert zero.recommended.selected_sat_indices == ()
    try:
        rs.run_regional_mrt_selection(
            store, mrt_target_s=1_800.0, area_target_pct=100.0, min_simultaneous=2
        )
    except NotImplementedError as exc:
        assert "K=1" in str(exc)
    else:
        raise AssertionError("K>1 没有明确拒绝。")
    print("[通过] MRT≥分析时长的空组合和 K>1 边界")


def test_real_opportunity_store() -> None:
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
    result = rs.run_regional_mrt_selection(
        store, mrt_target_s=1_800.0, area_target_pct=90.0, method="greedy"
    )
    assert len(result.recommended.metrics.grid_metrics) == len(grid.cells)
    assert 0.0 <= result.recommended.achieved_area_pct <= 100.0
    print(f"[通过] 真实杭州机会窗口可进入任务②（{len(grid.cells)} 个网格）")


def main() -> int:
    tests = [test_small_exact_agreement, test_empty_and_invalid_requests, test_real_opportunity_store]
    for test in tests:
        test()
    print(f"区域选星任务②自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
