# -*- coding: utf-8 -*-
"""同机 Skill 的应用调用层：一次覆盖计算，可选执行五项选星之一。"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import regional_metrics
import regional_opportunities
import regional_selection
import satellite_loader
from region_grid import build_clipped_grid, load_city_boundary

from .formatter import (
    build_debug_map,
    build_human_tables,
    build_summary_cards,
    build_weak_areas,
)
from .schemas import (
    PROFILE_SPECS,
    PROJECT_ROOT,
    TLE_SOURCES,
    SkillInputError,
    SkillRequest,
)


@dataclass(frozen=True)
class TaskOutcome:
    """任务分派后的统一内部结果。"""

    response: dict[str, Any]
    metrics: regional_metrics.RegionalMetricsResult
    selected_sat_indices: tuple[int, ...]
    feasible: bool
    optimality: str
    used_area_target_pct: float | None


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _resolve_project_file(root: Path, value: str) -> Path:
    """只允许 Skill 读取项目目录内文件，避免聊天参数变成任意文件读取入口。"""

    candidate = Path(value)
    path = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise SkillInputError("tle_path 必须位于当前卫星覆盖项目目录内。") from exc
    if not path.is_file():
        raise SkillInputError(f"TLE 文件不存在：{path}")
    return path


def _load_satellites(
    request: SkillRequest,
    root: Path,
) -> tuple[list[Any], Any, Path, str]:
    tle_value = request.tle_path or TLE_SOURCES[request.tle_source]
    tle_path = _resolve_project_file(root, tle_value)
    tle_bytes = tle_path.read_bytes()
    try:
        tle_text = tle_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillInputError("TLE 文件必须是 UTF-8 文本。") from exc
    timescale = satellite_loader.load.timescale(builtin=True)
    satellites = satellite_loader.parse_tle_text(tle_text, ts=timescale)
    if request.satellite_ids is not None:
        wanted = set(request.satellite_ids)
        satellites = [sat for sat in satellites if int(sat.norad_id) in wanted]
        found = {int(sat.norad_id) for sat in satellites}
        missing = sorted(wanted - found)
        if missing:
            raise SkillInputError(
                "部分 satellite_ids 不在所选 TLE 中。",
                details={"missing_satellite_ids": missing},
            )
    if not satellites:
        raise SkillInputError("候选 TLE 中没有可用卫星。")
    if request.satellite_profile_overrides:
        available_ids = {int(sat.norad_id) for sat in satellites}
        override_ids = {int(item["norad_id"]) for item in request.satellite_profile_overrides}
        missing_overrides = sorted(override_ids - available_ids)
        if missing_overrides:
            raise SkillInputError(
                "部分 satellite_profile_overrides 的 NORAD 不在候选 TLE 中。",
                details={"missing_override_satellite_ids": missing_overrides},
            )
    if request.n_budget is not None and request.n_budget > len(satellites):
        raise SkillInputError(
            f"n_budget={request.n_budget} 超过候选卫星数 {len(satellites)}。"
        )
    return satellites, timescale, tle_path, _sha256_bytes(tle_bytes)


def _leaf(result: Any) -> Any:
    if hasattr(result, "recommended"):
        return result.recommended
    if hasattr(result, "selection"):
        return result.selection
    return result


def _area_constrained_outcome(
    request: SkillRequest,
    store: regional_opportunities.OpportunityStore,
    run_once: Any,
) -> TaskOutcome:
    """任务①②先跑严格面积，确实不可行时才显式跑降级面积。"""

    strict = run_once(request.area_target_pct)
    strict_leaf = _leaf(strict)
    fallback = None
    chosen = strict
    used_area_target_pct = request.area_target_pct
    if (
        not bool(strict_leaf.feasible)
        and request.allow_area_fallback
        and request.fallback_area_target_pct < request.area_target_pct
    ):
        fallback = run_once(request.fallback_area_target_pct)
        chosen = fallback
        used_area_target_pct = request.fallback_area_target_pct
    chosen_leaf = _leaf(chosen)
    return TaskOutcome(
        response={
            "objective": request.operation,
            "strict_area_target_pct": request.area_target_pct,
            "fallback_area_target_pct": request.fallback_area_target_pct,
            "strict_result": strict.to_dict(),
            "fallback_result": fallback.to_dict() if fallback is not None else None,
            "fallback_attempted": fallback is not None,
            "used_area_target_pct": used_area_target_pct,
            "recommended_result": chosen_leaf.to_dict(),
        },
        metrics=chosen_leaf.metrics,
        selected_sat_indices=tuple(chosen_leaf.selected_sat_indices),
        feasible=bool(chosen_leaf.feasible),
        optimality=str(chosen_leaf.status),
        used_area_target_pct=used_area_target_pct,
    )


def run_selection_task(
    request: SkillRequest,
    store: regional_opportunities.OpportunityStore,
) -> TaskOutcome:
    """把五个公开任务映射到已经验证的区域选星入口。"""

    if request.operation == "task_1_min_satellites_temporal":
        assert request.temporal_coverage_target_pct is not None

        def run_once(area_target_pct: float) -> Any:
            return regional_selection.run_regional_temporal_selection(
                store,
                temporal_coverage_target_pct=request.temporal_coverage_target_pct,
                area_target_pct=area_target_pct,
                min_simultaneous=request.min_simultaneous,
                method=request.method,
                time_limit_s=request.selection_time_limit_s,
            )

        return _area_constrained_outcome(request, store, run_once)

    if request.operation == "task_2_min_satellites_mrt":
        assert request.mrt_target_minutes is not None

        def run_once(area_target_pct: float) -> Any:
            return regional_selection.run_regional_mrt_selection(
                store,
                mrt_target_s=request.mrt_target_minutes * 60.0,
                area_target_pct=area_target_pct,
                min_simultaneous=request.min_simultaneous,
                method=request.method,
                time_limit_s=request.selection_time_limit_s,
            )

        return _area_constrained_outcome(request, store, run_once)

    if request.operation == "task_3_max_qualified_area":
        assert request.n_budget is not None
        assert request.temporal_coverage_target_pct is not None
        result = regional_selection.run_regional_budget_selection(
            store,
            n_budget=request.n_budget,
            temporal_coverage_target_pct=request.temporal_coverage_target_pct,
            min_simultaneous=request.min_simultaneous,
            method=request.method,
            exact_budget=request.exact_budget,
            time_limit_s=request.selection_time_limit_s,
        )
        leaf = result.recommended
        return TaskOutcome(
            response={
                "objective": request.operation,
                "result": result.to_dict(),
                "recommended_result": leaf.to_dict(),
            },
            metrics=leaf.metrics,
            selected_sat_indices=tuple(leaf.selected_sat_indices),
            feasible=bool(leaf.feasible),
            optimality=str(leaf.status),
            used_area_target_pct=None,
        )

    if request.operation == "task_4_min_area_quantile_mrt":
        assert request.n_budget is not None
        result = regional_selection.select_min_area_quantile_mrt(
            store,
            area_target_pct=request.area_target_pct,
            n_budget=request.n_budget,
            method=request.method,
            min_simultaneous=request.min_simultaneous,
            time_limit_s=request.selection_time_limit_s,
        )
        leaf = result.selection
        return TaskOutcome(
            response={
                "objective": request.operation,
                "result": result.to_dict(),
                "recommended_result": leaf.to_dict(),
            },
            metrics=leaf.metrics,
            selected_sat_indices=tuple(leaf.selected_sat_indices),
            feasible=bool(result.feasible),
            optimality=str(result.status),
            used_area_target_pct=request.area_target_pct,
        )

    if request.operation == "task_5_min_area_weighted_art":
        assert request.n_budget is not None
        result = regional_selection.run_regional_art_selection(
            store,
            n_budget=request.n_budget,
            exact_budget=request.exact_budget,
            mrt_area_quantile_pct=request.mrt_area_quantile_pct,
            min_simultaneous=request.min_simultaneous,
            max_local_rounds=request.max_local_rounds,
        )
        return TaskOutcome(
            response={
                "objective": request.operation,
                "result": result.to_dict(),
                "recommended_result": result.to_dict(),
            },
            metrics=result.metrics,
            selected_sat_indices=tuple(result.selected_sat_indices),
            feasible=bool(result.feasible),
            optimality=str(result.status),
            used_area_target_pct=None,
        )
    raise SkillInputError(f"当前 operation 不是选星任务：{request.operation}")


def _request_summary(
    request: SkillRequest,
    *,
    tle_path: Path,
    satellite_count: int,
) -> dict[str, Any]:
    return {
        "operation": request.operation,
        "city_adcode": request.city_adcode,
        "start_utc": request.start_utc.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end_utc.isoformat().replace("+00:00", "Z"),
        "analysis_horizon_h": request.analysis_horizon_h,
        "tle_source": request.tle_source,
        "tle_path": str(tle_path),
        "candidate_satellite_count": satellite_count,
        "profile_slug": request.profile_slug,
        "advanced_mode": bool(
            request.sensor_profile or request.satellite_profile_overrides
        ),
        "satellite_profile_override_count": len(request.satellite_profile_overrides),
        "grid_size_km": request.grid_size_km,
        "detection_step_s": request.detection_step_s,
        "decision_step_s": request.decision_step_s,
        "min_simultaneous": request.min_simultaneous,
        "method": request.method,
    }


def execute_request(
    request: SkillRequest,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """完成一次同机 Skill 请求；选星模式内部只传播一次轨道。"""

    project_root = Path(root).resolve() if root else PROJECT_ROOT
    total_start = time.perf_counter()
    satellites, timescale, tle_path, tle_hash = _load_satellites(request, project_root)
    boundary_path = project_root / "data" / "geo" / f"{request.city_adcode}.json"
    if not boundary_path.is_file():
        raise SkillInputError(
            f"项目中没有城市 {request.city_adcode} 的冻结边界。",
            details={"boundary_path": str(boundary_path)},
        )

    grid_start = time.perf_counter()
    boundary = load_city_boundary(boundary_path, adcode=request.city_adcode)
    grid = build_clipped_grid(boundary, grid_size_m=request.grid_size_km * 1_000.0)
    grid_seconds = time.perf_counter() - grid_start

    profile = PROFILE_SPECS[request.profile_slug]
    config = deepcopy(profile["config"])
    advanced_mode = bool(request.sensor_profile or request.satellite_profile_overrides)
    if request.sensor_profile:
        # 高级字段只覆盖用户明确给出的项；没有给出的项仍沿用预设。
        config.update(deepcopy(request.sensor_profile))
    if request.satellite_profile_overrides:
        # 对外使用 norad_id；区域几何内部沿用历史实现使用的 norad 键。
        config["sat_sensor_overrides"] = [
            {"norad": int(item["norad_id"]), **{
                key: deepcopy(value)
                for key, value in item.items()
                if key != "norad_id"
            }}
            for item in request.satellite_profile_overrides
        ]
    config["coarse_step_seconds"] = request.detection_step_s
    opportunity_start = time.perf_counter()
    store = regional_opportunities.compute_regional_opportunities(
        satellites,
        grid,
        request.start_utc,
        request.end_utc,
        config,
        decision_step_s=request.decision_step_s,
        cell_chunk_size=256,
        timescale=timescale,
    )
    opportunity_seconds = time.perf_counter() - opportunity_start

    selection_seconds = 0.0
    if request.operation == "coverage_only":
        metrics_start = time.perf_counter()
        selected_indices = tuple(range(store.n_satellites))
        metrics = regional_metrics.compute_regional_metrics(
            store,
            selected_indices,
            min_simultaneous=request.min_simultaneous,
        )
        metrics_seconds = time.perf_counter() - metrics_start
        selection_payload = None
        feasible = True
        optimality = "not_applicable"
        used_area_target_pct = None
        metrics_scope = "all_candidates"
    else:
        selection_start = time.perf_counter()
        outcome = run_selection_task(request, store)
        selection_seconds = time.perf_counter() - selection_start
        metrics_seconds = 0.0  # 选星入口已经用同一份窗口完成复核。
        selected_indices = outcome.selected_sat_indices
        metrics = outcome.metrics
        selection_payload = outcome.response
        feasible = outcome.feasible
        optimality = outcome.optimality
        used_area_target_pct = outcome.used_area_target_pct
        metrics_scope = "selected_satellites"

    satellite_table, pass_table = build_human_tables(
        store,
        selected_indices,
        max_windows=request.max_windows,
        include_empty_satellites=request.operation != "coverage_only",
    )
    response: dict[str, Any] = {
        "status": "completed",
        "request_id": str(uuid.uuid4()),
        "operation": request.operation,
        "result_scope": "geometric_observation_opportunities",
        "metrics_satellite_scope": metrics_scope,
        "feasible": feasible,
        "optimality": optimality,
        "used_area_target_pct": used_area_target_pct,
        "input_summary": _request_summary(
            request,
            tle_path=tle_path,
            satellite_count=len(satellites),
        ),
        "profile": {
            "slug": request.profile_slug,
            "mode": "advanced" if advanced_mode else "preset",
            "effective_config": deepcopy(config),
            "satellite_profile_overrides": [
                deepcopy(item) for item in request.satellite_profile_overrides
            ],
        },
        "city": {
            "adcode": grid.adcode,
            "name": grid.city_name,
            "grid_cell_count": len(grid.cells),
            "area_km2": round(grid.total_area_km2, 4),
            "boundary_hash": grid.boundary_hash,
        },
        "opportunity_summary": {
            "nonempty_satellite_grid_pairs": store.n_nonempty_pairs,
            "raw_window_count": store.n_windows,
        },
        "summary_cards": build_summary_cards(metrics, len(selected_indices)),
        "selection": selection_payload,
        "satellite_table": satellite_table,
        "city_pass_window_table": pass_table,
        "weak_areas": build_weak_areas(store, metrics, limit=request.max_weak_areas),
        "coverage_map": {
            "available_metrics": ["temporal_coverage_pct", "mrt_s", "art_s", "qualified"],
            "geojson_included": bool(request.debug),
            "geojson": build_debug_map(store, metrics) if request.debug else None,
        },
        "assumptions": [
            (
                "用户提供了高级 sensor_profile/逐星覆盖配置；本次计算以回显的 effective_config 为准，"
                "未提供的字段继续继承 profile_slug 预设。"
                if advanced_mode
                else profile["assumption"]
            ),
            "结果表示固定 Profile 下的几何观测机会，不包含云量、任务排程和姿态行为优化。",
            "代表点不带圆；面积由裁剪网格单元的真实面积权重统计。",
        ],
        "warnings": [
            "当前内置 TLE 和 Profile 用于 Skill 接入验收；正式平台目录映射尚待研发注册样例和逐星参数。"
        ],
        "timings_s": {
            "grid": round(grid_seconds, 4),
            "opportunities": round(opportunity_seconds, 4),
            "metrics": round(metrics_seconds, 4),
            "selection": round(selection_seconds, 4),
            "total": round(time.perf_counter() - total_start, 4),
        },
        "reproducibility": {
            "tle_sha256": tle_hash,
            "boundary_sha256": grid.boundary_hash,
            "profile_sha256": _sha256_bytes(
                json.dumps(config, sort_keys=True).encode("utf-8")
            ),
            "profile_slug": request.profile_slug,
        },
        "debug_artifacts": {
            "grid_metrics": metrics.to_grid_rows(),
            "opportunity_windows": store.to_rows(),
        }
        if request.debug
        else None,
    }
    return response


__all__ = ["TaskOutcome", "execute_request", "run_selection_task"]
