# -*- coding: utf-8 -*-
"""中国遥感全量（测试 303 颗）综合验证。

矩阵：10 个坐标（东/西半球、高低纬、南北半球）× 8h 覆盖 + 任务①；
      杭州 24h；杭州五任务全跑；时间精度对比（1 分钟+10s 精化 vs 10 秒步长）。

运行：.venv/Scripts/python src\validate_china_remote.py
输出：output/validation/china_remote_matrix.csv
"""
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import coverage_analysis as cov
import satellite_loader as sl
import satellite_selection as ss

PROJ = Path(__file__).resolve().parent.parent


def t1end(dt_list):
    """左闭右开栅格：真实终点 = 最后一点 + 一个步长（1 分钟）。"""
    return dt_list[-1] + timedelta(minutes=1)

LOCATIONS = [
    ("杭州", 30.25, 120.15), ("北京", 39.90, 116.40), ("乌鲁木齐", 43.83, 87.62),
    ("广州", 23.13, 113.27), ("漠河", 53.50, 122.30), ("拉萨", 29.65, 91.14),
    ("悉尼", -33.87, 151.21), ("内罗毕", -1.29, 36.82), ("开罗", 30.04, 31.24),
    ("纽约", 40.71, -74.01), ("朗伊尔城", 78.22, 15.65),
]
T8 = ("2026-08-03 00:00:00", "2026-08-03 08:00:00")
T24 = ("2026-08-03 00:00:00", "2026-08-04 00:00:00")


def make_region_cfg(lat, lon, step_minutes=1, refine=10):
    region = {"mode": "circle", "lat": lat, "lon": lon, "radius_km": 5.0}
    cfg = {"fov_half_angle_deg": 30.0, "min_elevation_deg": 10.0,
           "step_minutes": step_minutes, "refine_step_seconds": refine,
           "grid_spacing_deg": 0.2}
    return region, cfg


def main():
    cols = sl.load_builtin_collections()
    col = next(c for c in cols if "中国遥感全量" in c["name"])
    recs = sl.resolve_collection(col, offline=True)
    sats = sl.records_to_satellites(recs)
    ts = sl.load.timescale(builtin=True)
    n_sat = len(sats)
    print(f"卫星池：{n_sat} 颗（{col['name']}）")
    rows = []

    # ---------- 1) 10 坐标 × 8h：覆盖 + 任务①（覆盖率≥100% 最少颗数） ----------
    print("\n== 1) 10 坐标 × 8h 覆盖 + 任务① ==")
    for name, lat, lon in LOCATIONS:
        region, cfg = make_region_cfg(lat, lon)
        t0 = time.time()
        t, dt_list = cov.make_time_array(ts, *T8, 1)
        t1 = t1end(dt_list)
        wbs = [cov.analyze_one_satellite(s, t, dt_list, region, cfg) for s in sats]
        el = time.time() - t0
        sat_cov = ss.build_slice_cover(sats, wbs, dt_list)
        full = ss.evaluate_selection(sat_cov, list(range(n_sat)), 1, 0, 1,
                                     dt_list[0], t1)
        sel = ss.run_selection(sats, wbs, dt_list[0], t1, step_minutes=1,
                               k_min=1, mrt_minutes=180, method="greedy",
                               req_mode="coverage_pct", coverage_pct=100.0)
        n_cover = sum(1 for w in wbs if w)
        n_win = sum(len(w) for w in wbs)
        rows.append({
            "场景": f"{name} 8h", "卫星数": n_sat, "有覆盖星": n_cover, "窗口数": n_win,
            "全池覆盖率%": round(full["coverage_pct"], 2),
            "全池最大空窗分": round(full["mrt_actual_minutes"], 1),
            "任务①选星数": len(sel["selected"]),
            "任务①满足": "是" if sel["eval"]["satisfied"] else "否",
            "耗时s": round(el, 1),
        })
        sel_names = "、".join(sel["selected_names"][:3]) if sel["selected_names"] else "（无）"
        print(f"[{name}] 8h: 覆盖星{n_cover}/{n_sat} 窗口{n_win} "
              f"全池覆盖率{full['coverage_pct']:.1f}% 最大空窗{full['mrt_actual_minutes']:.0f}分 "
              f"任务①选{len(sel['selected'])}颗 满足={sel['eval']['satisfied']} "
              f"选中={sel_names} 耗时{el:.1f}s")

    # ---------- 2) 杭州 24h ----------
    print("\n== 2) 杭州 24h ==")
    region, cfg = make_region_cfg(30.25, 120.15)
    t, dt_list = cov.make_time_array(ts, *T24, 1)
    t1 = t1end(dt_list)
    wbs24 = [cov.analyze_one_satellite(s, t, dt_list, region, cfg) for s in sats]
    sat_cov24 = ss.build_slice_cover(sats, wbs24, dt_list)
    full24 = ss.evaluate_selection(sat_cov24, list(range(n_sat)), 1, 0, 1,
                                   dt_list[0], t1)
    rows.append({
        "场景": "杭州 24h", "卫星数": n_sat,
        "有覆盖星": sum(1 for w in wbs24 if w),
        "窗口数": sum(len(w) for w in wbs24),
        "全池覆盖率%": round(full24["coverage_pct"], 2),
        "全池最大空窗分": round(full24["mrt_actual_minutes"], 1),
    })
    print(f"杭州 24h: 覆盖星{sum(1 for w in wbs24 if w)} 全池覆盖率{full24['coverage_pct']:.1f}% "
          f"最大空窗{full24['mrt_actual_minutes']:.0f}分")

    # ---------- 3) 杭州 8h 五任务 ----------
    print("\n== 3) 杭州 8h 五任务 ==")
    region, cfg = make_region_cfg(30.25, 120.15)
    t, dt_list = cov.make_time_array(ts, *T8, 1)
    t1 = t1end(dt_list)
    wbs = [cov.analyze_one_satellite(s, t, dt_list, region, cfg) for s in sats]
    tasks = [
        ("① 覆盖率≥100%·最少", "coverage_pct", "min_sats", None, 100.0),
        ("② 重访MRT≤180·最少", "revisit", "min_sats", None, 100.0),
        ("③ 给定5颗·覆盖率最大", "any", "max_coverage", 5, 100.0),
        ("④ 给定5颗·最长空窗最小", "any", "min_max_gap", 5, 100.0),
        ("⑤ 给定5颗·平均空窗最小", "any", "min_avg_gap", 5, 100.0),
    ]
    for label, req, obj, n, p in tasks:
        r = ss.run_selection(sats, wbs, dt_list[0], t1, step_minutes=1,
                             k_min=1, mrt_minutes=180, method="greedy",
                             req_mode=req, objective=obj, n_sats_limit=n,
                             coverage_pct=p)
        ev = r["eval"]
        rows.append({
            "场景": f"杭州 8h {label}", "卫星数": n_sat, "选中星数": len(r["selected"]),
            "组合覆盖率%": round(ev["coverage_pct"], 2),
            "最大空窗分": round(ev["mrt_actual_minutes"], 1),
            "平均空窗分": round(ev["art_actual_minutes"], 1),
            "满足": "是" if ev["satisfied"] else ("不适用" if req == "any" else "否"),
        })
        print(f"{label}: 选{len(r['selected'])}颗 覆盖率{ev['coverage_pct']:.1f}% "
              f"最大空窗{ev['mrt_actual_minutes']:.0f}分 平均空窗{ev['art_actual_minutes']:.0f}分")

    # ---------- 4) 时间精度对比：10s 粗筛+1s 精化 vs 10 秒细算（P1 回归） ----------
    print("\n== 4) 时间精度对比（杭州 8h）==")
    region, cfg10 = make_region_cfg(30.25, 120.15, step_minutes=1, refine=1)
    t, dt_list = cov.make_time_array(ts, *T8, 1)
    wbs1 = [cov.analyze_one_satellite(s, t, dt_list, region, cfg10) for s in sats]
    region, cfg_fine = make_region_cfg(30.25, 120.15, step_minutes=1, refine=0)
    cfg_fine["step_minutes"] = 1
    # 10 秒步长：用 make_dt_series 逻辑
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    t0 = _dt(2026, 8, 3, tzinfo=_tz.utc); t1 = t0 + _td(hours=8)
    dt_fine = []
    tt = t0
    while tt <= t1:
        dt_fine.append(tt); tt += _td(seconds=10)
    tsf = ts.from_datetimes(dt_fine)
    cfg_fine["step_minutes"] = 1
    wbs10 = []
    for s in sats:
        wins = cov.analyze_one_satellite(s, tsf, dt_fine, region, cfg_fine)
        wbs10.append(wins)
    n_win1 = sum(len(w) for w in wbs1)
    n_win10 = sum(len(w) for w in wbs10)
    dur1 = sum((e - st).total_seconds() for w in wbs1 for st, e, _ in w)
    dur10 = sum((e - st).total_seconds() for w in wbs10 for st, e, _ in w)
    rows.append({
        "场景": "杭州 8h 时间精度对比", "窗口数(10s粗筛+1s精化)": n_win1,
        "窗口数(10s步长)": n_win10,
        "总覆盖秒(10s粗筛+1s精化)": int(dur1), "总覆盖秒(10s步长)": int(dur10),
        "时长偏差%": round(abs(dur1 - dur10) / max(dur10, 1) * 100, 2),
    })
    print(f"10s粗筛+1s精化: 窗口{n_win1} 总覆盖{dur1:.0f}s | 10s细算: 窗口{n_win10} 总覆盖{dur10:.0f}s "
          f"| 时长偏差{abs(dur1-dur10)/max(dur10,1)*100:.2f}%")
    assert n_win1 == n_win10, f"P1 检测仍有漏检：{n_win1} vs 真值 {n_win10}"
    assert abs(dur1 - dur10) / max(dur10, 1) < 0.02, "P1 时长偏差过大"

    # ---------- 输出 CSV ----------
    out_dir = PROJ / "output" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    out_path = out_dir / "china_remote_matrix.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n汇总已保存：{out_path}（{len(df)} 行）")


if __name__ == "__main__":
    main()
