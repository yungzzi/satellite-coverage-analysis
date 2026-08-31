# -*- coding: utf-8 -*-
"""SIRIAgent 平台候选池在浙江真实城市网格上的覆盖性能基准。"""

from __future__ import annotations

import argparse
import json
import sys
import tracemalloc
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from siriagent_skill.runner import run_skill  # noqa: E402


CITIES = {
    "330100": "杭州市",
    "330200": "宁波市",
    "330300": "温州市",
    "330400": "嘉兴市",
    "330500": "湖州市",
    "330600": "绍兴市",
    "330700": "金华市",
    "330800": "衢州市",
    "330900": "舟山市",
    "331000": "台州市",
    "331100": "丽水市",
}


def run_one(
    adcode: str,
    *,
    start_utc: str,
    horizon_h: float,
    grid_size_km: float,
    detection_step_s: int,
) -> dict[str, object]:
    tracemalloc.start()
    result = run_skill(
        {
            "operation": "coverage_only",
            "city_adcode": adcode,
            "start_utc": start_utc,
            "analysis_horizon_h": horizon_h,
            "grid_size_km": grid_size_km,
            "detection_step_s": detection_step_s,
            "decision_step_s": 60,
            "profile_slug": "siriagent_catalog_swath_v1",
            "catalog_sensor_type": "可见光",
            "max_resolution_m": 1.0,
            "debug": False,
        },
        root=ROOT,
    )
    _, peak_bytes = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    if result.get("status") != "completed":
        raise RuntimeError(f"{adcode} 基准失败：{result}")
    return {
        "adcode": adcode,
        "city_name": result["city"]["name"],
        "grid_cell_count": result["city"]["grid_cell_count"],
        "candidate_satellite_count": result["input_summary"]["candidate_satellite_count"],
        "nonempty_satellite_grid_pairs": result["opportunity_summary"][
            "nonempty_satellite_grid_pairs"
        ],
        "raw_window_count": result["opportunity_summary"]["raw_window_count"],
        "opportunities_s": result["timings_s"]["opportunities"],
        "total_s": result["timings_s"]["total"],
        "python_peak_tracemalloc_mb": round(peak_bytes / 1024.0 / 1024.0, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="平台 88 颗默认候选星 × 浙江城市网格性能基准")
    parser.add_argument("--all-cities", action="store_true")
    parser.add_argument("--cities", nargs="*", choices=sorted(CITIES))
    parser.add_argument("--start-utc", default="2026-08-30T00:00:00Z")
    parser.add_argument("--horizon-h", type=float, default=8.0)
    parser.add_argument("--grid-size-km", type=float, default=5.0)
    parser.add_argument("--detection-step-s", type=int, default=10)
    parser.add_argument("--output", help="可选 JSON 输出路径（必须位于项目目录内）")
    args = parser.parse_args()

    city_codes = list(CITIES) if args.all_cities else (args.cities or ["330600", "331100"])
    rows: list[dict[str, object]] = []
    for adcode in city_codes:
        row = run_one(
            adcode,
            start_utc=args.start_utc,
            horizon_h=args.horizon_h,
            grid_size_km=args.grid_size_km,
            detection_step_s=args.detection_step_s,
        )
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))

    payload = {
        "benchmark": "siriagent_catalog_visible_le_1m_v1",
        "start_utc": args.start_utc,
        "horizon_h": args.horizon_h,
        "grid_size_km": args.grid_size_km,
        "detection_step_s": args.detection_step_s,
        "rows": rows,
        "total_all_cities_s": round(sum(float(row["total_s"]) for row in rows), 4),
    }
    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = (ROOT / output_path).resolve()
        try:
            output_path.relative_to(ROOT)
        except ValueError as exc:
            raise ValueError("--output 必须位于项目目录内。") from exc
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"结果已写入：{output_path}")
    print(f"完成 {len(rows)} 个城市，总计 {payload['total_all_cities_s']:.4f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
