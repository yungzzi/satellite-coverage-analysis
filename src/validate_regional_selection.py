# -*- coding: utf-8 -*-
r"""区域选星任务①～⑤自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_regional_selection.py

用两颗测试卫星的两网格手算窗口做全部子集枚举，核对：

* 100% 面积目标时最少卫星数为 2；
* 90% 面积目标时最少卫星数仍为 2（两个等面积网格，单星只有 50%）；
* 50% 面积目标时最少卫星数为 1，用来覆盖降级模式；
* 任务①时间覆盖率门槛在相同小例子上与穷举一致；
* 任务③给定预算时，字典序目标与穷举一致；
* 任务④面积分位 MRT 的离散二分与窗口级面积分位值一致；
* 任务⑤面积加权 ART 的贪心、交换和反向删除与小规模穷举一致；
* 贪心+反向删除和 K=1 滑动窗口 ILP 与穷举最优值一致；
* 真实杭州机会窗口能够直接交给任务②，而不会重新传播轨道。
"""

from __future__ import annotations

import itertools
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np

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


def brute_force_temporal_minimum(
    store, *, temporal_coverage_target_pct: float, area_target_pct: float
) -> int | None:
    """任务①的小规模基准：枚举所有子集，寻找最少颗数。"""

    for size in range(store.n_satellites + 1):
        for subset in itertools.combinations(range(store.n_satellites), size):
            metrics = rm.compute_regional_metrics(
                store,
                subset,
                temporal_coverage_target_pct=temporal_coverage_target_pct,
            )
            if (metrics.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
                return size
    return None


def brute_force_budget_best(
    store,
    *,
    n_budget: int,
    temporal_coverage_target_pct: float,
    exact_budget: bool = False,
) -> tuple[tuple[float, float], tuple[int, ...]]:
    """任务③小规模基准：穷举预算内组合，按面积→平均覆盖率排序。"""

    best_score: tuple[float, float] | None = None
    best_subset: tuple[int, ...] = ()
    sizes = [n_budget] if exact_budget else range(n_budget + 1)
    for size in sizes:
        for subset in itertools.combinations(range(store.n_satellites), size):
            metrics = rm.compute_regional_metrics(
                store,
                subset,
                temporal_coverage_target_pct=temporal_coverage_target_pct,
            )
            score = (
                float(metrics.city.qualified_area_pct or 0.0),
                float(metrics.city.area_weighted_temporal_coverage_pct),
            )
            if best_score is None or score > best_score:
                best_score = score
                best_subset = subset
    assert best_score is not None
    return best_score, best_subset


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
    k2 = rm.compute_regional_metrics(store, [0, 1], min_simultaneous=2)
    assert np.isclose(k2.grid_metrics[0].temporal_coverage_pct, 100.0 / 6.0)
    assert np.isclose(k2.grid_metrics[1].temporal_coverage_pct, 0.0)
    result = rs.run_regional_mrt_selection(
        store,
        mrt_target_s=2_400.0,
        area_target_pct=50.0,
        min_simultaneous=2,
        method="both",
        time_limit_s=30,
    )
    assert result.recommended.min_simultaneous == 2
    assert result.recommended.feasible
    assert len(result.recommended.selected_sat_indices) == 2
    print("[通过] MRT≥分析时长的空组合和 K=2 同时覆盖")


def test_temporal_selection() -> None:
    store = synthetic_store()
    for target_q, target_area, expected in (
        (30.0, 100.0, 1),
        (50.0, 50.0, 1),
        (50.0, 100.0, None),
    ):
        optimum = brute_force_temporal_minimum(
            store,
            temporal_coverage_target_pct=target_q,
            area_target_pct=target_area,
        )
        assert optimum == expected
        result = rs.run_regional_temporal_selection(
            store,
            temporal_coverage_target_pct=target_q,
            area_target_pct=target_area,
            method="both",
            time_limit_s=30,
        )
        assert result.recommended.feasible is (expected is not None)
        if expected is not None:
            assert len(result.recommended.selected_sat_indices) == expected
            if result.ilp and result.ilp.status not in {"unsupported", "skipped"}:
                assert result.ilp.feasible
                assert len(result.ilp.selected_sat_indices) == expected
                assert result.ilp.status == "proven_optimal"
        else:
            assert result.recommended.achieved_area_pct < target_area
    k2 = rs.run_regional_temporal_selection(
        store,
        temporal_coverage_target_pct=10.0,
        area_target_pct=50.0,
        min_simultaneous=2,
        method="both",
        time_limit_s=30,
    )
    assert k2.recommended.min_simultaneous == 2
    assert k2.recommended.feasible
    assert len(k2.recommended.selected_sat_indices) == 2
    print("[通过] 任务①时间覆盖率的穷举、贪心和 ILP 一致")


def test_budget_selection() -> None:
    store = synthetic_store()
    for q_target, budget, expected_score in (
        (30.0, 1, (100.0, 100.0 / 3.0)),
        (50.0, 1, (50.0, 25.0)),
        (50.0, 2, (50.0, 50.0)),
    ):
        optimum, _ = brute_force_budget_best(
            store,
            n_budget=budget,
            temporal_coverage_target_pct=q_target,
        )
        assert all(abs(left - right) < 1e-9 for left, right in zip(optimum, expected_score))
        result = rs.run_regional_budget_selection(
            store,
            n_budget=budget,
            temporal_coverage_target_pct=q_target,
            method="both",
            time_limit_s=30,
        )
        assert result.recommended.feasible
        actual = (
            result.recommended.achieved_area_pct,
            result.recommended.area_weighted_temporal_coverage_pct,
        )
        assert all(abs(left - right) < 1e-9 for left, right in zip(actual, expected_score))
        assert len(result.recommended.selected_sat_indices) <= budget
        if result.ilp and result.ilp.status not in {"unsupported", "skipped"}:
            assert result.ilp.status == "proven_discrete_optimal"
    exact = rs.run_regional_budget_selection(
        store,
        n_budget=2,
        temporal_coverage_target_pct=30.0,
        exact_budget=True,
        method="both",
        time_limit_s=30,
    )
    assert len(exact.recommended.selected_sat_indices) == 2
    k2 = rs.run_regional_budget_selection(
        store,
        n_budget=2,
        temporal_coverage_target_pct=10.0,
        min_simultaneous=2,
        method="both",
        time_limit_s=30,
    )
    assert k2.recommended.min_simultaneous == 2
    assert k2.recommended.feasible
    assert len(k2.recommended.selected_sat_indices) <= 2
    print("[通过] 任务③预算字典序目标与穷举一致")


def _brute_force_area_quantile_mrt(store, *, area_target_pct: float, n_budget: int):
    """小规模基准：枚举不超过预算的组合，最小化面积分位 MRT。"""

    best = None
    best_subset = ()
    for size in range(n_budget + 1):
        for subset in itertools.combinations(range(store.n_satellites), size):
            metrics = rm.compute_regional_metrics(store, subset)
            values = sorted(
                (item.mrt_s, item.area_m2, item.grid_index)
                for item in metrics.grid_metrics
            )
            total_area = sum(item.area_m2 for item in metrics.grid_metrics)
            target_area = total_area * area_target_pct / 100.0
            accumulated = 0.0
            quantile = 0.0
            for mrt_s, area_m2, _grid_index in values:
                accumulated += area_m2
                if accumulated + 1e-9 >= target_area:
                    quantile = mrt_s
                    break
            if best is None or quantile < best:
                best = quantile
                best_subset = subset
    assert best is not None
    return float(best), best_subset


def test_quantile_mrt_selection() -> None:
    store = synthetic_store()
    for beta, budget, expected in ((50.0, 1, 1_200.0), (100.0, 1, 2_400.0), (100.0, 2, 1_800.0)):
        brute, _subset = _brute_force_area_quantile_mrt(
            store, area_target_pct=beta, n_budget=budget
        )
        assert brute == expected
        result = rs.select_min_area_quantile_mrt(
            store,
            area_target_pct=beta,
            n_budget=budget,
            method="ilp",
            time_limit_s=30,
        )
        if result.status != "unsupported":
            assert result.feasible
            assert abs(result.area_quantile_mrt_s - brute) < 1e-9
            assert result.area_quantile_mrt_s <= result.mrt_threshold_s + 1e-9
            assert len(result.selected_sat_indices) <= budget
            assert result.status == "proven_discrete_optimal"
    k2 = rs.select_min_area_quantile_mrt(
        store,
        area_target_pct=50.0,
        n_budget=2,
        min_simultaneous=2,
        method="ilp",
        time_limit_s=30,
    )
    assert k2.feasible
    assert k2.min_simultaneous == 2
    assert abs(k2.area_quantile_mrt_s - 2_400.0) < 1e-9
    assert len(k2.selected_sat_indices) <= 2
    print("[通过] 任务④面积分位 MRT 二分与全子集穷举一致")


def _art_score(metrics, mrt_area_quantile_pct: float = 90.0):
    """任务⑤的小规模穷举评分，顺序与生产代码一致。"""

    return (
        metrics.city.area_weighted_art_s,
        rs._area_quantile_mrt(metrics, mrt_area_quantile_pct),
        metrics.city.area_weighted_mrt_s,
        -metrics.city.area_weighted_temporal_coverage_pct,
    )


def _brute_force_min_art(store, *, n_budget: int, exact_budget: bool = False):
    best_score = None
    best_subset = ()
    sizes = [n_budget] if exact_budget else range(n_budget + 1)
    for size in sizes:
        for subset in itertools.combinations(range(store.n_satellites), size):
            score = _art_score(rm.compute_regional_metrics(store, subset))
            if best_score is None or score < best_score:
                best_score = score
                best_subset = subset
    assert best_score is not None
    return best_score, best_subset


def test_art_selection() -> None:
    store = synthetic_store()
    for budget in (1, 2):
        optimum, _subset = _brute_force_min_art(store, n_budget=budget)
        result = rs.run_regional_art_selection(
            store, n_budget=budget, max_local_rounds=3
        )
        actual = _art_score(result.metrics)
        assert all(abs(left - right) < 1e-9 for left, right in zip(actual, optimum))
        assert result.feasible
        assert len(result.selected_sat_indices) <= budget
        assert result.status == "feasible_heuristic"
    exact = rs.run_regional_art_selection(store, n_budget=2, exact_budget=True)
    optimum, _subset = _brute_force_min_art(store, n_budget=2, exact_budget=True)
    actual = _art_score(exact.metrics)
    assert len(exact.selected_sat_indices) == 2
    assert all(abs(left - right) < 1e-9 for left, right in zip(actual, optimum))
    k2 = rs.run_regional_art_selection(store, n_budget=2, min_simultaneous=2)
    assert k2.min_simultaneous == 2
    assert k2.feasible
    assert len(k2.selected_sat_indices) == 2

    # K=3 回归：0 号是按索引排在最前的短窗口诱饵，1/2/3 才能形成三重覆盖。
    # 旧算法会先随意拿 0 号，预算用完后仍无 K=3 覆盖，最终反向删除成空组合。
    base = synthetic_store()
    cell = base.grid.cells[0]

    def k3_window(sat_index: int, start_minute: int, end_minute: int):
        begin = base.start_utc + timedelta(minutes=start_minute)
        finish = base.start_utc + timedelta(minutes=end_minute)
        return ro.AccessWindow(
            sat_index=sat_index,
            sat_id=200 + sat_index,
            sat_name=f"K3测试星{sat_index}",
            grid_index=0,
            grid_id=cell.grid_id,
            start_utc=begin,
            end_utc=finish,
            duration_s=(finish - begin).total_seconds(),
        )

    k3_store = ro.OpportunityStore(
        satellites=tuple(f"K3测试星{index}" for index in range(4)),
        grid=base.grid,
        start_utc=base.start_utc,
        end_utc=base.end_utc,
        detection_step_s=60,
        decision_step_s=60,
        windows={
            (0, 0): (k3_window(0, 40, 50),),
            (1, 0): (k3_window(1, 0, 30),),
            (2, 0): (k3_window(2, 0, 30),),
            (3, 0): (k3_window(3, 0, 30),),
        },
    )
    k3 = rs.run_regional_art_selection(
        k3_store,
        n_budget=3,
        min_simultaneous=3,
        max_local_rounds=1,
    )
    assert k3.selected_sat_indices == (1, 2, 3)
    assert k3.metrics.grid_metrics[0].temporal_coverage_pct > 0.0
    print("[通过] 任务⑤ ART 贪心/局部改进与全子集穷举一致")


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
    tests = [
        test_small_exact_agreement,
        test_empty_and_invalid_requests,
        test_temporal_selection,
        test_budget_selection,
        test_quantile_mrt_selection,
        test_art_selection,
        test_real_opportunity_store,
    ]
    for test in tests:
        test()
    print(f"区域选星任务①～⑤自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
