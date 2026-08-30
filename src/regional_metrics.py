# -*- coding: utf-8 -*-
"""区域覆盖指标：从稀疏窗口计算网格指标和城市汇总。

本模块不传播轨道、不调用选星器，也不改变旧单点路径。它只消费
``regional_opportunities.OpportunityStore`` 中已经算好的覆盖窗口：

``卫星窗口 -> 每个网格的并集/重叠 -> q、MRT、ART -> 面积加权城市指标``。

窗口采用半开区间 ``[start_utc, end_utc)``。因此首个窗口前和最后一个窗口后的空档都会
进入空窗统计；全程覆盖时没有空窗，MRT/ART 定义为 0；全程不覆盖时只有一个长度为
分析时长的空窗。面积权重来自裁剪网格单元的 ``area_m2``，不是代表点数量。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Sequence

import numpy as np

from regional_opportunities import OpportunityStore


@dataclass(frozen=True)
class TimeInterval:
    """一个半开时间区间。"""

    start_utc: datetime
    end_utc: datetime

    @property
    def duration_s(self) -> float:
        return max(0.0, (self.end_utc - self.start_utc).total_seconds())


@dataclass(frozen=True)
class GridMetric:
    """一个裁剪网格单元在选定卫星集合下的指标。"""

    grid_index: int
    grid_id: str
    area_m2: float
    covered_duration_s: float
    temporal_coverage_pct: float
    mrt_s: float
    art_s: float
    raw_opportunity_count: int
    merged_coverage_window_count: int
    gaps: tuple[TimeInterval, ...]
    qualified: bool | None


@dataclass(frozen=True)
class CityMetrics:
    """区域级汇总指标，所有百分比均为 0～100。"""

    horizon_s: float
    total_area_m2: float
    area_weighted_temporal_coverage_pct: float
    average_observable_duration_s: float
    area_weighted_mrt_s: float
    area_weighted_art_s: float
    qualified_area_pct: float | None
    qualified_grid_count: int | None
    area_coverage_time_pct: float
    full_area_coverage_time_pct: float


@dataclass(frozen=True)
class RegionalMetricsResult:
    """区域指标结果；保留网格明细供地图和后续选星使用。"""

    selected_sat_indices: tuple[int, ...]
    min_simultaneous: int
    temporal_coverage_target_pct: float | None
    mrt_target_s: float | None
    art_target_s: float | None
    grid_metrics: tuple[GridMetric, ...]
    city: CityMetrics

    def to_grid_rows(self) -> list[dict[str, Any]]:
        """转换为地图/调试数据；正式聊天结果不必逐格展示。"""

        return [
            {
                "grid_index": item.grid_index,
                "grid_id": item.grid_id,
                "area_m2": item.area_m2,
                "temporal_coverage_pct": item.temporal_coverage_pct,
                "covered_duration_s": item.covered_duration_s,
                "mrt_s": item.mrt_s,
                "art_s": item.art_s,
                "raw_opportunity_count": item.raw_opportunity_count,
                "merged_coverage_window_count": item.merged_coverage_window_count,
                "qualified": item.qualified,
            }
            for item in self.grid_metrics
        ]

    def to_city_dict(self) -> dict[str, Any]:
        """转换为人类可读指标卡使用的字典。"""

        return {
            "horizon_s": self.city.horizon_s,
            "total_area_m2": self.city.total_area_m2,
            "area_weighted_temporal_coverage_pct": self.city.area_weighted_temporal_coverage_pct,
            "average_observable_duration_s": self.city.average_observable_duration_s,
            "area_weighted_mrt_s": self.city.area_weighted_mrt_s,
            "area_weighted_art_s": self.city.area_weighted_art_s,
            "qualified_area_pct": self.city.qualified_area_pct,
            "qualified_grid_count": self.city.qualified_grid_count,
            "area_coverage_time_pct": self.city.area_coverage_time_pct,
            "full_area_coverage_time_pct": self.city.full_area_coverage_time_pct,
        }


def _validate_target_pct(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not 0.0 <= value <= 100.0:
        raise ValueError(f"{name} 必须在 0～100 之间。")
    return value


def _normalize_satellite_indices(
    store: OpportunityStore, selected_sat_indices: Sequence[int] | None
) -> tuple[int, ...]:
    """校验并稳定化选星索引；None 表示全池。"""

    if selected_sat_indices is None:
        return tuple(range(store.n_satellites))
    result = tuple(sorted({int(index) for index in selected_sat_indices}))
    if any(index < 0 or index >= store.n_satellites for index in result):
        raise ValueError("selected_sat_indices 中存在超出卫星列表范围的索引。")
    return result


def _clip_interval(
    start: datetime, end: datetime, lower: datetime, upper: datetime
) -> tuple[datetime, datetime] | None:
    start = max(start, lower)
    end = min(end, upper)
    return (start, end) if end > start else None


def _merge_intervals(
    intervals: Iterable[tuple[datetime, datetime]],
    lower: datetime,
    upper: datetime,
) -> list[tuple[datetime, datetime]]:
    """合并重叠或首尾相接的半开区间。"""

    clipped = []
    for start, end in intervals:
        item = _clip_interval(start, end, lower, upper)
        if item is not None:
            clipped.append(item)
    if not clipped:
        return []
    clipped.sort(key=lambda item: (item[0], item[1]))
    merged: list[tuple[datetime, datetime]] = [clipped[0]]
    for start, end in clipped[1:]:
        old_start, old_end = merged[-1]
        if start <= old_end:
            merged[-1] = (old_start, max(old_end, end))
        else:
            merged.append((start, end))
    return merged


def _at_least_k_intervals(
    intervals: Iterable[tuple[datetime, datetime]],
    lower: datetime,
    upper: datetime,
    min_simultaneous: int,
) -> list[tuple[datetime, datetime]]:
    """扫描线计算至少 K 个卫星同时覆盖的时间区间。"""

    if min_simultaneous <= 1:
        return _merge_intervals(intervals, lower, upper)
    events: dict[datetime, int] = {lower: 0, upper: 0}
    for start, end in intervals:
        item = _clip_interval(start, end, lower, upper)
        if item is None:
            continue
        start, end = item
        events[start] = events.get(start, 0) + 1
        events[end] = events.get(end, 0) - 1
    ordered = sorted(events.items())
    active = 0
    previous = lower
    covered: list[tuple[datetime, datetime]] = []
    for timestamp, delta in ordered:
        if timestamp > previous and active >= min_simultaneous:
            covered.append((previous, timestamp))
        active += delta
        previous = timestamp
    return _merge_intervals(covered, lower, upper)


def _complement(
    intervals: Sequence[tuple[datetime, datetime]], lower: datetime, upper: datetime
) -> list[tuple[datetime, datetime]]:
    """求覆盖区间在分析时段内的补集；首尾空窗自然保留。"""

    gaps: list[tuple[datetime, datetime]] = []
    cursor = lower
    for start, end in intervals:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < upper:
        gaps.append((cursor, upper))
    return gaps


def _to_time_intervals(intervals: Iterable[tuple[datetime, datetime]]) -> tuple[TimeInterval, ...]:
    return tuple(TimeInterval(start_utc=start, end_utc=end) for start, end in intervals)


def _area_coverage_time_fraction(
    intervals_by_grid: Sequence[Sequence[tuple[datetime, datetime]]],
    areas_m2: np.ndarray,
    lower: datetime,
    upper: datetime,
    target_area_pct: float,
) -> float:
    """分析时段内，覆盖面积达到目标比例的时间百分比。"""

    total_area = float(np.sum(areas_m2))
    horizon_s = (upper - lower).total_seconds()
    if total_area <= 0.0 or horizon_s <= 0.0:
        return 0.0
    target_area = total_area * target_area_pct / 100.0
    events: dict[datetime, float] = {lower: 0.0, upper: 0.0}
    for index, intervals in enumerate(intervals_by_grid):
        area = float(areas_m2[index])
        for start, end in intervals:
            events[start] = events.get(start, 0.0) + area
            events[end] = events.get(end, 0.0) - area
    active_area = 0.0
    previous = lower
    covered_s = 0.0
    tolerance = max(1e-9, total_area * 1e-12)
    for timestamp, delta in sorted(events.items()):
        if timestamp > previous and active_area + tolerance >= target_area:
            covered_s += (timestamp - previous).total_seconds()
        active_area += delta
        previous = timestamp
    return max(0.0, min(100.0, 100.0 * covered_s / horizon_s))


def compute_regional_metrics(
    store: OpportunityStore,
    selected_sat_indices: Sequence[int] | None = None,
    *,
    min_simultaneous: int = 1,
    temporal_coverage_target_pct: float | None = None,
    mrt_target_s: float | None = None,
    art_target_s: float | None = None,
    area_coverage_target_pct: float = 90.0,
) -> RegionalMetricsResult:
    """由稀疏窗口计算区域指标。

    ``min_simultaneous`` 表示同一网格、同一时刻至少需要几颗已选卫星同时覆盖；
    它不是观测窗口数量。K=1 时是窗口并集，K>1 时用扫描线求重叠区间。

    ``temporal_coverage_target_pct``、``mrt_target_s`` 和 ``art_target_s`` 都可选。
    至少给出一个时，``GridMetric.qualified`` 和城市达标面积比例才有定义；不传时
    仍完整计算基础指标，但 ``qualified_area_pct`` 返回 None。

    ``area_coverage_target_pct`` 用于城市的“同时覆盖时间比例”：例如 90 表示在多少
    时间内，按网格面积加权的当前覆盖面积至少达到全市 90%。100 即严格全市同时覆盖。
    """

    if int(min_simultaneous) != min_simultaneous or min_simultaneous <= 0:
        raise ValueError("min_simultaneous 必须是正整数。")
    min_simultaneous = int(min_simultaneous)
    temporal_target = _validate_target_pct(temporal_coverage_target_pct, "时间覆盖率目标")
    area_target = _validate_target_pct(area_coverage_target_pct, "同时覆盖面积目标")
    if mrt_target_s is not None and float(mrt_target_s) < 0.0:
        raise ValueError("mrt_target_s 不能为负数。")
    if art_target_s is not None and float(art_target_s) < 0.0:
        raise ValueError("art_target_s 不能为负数。")
    mrt_target = None if mrt_target_s is None else float(mrt_target_s)
    art_target = None if art_target_s is None else float(art_target_s)
    selected = _normalize_satellite_indices(store, selected_sat_indices)

    lower = store.start_utc
    upper = store.end_utc
    horizon_s = max(0.0, (upper - lower).total_seconds())
    areas = np.asarray([cell.area_m2 for cell in store.grid.cells], dtype=float)
    if len(areas) != store.n_cells or np.any(areas <= 0.0):
        raise ValueError("RegionGrid 的面积权重必须全部为正。")

    grid_metrics: list[GridMetric] = []
    intervals_by_grid: list[list[tuple[datetime, datetime]]] = []
    for grid_index, cell in enumerate(store.grid.cells):
        raw_windows: list[tuple[datetime, datetime]] = []
        raw_count = 0
        for sat_index in selected:
            items = store.windows.get((sat_index, grid_index), ())
            raw_count += len(items)
            raw_windows.extend((item.start_utc, item.end_utc) for item in items)
        covered = _at_least_k_intervals(raw_windows, lower, upper, min_simultaneous)
        intervals_by_grid.append(covered)
        gaps = _complement(covered, lower, upper)
        covered_s = sum((end - start).total_seconds() for start, end in covered)
        q_pct = 0.0 if horizon_s <= 0 else 100.0 * covered_s / horizon_s
        gap_lengths = [(end - start).total_seconds() for start, end in gaps]
        mrt_s = max(gap_lengths, default=0.0)
        art_s = float(np.mean(gap_lengths)) if gap_lengths else 0.0
        qualified: bool | None = None
        if temporal_target is not None or mrt_target is not None or art_target is not None:
            qualified = (
                (temporal_target is None or q_pct >= temporal_target)
                and (mrt_target is None or mrt_s <= mrt_target)
                and (art_target is None or art_s <= art_target)
            )
        grid_metrics.append(
            GridMetric(
                grid_index=grid_index,
                grid_id=cell.grid_id,
                area_m2=float(cell.area_m2),
                covered_duration_s=float(covered_s),
                temporal_coverage_pct=float(max(0.0, min(100.0, q_pct))),
                mrt_s=float(mrt_s),
                art_s=float(art_s),
                raw_opportunity_count=raw_count,
                merged_coverage_window_count=len(covered),
                gaps=_to_time_intervals(gaps),
                qualified=qualified,
            )
        )

    total_area = float(np.sum(areas))
    q_values = np.asarray([item.temporal_coverage_pct / 100.0 for item in grid_metrics])
    mrt_values = np.asarray([item.mrt_s for item in grid_metrics])
    art_values = np.asarray([item.art_s for item in grid_metrics])
    area_weighted_q = float(np.dot(areas, q_values) / total_area)
    area_weighted_mrt = float(np.dot(areas, mrt_values) / total_area)
    area_weighted_art = float(np.dot(areas, art_values) / total_area)
    qualified_area_pct: float | None = None
    qualified_count: int | None = None
    if any(item.qualified is not None for item in grid_metrics):
        qualified_mask = np.asarray([bool(item.qualified) for item in grid_metrics])
        qualified_area_pct = float(100.0 * np.sum(areas[qualified_mask]) / total_area)
        qualified_count = int(np.sum(qualified_mask))
    city = CityMetrics(
        horizon_s=float(horizon_s),
        total_area_m2=total_area,
        area_weighted_temporal_coverage_pct=float(100.0 * area_weighted_q),
        average_observable_duration_s=float(horizon_s * area_weighted_q),
        area_weighted_mrt_s=area_weighted_mrt,
        area_weighted_art_s=area_weighted_art,
        qualified_area_pct=qualified_area_pct,
        qualified_grid_count=qualified_count,
        area_coverage_time_pct=_area_coverage_time_fraction(
            intervals_by_grid, areas, lower, upper, area_target
        ),
        full_area_coverage_time_pct=_area_coverage_time_fraction(
            intervals_by_grid, areas, lower, upper, 100.0
        ),
    )
    return RegionalMetricsResult(
        selected_sat_indices=selected,
        min_simultaneous=min_simultaneous,
        temporal_coverage_target_pct=temporal_target,
        mrt_target_s=mrt_target,
        art_target_s=art_target,
        grid_metrics=tuple(grid_metrics),
        city=city,
    )


__all__ = [
    "TimeInterval",
    "GridMetric",
    "CityMetrics",
    "RegionalMetricsResult",
    "compute_regional_metrics",
]
