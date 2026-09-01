# -*- coding: utf-8 -*-
"""平台默认 10 m 候选星在真实城市网格上的业务验收。

与逐个调用六次 Skill 不同，本脚本只生成一次“卫星 × 网格”机会窗口，然后复用这份事实数据
验收覆盖指标和五项选星。这样测到的是选星算法本身的耗时，也符合覆盖层与选星层分离的设计。

默认验收口径：

* 绍兴 5 km 裁剪网格；
* SIRIAgent 目录中可见光且分辨率不差于 10 m 的 100 颗候选；
* 2026-08-30 00:00 UTC 起算 8 小时；10 秒检测、60 秒决策；
* K=1 完整运行任务①～⑤；K=2/K=3 运行全池指标、任务②和任务⑤代表性测试。

任务阈值只用于验收，绝不自动写成产品默认值。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import regional_metrics  # noqa: E402
import regional_opportunities  # noqa: E402
from region_grid import build_clipped_grid, load_city_boundary  # noqa: E402
from siriagent_skill.facade import _load_satellites, run_selection_task  # noqa: E402
from siriagent_skill.schemas import PROFILE_SPECS, SkillRequest, normalize_request  # noqa: E402


DEFAULT_OUTPUT = ROOT / "output" / "siriagent_business_acceptance_shaoxing.json"


def _rss_mb() -> float | None:
    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1024**2, 1)
    except Exception:
        return None


def _weighted_quantile(
    values: Iterable[float], weights: Iterable[float], percentile: float
) -> float:
    """计算面积加权分位数；percentile 使用 0～100。"""

    value_array = np.asarray(tuple(values), dtype=float)
    weight_array = np.asarray(tuple(weights), dtype=float)
    if value_array.size == 0 or value_array.size != weight_array.size:
        raise ValueError("加权分位数的数值和权重必须非空且等长。")
    if np.any(~np.isfinite(value_array)) or np.any(~np.isfinite(weight_array)):
        raise ValueError("加权分位数只接受有限数字。")
    if np.any(weight_array < 0.0) or float(np.sum(weight_array)) <= 0.0:
        raise ValueError("加权分位数权重必须非负且总和大于 0。")
    percentile = max(0.0, min(100.0, float(percentile)))
    order = np.argsort(value_array, kind="stable")
    ordered_values = value_array[order]
    ordered_weights = weight_array[order]
    target = float(np.sum(ordered_weights)) * percentile / 100.0
    index = int(np.searchsorted(np.cumsum(ordered_weights), target, side="left"))
    return float(ordered_values[min(index, len(ordered_values) - 1)])


def _metrics_summary(metrics: regional_metrics.RegionalMetricsResult) -> dict[str, Any]:
    rows = metrics.grid_metrics
    areas = [item.area_m2 for item in rows]

    def area_quantiles(values: Iterable[float]) -> dict[str, float]:
        values = tuple(values)
        return {
            str(percentile): round(_weighted_quantile(values, areas, percentile), 4)
            for percentile in (0, 10, 50, 90, 100)
        }

    return {
        **metrics.to_city_dict(),
        "temporal_coverage_pct_area_quantiles": area_quantiles(
            item.temporal_coverage_pct for item in rows
        ),
        "mrt_minutes_area_quantiles": area_quantiles(item.mrt_s / 60.0 for item in rows),
        "art_minutes_area_quantiles": area_quantiles(item.art_s / 60.0 for item in rows),
    }


def _satellite_identity(satellite: Any) -> tuple[int, str]:
    sat_id = getattr(satellite, "norad_id", None)
    if sat_id is None:
        sat_id = getattr(getattr(satellite, "model", None), "satnum", None)
    name = str(getattr(satellite, "name", "") or "")
    return int(sat_id), name


def _base_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "city_adcode": args.city,
        "start_utc": args.start_utc,
        "analysis_horizon_h": args.hours,
        "grid_size_km": args.grid_size_km,
        "detection_step_s": args.detection_step_s,
        "decision_step_s": args.decision_step_s,
        "profile_slug": "siriagent_catalog_swath_v1",
        "catalog_sensor_type": "可见光",
        "max_resolution_m": 10.0,
        "selection_time_limit_s": args.ilp_time_limit_s,
        "debug": False,
    }


def _task_cases(args: argparse.Namespace) -> list[tuple[str, dict[str, Any]]]:
    """固定验收矩阵；这些数字不是产品默认值。"""

    common = _base_payload(args)
    return [
        (
            "k1_task1_temporal",
            {
                **common,
                "operation": "task_1_min_satellites_temporal",
                "min_simultaneous": 1,
                "temporal_coverage_target_pct": 5.0,
                "area_target_pct": 100.0,
                "fallback_area_target_pct": 90.0,
                "allow_area_fallback": True,
                "method": "both",
            },
        ),
        (
            "k1_task2_mrt",
            {
                **common,
                "operation": "task_2_min_satellites_mrt",
                "min_simultaneous": 1,
                "mrt_target_minutes": 240.0,
                "area_target_pct": 100.0,
                "fallback_area_target_pct": 90.0,
                "allow_area_fallback": True,
                "method": "both",
            },
        ),
        (
            "k1_task3_budget_area",
            {
                **common,
                "operation": "task_3_max_qualified_area",
                "min_simultaneous": 1,
                "n_budget": 10,
                "temporal_coverage_target_pct": 5.0,
                "method": "both",
            },
        ),
        (
            "k1_task4_budget_mrt",
            {
                **common,
                "operation": "task_4_min_area_quantile_mrt",
                "min_simultaneous": 1,
                "n_budget": 10,
                "area_target_pct": 90.0,
                "method": "ilp",
            },
        ),
        (
            "k1_task5_budget_art",
            {
                **common,
                "operation": "task_5_min_area_weighted_art",
                "min_simultaneous": 1,
                "n_budget": 10,
                "mrt_area_quantile_pct": 90.0,
                "method": "greedy",
            },
        ),
        *[
            (
                f"k{k}_task2_mrt_representative",
                {
                    **common,
                    "operation": "task_2_min_satellites_mrt",
                    "min_simultaneous": k,
                    "mrt_target_minutes": 360.0,
                    "area_target_pct": 90.0,
                    "allow_area_fallback": False,
                    "method": "greedy",
                },
            )
            for k in (2, 3)
        ],
        *[
            (
                f"k{k}_task5_art_representative",
                {
                    **common,
                    "operation": "task_5_min_area_weighted_art",
                    "min_simultaneous": k,
                    "n_budget": 10,
                    "mrt_area_quantile_pct": 90.0,
                    "method": "greedy",
                },
            )
            for k in (2, 3)
        ],
    ]


def _build_store(
    request: SkillRequest,
) -> tuple[regional_opportunities.OpportunityStore, dict[str, Any], dict[str, Any]]:
    satellites, timescale, tle_path, tle_hash, catalog_context = _load_satellites(
        request, ROOT
    )
    if catalog_context is None:
        raise RuntimeError("业务验收必须使用冻结的平台目录。")
    boundary_path = ROOT / "data" / "geo" / f"{request.city_adcode}.json"
    boundary = load_city_boundary(boundary_path, adcode=request.city_adcode)
    grid = build_clipped_grid(boundary, grid_size_m=request.grid_size_km * 1_000.0)
    profile = PROFILE_SPECS[request.profile_slug]
    config = deepcopy(profile["config"])
    config["sat_sensor_overrides"] = deepcopy(catalog_context["profile_overrides"])
    config["coarse_step_seconds"] = request.detection_step_s

    started = time.perf_counter()
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
    timing = {
        "opportunity_seconds": round(time.perf_counter() - started, 4),
        "rss_mb_after_opportunities": _rss_mb(),
    }
    provenance = {
        "tle_path": str(tle_path),
        "tle_sha256": tle_hash,
        "catalog_path": catalog_context["path"],
        "catalog_sha256": catalog_context["sha256"],
        "catalog_snapshot_label": catalog_context["snapshot_label"],
        "profile_policy_version": catalog_context["profile_policy_version"],
        "generated_profile_override_count": len(catalog_context["profile_overrides"]),
        "blocked_satellites": catalog_context["blocked_satellites"],
        "boundary_sha256": grid.boundary_hash,
    }
    return store, timing, provenance


def _physical_ceiling_area_pct(
    store: regional_opportunities.OpportunityStore,
    request: SkillRequest,
) -> float | None:
    if request.operation in {
        "task_1_min_satellites_temporal",
        "task_3_max_qualified_area",
    }:
        metrics = regional_metrics.compute_regional_metrics(
            store,
            min_simultaneous=request.min_simultaneous,
            temporal_coverage_target_pct=request.temporal_coverage_target_pct,
        )
    elif request.operation == "task_2_min_satellites_mrt":
        metrics = regional_metrics.compute_regional_metrics(
            store,
            min_simultaneous=request.min_simultaneous,
            mrt_target_s=float(request.mrt_target_minutes or 0.0) * 60.0,
        )
    else:
        return None
    return float(metrics.city.qualified_area_pct or 0.0)


def _task_summary(
    case_id: str,
    request: SkillRequest,
    store: regional_opportunities.OpportunityStore,
    *,
    technical_failures: list[str],
    scientific_findings: list[str],
) -> dict[str, Any]:
    ceiling = _physical_ceiling_area_pct(store, request)
    started = time.perf_counter()
    outcome = run_selection_task(request, store)
    elapsed = time.perf_counter() - started
    selected = tuple(outcome.selected_sat_indices)
    identities = [_satellite_identity(store.satellites[index]) for index in selected]
    if len(selected) != len(set(selected)):
        technical_failures.append(f"{case_id}: 推荐组合含重复卫星索引。")
    if any(index < 0 or index >= store.n_satellites for index in selected):
        technical_failures.append(f"{case_id}: 推荐组合含候选池外索引。")
    if request.n_budget is not None and len(selected) > request.n_budget:
        technical_failures.append(
            f"{case_id}: 选中 {len(selected)} 颗，超过预算 {request.n_budget}。"
        )
    if (
        request.operation == "task_5_min_area_weighted_art"
        and request.n_budget is not None
        and request.n_budget >= request.min_simultaneous
        and len(selected) < request.min_simultaneous
    ):
        full_metrics = regional_metrics.compute_regional_metrics(
            store,
            min_simultaneous=request.min_simultaneous,
        )
        if full_metrics.city.area_weighted_temporal_coverage_pct > 0.0:
            technical_failures.append(
                f"{case_id}: 全池存在 K={request.min_simultaneous} 重覆盖，预算也足够，"
                "任务⑤却没有形成 K 颗种子。"
            )
    if outcome.feasible and outcome.used_area_target_pct is not None:
        achieved = float(outcome.metrics.city.qualified_area_pct or 0.0)
        if achieved + 1e-7 < outcome.used_area_target_pct:
            technical_failures.append(
                f"{case_id}: 声称可行，但窗口复核面积 {achieved:.6f}% "
                f"低于目标 {outcome.used_area_target_pct:.6f}%。"
            )

    fallback_attempted = bool(outcome.response.get("fallback_attempted", False))
    if request.operation in {
        "task_1_min_satellites_temporal",
        "task_2_min_satellites_mrt",
    }:
        strict = outcome.response["strict_result"]["recommended"]
        if ceiling is not None and ceiling + 1e-7 < request.area_target_pct:
            if strict["feasible"]:
                technical_failures.append(
                    f"{case_id}: 全池物理上限只有 {ceiling:.4f}%，严格结果却声称可行。"
                )
            if (
                request.allow_area_fallback
                and request.fallback_area_target_pct < request.area_target_pct
                and not fallback_attempted
            ):
                technical_failures.append(f"{case_id}: 严格不可行却没有显式尝试面积降级。")
        if (
            ceiling is not None
            and ceiling + 1e-7 >= request.fallback_area_target_pct
            and fallback_attempted
            and not outcome.feasible
        ):
            scientific_findings.append(
                f"{case_id}: 全池证明降级目标物理可达，但当前求解未找到可行组合；"
                "需要检查时间上限或启发式质量。"
            )

    if request.operation == "task_3_max_qualified_area":
        detail = outcome.response["result"]
        greedy = detail.get("greedy")
        ilp = detail.get("ilp")

        def exact_score(item: Mapping[str, Any]) -> tuple[float, float]:
            return (
                float(item["achieved_area_pct"]),
                float(item["area_weighted_temporal_coverage_pct"]),
            )

        if greedy and ilp:
            greedy_score = exact_score(greedy)
            ilp_score = exact_score(ilp)
            if greedy_score > ilp_score and ilp["status"] == "proven_optimal":
                technical_failures.append(
                    f"{case_id}: ILP 声称连续窗口全局最优，但贪心窗口复核分数更高。"
                )
            elif greedy_score > ilp_score and ilp["status"] == "proven_discrete_optimal":
                scientific_findings.append(
                    f"{case_id}: ILP 在离散时间片上已证明最优，但窗口级贪心方案更好；"
                    "推荐应按窗口复核选择贪心，并保留离散化差异说明。"
                )
            recommended = detail["recommended"]
            if exact_score(recommended) != max(greedy_score, ilp_score):
                technical_failures.append(
                    f"{case_id}: 推荐方案不是贪心/ILP 中窗口级字典序更优者。"
                )

    return {
        "case_id": case_id,
        "operation": request.operation,
        "min_simultaneous": request.min_simultaneous,
        "method": request.method,
        "acceptance_inputs": {
            "temporal_coverage_target_pct": request.temporal_coverage_target_pct,
            "mrt_target_minutes": request.mrt_target_minutes,
            "area_target_pct": request.area_target_pct,
            "fallback_area_target_pct": request.fallback_area_target_pct,
            "n_budget": request.n_budget,
            "mrt_area_quantile_pct": request.mrt_area_quantile_pct,
            "selection_time_limit_s": request.selection_time_limit_s,
        },
        "physical_full_pool_ceiling_area_pct": (
            round(ceiling, 6) if ceiling is not None else None
        ),
        "selection_seconds": round(elapsed, 4),
        "feasible": outcome.feasible,
        "optimality": outcome.optimality,
        "used_area_target_pct": outcome.used_area_target_pct,
        "fallback_attempted": fallback_attempted,
        "selected_satellite_count": len(selected),
        "selected_satellite_ids": [item[0] for item in identities],
        "selected_satellite_names": [item[1] for item in identities],
        "metrics": _metrics_summary(outcome.metrics),
        "selection_detail": outcome.response,
    }


def run_acceptance(args: argparse.Namespace) -> dict[str, Any]:
    base_request = normalize_request({**_base_payload(args), "operation": "coverage_only"})
    store, opportunity_timing, provenance = _build_store(base_request)
    technical_failures: list[str] = []
    scientific_findings: list[str] = []

    if store.n_satellites != 88:
        technical_failures.append(
            f"平台默认候选池应为 88 颗，实际 {store.n_satellites} 颗。"
        )
    if store.n_windows <= 0 or store.n_nonempty_pairs <= 0:
        technical_failures.append("真实平台候选池没有产生任何窗口。")
    for window in store.iter_windows():
        if window.end_utc <= window.start_utc or window.duration_s <= 0.0:
            technical_failures.append("机会底座含非正时长窗口。")
            break
        expected = (window.end_utc - window.start_utc).total_seconds()
        if abs(expected - window.duration_s) > 1e-6:
            technical_failures.append("机会窗口 duration_s 与起止时刻不一致。")
            break

    full_pool_metrics: dict[int, regional_metrics.RegionalMetricsResult] = {}
    full_pool_rows: list[dict[str, Any]] = []
    for k in (1, 2, 3):
        started = time.perf_counter()
        metrics = regional_metrics.compute_regional_metrics(
            store,
            min_simultaneous=k,
        )
        full_pool_metrics[k] = metrics
        full_pool_rows.append(
            {
                "min_simultaneous": k,
                "metrics_seconds": round(time.perf_counter() - started, 4),
                "metrics": _metrics_summary(metrics),
            }
        )

    # K 增大时覆盖状态只能减少。逐网格检查比只比较城市平均值更严格。
    for grid_index in range(store.n_cells):
        q1 = full_pool_metrics[1].grid_metrics[grid_index].temporal_coverage_pct
        q2 = full_pool_metrics[2].grid_metrics[grid_index].temporal_coverage_pct
        q3 = full_pool_metrics[3].grid_metrics[grid_index].temporal_coverage_pct
        if q1 + 1e-9 < q2 or q2 + 1e-9 < q3:
            technical_failures.append(
                f"网格 {grid_index} 出现 K 增大而时间覆盖率上升。"
            )
            break

    task_rows: list[dict[str, Any]] = []
    for case_id, payload in _task_cases(args):
        print(f"[运行] {case_id}", flush=True)
        request = normalize_request(payload)
        task_rows.append(
            _task_summary(
                case_id,
                request,
                store,
                technical_failures=technical_failures,
                scientific_findings=scientific_findings,
            )
        )
        print(
            f"[完成] {case_id}: {task_rows[-1]['selection_seconds']:.4f}s, "
            f"{task_rows[-1]['selected_satellite_count']}颗, "
            f"feasible={task_rows[-1]['feasible']}, "
            f"status={task_rows[-1]['optimality']}",
            flush=True,
        )

    return {
        "schema_version": "siriagent_business_acceptance_v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "acceptance_scope": (
            "固定平台目录与Profile下的几何机会、区域指标和选星业务验收；"
            "不代表云量、任务调度或厂家载荷性能验收。"
        ),
        "threshold_policy": (
            "本文件中的5%时间覆盖率、240/360分钟MRT和10星预算仅用于构造可解释验收场景，"
            "不会自动成为产品默认值。"
        ),
        "input": {
            **_base_payload(args),
            "candidate_satellite_count": store.n_satellites,
        },
        "city": {
            "adcode": store.grid.adcode,
            "name": store.grid.city_name,
            "grid_cell_count": store.n_cells,
            "area_km2": round(store.grid.total_area_km2, 4),
        },
        "provenance": provenance,
        "opportunity": {
            **opportunity_timing,
            "nonempty_satellite_grid_pairs": store.n_nonempty_pairs,
            "raw_window_count": store.n_windows,
        },
        "full_pool_k_metrics": full_pool_rows,
        "task_results": task_rows,
        "technical_failures": technical_failures,
        "scientific_findings": scientific_findings,
        "acceptance_passed": not technical_failures,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SIRIAgent 平台默认候选池业务验收")
    parser.add_argument("--city", default="330600", help="默认 330600（绍兴）")
    parser.add_argument("--start-utc", default="2026-08-30T00:00:00Z")
    parser.add_argument("--hours", type=float, default=8.0)
    parser.add_argument("--grid-size-km", type=float, default=5.0)
    parser.add_argument("--detection-step-s", type=int, default=10)
    parser.add_argument("--decision-step-s", type=int, default=60)
    parser.add_argument("--ilp-time-limit-s", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    result = run_acceptance(args)
    output = args.output if args.output.is_absolute() else (ROOT / args.output).resolve()
    try:
        output.relative_to(ROOT)
    except ValueError as exc:
        raise ValueError("--output 必须位于项目目录内。") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"结果已写入：{output}")
    print(
        f"业务验收：{'通过' if result['acceptance_passed'] else '未通过'}；"
        f"技术失败 {len(result['technical_failures'])} 项，"
        f"科学/求解发现 {len(result['scientific_findings'])} 项。"
    )
    return 0 if result["acceptance_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
