# -*- coding: utf-8 -*-
"""把内部窗口/网格指标整理成适合 Agent 和平台展示的结果。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Sequence

from regional_metrics import RegionalMetricsResult
from regional_opportunities import AccessWindow, OpportunityStore


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _merge_intervals(
    intervals: Iterable[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """合并相交或首尾相接的半开区间。"""

    ordered = sorted((start, end) for start, end in intervals if end > start)
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _sat_id(sat: Any) -> int:
    value = getattr(sat, "norad_id", None)
    if value is None:
        value = getattr(getattr(sat, "model", None), "satnum", None)
    return int(value)


def _sat_name(sat: Any) -> str:
    return str(getattr(sat, "name", None) or getattr(getattr(sat, "orbit", None), "name", ""))


def _area_stats_in_pass(
    store: OpportunityStore,
    windows: Sequence[AccessWindow],
    pass_start: datetime,
    pass_end: datetime,
) -> tuple[float, float]:
    """计算一次城市过境中的时间平均/最大覆盖面积比例。"""

    duration_s = (pass_end - pass_start).total_seconds()
    total_area = float(store.grid.total_area_m2)
    if duration_s <= 0.0 or total_area <= 0.0:
        return 0.0, 0.0
    events: dict[datetime, float] = {}
    for window in windows:
        start = max(pass_start, window.start_utc)
        end = min(pass_end, window.end_utc)
        if end <= start:
            continue
        area = float(store.grid.cells[window.grid_index].area_m2)
        events[start] = events.get(start, 0.0) + area
        events[end] = events.get(end, 0.0) - area
    active_area = 0.0
    previous = pass_start
    area_seconds = 0.0
    max_area = 0.0
    for timestamp, delta in sorted(events.items()):
        if timestamp > previous:
            area_seconds += active_area * (timestamp - previous).total_seconds()
        active_area += delta
        max_area = max(max_area, active_area)
        previous = timestamp
    average_pct = 100.0 * area_seconds / (duration_s * total_area)
    maximum_pct = 100.0 * max_area / total_area
    return max(0.0, min(100.0, average_pct)), max(0.0, min(100.0, maximum_pct))


def build_human_tables(
    store: OpportunityStore,
    selected_sat_indices: Sequence[int],
    *,
    max_windows: int = 10,
    include_empty_satellites: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成卫星表和城市过境窗口表；不把逐网格窗口直接暴露给用户。"""

    satellite_rows: list[dict[str, Any]] = []
    pass_rows: list[dict[str, Any]] = []
    selected_set = set(selected_sat_indices)
    windows_by_satellite: dict[int, list[AccessWindow]] = {
        sat_index: [] for sat_index in selected_sat_indices
    }
    # 只扫描一次稀疏窗口，避免每颗卫星都重新遍历整份存储。
    for window in store.iter_windows():
        if window.sat_index in selected_set:
            windows_by_satellite[window.sat_index].append(window)
    for sat_index in selected_sat_indices:
        sat = store.satellites[sat_index]
        windows = windows_by_satellite[sat_index]
        city_passes = _merge_intervals((item.start_utc, item.end_utc) for item in windows)
        if not city_passes and not include_empty_satellites:
            continue
        cumulative_s = sum((end - start).total_seconds() for start, end in city_passes)
        sensor = getattr(sat, "sensor", None) or {}
        satellite_rows.append(
            {
                "satellite": _sat_name(sat),
                "norad_id": _sat_id(sat),
                "sensor_class": sensor.get("sensor_class"),
                "resolution_m": sensor.get("resolution_m"),
                "swath_km": sensor.get("swath_km"),
                "city_pass_count": len(city_passes),
                "cumulative_city_intersection_minutes": round(cumulative_s / 60.0, 3),
                "first_window_utc": _iso(city_passes[0][0]) if city_passes else None,
                "raw_grid_window_count": len(windows),
            }
        )
        for start, end in city_passes:
            average_pct, maximum_pct = _area_stats_in_pass(store, windows, start, end)
            pass_rows.append(
                {
                    "satellite": _sat_name(sat),
                    "norad_id": _sat_id(sat),
                    "start_utc": _iso(start),
                    "end_utc": _iso(end),
                    "duration_minutes": round((end - start).total_seconds() / 60.0, 3),
                    "average_covered_area_pct": round(average_pct, 4),
                    "maximum_covered_area_pct": round(maximum_pct, 4),
                }
            )
    satellite_rows.sort(key=lambda row: (-row["cumulative_city_intersection_minutes"], row["norad_id"]))
    pass_rows.sort(key=lambda row: (row["start_utc"], row["norad_id"]))
    return satellite_rows, pass_rows[:max_windows]


def build_summary_cards(metrics: RegionalMetricsResult, satellite_count: int) -> dict[str, Any]:
    city = metrics.city
    return {
        "satellite_count": int(satellite_count),
        "city_mean_temporal_coverage_pct": round(city.area_weighted_temporal_coverage_pct, 4),
        "city_mean_observable_duration_minutes": round(city.average_observable_duration_s / 60.0, 3),
        "qualified_area_pct": (
            None if city.qualified_area_pct is None else round(city.qualified_area_pct, 4)
        ),
        "area_weighted_mrt_minutes": round(city.area_weighted_mrt_s / 60.0, 3),
        "area_weighted_art_minutes": round(city.area_weighted_art_s / 60.0, 3),
        "target_area_covered_time_pct": round(city.area_coverage_time_pct, 4),
        "full_city_covered_time_pct": round(city.full_area_coverage_time_pct, 4),
    }


def build_weak_areas(
    store: OpportunityStore,
    metrics: RegionalMetricsResult,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """返回最薄弱单元摘要；坐标便于后续前端定位，不输出整张逐格表。"""

    ordered = sorted(
        metrics.grid_metrics,
        key=lambda item: (
            item.temporal_coverage_pct,
            -item.mrt_s,
            -item.art_s,
            -item.area_m2,
            item.grid_index,
        ),
    )
    rows: list[dict[str, Any]] = []
    for item in ordered[:limit]:
        cell = store.grid.cells[item.grid_index]
        rows.append(
            {
                "grid_id": item.grid_id,
                "representative_lat": round(cell.representative_lat, 6),
                "representative_lon": round(cell.representative_lon, 6),
                "area_km2": round(item.area_m2 / 1_000_000.0, 4),
                "temporal_coverage_pct": round(item.temporal_coverage_pct, 4),
                "mrt_minutes": round(item.mrt_s / 60.0, 3),
                "art_minutes": round(item.art_s / 60.0, 3),
                "qualified": item.qualified,
            }
        )
    return rows


def build_debug_map(store: OpportunityStore, metrics: RegionalMetricsResult) -> dict[str, Any]:
    """把网格几何和指标拼成 GeoJSON，仅在 ``debug=true`` 时返回。"""

    feature_collection = store.grid.to_feature_collection()
    metric_by_id = {item.grid_id: item for item in metrics.grid_metrics}
    for feature in feature_collection["features"]:
        grid_id = feature["properties"]["grid_id"]
        metric = metric_by_id[grid_id]
        feature["properties"].update(
            {
                "temporal_coverage_pct": metric.temporal_coverage_pct,
                "mrt_s": metric.mrt_s,
                "art_s": metric.art_s,
                "qualified": metric.qualified,
            }
        )
    return feature_collection


__all__ = [
    "build_debug_map",
    "build_human_tables",
    "build_summary_cards",
    "build_weak_areas",
]
