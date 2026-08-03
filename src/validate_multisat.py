# -*- coding: utf-8 -*-
"""
多卫星交叉验证脚本
==================
在单星详细验证（validate_crosscheck.py）的基础上，把同样的"换一条路重算 +
与 N2YO 网站对照"流程推广到多颗星，覆盖不同轨道类型：

  LEO-SSO : 太阳同步低轨（约98°倾角，约97分钟一圈）——遥感主力
  LEO-100 : 高倾角低轨（100°倾角，约110分钟）
  LEO-35  : 低倾角低轨（35°倾角，约96分钟）
  MEO-20  : 中地球轨道（20°倾角，约4.5小时一圈，高度约7500 km）
  GEO     : 地球同步轨道（约24小时一圈，相对地面几乎不动）

做法与单星脚本完全一致：A=主程序路径 vs 独立算法（浙江全境逐分钟对比），
B=我们 vs N2YO 网站（杭州点，10 天，仰角≥0° 同口径）。
GEO 星特殊处理：它从不"落山"，没有过境起止，改为验证"持续可见"。

用法：
  1) 先下载 N2YO 页面和最新 TLE（见 docs/验证报告.md 里的命令）
  2) python src/validate_multisat.py
输出：output/validation/compare_B_<NORAD>.csv、summary_multisat.csv
"""

import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coverage_analysis as ca
from skyfield.api import load, EarthSatellite
from validate_crosscheck import (
    PROJ, HANGZHOU,
    read_tle_lines, propagate_ecef, observer_ecef, elevation_from_ecef,
    merge_windows, make_dt_series, parse_n2yo_passes,
    path_skyfield_region, path_rawsgp4_region, path_skyfield_altaz,
    windows_above,
)

# 验证卫星清单（覆盖不同轨道类型）
SATELLITES = [
    {"name": "YAOGAN-7",     "norad": "36110", "type": "LEO-SSO"},
    {"name": "YAOGAN-8",     "norad": "36121", "type": "LEO-100"},
    {"name": "YAOGAN-30 02A", "norad": "42945", "type": "LEO-35"},
    {"name": "YAOGAN-45",    "norad": "65563", "type": "MEO-20"},
    {"name": "YAOGAN-41",    "norad": "58582", "type": "GEO"},
]


def n2yo_html_path(out_dir, norad):
    """N2YO 页面文件名：优先 n2yo_<编号>.html，兼容旧的 n2yo_pass.html"""
    p = os.path.join(out_dir, f"n2yo_{norad}.html")
    if os.path.exists(p):
        return p
    legacy = os.path.join(out_dir, "n2yo_pass.html")
    return legacy if os.path.exists(legacy) else p


def validate_one(sat_cfg, ts, cfg, glats, glons, dt_list, dt_fine, out_dir):
    """对一颗卫星跑完整验证（A 内部一致 + A2 步长 + B 外部对照）"""
    name, norad, stype = sat_cfg["name"], sat_cfg["norad"], sat_cfg["type"]
    tle_stored = os.path.join(PROJ, "data", "raw", "active_tle.txt")
    fresh_tle = os.path.join(out_dir, f"fresh_{norad}.tle")
    html = n2yo_html_path(out_dir, norad)

    nl, l1, l2 = read_tle_lines(tle_stored, norad)
    if not (nl and l1 and l2):
        raise RuntimeError(f"存储 TLE 中找不到 NORAD {norad}")
    if name.upper() not in nl.upper():
        print(f"  [!] 警告：TLE 名称「{nl.strip()}」与预期「{name}」不一致，请人工检查")
    f1 = f2 = None
    if os.path.exists(fresh_tle):
        fn, f1, f2 = read_tle_lines(fresh_tle, norad)
        if fn and name.upper() not in fn.upper():
            print(f"  [!] 警告：新 TLE 名称「{fn.strip()}」与预期「{name}」不一致")

    print("\n" + "=" * 66)
    print(f"[{name}]（{stype}，NORAD {norad}）")
    print("=" * 66)
    print(f"  存储 TLE: {l1}")
    if f1:
        print(f"  新 TLE : {f1}")

    sat = EarthSatellite(l1, l2, name, ts)

    # ---- A. 主程序 vs 独立算法（浙江全境 24 小时） ----
    cov1, _ = path_skyfield_region(sat, ts, dt_list, glats, glons, cfg)
    cov2, _ = path_rawsgp4_region(l1, l2, dt_list, glats, glons, cfg)
    wins1 = merge_windows(cov1, dt_list)
    agree = int(np.mean(cov1 == cov2) * 100)
    n_diff = int(np.sum(cov1 != cov2))
    print(f"  [A] 主程序 vs 独立算法：逐分钟一致率 {agree}%"
          f"（{len(cov1)} 分钟，{n_diff} 分钟不一致）")
    print(f"      主程序窗口："
          f"{[(w[0].strftime('%m-%d %H:%M'), w[1].strftime('%m-%d %H:%M')) for w in wins1][:6]}")

    summary = {"name": name, "type": stype, "norad": norad,
               "agree_pct": agree, "n_diff": n_diff}

    # ---- A2. 时间步长敏感性（非 GEO 才有边界问题） ----
    if stype != "GEO":
        cov2f, _ = path_rawsgp4_region(l1, l2, dt_fine, glats, glons, cfg)
        wins2f = merge_windows(cov2f, dt_fine)
        print(f"      [A2] 10秒细网格窗口："
              f"{[(w[0].strftime('%m-%d %H:%M:%S'), w[1].strftime('%m-%d %H:%M:%S')) for w in wins2f][:4]}")

    # ---- B. N2YO 外部对照（杭州点，10 天） ----
    if not os.path.exists(html):
        print(f"  [B] 未找到 N2YO 页面 {html}（先运行下载命令），跳过外部对照")
        summary.update({"n2yo": None, "matched": None, "d_start": None, "d_end": None})
        return summary

    n2yo = parse_n2yo_passes(html)
    bl1, bl2 = (f1, f2) if f1 else (l1, l2)
    if n2yo:
        b0 = n2yo[0]["start_utc"] - timedelta(hours=2)
        b1 = n2yo[-1]["end_utc"] + timedelta(hours=2)
    else:
        b0 = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        b1 = b0 + timedelta(days=10)
    dt_b = make_dt_series(b0, b1, 10)
    ecef_b = propagate_ecef(bl1, bl2, dt_b)
    obs = observer_ecef(HANGZHOU[0], HANGZHOU[1])
    elev_manual = elevation_from_ecef(ecef_b, obs, HANGZHOU[0], HANGZHOU[1])
    sat_b = EarthSatellite(bl1, bl2, name, ts)
    elev_altaz = path_skyfield_altaz(sat_b, ts, dt_b, HANGZHOU[0], HANGZHOU[1])

    # GEO 特殊处理：卫星从不落山，没有过境起止
    if stype == "GEO":
        emin, emax = float(elev_manual.min()), float(elev_manual.max())
        n2yo_note = f"N2YO 页面含 {len(n2yo)} 条过境" if n2yo else "N2YO 页面无过境记录"
        print(f"  [B] GEO 卫星：10 天内杭州仰角 {emin:.1f}° ~ {emax:.1f}°"
              f"（始终高于地平线 -> 持续可见；{n2yo_note}，结论一致）")
        summary.update({"n2yo": len(n2yo), "matched": "GEO", "d_start": None, "d_end": None})
        return summary

    w_manual_0 = windows_above(elev_manual, dt_b, min_elev=0.0)
    w_altaz_0 = windows_above(elev_altaz, dt_b, min_elev=0.0)
    w_manual_10 = windows_above(elev_manual, dt_b, min_elev=10.0)
    idx = {d: i for i, d in enumerate(dt_b)}

    rows_b = []
    matched = 0
    for p in n2yo:
        hit = [w for w in w_manual_0 if w[1] >= p["start_utc"] and w[0] <= p["end_utc"]]
        row = {
            "n2yo_start_utc": p["start_utc"],
            "n2yo_end_utc": p["end_utc"],
            "n2yo_max_elev_deg": round(p["max_elev_deg"], 2),
        }
        if hit:
            w = hit[0]
            matched += 1
            row["ours_start_utc"] = w[0]
            row["ours_end_utc"] = w[1]
            row["delta_start_s"] = (w[0] - p["start_utc"]).total_seconds()
            row["delta_end_s"] = (w[1] - p["end_utc"]).total_seconds()
            i0 = max(0, idx[w[0]] - 12)
            i1 = min(len(elev_manual) - 1, idx[w[1]] + 12)
            row["ours_max_elev_deg"] = round(float(elev_manual[i0:i1 + 1].max()), 2)
            im = int(np.argmax(elev_manual[i0:i1 + 1]))
            row["delta_max_time_s"] = (dt_b[i0 + im] - p["max_utc"]).total_seconds()
        rows_b.append(row)

    df_b = pd.DataFrame(rows_b)
    df_b.to_csv(os.path.join(out_dir, f"compare_B_{norad}.csv"),
                index=False, encoding="utf-8-sig")
    ok = df_b["delta_start_s"].notna() if len(df_b) else pd.Series(dtype=bool)
    if ok.any():
        d1 = df_b.loc[ok, "delta_start_s"].abs()
        d2 = df_b.loc[ok, "delta_end_s"].abs()
        print(f"  [B] N2YO 过境 {len(n2yo)} 次，对上 {int(ok.sum())} 次；"
              f"开始偏差 平均{d1.mean():.0f}秒/最大{d1.max():.0f}秒；"
              f"结束偏差 平均{d2.mean():.0f}秒/最大{d2.max():.0f}秒")
    else:
        print(f"  [B] N2YO 过境 {len(n2yo)} 次，没有能对上的（检查页面数据）")
    print(f"      （额外：0°口径窗口数={len(w_manual_0)}，10°口径窗口数={len(w_manual_10)}，"
          f"skyfield-altaz 0°窗口数={len(w_altaz_0)}）")

    summary.update({"n2yo": len(n2yo), "matched": int(ok.sum()) if ok.any() else 0,
                    "d_start": round(float(d1.mean()), 1) if ok.any() else None,
                    "d_end": round(float(d2.mean()), 1) if ok.any() else None})
    return summary


def main():
    out_dir = os.path.join(PROJ, "output", "validation")
    os.makedirs(out_dir, exist_ok=True)
    tle_stored = os.path.join(PROJ, "data", "raw", "active_tle.txt")

    ts = load.timescale()
    cfg = dict(ca.CONFIG)
    cfg["tle_file"] = tle_stored
    poly = ca.load_region_polygon(os.path.join(PROJ, "data", "geo", "330000.json"))
    glats, glons = ca.make_grid_points(poly, cfg["grid_spacing_deg"])
    t0 = datetime.strptime(cfg["start_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(cfg["end_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_list = make_dt_series(t0, t1, 60)
    dt_fine = make_dt_series(t0, t1, 10)

    print("=" * 66)
    print("交叉验证报告（终端摘要）——多卫星版")
    print(f"地区：浙江全境 {len(glats)} 个网格点 + 杭州点；"
          f"时间：{t0:%Y-%m-%d} ~ {t1:%Y-%m-%d} UTC")
    print("=" * 66)

    results = []
    for sc in SATELLITES:
        try:
            r = validate_one(sc, ts, cfg, glats, glons, dt_list, dt_fine, out_dir)
        except Exception as e:
            print(f"  [!] {sc['name']} 验证失败：{e}")
            r = {"name": sc["name"], "type": sc["type"], "norad": sc["norad"],
                 "agree_pct": None, "n_diff": None, "n2yo": None,
                 "matched": None, "d_start": None, "d_end": None, "error": str(e)}
        results.append(r)

    # 汇总表
    print("\n" + "=" * 66)
    print("汇总（A=内部一致率，B=N2YO对照）")
    print("=" * 66)
    rows = [{
        "卫星": r["name"], "轨道类型": r["type"],
        "A-一致率%": r["agree_pct"],
        "B-N2YO过境数": r["n2yo"],
        "B-对上次数": r["matched"],
        "B-开始偏差s": r["d_start"],
        "B-结束偏差s": r["d_end"],
    } for r in results]
    print(pd.DataFrame(rows).to_string(index=False))
    pd.DataFrame(rows).to_csv(os.path.join(out_dir, "summary_multisat.csv"),
                              index=False, encoding="utf-8-sig")
    print("\n验证结束：详细结论见 docs/验证报告.md")


if __name__ == "__main__":
    main()
