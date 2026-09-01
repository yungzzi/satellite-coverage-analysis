# -*- coding: utf-8 -*-
"""真实城市网格下 K=1/K=2 的区域流程性能基准。

这个脚本只做测量，不修改覆盖窗口或选星结果。默认使用杭州 5 km 裁剪网格、
固定的吉林一号测试 TLE 和 8 小时分析时段，方便在没有联网的情况下重复运行。

示例（在项目根目录执行）：
    .venv\\Scripts\\python.exe src\\benchmark_regional_k.py

默认使用生产口径的 60 秒决策片和全部 58 颗固定测试卫星；如果只想快速观察
模型规模，可传 ``--sat-limit 20 --decision-step 300``。机会窗口仍按默认
10 秒检测，K=1 与 K=2 复用同一份窗口。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import regional_metrics
import regional_opportunities
import regional_selection
import satellite_loader
from region_grid import build_clipped_grid, load_city_boundary


def _rss_mb() -> float | None:
    """尽量读取当前进程常驻内存；没有 psutil 时返回 None。"""

    try:
        import psutil

        return round(psutil.Process().memory_info().rss / 1024**2, 1)
    except Exception:
        return None


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实城市网格 K=1/K=2 性能基准")
    parser.add_argument(
        "--city",
        default="330100",
        help="城市 adcode；默认 330100（杭州）",
    )
    parser.add_argument(
        "--tle",
        type=Path,
        default=ROOT / "data" / "test_data" / "jilin1_all.tle",
        help="固定 TLE 文件，默认吉林一号 58 颗测试集",
    )
    parser.add_argument(
        "--sat-limit",
        type=int,
        default=58,
        help="最多使用多少颗卫星；默认 58（吉林一号固定测试集全量）",
    )
    parser.add_argument("--hours", type=float, default=8.0, help="分析时长，默认 8 小时")
    parser.add_argument(
        "--detection-step",
        type=int,
        default=10,
        help="机会窗口检测步长（秒），默认 10",
    )
    parser.add_argument(
        "--decision-step",
        type=int,
        default=60,
        help="选星决策片长度（秒），默认 60；可传 300 做快速模型规模测试",
    )
    parser.add_argument(
        "--selection-time-limit",
        type=int,
        default=15,
        help="每次 ILP 时间上限（秒），默认 15",
    )
    parser.add_argument(
        "--mrt-target",
        type=float,
        default=7_200.0,
        help="任务② MRT 上限（秒），默认 7200",
    )
    parser.add_argument(
        "--area-target",
        type=float,
        default=1.0,
        help="任务②达标面积比例，默认 1%%，避免因物理不可行而提前跳过模型",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="可选 JSON 输出路径；不传时只打印到终端",
    )
    return parser.parse_args()


def _duration_s(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds())


def _run_selection(store: Any, k: int, args: argparse.Namespace) -> dict[str, Any]:
    """计时贪心和 ILP；ILP 状态也保留，便于看是否达到时间上限。"""

    row: dict[str, Any] = {"k": k}
    greedy_start = time.perf_counter()
    greedy = regional_selection.greedy_select_mrt(
        store,
        mrt_target_s=args.mrt_target,
        area_target_pct=args.area_target,
        min_simultaneous=k,
    )
    row["greedy_seconds"] = round(time.perf_counter() - greedy_start, 3)
    row["greedy_selected_count"] = len(greedy.selected_sat_indices)
    row["greedy_status"] = greedy.status
    row["greedy_feasible"] = bool(greedy.feasible)

    ilp_start = time.perf_counter()
    ilp = regional_selection.ilp_select_mrt(
        store,
        mrt_target_s=args.mrt_target,
        area_target_pct=args.area_target,
        min_simultaneous=k,
        time_limit_s=args.selection_time_limit,
    )
    row["ilp_seconds"] = round(time.perf_counter() - ilp_start, 3)
    row["ilp_selected_count"] = len(ilp.selected_sat_indices)
    row["ilp_status"] = ilp.status
    row["ilp_feasible"] = bool(ilp.feasible)
    row["ilp_constraint_count"] = int(ilp.constraint_count)
    row["ilp_note"] = ilp.note
    return row


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    """运行一次完整基准并返回可序列化结果。"""

    if args.sat_limit <= 0:
        raise ValueError("--sat-limit 必须大于 0。")
    if args.hours <= 0:
        raise ValueError("--hours 必须大于 0。")
    if args.detection_step <= 0 or args.decision_step <= 0:
        raise ValueError("检测步长和决策步长必须大于 0。")
    if not args.tle.is_file():
        raise FileNotFoundError(f"TLE 文件不存在：{args.tle}")

    ts = satellite_loader.load.timescale(builtin=True)
    satellites = satellite_loader.parse_tle_text(args.tle.read_text(encoding="utf-8"), ts=ts)
    satellites = satellites[: args.sat_limit]
    if not satellites:
        raise ValueError("TLE 文件中没有可用卫星。")

    boundary = load_city_boundary(ROOT / "data" / "geo" / f"{args.city}.json", adcode=args.city)
    grid = build_clipped_grid(boundary)
    start = datetime(2026, 8, 8, tzinfo=timezone.utc)
    end = start + timedelta(hours=float(args.hours))
    config = {
        "fov_half_angle_deg": 30.0,
        "min_elevation_deg": 0.0,
        "coarse_step_seconds": args.detection_step,
    }

    opportunity_start = time.perf_counter()
    store = regional_opportunities.compute_regional_opportunities(
        satellites,
        grid,
        start,
        end,
        config,
        cell_chunk_size=256,
        decision_step_s=args.decision_step,
        timescale=ts,
    )
    opportunity_seconds = time.perf_counter() - opportunity_start

    metrics_rows: list[dict[str, Any]] = []
    all_indices = list(range(len(satellites)))
    for k in (1, 2):
        metrics_start = time.perf_counter()
        metrics = regional_metrics.compute_regional_metrics(
            store,
            all_indices,
            min_simultaneous=k,
        )
        metrics_rows.append(
            {
                "k": k,
                "seconds": round(time.perf_counter() - metrics_start, 3),
                "area_weighted_temporal_coverage_pct": round(
                    metrics.city.area_weighted_temporal_coverage_pct, 4
                ),
                "area_weighted_mrt_s": round(metrics.city.area_weighted_mrt_s, 3),
                "area_weighted_art_s": round(metrics.city.area_weighted_art_s, 3),
            }
        )

    selection_rows = [
        _run_selection(store, k, args)
        for k in (1, 2)
    ]
    result: dict[str, Any] = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "city": {
            "adcode": args.city,
            "name": grid.city_name,
            "n_cells": len(grid.cells),
            "area_km2": round(grid.total_area_km2, 3),
            "grid_size_km": grid.grid_size_m / 1000.0,
            "boundary_hash": grid.boundary_hash,
        },
        "input": {
            "tle": str(args.tle),
            "n_satellites": len(satellites),
            "hours": float(args.hours),
            "detection_step_s": args.detection_step,
            "decision_step_s": args.decision_step,
            "mrt_target_s": float(args.mrt_target),
            "area_target_pct": float(args.area_target),
            "selection_time_limit_s": args.selection_time_limit,
        },
        "opportunity": {
            "seconds": round(opportunity_seconds, 3),
            "n_nonempty_pairs": store.n_nonempty_pairs,
            "n_windows": store.n_windows,
            "rss_mb_after": _rss_mb(),
        },
        "metrics": metrics_rows,
        "selection": selection_rows,
    }
    return result


def main() -> int:
    args = _parse_args()
    result = run_benchmark(args)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"\n已写入：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
