# -*- coding: utf-8 -*-
"""区域选星任务①/②（当前均为 K=1）。

任务②给定“每个网格最大空窗 MRT 不超过 ``mrt_target_s``”，任务①给定“每个网格
时间覆盖率不低于 ``temporal_coverage_target_pct``”；两者都要求至少
``area_target_pct`` 的城市面积达标，求卫星数量尽量少。

实现分两层：

* 贪心 + 反向删除：每轮优先改善达标面积、再缩短面积加权 MRT/ART，得到工程近似解；
* K=1 滑动窗口 ILP：一个长度为 ``MRT/步长+1`` 的时间窗内必须至少有一颗已选卫星
  覆盖。90% 模式增加网格达标变量 ``z_g``，严格 100% 模式直接约束所有网格。

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "area_target_pct": self.area_target_pct,
            "mrt_target_s": self.mrt_target_s,
            "greedy": self.greedy.to_dict() if self.greedy else None,
            "ilp": self.ilp.to_dict() if self.ilp else None,
            "recommended": self.recommended.to_dict(),
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "area_target_pct": self.area_target_pct,
            "temporal_coverage_target_pct": self.temporal_coverage_target_pct,
            "greedy": self.greedy.to_dict() if self.greedy else None,
            "ilp": self.ilp.to_dict() if self.ilp else None,
            "recommended": self.recommended.to_dict(),
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
    if int(min_simultaneous) != 1:
        raise NotImplementedError(
            "当前区域任务②先实现 K=1 滑动窗口模型；K>1 使用通用 y 模型，后续阶段接入。"
        )
    if store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    return area_target_pct, mrt_target_s


def _validate_temporal_inputs(
    store: OpportunityStore,
    area_target_pct: float,
    temporal_coverage_target_pct: float,
    min_simultaneous: int,
) -> tuple[float, float]:
    """校验任务①参数；首版与任务②一样先走 K=1 快速路径。"""

    area_target_pct = float(area_target_pct)
    temporal_coverage_target_pct = float(temporal_coverage_target_pct)
    if not 0.0 <= area_target_pct <= 100.0:
        raise ValueError("area_target_pct 必须在 0～100 之间。")
    if not 0.0 <= temporal_coverage_target_pct <= 100.0:
        raise ValueError("temporal_coverage_target_pct 必须在 0～100 之间。")
    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    if int(min_simultaneous) != 1:
        raise NotImplementedError(
            "当前区域任务①先实现 K=1 的时间覆盖率模型；K>1 使用通用 y 模型，后续阶段接入。"
        )
    if store.decision_step_s <= 0:
        raise ValueError("OpportunityStore.decision_step_s 必须大于 0。")
    return area_target_pct, temporal_coverage_target_pct


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
) -> RegionalMetricsResult:
    """统一用窗口级指标评估一个组合。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=1,
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
) -> SelectionResult:
    selected = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate(store, selected, mrt_target_s)
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
    current = _evaluate(store, selected, mrt_target_s)
    while (current.city.qualified_area_pct or 0.0) + 1e-9 < area_target_pct:
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = _score(current)
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate(store, [*selected, sat_index], mrt_target_s)
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
                trial = _evaluate(store, trial_selected, mrt_target_s)
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
    )


def ilp_select_mrt(
    store: OpportunityStore,
    *,
    mrt_target_s: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    time_limit_s: int = 180,
) -> SelectionResult:
    """K=1 滑动窗口 ILP，最小化选星数量并做窗口级复核。"""

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
        )

    step_s = int(store.decision_step_s)
    candidates, _starts, _ends = build_bin_candidates(store, decision_step_s=step_s)
    n_bins = len(_starts)
    window_bins = max(1, int(math.floor(mrt_target_s / step_s)) + 1)
    strict = area_target_pct >= 100.0 - 1e-9
    grouped_constraints = _constraint_sets(candidates, window_bins, strict=strict)

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
        )
    selected = [index for index, variable in enumerate(x) if (pulp.value(variable) or 0.0) > 0.5]
    metrics = _evaluate(store, selected, mrt_target_s)
    achieved = float(metrics.city.qualified_area_pct or 0.0)
    feasible = achieved + 1e-9 >= area_target_pct
    if solver_status == "Optimal" and feasible:
        status = "proven_optimal"
        note = "K=1 滑动窗口 ILP 已证明离散模型最少；窗口级复核通过。"
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
    )
    return result


def _evaluate_temporal(
    store: OpportunityStore,
    selected: Sequence[int],
    temporal_coverage_target_pct: float,
) -> RegionalMetricsResult:
    """用窗口级指标评估任务①的单元时间覆盖率达标面积。"""

    return compute_regional_metrics(
        store,
        selected_sat_indices=selected,
        min_simultaneous=1,
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
) -> TemporalSelectionResult:
    selected_tuple = tuple(sorted({int(index) for index in selected}))
    metrics = _evaluate_temporal(store, selected_tuple, temporal_coverage_target_pct)
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
    current = _evaluate_temporal(store, selected, temporal_coverage_target_pct)
    while (current.city.qualified_area_pct or 0.0) + 1e-9 < area_target_pct:
        best_index: int | None = None
        best_metrics: RegionalMetricsResult | None = None
        best_score = _score_temporal(current)
        for sat_index in usable:
            if sat_index in selected:
                continue
            trial = _evaluate_temporal(
                store, [*selected, sat_index], temporal_coverage_target_pct
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
                    store, trial_selected, temporal_coverage_target_pct
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
    )


def ilp_select_temporal(
    store: OpportunityStore,
    *,
    temporal_coverage_target_pct: float,
    area_target_pct: float = 100.0,
    min_simultaneous: int = 1,
    time_limit_s: int = 180,
) -> TemporalSelectionResult:
    """任务① K=1 时间覆盖率 ILP，最小化选星数量并做窗口级复核。"""

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
            # 两条不等式共同表达 y = OR(x_s)：有星才能 y=1，有任意一颗被选则 y 必须为 1。
            problem += y <= selected_in_group
            problem += selected_in_group - len(candidate_set) * y <= 0
            y_terms.append((float(duration), y))
            constraint_count += 2
        problem += pulp.lpSum(duration * y for duration, y in y_terms) \
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
        )
    selected = [
        index
        for index, variable in enumerate(x)
        if (pulp.value(variable) or 0.0) > 0.5
    ]
    metrics = _evaluate_temporal(store, selected, temporal_coverage_target_pct)
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
    )


__all__ = [
    "SelectionResult",
    "RegionalSelectionResult",
    "TemporalSelectionResult",
    "RegionalTemporalSelectionResult",
    "build_bin_candidates",
    "greedy_select_mrt",
    "ilp_select_mrt",
    "run_regional_mrt_selection",
    "greedy_select_temporal",
    "ilp_select_temporal",
    "run_regional_temporal_selection",
]
