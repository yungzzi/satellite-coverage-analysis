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
from threading import RLock
from typing import Any

import regional_metrics
import regional_opportunities
import regional_selection
import satellite_loader
import siriagent_catalog
from region_grid import build_clipped_grid, load_city_boundary

from .formatter import (
    build_human_tables,
    build_quality_map,
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


# 同一进程中连续选择不同任务时，覆盖机会窗口完全相同，可以直接复用。
# 只保留少量最近结果，避免常驻服务长期积累大城市的窗口数据。
_OPPORTUNITY_CACHE_LIMIT = 2
_OPPORTUNITY_CACHE: dict[str, regional_opportunities.OpportunityStore] = {}
_OPPORTUNITY_CACHE_ORDER: list[str] = []
_OPPORTUNITY_CACHE_LOCK = RLock()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _opportunity_cache_key(
    request: SkillRequest,
    *,
    project_root: Path,
    tle_hash: str,
    grid: Any,
    config: dict[str, Any],
    satellites: list[Any],
) -> str:
    """为覆盖机会缓存生成稳定键；所有会影响几何结果的输入都必须纳入。"""

    satellite_ids = []
    for satellite in satellites:
        value = getattr(satellite, "norad_id", None)
        if value is None:
            value = getattr(getattr(satellite, "model", None), "satnum", None)
        satellite_ids.append(int(value))
    payload = {
        "version": 1,
        "project_root": str(project_root),
        "tle_hash": tle_hash,
        "boundary_hash": getattr(grid, "boundary_hash", None),
        "city_adcode": request.city_adcode,
        "start_utc": request.start_utc.isoformat(),
        "end_utc": request.end_utc.isoformat(),
        "grid_size_km": request.grid_size_km,
        "detection_step_s": request.detection_step_s,
        "decision_step_s": request.decision_step_s,
        "satellite_ids": satellite_ids,
        "config": config,
    }
    return _sha256_bytes(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8"))


def _cached_opportunities(key: str) -> regional_opportunities.OpportunityStore | None:
    with _OPPORTUNITY_CACHE_LOCK:
        store = _OPPORTUNITY_CACHE.get(key)
        if store is None:
            return None
        if key in _OPPORTUNITY_CACHE_ORDER:
            _OPPORTUNITY_CACHE_ORDER.remove(key)
        _OPPORTUNITY_CACHE_ORDER.append(key)
        return store


def _store_cached_opportunities(key: str, store: regional_opportunities.OpportunityStore) -> None:
    with _OPPORTUNITY_CACHE_LOCK:
        _OPPORTUNITY_CACHE[key] = store
        if key in _OPPORTUNITY_CACHE_ORDER:
            _OPPORTUNITY_CACHE_ORDER.remove(key)
        _OPPORTUNITY_CACHE_ORDER.append(key)
        while len(_OPPORTUNITY_CACHE_ORDER) > _OPPORTUNITY_CACHE_LIMIT:
            old_key = _OPPORTUNITY_CACHE_ORDER.pop(0)
            _OPPORTUNITY_CACHE.pop(old_key, None)


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
) -> tuple[list[Any], Any, Path, str, dict[str, Any] | None]:
    tle_value = request.tle_path or TLE_SOURCES[request.tle_source]
    tle_path = _resolve_project_file(root, tle_value)
    tle_bytes = tle_path.read_bytes()
    try:
        tle_text = tle_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SkillInputError("TLE 文件必须是 UTF-8 文本。") from exc
    timescale = satellite_loader.load.timescale(builtin=True)
    satellites = satellite_loader.parse_tle_text(tle_text, ts=timescale)
    catalog_context: dict[str, Any] | None = None
    use_catalog = (
        request.tle_source == "siriagent_110_snapshot"
        or request.profile_slug == siriagent_catalog.CATALOG_PROFILE_SLUG
    )
    if use_catalog:
        try:
            catalog = siriagent_catalog.load_catalog(root)
            entries = siriagent_catalog.select_entries(
                catalog,
                sensor_type=request.catalog_sensor_type,
                max_resolution_m=request.max_resolution_m,
                satellite_ids=request.satellite_ids,
            )
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise SkillInputError(f"SIRIAgent 冻结目录不可用：{exc}") from exc
        if request.satellite_ids is not None:
            catalog_ids = {int(item["norad_id"]) for item in entries}
            filtered_ids = sorted(set(request.satellite_ids) - catalog_ids)
            if filtered_ids:
                raise SkillInputError(
                    "部分 satellite_ids 不满足平台目录的传感器/分辨率筛选。",
                    details={"filtered_satellite_ids": filtered_ids},
                )
        if not entries:
            raise SkillInputError(
                "平台目录筛选后没有候选卫星。",
                details={
                    "catalog_sensor_type": request.catalog_sensor_type,
                    "max_resolution_m": request.max_resolution_m,
                },
            )
        profile_overrides: list[dict[str, Any]] = []
        blocked: list[dict[str, Any]] = []
        if (
            request.profile_slug == siriagent_catalog.CATALOG_PROFILE_SLUG
            and request.sensor_profile is None
        ):
            profile_overrides, blocked = siriagent_catalog.build_catalog_overrides(
                entries,
                user_overrides=request.satellite_profile_overrides,
            )
            ready_ids = {int(item["norad"]) for item in profile_overrides}
            entries = [item for item in entries if int(item["norad_id"]) in ready_ids]
            if not entries:
                raise SkillInputError(
                    "候选卫星都缺少可运行的几何 Profile。SAR 必须补充原生入射角带。",
                    details={"blocked_satellites": blocked},
                )
        allowed_ids = {int(item["norad_id"]) for item in entries}
        satellites = [sat for sat in satellites if int(sat.norad_id) in allowed_ids]
        found_ids = {int(sat.norad_id) for sat in satellites}
        missing_tle_ids = sorted(allowed_ids - found_ids)
        if missing_tle_ids:
            raise SkillInputError(
                "当前 TLE 文件缺少平台目录筛选后的候选卫星，不能静默缩小候选池。",
                details={"missing_tle_satellite_ids": missing_tle_ids},
            )
        entry_by_id = {int(item["norad_id"]): item for item in entries}
        for sat in satellites:
            entry = entry_by_id[int(sat.norad_id)]
            # Satellite 包装类原本就预留 sensor 铭牌；这里只附目录展示字段，
            # 几何仍由下方版本化 Profile override 决定。
            sat.sensor = {
                "sensor_class": entry["sensor_class"],
                "resolution_m": float(entry["resolution_m"]),
                "swath_km": float(entry["swath_km"]),
                "profile_engine_ready": bool((entry.get("profile") or {}).get("engine_ready")),
            }
        catalog_context = {
            "path": catalog["_catalog_path"],
            "sha256": catalog["_catalog_sha256"],
            "snapshot_label": catalog.get("snapshot_label"),
            "profile_policy_version": catalog.get("profile_policy_version"),
            "sensor_type_filter": request.catalog_sensor_type,
            "max_resolution_m": request.max_resolution_m,
            "selected_catalog_count": len(satellites),
            "blocked_satellites": blocked,
            "profile_overrides": profile_overrides,
        }
    elif request.satellite_ids is not None:
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
    return satellites, timescale, tle_path, _sha256_bytes(tle_bytes), catalog_context


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
    catalog_context: dict[str, Any] | None,
) -> dict[str, Any]:
    summary = {
        "operation": request.operation,
        "city_adcode": request.city_adcode,
        "start_utc": request.start_utc.isoformat().replace("+00:00", "Z"),
        "end_utc": request.end_utc.isoformat().replace("+00:00", "Z"),
        "analysis_horizon_h": request.analysis_horizon_h,
        "tle_source": request.tle_source,
        "tle_path": str(tle_path),
        "candidate_satellite_count": satellite_count,
        "catalog_sensor_type": request.catalog_sensor_type,
        "max_resolution_m": request.max_resolution_m,
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
    if catalog_context:
        summary["catalog_mapping"] = {
            key: deepcopy(catalog_context[key])
            for key in (
                "snapshot_label",
                "profile_policy_version",
                "sensor_type_filter",
                "max_resolution_m",
                "selected_catalog_count",
                "blocked_satellites",
            )
        }
    return summary


def execute_request(
    request: SkillRequest,
    *,
    root: str | Path | None = None,
) -> dict[str, Any]:
    """完成一次同机 Skill 请求；选星模式内部只传播一次轨道。"""

    project_root = Path(root).resolve() if root else PROJECT_ROOT
    total_start = time.perf_counter()
    satellites, timescale, tle_path, tle_hash, catalog_context = _load_satellites(
        request, project_root
    )
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
    if (
        catalog_context
        and request.profile_slug == siriagent_catalog.CATALOG_PROFILE_SLUG
        and request.sensor_profile is None
    ):
        config["sat_sensor_overrides"] = deepcopy(catalog_context["profile_overrides"])
    elif request.satellite_profile_overrides:
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
    cache_key = _opportunity_cache_key(
        request,
        project_root=project_root,
        tle_hash=tle_hash,
        grid=grid,
        config=config,
        satellites=satellites,
    )
    opportunity_start = time.perf_counter()
    store = _cached_opportunities(cache_key)
    opportunity_cache_hit = store is not None
    if store is None:
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
        _store_cached_opportunities(cache_key, store)
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
    assumptions = [
        (
            "用户提供了高级 sensor_profile/逐星覆盖配置；本次计算以回显的 effective_config 为准，"
            "未提供的字段继续继承 profile_slug 预设。"
            if advanced_mode
            else profile["assumption"]
        ),
    ]
    if store.metadata.get("targeting"):
        assumptions.append(
            "Targeted 区域指向自动锁定所有保留网格代表点的面积加权形心；"
            "它表示整座区域共用一个跟踪目标，不表示每个网格点都能同时独立转轴。"
        )
    assumptions.extend(
        [
            "结果表示固定 Profile 下的几何观测机会，不包含云量、任务排程和姿态行为优化。",
            "代表点不带圆；面积由裁剪网格单元的真实面积权重统计。",
        ]
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
            catalog_context=catalog_context,
        ),
        "profile": {
            "slug": request.profile_slug,
            "mode": "advanced" if advanced_mode else "preset",
            "effective_config": deepcopy(config),
            "satellite_profile_overrides": [
                deepcopy(item) for item in request.satellite_profile_overrides
            ],
            "targeting": deepcopy(store.metadata.get("targeting")),
            "catalog_mapping": {
                "catalog_sha256": catalog_context["sha256"],
                "profile_policy_version": catalog_context["profile_policy_version"],
                "generated_override_count": len(catalog_context["profile_overrides"]),
                "blocked_satellites": deepcopy(catalog_context["blocked_satellites"]),
            }
            if catalog_context
            else None,
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
            "kind": "grid_quality_map",
            "description": (
                "按 5 km 裁剪网格显示所选卫星组合的区域覆盖质量；"
                "时间覆盖率是分析时段内累计被覆盖的时间比例。"
            ),
            "available_metrics": ["temporal_coverage_pct", "mrt_s", "art_s", "qualified"],
            "default_metric": "temporal_coverage_pct",
            # 地图是人类可读的正式结果，不再依赖 debug 开关；debug 只控制更大的逐格审计表。
            "geojson_included": True,
            "geojson": build_quality_map(store, metrics),
        },
        "assumptions": assumptions,
        "warnings": (
            [
                "平台 110 星目录与 TLE 已冻结；分辨率只作候选筛选，平台 orbit_period_min 不参与计算。",
                "非 SAR 跨轨宽度来自平台 swath_km，沿轨 60°仍是场景假设；尚不能称为厂家验证的真实载荷 Profile。",
            ]
            + (
                [
                    f"有 {len(catalog_context['blocked_satellites'])} 颗 SAR 因缺原生入射角带已从候选池排除。"
                ]
                if catalog_context["blocked_satellites"]
                else []
            )
            if catalog_context
            else [
                "当前内置 TLE 和 Profile 用于 Skill 接入验收；正式平台逐星载荷参数仍待补齐。"
            ]
        ),
        "timings_s": {
            "grid": round(grid_seconds, 4),
            "opportunities": round(opportunity_seconds, 4),
            "opportunity_cache_hit": opportunity_cache_hit,
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
            "catalog_sha256": catalog_context["sha256"] if catalog_context else None,
            "profile_policy_version": (
                catalog_context["profile_policy_version"] if catalog_context else None
            ),
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
