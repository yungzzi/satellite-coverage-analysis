# -*- coding: utf-8 -*-
"""N2YO 交叉验证（中国遥感快照版）。

用法：
  1) 从 N2YO 下载过境页保存为 n2yo_<NORAD>.html（杭州 30.25,120.15）：
     https://www.n2yo.com/passes/?s=<NORAD>&lat=30.25&lng=120.15&alt=10&days=2
  2) python src/validate_n2yo_crosscheck.py --n2yo-dir <html目录> [--days 2]

对比口径：仰角≥0° 起止窗口（N2YO 口径），只比最大仰角≥10° 的可见过境。
输出：output/validation/n2yo_crosscheck_summary.csv
注意：TLE 快照时效影响外推精度；GEO 星 N2YO 无过境数据（静止轨道无过境）。
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
from skyfield.api import load, EarthSatellite
from validate_crosscheck import parse_n2yo_passes, windows_above, path_skyfield_altaz

PROJ = Path(__file__).resolve().parent.parent
TLE = PROJ / "data" / "test_data" / "china_remote_sensing_all.tle"
LAT, LON = 30.25, 120.15
STEP_S = 10


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n2yo-dir", required=True, help="存放 n2yo_<NORAD>.html 的目录")
    ap.add_argument("--days", type=int, default=2, help="对比天数")
    args = ap.parse_args()

    files = {p.stem.replace("n2yo_", ""): p for p in Path(args.n2yo_dir).glob("n2yo_*.html")}
    if not files:
        print("未找到 n2yo_*.html"); return 1

    lines = [ln.strip() for ln in open(TLE, encoding="ascii", errors="replace") if ln.strip()]
    tle_by_norad = {}
    i = 0
    while i + 2 < len(lines):
        if lines[i + 1].startswith("1 "):
            tle_by_norad[lines[i + 1][2:7]] = (lines[i], lines[i + 1], lines[i + 2])
        i += 3

    ts = load.timescale(builtin=True)
    t0 = datetime(2026, 8, 8, tzinfo=timezone.utc)
    dt_list = []
    t = t0
    while t <= t0 + timedelta(days=args.days):
        dt_list.append(t); t += timedelta(seconds=STEP_S)
    dt_s = [x.timestamp() for x in dt_list]

    rows = []
    for norad, html in sorted(files.items()):
        tle = tle_by_norad.get(norad)
        if not tle:
            print(f"[{norad}] 快照中无此星，跳过"); continue
        nl, l1, l2 = tle
        sat = EarthSatellite(l1, l2, nl, ts)
        elev = path_skyfield_altaz(sat, ts, dt_list, LAT, LON)
        wins = windows_above(elev, dt_s, min_elev=0.0)
        visible = []
        for a, b in wins:
            ia = int((a - dt_s[0]) / STEP_S); ib = int((b - dt_s[0]) / STEP_S)
            mx = float(elev[ia:ib].max())
            if mx >= 10.0:
                visible.append((datetime.fromtimestamp(a, tz=timezone.utc),
                                datetime.fromtimestamp(b, tz=timezone.utc), mx))
        n2 = parse_n2yo_passes(str(html))
        n_match = 0
        used = set()
        for p in n2:
            best, bi = None, None
            for k, (a, b, _mx) in enumerate(visible):
                if k in used:
                    continue
                d = abs((a - p["start_utc"]).total_seconds())
                if best is None or d < best:
                    best, bi = d, k
            if bi is not None and best is not None and best < 600:
                used.add(bi)
                a, b, mx = visible[bi]
                n_match += 1
                rows.append({
                    "norad": norad, "name": nl.strip(),
                    "n2yo_start": p["start_utc"].strftime("%Y-%m-%d %H:%M:%S"),
                    "our_start": a.strftime("%Y-%m-%d %H:%M:%S"),
                    "start_diff_s": round(abs((a - p["start_utc"]).total_seconds()), 1),
                    "end_diff_s": round(abs((b - p["end_utc"]).total_seconds()), 1),
                    "n2yo_max_deg": round(p["max_elev_deg"], 1),
                    "our_max_deg": round(mx, 1),
                })
        print(f"[{nl.strip()}] ({norad}) 匹配 {n_match} / N2YO {len(n2)} / 我们可见 {len(visible)}")

    out_dir = PROJ / "output" / "validation"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "n2yo_crosscheck_summary.csv"
    pd.DataFrame(rows).to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"对比明细已保存：{out_path}（{len(rows)} 条过境匹配）")
    return 0


if __name__ == "__main__":
    sys.exit(main())