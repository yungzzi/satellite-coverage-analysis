# -*- coding: utf-8 -*-
"""区域选星任务①～⑤（支持 K=1 与 K>1）。

任务②给定“每个网格最大空窗 MRT 不超过 ``mrt_target_s``”，任务①给定“每个网格
时间覆盖率不低于 ``temporal_coverage_target_pct``”；两者都要求至少
``area_target_pct`` 的城市面积达标，求卫星数量尽量少。

实现分两层：

* 贪心 + 反向删除：每轮优先改善达标面积、再缩短面积加权 MRT/ART，得到工程近似解；
* K=1 保留滑动窗口 ILP 快速路径；K>1 为每个网格—时间片建立同时覆盖变量
  ``y[g,t]``，保证同一时刻至少有 K 颗已选卫星覆盖。90% 模式增加网格达标变量
  ``z_g``，严格 100% 模式直接约束所有网格。
* 任务③给定最多（或恰好）``N`` 颗卫星时，先最大化达到时间覆盖率门槛的城市面积，
  再在面积相同的方案中最大化面积加权时间覆盖率；ILP 用两阶段求解保持字典序目标。
* 任务④给定最多 ``N`` 颗卫星时，对面积分位 MRT 上限做离散二分，每次复用任务②的
  可行性模型，最后用窗口级指标计算真实分位 MRT。
* 任务⑤给定最多 ``N`` 颗卫星时，以面积加权 ART 为主目标做贪心、单星交换和反向删除，
  同时报告面积分位 MRT 以暴露局部薄弱区域。

最终可行性不以离散模型为准，而是重新调用 ``regional_metrics`` 的窗口级指标复核。
因此分钟/检测步长边界误差不会被包装成“已满足”。本模块不修改旧的单点选星入口。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from regional_metrics import RegionalMetricsResult, compute_regional_metrics
from regional_opportunities import OpportunityStore, refine_regional_opportunity_windows


@dataclass(frozen=True)
class SelectionResult:
    """一次贪心或 ILP 选星结果。"""

    method: str
    selected_sat_indices: tuple[int, ...]
    selected_sat_ids: tuple[int, ...]
    selected_sat_names: tuple[str, ...]
    area_target_pct: float
    mrt_target_s: float
    achieved_area_pct: float
    feasible: bool
    status: str
    note: str
    constraint_count: int
    metrics: RegionalMetricsResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        """转换为服务层/报告层可消费的普通字典。"""

        return {
            "method": self.method,
            "selected_sat_indices": list(self.selected_sat_indices),
            "selected_sat_ids": list(self.selected_sat_ids),
            "selected_sat_names": list(self.selected_sat_names),
            "area_target_pct": self.area_target_pct,
            "mrt_target_s": self.mrt_target_s,
            "achieved_area_pct": self.achieved_area_pct,
            "feasible": self.feasible,
            "status": self.status,
            "note": self.note,
            "constraint_count": self.constraint_count,
            "min_simultaneous": self.min_simultaneous,
            "metrics": self.metrics.to_city_dict(),
        }


@dataclass(frozen=True)
class RegionalSelectionResult:
    """任务②的贪心/ILP 对照结果和推荐方案。"""

    task: str
    area_target_pct: float
    mrt_target_s: float
    greedy: SelectionResult | None
    ilp: SelectionResult | None
    recommended: SelectionResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "area_target_pct": self.area_target_pct,
            "mrt_target_s": self.mrt_target_s,
            "greedy": self.greedy.to_dict() if self.greedy else None,
            "ilp": self.ilp.to_dict() if self.ilp else None,
            "recommended": self.recommended.to_dict(),
            "min_simultaneous": self.min_simultaneous,
        }


@dataclass(frozen=True)
class TemporalSelectionResult:
    """任务①一次贪心或 ILP 选星结果。"""

    method: str
    selected_sat_indices: tuple[int, ...]
    selected_sat_ids: tuple[int, ...]
    selected_sat_names: tuple[str, ...]
    area_target_pct: float
    temporal_coverage_target_pct: float
    achieved_area_pct: float
    feasible: bool
    status: str
    note: str
    constraint_count: int
    metrics: RegionalMetricsResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "selected_sat_indices": list(self.selected_sat_indices),
            "selected_sat_ids": list(self.selected_sat_ids),
            "selected_sat_names": list(self.selected_sat_names),
            "area_target_pct": self.area_target_pct,
            "temporal_coverage_target_pct": self.temporal_coverage_target_pct,
            "achieved_area_pct": self.achieved_area_pct,
            "feasible": self.feasible,
            "status": self.status,
            "note": self.note,
            "constraint_count": self.constraint_count,
            "min_simultaneous": self.min_simultaneous,
            "metrics": self.metrics.to_city_dict(),
        }


@dataclass(frozen=True)
class RegionalTemporalSelectionResult:
    """任务①的贪心/ILP 对照结果和推荐方案。"""

    task: str
    area_target_pct: float
    temporal_coverage_target_pct: float
    greedy: TemporalSelectionResult | None
    ilp: TemporalSelectionResult | None
    recommended: TemporalSelectionResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "area_target_pct": self.area_target_pct,
            "temporal_coverage_target_pct": self.temporal_coverage_target_pct,
            "greedy": self.greedy.to_dict() if self.greedy else None,
            "ilp": self.ilp.to_dict() if self.ilp else None,
            "recommended": self.recommended.to_dict(),
            "min_simultaneous": self.min_simultaneous,
        }


@dataclass(frozen=True)
class BudgetSelectionResult:
    """任务③一次预算选星结果。"""

    method: str
    selected_sat_indices: tuple[int, ...]
    selected_sat_ids: tuple[int, ...]
    selected_sat_names: tuple[str, ...]
    n_budget: int
    exact_budget: bool
    temporal_coverage_target_pct: float
    achieved_area_pct: float
    area_weighted_temporal_coverage_pct: float
    feasible: bool
    status: str
    note: str
    constraint_count: int
    metrics: RegionalMetricsResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "selected_sat_indices": list(self.selected_sat_indices),
            "selected_sat_ids": list(self.selected_sat_ids),
            "selected_sat_names": list(self.selected_sat_names),
            "n_budget": self.n_budget,
            "exact_budget": self.exact_budget,
            "temporal_coverage_target_pct": self.temporal_coverage_target_pct,
            "achieved_area_pct": self.achieved_area_pct,
            "area_weighted_temporal_coverage_pct": self.area_weighted_temporal_coverage_pct,
            "feasible": self.feasible,
            "status": self.status,
            "note": self.note,
            "constraint_count": self.constraint_count,
            "min_simultaneous": self.min_simultaneous,
            "metrics": self.metrics.to_city_dict(),
        }


@dataclass(frozen=True)
class RegionalBudgetSelectionResult:
    """任务③的贪心/ILP 对照结果和推荐方案。"""

    task: str
    n_budget: int
    exact_budget: bool
    temporal_coverage_target_pct: float
    greedy: BudgetSelectionResult | None
    ilp: BudgetSelectionResult | None
    recommended: BudgetSelectionResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "n_budget": self.n_budget,
            "exact_budget": self.exact_budget,
            "temporal_coverage_target_pct": self.temporal_coverage_target_pct,
            "greedy": self.greedy.to_dict() if self.greedy else None,
            "ilp": self.ilp.to_dict() if self.ilp else None,
            "recommended": self.recommended.to_dict(),
            "min_simultaneous": self.min_simultaneous,
        }


@dataclass(frozen=True)
class AreaQuantileMrtSelectionResult:
    """任务④的面积分位 MRT 结果。"""

    task: str
    area_target_pct: float
    n_budget: int
    method: str
    mrt_threshold_s: float
    area_quantile_mrt_s: float
    selected_sat_indices: tuple[int, ...]
    selected_sat_ids: tuple[int, ...]
    selected_sat_names: tuple[str, ...]
    achieved_area_pct: float
    area_weighted_mrt_s: float
    feasible: bool
    status: str
    note: str
    evaluations: int
    evaluated_thresholds_s: tuple[float, ...]
    selection: SelectionResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "area_target_pct": self.area_target_pct,
            "n_budget": self.n_budget,
            "method": self.method,
            "mrt_threshold_s": self.mrt_threshold_s,
            "area_quantile_mrt_s": self.area_quantile_mrt_s,
            "selected_sat_indices": list(self.selected_sat_indices),
            "selected_sat_ids": list(self.selected_sat_ids),
            "selected_sat_names": list(self.selected_sat_names),
            "achieved_area_pct": self.achieved_area_pct,
            "area_weighted_mrt_s": self.area_weighted_mrt_s,
            "feasible": self.feasible,
            "status": self.status,
            "note": self.note,
            "evaluations": self.evaluations,
            "evaluated_thresholds_s": list(self.evaluated_thresholds_s),
            "selection": self.selection.to_dict(),
            "min_simultaneous": self.min_simultaneous,
        }


@dataclass(frozen=True)
class ArtSelectionResult:
    """任务⑤的一次贪心/局部改进结果。"""

    method: str
    selected_sat_indices: tuple[int, ...]
    selected_sat_ids: tuple[int, ...]
    selected_sat_names: tuple[str, ...]
    n_budget: int
    exact_budget: bool
    mrt_area_quantile_pct: float
    area_weighted_art_s: float
    area_quantile_mrt_s: float
    area_weighted_mrt_s: float
    area_weighted_temporal_coverage_pct: float
    feasible: bool
    status: str
    note: str
    iterations: int
    exchange_count: int
    deletion_count: int
    metrics: RegionalMetricsResult
    min_simultaneous: int = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "selected_sat_indices": list(self.selected_sat_indices),
            "selected_sat_ids": list(self.selected_sat_ids),
            "selected_sat_names": list(self.selected_sat_names),
            "n_budget": self.n_budget,
            "exact_budget": self.exact_budget,
            "mrt_area_quantile_pct": self.mrt_area_quantile_pct,
            "area_weighted_art_s": self.area_weighted_art_s,
            "area_quantile_mrt_s": self.area_quantile_mrt_s,
            "area_weighted_mrt_s": self.area_weighted_mrt_s,
            "area_weighted_temporal_coverage_pct": self.area_weighted_temporal_coverage_pct,
            "feasible": self.feasible,
            "status": self.status,
            "note": self.note,
            "iterations": self.iterations,
            "exchange_count": self.exchange_count,
            "deletion_count": self.deletion_count,
            "min_simultaneous": self.min_simultaneous,
            "metrics": self.metrics.to_city_dict(),
        }


def _validate_inputs(
    store: OpportunityStore,
    area_target_pct: float,
    mrt_target_s: float,
    min_simultaneous: int,
) -> tuple[float, float]:
    area_target_pct = float(area_target_pct)
    mrt_target_s = float(mrt_target_s)
    if not 0.0 <= area_target_pct <= 100.0:
        raise ValueError("area_target_pct 必须在 0～100 之间。")
    if mrt_target_s < 0.0:
        raise ValueError("mrt_target_s 不能为负数。")
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    return area_target_pct, mrt_target_s


def _validate_temporal_inputs(
    store: OpportunityStore,
    area_target_pct: float,
    temporal_coverage_target_pct: float,
    min_simultaneous: int,
) -> tuple[float, float]:
    """校验任务①参数；K=1 走快速路径，K>1 使用同时覆盖模型。"""

    area_target_pct = float(area_target_pct)
    temporal_coverage_target_pct = float(temporal_coverage_target_pct)
    if not 0.0 <= area_target_pct <= 100.0:
        raise ValueError("area_target_pct 必须在 0～100 之间。")
    if not 0.0 <= temporal_coverage_target_pct <= 100.0:
        raise ValueError("temporal_coverage_target_pct 必须在 0～100 之间。")
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    return area_target_pct, temporal_coverage_target_pct


def _validate_budget_inputs(
    store: OpportunityStore,
    n_budget: int,
    temporal_coverage_target_pct: float,
    min_simultaneous: int,
) -> tuple[int, float]:
    """校验任务③预算、时间覆盖率和同时覆盖参数。"""

    if int(n_budget) != n_budget or int(n_budget) < 0:
        raise ValueError("n_budget 必须是非负整数。")
    n_budget = int(n_budget)
    temporal_coverage_target_pct = float(temporal_coverage_target_pct)
    if not 0.0 <= temporal_coverage_target_pct <= 100.0:
        raise ValueError("temporal_coverage_target_pct 必须在 0～100 之间。")
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    return n_budget, temporal_coverage_target_pct


def _decision_bins(store: OpportunityStore, step_s: int) -> tuple[list[datetime], list[datetime]]:
    """把分析时段切成决策片，最后一片允许短于 step_s。"""

    starts: list[datetime] = []
    ends: list[datetime] = []
    current = store.start_utc
    delta = timedelta(seconds=step_s)
    while current < store.end_utc:
        starts.append(current)
        ends.append(min(current + delta, store.end_utc))
        current += delta
    return starts, ends


def _window_bin_range(
    start: datetime,
    end: datetime,
    lower: datetime,
    upper: datetime,
    step_s: int,
    n_bins: int,
) -> tuple[int, int] | None:
    """返回与半开窗口有交集的决策片下标 [lo, hi)。"""

    start = max(start, lower)
    end = min(end, upper)
    if end <= start:
        return None
    offset_start = (start - lower).total_seconds()
    offset_end = (end - lower).total_seconds()
    lo = max(0, int(math.floor(offset_start / step_s)))
    hi = min(n_bins, int(math.ceil(offset_end / step_s)))
    if hi <= lo:
        return None
    return lo, hi


def build_bin_candidates(
    store: OpportunityStore,
    *,
    decision_step_s: int | None = None,
) -> tuple[list[list[set[int]]], list[datetime], list[datetime]]:
    """构造 ``candidate_sets[grid][time_bin]``。

    集合中的整数是卫星索引；一个窗口只要与时间片有正交集，就认为该片可覆盖。
    这是供滑动窗口 ILP 使用的离散近似，最终仍回到原始窗口复核。
    """

    step_s = int(decision_step_s or store.decision_step_s)
    if step_s <= 0:
        raise ValueError("decision_step_s 必须大于 0。")
    starts, ends = _decision_bins(store, step_s)
    candidates = [[set() for _ in starts] for _ in store.grid.cells]
    for (sat_index, grid_index), windows in store.windows.items():
        if not (0 <= int(sat_index) < store.n_satellites):
            raise ValueError(f"窗口中的 sat_index 越界：{sat_index}")
        if not (0 <= grid_index < len(candidates)):
            raise ValueError(f"窗口中的 grid_index 越界：{grid_index}")
        for window in windows:
            span = _window_bin_range(
                window.start_utc,
                window.end_utc,
                store.start_utc,
                store.end_utc,
                step_s,
                len(starts),
            )
            if span is None:
                continue
            lo, hi = span
            for time_index in range(lo, hi):
                candidates[grid_index][time_index].add(int(sat_index))
    return candidates, starts, ends


def _add_k_event_variables(
    problem: Any,
    pulp: Any,
    x: Sequence[Any],
    grid_candidates: Sequence[set[int]],
    *,
    min_simultaneous: int,
    prefix: str,
) -> tuple[dict[int, Any], int]:
    """为 K>1 的每个时间片建立“至少 K 星同时覆盖”变量。

    ``y[t]=1`` 当且仅当该时间片的候选卫星中至少有 K 颗被选。两条不等式分别
    防止 y 凭空取 1，以及在已有 K 颗时把 y 错取为 0。
    """

    variables: dict[int, Any] = {}
    constraint_count = 0
    k = int(min_simultaneous)
    for time_index, candidate_set in enumerate(grid_candidates):
        if not candidate_set:
            continue
        y = pulp.LpVariable(f"{prefix}_{time_index}", cat=pulp.LpBinary)
        selected = pulp.lpSum(x[index] for index in sorted(candidate_set))
        problem += selected >= k * y
        problem += selected <= (k - 1) + len(candidate_set) * y
        variables[time_index] = y
        constraint_count += 2
    return variables, constraint_count


def _constraint_sets(
    candidates: Sequence[Sequence[set[int]]],
    window_bins: int,
    *,
    strict: bool,
) -> list[list[frozenset[int]]]:
    """生成每个网格的滑动窗口候选卫星集合，并删除重复集合。"""

    result: list[list[frozenset[int]]] = []
    for grid_candidates in candidates:
        n_bins = len(grid_candidates)
        if window_bins > n_bins:
            result.append([])
            continue
        seen: set[frozenset[int]] = set()
        current: list[frozenset[int]] = []
        for start in range(n_bins - window_bins + 1):
            union: set[int] = set()
            for item in grid_candidates[start : start + window_bins]:
                union.update(item)
            key = frozenset(union)
            if strict:
                # 100% 模式所有网格共享同一个 x 变量；跨网格去重安全。
                current.append(key)
            elif key not in seen:
                seen.add(key)
                current.append(key)
        result.append(current)
    if strict:
        all_unique: list[frozenset[int]] = []
        seen_global: set[frozenset[int]] = set()
        for items in result:
            for item in items:
                if item not in seen_global:
                    seen_global.add(item)
                    all_unique.append(item)
        return [all_unique]
    return result


def _evaluate(
    store: OpportunityStore,
    selected: Sequence[int],
    mrt_target_s: float,
    min_simultaneous: int = 1,
) -> RegionalMetricsResult:
    """统一用窗口级指标评估一个组合。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=min_simultaneous,
        mrt_target_s=mrt_target_s,
    )


def _score(metrics: RegionalMetricsResult) -> tuple[float, float, float, float]:
    """贪心字典序评分：达标面积优先，其次 MRT、ART、时间覆盖率。"""

    city = metrics.city
    qualified = float(city.qualified_area_pct or 0.0)
    return (
        qualified,
        -float(city.area_weighted_mrt_s),
        -float(city.area_weighted_art_s),
        float(city.area_weighted_temporal_coverage_pct),
    )


def _satellite_identity(store: OpportunityStore, index: int) -> tuple[int, str]:
    """兼容 Satellite、EarthSatellite 和自检用占位对象，生成稳定标识。"""

    sat = store.satellites[index]
    sat_id = getattr(sat, "norad_id", None)
    if sat_id is None:
        model = getattr(sat, "model", None)
        sat_id = getattr(model, "satnum", None)
    sat_name = getattr(sat, "name", None)
    if not sat_name:
        for (sat_index, _grid_index), windows in store.windows.items():
            if int(sat_index) == index and windows:
                sat_id = sat_id if sat_id is not None else windows[0].sat_id
                sat_name = windows[0].sat_name
                break
    return int(sat_id) if sat_id is not None else -1, str(sat_name or f"SAT-{index}")


def _result(
    store: OpportunityStore,
    selected: Sequence[int],
    *,
    method: str,
    area_target_pct: float,
    mrt_target_s: float,
    status: str,
    note: str,
    constraint_count: int,
    min_simultaneous: int = 1,
) -> SelectionResult:
    selected = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate(store, selected, mrt_target_s, min_simultaneous)
    achieved = float(metrics.city.qualified_area_pct or 0.0)
    feasible = achieved + 1e-9 >= area_target_pct
    sat_ids = tuple(_satellite_identity(store, index)[0] for index in selected)
    sat_names = tuple(_satellite_identity(store, index)[1] for index in selected)
    if feasible and status in {"infeasible", "infeasible_heuristic"}:
        status = "feasible_heuristic"
    return SelectionResult(
        method=method,
        selected_sat_indices=selected,
        selected_sat_ids=sat_ids,
        selected_sat_names=sat_names,
        area_target_pct=area_target_pct,
        mrt_target_s=mrt_target_s,
        achieved_area_pct=achieved,
        feasible=feasible,
        status=status,
        note=note,
        constraint_count=int(constraint_count),
        metrics=metrics,
        min_simultaneous=min_simultaneous,
    )


def greedy_select_mrt(
    store: OpportunityStore,
    *,
    mrt_target_s: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
) -> SelectionResult:
    """贪心选星，再做反向删除；返回可行性和窗口级复核结果。"""

    area_target_pct, mrt_target_s = _validate_inputs(
        store, area_target_pct, mrt_target_s, min_simultaneous
    )
    usable = sorted({sat_index for sat_index, _ in store.windows})
    selected: list[int] = []
    current = _evaluate(store, selected, mrt_target_s, min_simultaneous)
    while (current.city.qualified_area_pct or 0.0) + 1e-9 < area_target_pct:
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = _score(current)
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate(
                store, [*selected, sat_index], mrt_target_s, min_simultaneous
            )
            score = _score(trial)
            if best_metrics is None or score > best_score:
                best_index = sat_index
                best_metrics = trial
                best_score = score
        if best_index is None or best_metrics is None:
            break
        selected.append(best_index)
        current = best_metrics

    # 反向删除：只要删掉后仍达到面积目标，就永久删除该星。
    if (current.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
        changed = True
        while changed:
            changed = False
            for sat_index in list(selected):
                trial_selected = [item for item in selected if item != sat_index]
                trial = _evaluate(store, trial_selected, mrt_target_s, min_simultaneous)
                if (trial.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
                    selected = trial_selected
                    current = trial
                    changed = True
                    break
    feasible = (current.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct
    note = (
        "贪心达到面积目标，并完成反向删除；结果是近似解。"
        if feasible
        else "全池/贪心未达到面积目标；返回当前最好的近似方案。"
    )
    return _result(
        store,
        selected,
        method="greedy",
        area_target_pct=area_target_pct,
        mrt_target_s=mrt_target_s,
        status="feasible_heuristic" if feasible else "infeasible_heuristic",
        note=note,
        constraint_count=0,
        min_simultaneous=min_simultaneous,
    )


def ilp_select_mrt(
    store: OpportunityStore,
    *,
    mrt_target_s: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    time_limit_s: int = 180,
) -> SelectionResult:
    """滑动窗口 ILP，最小化选星数量并做窗口级复核。K>1 使用 y[g,t]。"""

    area_target_pct, mrt_target_s = _validate_inputs(
        store, area_target_pct, mrt_target_s, min_simultaneous
    )
    try:
        import pulp
    except ImportError:
        return _result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            mrt_target_s=mrt_target_s,
            status="unsupported",
            note="未安装 pulp，无法运行区域 MRT 整数规划。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )

    n_sat = store.n_satellites
    if n_sat > 1500:
        return _result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            mrt_target_s=mrt_target_s,
            status="skipped",
            note="卫星数超过 1500，跳过 ILP；请使用贪心或候选预筛选。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
    if area_target_pct <= 0.0:
        return _result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            mrt_target_s=mrt_target_s,
            status="proven_optimal",
            note="面积目标为 0%，空组合即满足。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )

    step_s = int(store.decision_step_s)
    candidates, _starts, _ends = build_bin_candidates(store, decision_step_s=step_s)
    n_bins = len(_starts)
    window_bins = max(1, int(math.floor(mrt_target_s / step_s)) + 1)
    strict = area_target_pct >= 100.0 - 1e-9
    grouped_constraints = (
        _constraint_sets(candidates, window_bins, strict=strict)
        if min_simultaneous == 1
        else []
    )

    problem = pulp.LpProblem("regional_mrt_selection", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{index}", cat=pulp.LpBinary) for index in range(n_sat)]
    z = []
    if not strict:
        z = [pulp.LpVariable(f"z_{index}", cat=pulp.LpBinary) for index in range(store.n_cells)]
        areas = [float(cell.area_m2) for cell in store.grid.cells]
        problem += pulp.lpSum(areas[index] * z[index] for index in range(store.n_cells)) \
            >= float(area_target_pct / 100.0) * sum(areas)
    problem += pulp.lpSum(x)

    constraint_count = 0
    impossible_strict = False
    if min_simultaneous == 1:
        if strict:
            sets_to_add = grouped_constraints[0] if grouped_constraints else []
            for candidate in sets_to_add:
                problem += pulp.lpSum(x[index] for index in candidate) >= 1
                constraint_count += 1
            # 没有完整滑窗约束时，MRT 大于等于分析时长，空组合可行；否则 ILP 自然会判无解。
        else:
            for grid_index, grid_constraints in enumerate(grouped_constraints):
                for candidate in grid_constraints:
                    problem += pulp.lpSum(x[index] for index in candidate) >= z[grid_index]
                    constraint_count += 1
    else:
        # K>1 不能只对滑窗内的卫星集合做求和：不同卫星可能出现在窗口的不同时间。
        # 先用 y[g,t] 精确表示“该时间片同时有至少 K 颗所选卫星”，再要求每个 MRT 滑窗
        # 至少出现一次 y=1。
        for grid_index, grid_candidates in enumerate(candidates):
            y_by_time, added = _add_k_event_variables(
                problem,
                pulp,
                x,
                grid_candidates,
                min_simultaneous=min_simultaneous,
                prefix=f"y_mrt_{grid_index}",
            )
            constraint_count += added
            if window_bins > n_bins:
                continue
            for window_start in range(n_bins - window_bins + 1):
                event_variables = [
                    y_by_time[time_index]
                    for time_index in range(window_start, window_start + window_bins)
                    if time_index in y_by_time
                ]
                if not event_variables:
                    if strict:
                        impossible_strict = True
                    else:
                        problem += z[grid_index] <= 0
                        constraint_count += 1
                    continue
                event_expr = pulp.lpSum(event_variables)
                if strict:
                    problem += event_expr >= 1
                else:
                    problem += event_expr >= z[grid_index]
                constraint_count += 1

    if impossible_strict:
        return _result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            mrt_target_s=mrt_target_s,
            status="infeasible",
            note="K>1 的离散 MRT 滑窗中存在没有任何同时覆盖机会的窗口。",
            constraint_count=constraint_count,
            min_simultaneous=min_simultaneous,
        )

    problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=int(time_limit_s)))
    solver_status = pulp.LpStatus[problem.status]
    if solver_status not in {"Optimal", "Not Solved"}:
        return _result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            mrt_target_s=mrt_target_s,
            status="infeasible",
            note=f"ILP 状态为 {solver_status}，当前离散模型未找到可行方案。",
            constraint_count=constraint_count,
            min_simultaneous=min_simultaneous,
        )
    selected = [index for index, variable in enumerate(x) if (pulp.value(variable) or 0.0) > 0.5]
    metrics = _evaluate(store, selected, mrt_target_s, min_simultaneous)
    achieved = float(metrics.city.qualified_area_pct or 0.0)
    feasible = achieved + 1e-9 >= area_target_pct
    if solver_status == "Optimal" and feasible:
        status = "proven_optimal"
        note = (
            "K=1 滑动窗口 ILP 已证明离散模型最少；窗口级复核通过。"
            if min_simultaneous == 1
            else "K>1 通用 y[g,t] 滑动窗口 ILP 已证明离散模型最少；窗口级复核通过。"
        )
    elif feasible:
        status = "feasible_time_limit"
        note = "ILP 达到时间上限，当前可行解通过窗口级复核，未证明全局最优。"
    else:
        status = "verification_failed"
        note = "离散 ILP 解未通过原始窗口级面积复核，不能标为满足。"
    # 直接复用计算好的 metrics，避免 _result 再评估一次。
    result = _result(
        store,
        selected,
        method="ilp",
        area_target_pct=area_target_pct,
        mrt_target_s=mrt_target_s,
        status=status,
        note=note,
        constraint_count=constraint_count,
        min_simultaneous=min_simultaneous,
    )
    return result


def _evaluate_temporal(
    store: OpportunityStore,
    selected: Sequence[int],
    temporal_coverage_target_pct: float,
    min_simultaneous: int = 1,
) -> RegionalMetricsResult:
    """用窗口级指标评估任务①的单元时间覆盖率达标面积。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=min_simultaneous,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
    )


def _score_temporal(metrics: RegionalMetricsResult) -> tuple[float, float, float, float]:
    """任务①贪心评分：达标面积优先，再提高面积加权时间覆盖率。"""

    city = metrics.city
    return (
        float(city.qualified_area_pct or 0.0),
        float(city.area_weighted_temporal_coverage_pct),
        -float(city.area_weighted_mrt_s),
        -float(city.area_weighted_art_s),
    )


def _temporal_result(
    store: OpportunityStore,
    selected: Sequence[int],
    *,
    method: str,
    area_target_pct: float,
    temporal_coverage_target_pct: float,
    status: str,
    note: str,
    constraint_count: int,
    min_simultaneous: int = 1,
) -> TemporalSelectionResult:
    selected_tuple = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate_temporal(
        store, selected_tuple, temporal_coverage_target_pct, min_simultaneous
    )
    achieved = float(metrics.city.qualified_area_pct or 0.0)
    feasible = achieved + 1e-9 >= area_target_pct
    sat_ids = tuple(_satellite_identity(store, index)[0] for index in selected_tuple)
    sat_names = tuple(_satellite_identity(store, index)[1] for index in selected_tuple)
    if feasible and status in {"infeasible", "infeasible_heuristic"}:
        status = "feasible_heuristic"
    return TemporalSelectionResult(
        method=method,
        selected_sat_indices=selected_tuple,
        selected_sat_ids=sat_ids,
        selected_sat_names=sat_names,
        area_target_pct=area_target_pct,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        achieved_area_pct=achieved,
        feasible=feasible,
        status=status,
        note=note,
        constraint_count=int(constraint_count),
        metrics=metrics,
        min_simultaneous=min_simultaneous,
    )


def greedy_select_temporal(
    store: OpportunityStore,
    *,
    temporal_coverage_target_pct: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
) -> TemporalSelectionResult:
    """任务①贪心选星，再做反向删除；结果按窗口级指标复核。"""

    area_target_pct, temporal_coverage_target_pct = _validate_temporal_inputs(
        store, area_target_pct, temporal_coverage_target_pct, min_simultaneous
    )
    usable = sorted({sat_index for sat_index, _ in store.windows})
    selected: list[int] = []
    current = _evaluate_temporal(
        store, selected, temporal_coverage_target_pct, min_simultaneous
    )
    while (current.city.qualified_area_pct or 0.0) + 1e-9 < area_target_pct:
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = _score_temporal(current)
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate_temporal(
                store,
                [*selected, sat_index],
                temporal_coverage_target_pct,
                min_simultaneous,
            )
            score = _score_temporal(trial)
            if best_metrics is None or score > best_score:
                best_index = sat_index
                best_metrics = trial
                best_score = score
        if best_index is None or best_metrics is None:
            break
        selected.append(best_index)
        current = best_metrics

    # 达标后逐颗删除，保证结果尽量小；这只是启发式，不声称全局最优。
    if (current.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
        changed = True
        while changed:
            changed = False
            for sat_index in list(selected):
                trial_selected = [item for item in selected if item != sat_index]
                trial = _evaluate_temporal(
                    store,
                    trial_selected,
                    temporal_coverage_target_pct,
                    min_simultaneous,
                )
                if (trial.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct:
                    selected = trial_selected
                    current = trial
                    changed = True
                    break
    feasible = (current.city.qualified_area_pct or 0.0) + 1e-9 >= area_target_pct
    note = (
        "贪心达到时间覆盖率的面积目标，并完成反向删除；结果是近似解。"
        if feasible
        else "全池/贪心未达到时间覆盖率的面积目标；返回当前最好的近似方案。"
    )
    return _temporal_result(
        store,
        selected,
        method="greedy",
        area_target_pct=area_target_pct,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        status="feasible_heuristic" if feasible else "infeasible_heuristic",
        note=note,
        constraint_count=0,
        min_simultaneous=min_simultaneous,
    )


def ilp_select_temporal(
    store: OpportunityStore,
    *,
    temporal_coverage_target_pct: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    time_limit_s: int = 180,
) -> TemporalSelectionResult:
    """任务①时间覆盖率 ILP；K>1 使用 y[g,t] 表示同一时刻的 K 星同时覆盖。"""

    area_target_pct, temporal_coverage_target_pct = _validate_temporal_inputs(
        store, area_target_pct, temporal_coverage_target_pct, min_simultaneous
    )
    if int(time_limit_s) <= 0:
        raise ValueError("time_limit_s 必须大于 0 秒。")
    if area_target_pct <= 0.0 or temporal_coverage_target_pct <= 0.0:
        return _temporal_result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="proven_optimal",
            note="面积目标或时间覆盖率目标为 0%，空组合即满足。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
    try:
        import pulp
    except ImportError:
        return _temporal_result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="unsupported",
            note="未安装 pulp，无法运行区域时间覆盖率整数规划。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
    if store.n_satellites > 1500:
        return _temporal_result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="skipped",
            note="卫星数超过 1500，跳过 ILP；请使用贪心或候选预筛选。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )

    step_s = int(store.decision_step_s)
    candidates, starts, ends = build_bin_candidates(store, decision_step_s=step_s)
    horizon_s = max(0.0, (store.end_utc - store.start_utc).total_seconds())
    problem = pulp.LpProblem("regional_temporal_selection", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x_{index}", cat=pulp.LpBinary) for index in range(store.n_satellites)]
    z = [pulp.LpVariable(f"z_{index}", cat=pulp.LpBinary) for index in range(store.n_cells)]
    areas = [float(cell.area_m2) for cell in store.grid.cells]
    constraint_count = 0
    problem += pulp.lpSum(areas[index] * z[index] for index in range(store.n_cells)) \
        >= float(area_target_pct / 100.0) * sum(areas)
    constraint_count += 1
    problem += pulp.lpSum(x)

    for grid_index, grid_candidates in enumerate(candidates):
        if min_simultaneous == 1:
            # 相同候选集合的时间片共享一个 y 变量，只累加持续时间，减少模型规模。
            grouped: dict[frozenset[int], float] = {}
            for time_index, candidate_set in enumerate(grid_candidates):
                key = frozenset(int(item) for item in candidate_set)
                if key:
                    duration = (ends[time_index] - starts[time_index]).total_seconds()
                    grouped[key] = grouped.get(key, 0.0) + float(duration)
            y_terms = []
            for group_index, (candidate_set, duration) in enumerate(
                sorted(grouped.items(), key=lambda item: (tuple(sorted(item[0])), item[1]))
            ):
                y = pulp.LpVariable(f"y_{grid_index}_{group_index}", cat=pulp.LpBinary)
                selected_in_group = pulp.lpSum(x[index] for index in candidate_set)
                # 两条不等式共同表达 y = OR(x_s)。
                problem += y <= selected_in_group
                problem += selected_in_group - len(candidate_set) * y <= 0
                y_terms.append((float(duration), y))
                constraint_count += 2
            covered_duration = pulp.lpSum(duration * y for duration, y in y_terms)
        else:
            y_by_time, added = _add_k_event_variables(
                problem,
                pulp,
                x,
                grid_candidates,
                min_simultaneous=min_simultaneous,
                prefix=f"y_temporal_{grid_index}",
            )
            constraint_count += added
            covered_duration = pulp.lpSum(
                (ends[time_index] - starts[time_index]).total_seconds() * y
                for time_index, y in y_by_time.items()
            )
        problem += covered_duration \
            >= float(temporal_coverage_target_pct / 100.0 * horizon_s) * z[grid_index]
        constraint_count += 1

    problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=int(time_limit_s)))
    solver_status = pulp.LpStatus[problem.status]
    if solver_status not in {"Optimal", "Not Solved"}:
        return _temporal_result(
            store,
            [],
            method="ilp",
            area_target_pct=area_target_pct,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="infeasible",
            note=f"ILP 状态为 {solver_status}，当前离散模型未找到可行方案。",
            constraint_count=constraint_count,
            min_simultaneous=min_simultaneous,
        )
    selected = [
        index
        for index, variable in enumerate(x)
        if (pulp.value(variable) or 0.0) > 0.5
    ]
    metrics = _evaluate_temporal(
        store, selected, temporal_coverage_target_pct, min_simultaneous
    )
    achieved = float(metrics.city.qualified_area_pct or 0.0)
    feasible = achieved + 1e-9 >= area_target_pct
    if solver_status == "Optimal" and feasible:
        status = "proven_optimal"
        note = "时间覆盖率 ILP 已证明离散模型最少；窗口级复核通过。"
    elif feasible:
        status = "feasible_time_limit"
        note = "ILP 达到时间上限，当前可行解通过窗口级复核，未证明全局最优。"
    else:
        status = "verification_failed"
        note = "离散 ILP 解未通过原始窗口级面积复核，不能标为满足。"
    return _temporal_result(
        store,
        selected,
        method="ilp",
        area_target_pct=area_target_pct,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        status=status,
        note=note,
        constraint_count=constraint_count,
        min_simultaneous=min_simultaneous,
    )


def _recommend_temporal(
    greedy: TemporalSelectionResult | None,
    ilp: TemporalSelectionResult | None,
) -> TemporalSelectionResult:
    candidates = [item for item in (ilp, greedy) if item is not None]
    feasible = [item for item in candidates if item.feasible]
    if feasible:
        return min(
            feasible,
            key=lambda item: (
                len(item.selected_sat_indices),
                0 if item.status == "proven_optimal" else 1,
                -item.achieved_area_pct,
            ),
        )
    return max(
        candidates,
        key=lambda item: (
            item.achieved_area_pct,
            item.metrics.city.area_weighted_temporal_coverage_pct,
            -len(item.selected_sat_indices),
        ),
    )


def run_regional_temporal_selection(
    store: OpportunityStore,
    *,
    temporal_coverage_target_pct: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    method: str = "both",
    time_limit_s: int = 180,
    refine_step_seconds: float | None = None,
    timescale: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> RegionalTemporalSelectionResult:
    """任务①总入口；可在求解前按需一次性精化窗口边界。"""

    effective_store = store
    if refine_step_seconds is not None:
        effective_store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_seconds,
            timescale=timescale,
            cfg=config,
        )
    area_target_pct, temporal_coverage_target_pct = _validate_temporal_inputs(
        effective_store,
        area_target_pct,
        temporal_coverage_target_pct,
        min_simultaneous,
    )
    method = str(method).lower()
    if method not in {"greedy", "ilp", "both"}:
        raise ValueError("method 必须是 greedy、ilp 或 both。")
    greedy = (
        greedy_select_temporal(
            effective_store,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            area_target_pct=area_target_pct,
            min_simultaneous=min_simultaneous,
        )
        if method in {"greedy", "both"}
        else None
    )
    ilp = (
        ilp_select_temporal(
            effective_store,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            area_target_pct=area_target_pct,
            min_simultaneous=min_simultaneous,
            time_limit_s=time_limit_s,
        )
        if method in {"ilp", "both"}
        else None
    )
    recommended = _recommend_temporal(greedy, ilp)
    return RegionalTemporalSelectionResult(
        task="min_satellites_temporal_coverage",
        area_target_pct=area_target_pct,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        greedy=greedy,
        ilp=ilp,
        recommended=recommended,
        min_simultaneous=min_simultaneous,
    )


def _evaluate_budget(
    store: OpportunityStore,
    selected: Sequence[int],
    temporal_coverage_target_pct: float,
    min_simultaneous: int = 1,
) -> RegionalMetricsResult:
    """用统一窗口指标评估预算方案的达标面积和城市平均覆盖率。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=min_simultaneous,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
    )


def _score_budget(metrics: RegionalMetricsResult) -> tuple[float, float, float, float]:
    """任务③字典序目标：先面积，再面积—时间平均，最后用 MRT/ART 稳定打破平局。"""

    city = metrics.city
    return (
        float(city.qualified_area_pct or 0.0),
        float(city.area_weighted_temporal_coverage_pct),
        -float(city.area_weighted_mrt_s),
        -float(city.area_weighted_art_s),
    )


def _budget_result(
    store: OpportunityStore,
    selected: Sequence[int],
    *,
    method: str,
    n_budget: int,
    exact_budget: bool,
    temporal_coverage_target_pct: float,
    status: str,
    note: str,
    constraint_count: int,
    min_simultaneous: int = 1,
) -> BudgetSelectionResult:
    selected_tuple = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate_budget(
        store, selected_tuple, temporal_coverage_target_pct, min_simultaneous
    )
    achieved_area = float(metrics.city.qualified_area_pct or 0.0)
    return BudgetSelectionResult(
        method=method,
        selected_sat_indices=selected_tuple,
        selected_sat_ids=tuple(_satellite_identity(store, index)[0] for index in selected_tuple),
        selected_sat_names=tuple(_satellite_identity(store, index)[1] for index in selected_tuple),
        n_budget=n_budget,
        exact_budget=exact_budget,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        achieved_area_pct=achieved_area,
        area_weighted_temporal_coverage_pct=metrics.city.area_weighted_temporal_coverage_pct,
        feasible=len(selected_tuple) <= n_budget and (not exact_budget or len(selected_tuple) == n_budget),
        status=status,
        note=note,
        constraint_count=int(constraint_count),
        metrics=metrics,
        min_simultaneous=min_simultaneous,
    )


def greedy_select_budget(
    store: OpportunityStore,
    *,
    n_budget: int,
    temporal_coverage_target_pct: float,
    min_simultaneous: int = 1,
    exact_budget: bool = False,
) -> BudgetSelectionResult:
    """任务③预算贪心：最多（或恰好）N 颗，按字典序最大化目标。"""

    n_budget, temporal_coverage_target_pct = _validate_budget_inputs(
        store, n_budget, temporal_coverage_target_pct, min_simultaneous
    )
    if exact_budget and n_budget > store.n_satellites:
        raise ValueError("exact_budget=True 时，n_budget 不能超过候选卫星数。")
    limit = min(n_budget, store.n_satellites)
    usable = sorted({sat_index for sat_index, _ in store.windows})
    selected: list[int] = []
    current = _evaluate_budget(
        store, selected, temporal_coverage_target_pct, min_simultaneous
    )
    while len(selected) < limit:
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = _score_budget(current)
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate_budget(
                store,
                [*selected, sat_index],
                temporal_coverage_target_pct,
                min_simultaneous,
            )
            score = _score_budget(trial)
            if best_metrics is None or score > best_score:
                best_index = sat_index
                best_metrics = trial
                best_score = score
        # ``at most N`` 模式没有目标改善就停止；``exact N`` 仍需填满预算。
        if best_index is None:
            break
        if (
            not exact_budget
            and min_simultaneous <= len(selected)
            and best_metrics is not None
            and best_score <= _score_budget(current)
        ):
            break
        selected.append(best_index)
        current = best_metrics if best_metrics is not None else current

    if exact_budget and len(selected) < limit:
        # 没有覆盖机会的卫星也可以被“恰好 N”选中，但不改变目标值。
        for sat_index in range(store.n_satellites):
            if sat_index not in selected:
                selected.append(sat_index)
                if len(selected) >= limit:
                    break
    return _budget_result(
        store,
        selected,
        method="greedy",
        n_budget=n_budget,
        exact_budget=exact_budget,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        status="feasible_heuristic",
        note="预算贪心按字典序最大化达标面积和城市平均时间覆盖率；结果是近似解。",
        constraint_count=0,
        min_simultaneous=min_simultaneous,
    )


def ilp_select_budget(
    store: OpportunityStore,
    *,
    n_budget: int,
    temporal_coverage_target_pct: float,
    min_simultaneous: int = 1,
    exact_budget: bool = False,
    time_limit_s: int = 180,
) -> BudgetSelectionResult:
    """任务③两阶段 ILP；K>1 用 y[g,t] 统计同时覆盖后的时间覆盖率。"""

    n_budget, temporal_coverage_target_pct = _validate_budget_inputs(
        store, n_budget, temporal_coverage_target_pct, min_simultaneous
    )
    if exact_budget and n_budget > store.n_satellites:
        raise ValueError("exact_budget=True 时，n_budget 不能超过候选卫星数。")
    if int(time_limit_s) <= 0:
        raise ValueError("time_limit_s 必须大于 0 秒。")
    if n_budget == 0 or temporal_coverage_target_pct == 0.0:
        selected = list(range(min(n_budget, store.n_satellites))) if exact_budget else []
        return _budget_result(
            store,
            selected,
            method="ilp",
            n_budget=n_budget,
            exact_budget=exact_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="proven_optimal",
            note="预算为 0 或时间覆盖率目标为 0%，空（或恰好 N）组合即为最优。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
    try:
        import pulp
    except ImportError:
        return _budget_result(
            store,
            [],
            method="ilp",
            n_budget=n_budget,
            exact_budget=exact_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="unsupported",
            note="未安装 pulp，无法运行预算整数规划。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
    if store.n_satellites > 1500:
        return _budget_result(
            store,
            [],
            method="ilp",
            n_budget=n_budget,
            exact_budget=exact_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="skipped",
            note="卫星数超过 1500，跳过 ILP；请使用贪心或候选预筛选。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )

    candidates, starts, ends = build_bin_candidates(store, decision_step_s=store.decision_step_s)
    horizon_s = max(0.0, (store.end_utc - store.start_utc).total_seconds())
    if horizon_s <= 0.0:
        raise ValueError("OpportunityStore 的分析时段必须大于 0。")
    areas = [float(cell.area_m2) for cell in store.grid.cells]
    problem = pulp.LpProblem("regional_budget_selection", pulp.LpMaximize)
    x = [pulp.LpVariable(f"x_{index}", cat=pulp.LpBinary) for index in range(store.n_satellites)]
    z = [pulp.LpVariable(f"z_{index}", cat=pulp.LpBinary) for index in range(store.n_cells)]
    area_expr = pulp.lpSum(areas[index] * z[index] for index in range(store.n_cells))
    if exact_budget:
        problem += pulp.lpSum(x) == n_budget
    else:
        problem += pulp.lpSum(x) <= n_budget
    constraint_count = 1

    covered_duration_exprs = []
    for grid_index, grid_candidates in enumerate(candidates):
        if min_simultaneous == 1:
            grouped: dict[frozenset[int], float] = {}
            for time_index, candidate_set in enumerate(grid_candidates):
                key = frozenset(int(item) for item in candidate_set)
                if key:
                    duration = (ends[time_index] - starts[time_index]).total_seconds()
                    grouped[key] = grouped.get(key, 0.0) + float(duration)
            y_terms = []
            for group_index, (candidate_set, duration) in enumerate(
                sorted(grouped.items(), key=lambda item: (tuple(sorted(item[0])), item[1]))
            ):
                y = pulp.LpVariable(f"y_{grid_index}_{group_index}", cat=pulp.LpBinary)
                selected_in_group = pulp.lpSum(x[index] for index in candidate_set)
                # y 是该候选集合的 OR，不能凭空把没有选中的时间片算成覆盖。
                problem += y <= selected_in_group
                problem += selected_in_group - len(candidate_set) * y <= 0
                y_terms.append((float(duration), y))
                constraint_count += 2
            covered_duration = pulp.lpSum(duration * y for duration, y in y_terms)
        else:
            y_by_time, added = _add_k_event_variables(
                problem,
                pulp,
                x,
                grid_candidates,
                min_simultaneous=min_simultaneous,
                prefix=f"y_budget_{grid_index}",
            )
            constraint_count += added
            covered_duration = pulp.lpSum(
                (ends[time_index] - starts[time_index]).total_seconds() * y
                for time_index, y in y_by_time.items()
            )
        covered_duration_exprs.append(covered_duration)
        problem += covered_duration >= float(temporal_coverage_target_pct / 100.0 * horizon_s) * z[grid_index]
        constraint_count += 1

    problem += area_expr
    problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=int(time_limit_s)))
    stage1_status = pulp.LpStatus[problem.status]
    if stage1_status not in {"Optimal", "Not Solved"}:
        return _budget_result(
            store,
            [],
            method="ilp",
            n_budget=n_budget,
            exact_budget=exact_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="infeasible",
            note=f"ILP 第一阶段状态为 {stage1_status}，未找到可行预算方案。",
            constraint_count=constraint_count,
            min_simultaneous=min_simultaneous,
        )
    stage1_selected = [
        index for index, variable in enumerate(x) if (pulp.value(variable) or 0.0) > 0.5
    ]
    if stage1_status == "Not Solved":
        return _budget_result(
            store,
            stage1_selected,
            method="ilp",
            n_budget=n_budget,
            exact_budget=exact_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            status="feasible_time_limit",
            note="ILP 第一阶段达到时间上限，返回当前达标面积方案，未证明最优。",
            constraint_count=constraint_count,
            min_simultaneous=min_simultaneous,
        )

    best_area = float(pulp.value(area_expr) or 0.0)
    tolerance = max(1e-6, sum(areas) * 1e-10)
    problem += area_expr >= best_area - tolerance
    # 固定第一阶段的达标面积后，最大化面积—时间平均覆盖率的分子。
    temporal_objective = pulp.lpSum(
        areas[index] * covered_duration_exprs[index]
        for index in range(store.n_cells)
    )
    problem.setObjective(temporal_objective)
    problem.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=int(time_limit_s)))
    stage2_status = pulp.LpStatus[problem.status]
    selected = [
        index for index, variable in enumerate(x) if (pulp.value(variable) or 0.0) > 0.5
    ]
    if stage2_status == "Optimal":
        status = "proven_optimal"
        note = "两阶段预算 ILP 已证明：先最大化达标面积，再在同面积下最大化面积—时间平均覆盖率。"
    elif stage2_status == "Not Solved":
        status = "feasible_time_limit"
        note = "第一阶段已找到最优达标面积，但第二阶段达到时间上限，未证明平均覆盖率最优。"
    else:
        selected = stage1_selected
        status = "feasible_time_limit"
        note = "第一阶段已找到最优达标面积，第二阶段未完成，返回第一阶段方案。"
    return _budget_result(
        store,
        selected,
        method="ilp",
        n_budget=n_budget,
        exact_budget=exact_budget,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        status=status,
        note=note,
        constraint_count=constraint_count + 1,
        min_simultaneous=min_simultaneous,
    )


def _recommend_budget(
    greedy: BudgetSelectionResult | None,
    ilp: BudgetSelectionResult | None,
) -> BudgetSelectionResult:
    candidates = [item for item in (ilp, greedy) if item is not None]
    feasible = [item for item in candidates if item.feasible]
    if feasible:
        return max(
            feasible,
            key=lambda item: (
                item.achieved_area_pct,
                item.area_weighted_temporal_coverage_pct,
                1 if item.status == "proven_optimal" else 0,
            ),
        )
    return max(
        candidates,
        key=lambda item: (
            item.achieved_area_pct,
            item.area_weighted_temporal_coverage_pct,
        ),
    )


def run_regional_budget_selection(
    store: OpportunityStore,
    *,
    n_budget: int,
    temporal_coverage_target_pct: float,
    min_simultaneous: int = 1,
    method: str = "both",
    exact_budget: bool = False,
    time_limit_s: int = 180,
    refine_step_seconds: float | None = None,
    timescale: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> RegionalBudgetSelectionResult:
    """任务③总入口：最多/恰好 N 颗，返回贪心、ILP 和推荐方案。"""

    effective_store = store
    if refine_step_seconds is not None:
        effective_store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_seconds,
            timescale=timescale,
            cfg=config,
        )
    n_budget, temporal_coverage_target_pct = _validate_budget_inputs(
        effective_store, n_budget, temporal_coverage_target_pct, min_simultaneous
    )
    method = str(method).lower()
    if method not in {"greedy", "ilp", "both"}:
        raise ValueError("method 必须是 greedy、ilp 或 both。")
    greedy = (
        greedy_select_budget(
            effective_store,
            n_budget=n_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            min_simultaneous=min_simultaneous,
            exact_budget=exact_budget,
        )
        if method in {"greedy", "both"}
        else None
    )
    ilp = (
        ilp_select_budget(
            effective_store,
            n_budget=n_budget,
            temporal_coverage_target_pct=temporal_coverage_target_pct,
            min_simultaneous=min_simultaneous,
            exact_budget=exact_budget,
            time_limit_s=time_limit_s,
        )
        if method in {"ilp", "both"}
        else None
    )
    recommended = _recommend_budget(greedy, ilp)
    return RegionalBudgetSelectionResult(
        task="max_area_temporal_coverage_budget",
        n_budget=n_budget,
        exact_budget=exact_budget,
        temporal_coverage_target_pct=temporal_coverage_target_pct,
        greedy=greedy,
        ilp=ilp,
        recommended=recommended,
        min_simultaneous=min_simultaneous,
    )


def _area_quantile_mrt(metrics: RegionalMetricsResult, area_target_pct: float) -> float:
    """按网格真实面积计算 MRT 的面积分位数。"""

    if area_target_pct <= 0.0 or not metrics.grid_metrics:
        return 0.0
    total_area = sum(float(item.area_m2) for item in metrics.grid_metrics)
    if total_area <= 0.0:
        return 0.0
    target_area = total_area * area_target_pct / 100.0
    accumulated = 0.0
    for item in sorted(metrics.grid_metrics, key=lambda value: (value.mrt_s, value.grid_index)):
        accumulated += float(item.area_m2)
        if accumulated + max(1e-9, total_area * 1e-12) >= target_area:
            return float(item.mrt_s)
    return max(float(item.mrt_s) for item in metrics.grid_metrics)


def _pick_budget_mrt_candidate(
    result: RegionalSelectionResult,
    *,
    n_budget: int,
    method: str,
) -> SelectionResult | None:
    """从一次 MRT 可行性求解中挑出不超预算的候选。"""

    candidates: list[SelectionResult] = []
    if method in {"greedy", "both"} and result.greedy is not None:
        candidates.append(result.greedy)
    if method in {"ilp", "both"} and result.ilp is not None:
        candidates.append(result.ilp)
    valid_statuses = {"proven_optimal", "feasible_time_limit", "feasible_heuristic"}
    feasible = [
        item
        for item in candidates
        if item.status in valid_statuses
        and item.feasible
        and len(item.selected_sat_indices) <= n_budget
    ]
    if not feasible:
        return None
    return min(
        feasible,
        key=lambda item: (
            len(item.selected_sat_indices),
            0 if item.status == "proven_optimal" else 1,
            item.metrics.city.area_weighted_mrt_s,
        ),
    )


def _quantile_result(
    store: OpportunityStore,
    selection: SelectionResult,
    *,
    area_target_pct: float,
    n_budget: int,
    method: str,
    mrt_threshold_s: float,
    feasible: bool,
    status: str,
    note: str,
    evaluated_thresholds_s: Sequence[float],
) -> AreaQuantileMrtSelectionResult:
    metrics = selection.metrics
    return AreaQuantileMrtSelectionResult(
        task="min_area_quantile_mrt",
        area_target_pct=area_target_pct,
        n_budget=n_budget,
        method=method,
        mrt_threshold_s=float(mrt_threshold_s),
        area_quantile_mrt_s=_area_quantile_mrt(metrics, area_target_pct),
        selected_sat_indices=selection.selected_sat_indices,
        selected_sat_ids=selection.selected_sat_ids,
        selected_sat_names=selection.selected_sat_names,
        achieved_area_pct=float(metrics.city.qualified_area_pct or 0.0),
        area_weighted_mrt_s=float(metrics.city.area_weighted_mrt_s),
        feasible=bool(feasible),
        status=status,
        note=note,
        evaluations=len(evaluated_thresholds_s),
        evaluated_thresholds_s=tuple(float(item) for item in evaluated_thresholds_s),
        selection=selection,
        min_simultaneous=selection.min_simultaneous,
    )


def select_min_area_quantile_mrt(
    store: OpportunityStore,
    *,
    area_target_pct: float = 90.0,
    n_budget: int,
    method: str = "ilp",
    min_simultaneous: int = 1,
    time_limit_s: int = 180,
    refine_step_seconds: float | None = None,
    timescale: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> AreaQuantileMrtSelectionResult:
    """任务④：给定预算，最小化城市面积分位 MRT（支持 K=1 与 K>1）。

    先把 MRT 上限离散成 ``decision_step_s`` 的桶，再对“任务②是否能在 N 颗以内
    达到 beta 面积”做单调二分。最终结果仍按原始窗口计算面积分位 MRT；该结果不把
    离散桶误称为秒级全局最优。
    """

    area_target_pct = float(area_target_pct)
    if not 0.0 <= area_target_pct <= 100.0:
        raise ValueError("area_target_pct 必须在 0～100 之间。")
    if int(n_budget) != n_budget or int(n_budget) < 0:
        raise ValueError("n_budget 必须是非负整数。")
    n_budget = int(n_budget)
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if int(time_limit_s) <= 0:
        raise ValueError("time_limit_s 必须大于 0 秒。")
    method = str(method).lower()
    if method not in {"greedy", "ilp", "both"}:
        raise ValueError("method 必须是 greedy、ilp 或 both。")

    effective_store = store
    if refine_step_seconds is not None:
        effective_store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_seconds,
            timescale=timescale,
            cfg=config,
        )
    if effective_store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    horizon_s = (effective_store.end_utc - effective_store.start_utc).total_seconds()
    if horizon_s <= 0.0:
        raise ValueError("OpportunityStore 的分析时段必须大于 0。")

    # 面积目标为 0 时，不需要任何卫星；MRT 分位数按空组合定义为分析时长。
    if area_target_pct <= 0.0:
        empty = _result(
            effective_store,
            [],
            method=method,
            area_target_pct=0.0,
            mrt_target_s=0.0,
            status="proven_optimal",
            note="面积目标为 0%，空组合即可满足。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
        return _quantile_result(
            effective_store,
            empty,
            area_target_pct=area_target_pct,
            n_budget=n_budget,
            method=method,
            mrt_threshold_s=0.0,
            feasible=True,
            status="proven_optimal",
            note="面积目标为 0%，空组合即可满足。",
            evaluated_thresholds_s=(0.0,),
        )

    step_s = max(1, int(effective_store.decision_step_s))
    max_bucket = max(1, int(math.ceil(horizon_s / step_s)))
    evaluated: dict[int, SelectionResult | None] = {}
    traces: list[float] = []

    def evaluate(bucket: int) -> SelectionResult | None:
        bucket = max(0, min(max_bucket, int(bucket)))
        if bucket in evaluated:
            return evaluated[bucket]
        threshold_s = float(bucket * step_s)
        traces.append(threshold_s)
        run = run_regional_mrt_selection(
            effective_store,
            mrt_target_s=threshold_s,
            area_target_pct=area_target_pct,
            min_simultaneous=min_simultaneous,
            method=method,
            time_limit_s=time_limit_s,
        )
        candidate = _pick_budget_mrt_candidate(run, n_budget=n_budget, method=method)
        evaluated[bucket] = candidate
        return candidate

    high_candidate = evaluate(max_bucket)
    if high_candidate is None:
        # 无法运行求解器或连分析时长上限都没有可用候选，返回一次明确的失败结果。
        fallback = _result(
            effective_store,
            [],
            method=method,
            area_target_pct=area_target_pct,
            mrt_target_s=float(max_bucket * step_s),
            status="unsupported",
            note="任务④未找到可在预算内复核的候选；请检查求解器、预算或输入窗口。",
            constraint_count=0,
            min_simultaneous=min_simultaneous,
        )
        return _quantile_result(
            effective_store,
            fallback,
            area_target_pct=area_target_pct,
            n_budget=n_budget,
            method=method,
            mrt_threshold_s=float(max_bucket * step_s),
            feasible=False,
            status="unsupported",
            note=fallback.note,
            evaluated_thresholds_s=traces,
        )

    low = 0
    high = max_bucket
    while low < high:
        middle = (low + high) // 2
        if evaluate(middle) is not None:
            high = middle
        else:
            low = middle + 1
    candidate = evaluate(low)
    if candidate is None:
        # 理论上不应发生；保留高阈值可行解，避免把搜索异常伪装成空结果。
        candidate = high_candidate
        status = "feasible_time_limit"
        note = "二分过程中最低桶未能复核，返回已确认可行的最高阈值候选。"
    else:
        if method == "greedy":
            status = "feasible_heuristic"
            note = "按决策时间片二分得到最低可行 MRT 桶；贪心只提供近似选星。"
        elif candidate.status == "proven_optimal":
            status = "proven_discrete_optimal"
            note = "在离散 MRT 桶上完成二分；每次 ILP 可行性求解均通过窗口级复核。"
        else:
            status = "feasible_time_limit"
            note = "找到预算内可行的 MRT 桶，但部分 ILP 未证明最优。"
    threshold_s = float(low * step_s)
    return _quantile_result(
        effective_store,
        candidate,
        area_target_pct=area_target_pct,
        n_budget=n_budget,
        method=method,
        mrt_threshold_s=threshold_s,
        feasible=True,
        status=status,
        note=note,
        evaluated_thresholds_s=traces,
    )


def _evaluate_art(
    store: OpportunityStore,
    selected: Sequence[int],
    min_simultaneous: int = 1,
) -> RegionalMetricsResult:
    """用统一窗口指标评估任务⑤的 ART 目标。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=min_simultaneous,
    )


def _score_art(
    metrics: RegionalMetricsResult, mrt_area_quantile_pct: float
) -> tuple[float, float, float, float]:
    """任务⑤字典序评分：ART、面积分位 MRT、面积加权 MRT、覆盖率。"""

    city = metrics.city
    return (
        float(city.area_weighted_art_s),
        _area_quantile_mrt(metrics, mrt_area_quantile_pct),
        float(city.area_weighted_mrt_s),
        -float(city.area_weighted_temporal_coverage_pct),
    )


def _score_art_better(
    candidate: tuple[float, ...], current: tuple[float, ...], tolerance: float = 1e-9
) -> bool:
    """带数值容差的字典序“小于”比较。"""

    for left, right in zip(candidate, current):
        if left < right - tolerance:
            return True
        if left > right + tolerance:
            return False
    return False


def _score_art_not_worse(
    candidate: tuple[float, ...], current: tuple[float, ...], tolerance: float = 1e-9
) -> bool:
    """判断候选是否不劣于当前解（用于安全反向删除）。"""

    return _score_art_better(candidate, current, tolerance) or all(
        abs(left - right) <= tolerance for left, right in zip(candidate, current)
    )


def _art_result(
    store: OpportunityStore,
    selected: Sequence[int],
    *,
    n_budget: int,
    exact_budget: bool,
    mrt_area_quantile_pct: float,
    status: str,
    note: str,
    iterations: int,
    exchange_count: int,
    deletion_count: int,
    min_simultaneous: int = 1,
) -> ArtSelectionResult:
    selected_tuple = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate_art(store, selected_tuple, min_simultaneous)
    identities = [_satellite_identity(store, index) for index in selected_tuple]
    return ArtSelectionResult(
        method="greedy_local_search",
        selected_sat_indices=selected_tuple,
        selected_sat_ids=tuple(item[0] for item in identities),
        selected_sat_names=tuple(item[1] for item in identities),
        n_budget=n_budget,
        exact_budget=exact_budget,
        mrt_area_quantile_pct=mrt_area_quantile_pct,
        area_weighted_art_s=float(metrics.city.area_weighted_art_s),
        area_quantile_mrt_s=_area_quantile_mrt(metrics, mrt_area_quantile_pct),
        area_weighted_mrt_s=float(metrics.city.area_weighted_mrt_s),
        area_weighted_temporal_coverage_pct=float(
            metrics.city.area_weighted_temporal_coverage_pct
        ),
        feasible=len(selected_tuple) <= n_budget
        and (not exact_budget or len(selected_tuple) == n_budget),
        status=status,
        note=note,
        iterations=iterations,
        exchange_count=exchange_count,
        deletion_count=deletion_count,
        metrics=metrics,
        min_simultaneous=min_simultaneous,
    )


def greedy_select_art(
    store: OpportunityStore,
    *,
    n_budget: int,
    exact_budget: bool = False,
    mrt_area_quantile_pct: float = 90.0,
    min_simultaneous: int = 1,
    max_local_rounds: int = 3,
) -> ArtSelectionResult:
    """任务⑤：预算内贪心降低面积加权 ART，并做交换与反向删除。"""

    if int(n_budget) != n_budget or int(n_budget) < 0:
        raise ValueError("n_budget 必须是非负整数。")
    n_budget = int(n_budget)
    if not 0.0 <= float(mrt_area_quantile_pct) <= 100.0:
        raise ValueError("mrt_area_quantile_pct 必须在 0～100 之间。")
    mrt_area_quantile_pct = float(mrt_area_quantile_pct)
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if int(max_local_rounds) != max_local_rounds or int(max_local_rounds) < 0:
        raise ValueError("max_local_rounds 必须是非负整数。")
    max_local_rounds = int(max_local_rounds)
    if exact_budget and n_budget > store.n_satellites:
        raise ValueError("exact_budget=True 时，n_budget 不能超过候选卫星数。")

    limit = min(n_budget, store.n_satellites)
    usable = sorted({sat_index for sat_index, _ in store.windows})
    selected: list[int] = []
    current = _evaluate_art(store, selected, min_simultaneous)
    iterations = 0

    # 正向贪心：每轮选择让 ART 字典序下降最多的卫星。
    while len(selected) < min(limit, len(usable)):
        current_score = _score_art(current, mrt_area_quantile_pct)
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = current_score
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate_art(store, [*selected, sat_index], min_simultaneous)
            score = _score_art(trial, mrt_area_quantile_pct)
            if best_metrics is None or _score_art_better(score, best_score):
                best_index = sat_index
                best_metrics = trial
                best_score = score
        if best_index is None or best_metrics is None:
            break
        if (
            not exact_budget
            and min_simultaneous <= len(selected)
            and not _score_art_better(best_score, current_score)
        ):
            break
        selected.append(best_index)
        current = best_metrics
        iterations += 1

    if exact_budget and len(selected) < limit:
        # 恰好 N 颗时，剩余位置可由无覆盖机会的卫星填充，不改变 ART。
        for sat_index in range(store.n_satellites):
            if sat_index not in selected:
                selected.append(sat_index)
                if len(selected) >= limit:
                    break
        current = _evaluate_art(store, selected, min_simultaneous)

    # 单星交换：避免“第一颗选错后，后续永远补不回来”的贪心局部陷阱。
    exchange_count = 0
    for _round in range(max_local_rounds):
        if not selected:
            break
        current_score = _score_art(current, mrt_area_quantile_pct)
        best_swap: tuple[int, int] | None = None
        best_metrics = None
        best_score = current_score
        for old_index in selected:
            for new_index in usable:
                if new_index in selected:
                    continue
                trial_selected = [new_index if item == old_index else item for item in selected]
                trial = _evaluate_art(store, trial_selected, min_simultaneous)
                score = _score_art(trial, mrt_area_quantile_pct)
                if best_metrics is None or _score_art_better(score, best_score):
                    best_swap = (old_index, new_index)
                    best_metrics = trial
                    best_score = score
        if best_swap is None or best_metrics is None or not _score_art_better(best_score, current_score):
            break
        old_index, new_index = best_swap
        selected[selected.index(old_index)] = new_index
        current = best_metrics
        exchange_count += 1
        iterations += 1

    # 反向删除：最多预算模式下，删除不损失目标值的冗余卫星。
    deletion_count = 0
    if not exact_budget:
        changed = True
        while changed:
            changed = False
            current_score = _score_art(current, mrt_area_quantile_pct)
            for sat_index in list(selected):
                trial_selected = [item for item in selected if item != sat_index]
                trial = _evaluate_art(store, trial_selected, min_simultaneous)
                if _score_art_not_worse(
                    _score_art(trial, mrt_area_quantile_pct), current_score
                ):
                    selected = trial_selected
                    current = trial
                    deletion_count += 1
                    changed = True
                    break

    return _art_result(
        store,
        selected,
        n_budget=n_budget,
        exact_budget=exact_budget,
        mrt_area_quantile_pct=mrt_area_quantile_pct,
        status="feasible_heuristic",
        note="贪心降低面积加权 ART，并完成单星交换和安全反向删除；结果是近似解。",
        iterations=iterations,
        exchange_count=exchange_count,
        deletion_count=deletion_count,
        min_simultaneous=min_simultaneous,
    )


def run_regional_art_selection(
    store: OpportunityStore,
    *,
    n_budget: int,
    exact_budget: bool = False,
    mrt_area_quantile_pct: float = 90.0,
    min_simultaneous: int = 1,
    max_local_rounds: int = 3,
    refine_step_seconds: float | None = None,
    timescale: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> ArtSelectionResult:
    """任务⑤总入口；可选地先统一精化一次区域机会窗口。"""

    effective_store = store
    if refine_step_seconds is not None:
        effective_store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_seconds,
            timescale=timescale,
            cfg=config,
        )
    return greedy_select_art(
        effective_store,
        n_budget=n_budget,
        exact_budget=exact_budget,
        mrt_area_quantile_pct=mrt_area_quantile_pct,
        min_simultaneous=min_simultaneous,
        max_local_rounds=max_local_rounds,
    )


def _recommend(
    greedy: SelectionResult | None,
    ilp: SelectionResult | None,
) -> SelectionResult:
    candidates = [item for item in (ilp, greedy) if item is not None]
    feasible = [item for item in candidates if item.feasible]
    if feasible:
        return min(
            feasible,
            key=lambda item: (
                len(item.selected_sat_indices),
                0 if item.status == "proven_optimal" else 1,
                -item.achieved_area_pct,
            ),
        )
    return max(
        candidates,
        key=lambda item: (
            item.achieved_area_pct,
            -item.metrics.city.area_weighted_mrt_s,
            -len(item.selected_sat_indices),
        ),
    )


def run_regional_mrt_selection(
    store: OpportunityStore,
    *,
    mrt_target_s: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    method: str = "both",
    time_limit_s: int = 180,
    refine_step_seconds: float | None = None,
    timescale: Any | None = None,
    config: Mapping[str, Any] | None = None,
) -> RegionalSelectionResult:
    """任务②总入口：运行贪心、ILP 或两者并给出推荐方案。

    ``refine_step_seconds`` 不为 ``None`` 时，会在选星前对机会窗口做一次按需边界
    精化，之后所有候选组合共享这份精化结果；默认为 ``None``，避免用户只想快速
    预览时为全池窗口付出额外计算成本。
    """

    effective_store = store
    if refine_step_seconds is not None:
        effective_store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_seconds,
            timescale=timescale,
            cfg=config,
        )
    area_target_pct, mrt_target_s = _validate_inputs(
        effective_store, area_target_pct, mrt_target_s, min_simultaneous
    )
    method = str(method).lower()
    if method not in {"greedy", "ilp", "both"}:
        raise ValueError("method 必须是 greedy、ilp 或 both。")
    greedy = (
        greedy_select_mrt(
            effective_store,
            mrt_target_s=mrt_target_s,
            area_target_pct=area_target_pct,
            min_simultaneous=min_simultaneous,
        )
        if method in {"greedy", "both"}
        else None
    )
    ilp = (
        ilp_select_mrt(
            effective_store,
            mrt_target_s=mrt_target_s,
            area_target_pct=area_target_pct,
            min_simultaneous=min_simultaneous,
            time_limit_s=time_limit_s,
        )
        if method in {"ilp", "both"}
        else None
    )
    recommended = _recommend(greedy, ilp)
    return RegionalSelectionResult(
        task="min_satellites_mrt",
        area_target_pct=area_target_pct,
        mrt_target_s=mrt_target_s,
        greedy=greedy,
        ilp=ilp,
        recommended=recommended,
        min_simultaneous=min_simultaneous,
    )


__all__ = [
    "SelectionResult",
    "RegionalSelectionResult",
    "TemporalSelectionResult",
    "RegionalTemporalSelectionResult",
    "BudgetSelectionResult",
    "RegionalBudgetSelectionResult",
    "AreaQuantileMrtSelectionResult",
    "ArtSelectionResult",
    "build_bin_candidates",
    "greedy_select_mrt",
    "ilp_select_mrt",
    "run_regional_mrt_selection",
    "greedy_select_temporal",
    "ilp_select_temporal",
    "run_regional_temporal_selection",
    "greedy_select_budget",
    "ilp_select_budget",
    "run_regional_budget_selection",
    "select_min_area_quantile_mrt",
    "greedy_select_art",
    "run_regional_art_selection",
]
