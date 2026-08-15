# -*- coding: utf-8 -*-
"""ATK 引擎 vs 自研引擎对拍（中国遥感全量 303 × 杭州 5km × 8h × fov45/仰角10）。

对比：窗口总数、逐星窗口数、逐分钟"是否有覆盖"一致性、同时覆盖星数差异。
报告存 output/atk_engine_compare_report.txt。
"""
import io, os, sys, time, math
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

import coverage_analysis as cov
import satellite_loader as sl

COLLECTION = "中国遥感全量（内置·测试基准）"
START = "2026-08-12 00:00:00"
END = "2026-08-12 08:00:00"
FOV, ELEV = 45.0, 10.0
LAT, LON, RAD = 30.25, 120.17, 5.0


def build_cfg():
    return {
        "region_name": f"杭州({LAT},{LON}) 半径{RAD}km",
        "region_mode": "circle",
        "tle_file": f"收藏夹：{COLLECTION}",
        "fov_half_angle_deg": FOV,
        "min_elevation_deg": ELEV,
        "step_minutes": 1,
        "refine_step_seconds": 1,
        "grid_spacing_deg": 0.2,
        "save_gantt": False,
        "save_plots": False,
        "max_gantt_rows": 15,
    }


def main():
    recs = sl.resolve_collections_to_records([COLLECTION])
    print(f"卫星总数：{len(recs)}", flush=True)
    region = {"mode": "circle", "lat": LAT, "lon": LON, "radius_km": RAD}
    cfg = build_cfg()

    lines = []
    def log(msg=""):
        print(msg, flush=True)
        lines.append(str(msg))

    t = time.time()
    self_res = cov.analyze_collection_pipeline(recs, region, cfg, START, END, engine="self")
    log(f"[自研] {time.time()-t:.1f}s  窗口 {self_res['n_win']}  "
        f"覆盖星 {len(self_res['dfs'][1])}")

    t = time.time()
    atk_res = cov.analyze_collection_pipeline(recs, region, cfg, START, END, engine="atk")
    log(f"[ATK ] {time.time()-t:.1f}s  窗口 {atk_res['n_win']}  "
        f"覆盖星 {len(atk_res['dfs'][1])}")

    dfw_self, dfs_self, dfst_self, _ = self_res["dfs"]
    dfw_atk, dfs_atk, dfst_atk, _ = atk_res["dfs"]

    # 逐星窗口数对比
    self_cnt = dict(zip(dfs_self["satellite"], dfs_self["n_windows"]))
    atk_cnt = dict(zip(dfs_atk["satellite"], dfs_atk["n_windows"]))
    names = sorted(set(self_cnt) | set(atk_cnt))
    diff_sats = [n for n in names if self_cnt.get(n, 0) != atk_cnt.get(n, 0)]
    log(f"逐星窗口数不一致的卫星数：{len(diff_sats)}/{len(names)}")
    for n in diff_sats[:10]:
        log(f"    {n}: 自研 {self_cnt.get(n, 0)} vs ATK {atk_cnt.get(n, 0)}")

    # 逐分钟精确对比（480 分钟，按窗口覆盖时刻统计，不受状态表合并影响）
    def per_minute_counts(windows_by_sat, t0, t1):
        n = int((t1 - t0).total_seconds() // 60)
        minutes = [t0 + timedelta(minutes=i) for i in range(n)]
        cnt = []
        for m in minutes:
            cnt.append(sum(1 for ws in windows_by_sat
                           if any(s <= m < e for s, e, _ in ws)))
        return minutes, cnt

    m_self, c_self = per_minute_counts(self_res["windows_by_sat"], self_res["t0"], self_res["t1"])
    m_atk, c_atk = per_minute_counts(atk_res["windows_by_sat"], atk_res["t0"], atk_res["t1"])
    agree = sum(1 for a, b in zip(c_self, c_atk) if (a > 0) == (b > 0))
    diffs = sorted(((abs(a - b), i, a, b) for i, (a, b) in enumerate(zip(c_self, c_atk))),
                   reverse=True)
    max_diff = diffs[0][0] if diffs else 0
    mean_diff = sum(d[0] for d in diffs) / len(diffs) if diffs else 0.0
    log(f"逐分钟对比：{len(m_self)} 分钟；'是否有覆盖'一致 {agree}/{len(m_self)} "
        f"({100*agree/len(m_self):.1f}%)")
    log(f"同时覆盖星数：最大差 {max_diff}，平均差 {mean_diff:.3f}")
    for d, i, a, b in diffs[:5]:
        log(f"    差异时刻 {m_self[i]:%H:%M:%S}：自研 {a} 颗 vs ATK {b} 颗")

    # 状态表（合并连续相同状态）行数对比，仅作参考
    st_self = dfst_self.set_index("start_utc")
    st_atk = dfst_atk.set_index("start_utc")
    log(f"状态表（合并连续状态）行数：自研 {len(st_self)} vs ATK {len(st_atk)}")

    # 报告落盘
    out_dir = Path.cwd() / "output"
    out_dir.mkdir(exist_ok=True)
    report = out_dir / "atk_engine_compare_report.txt"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"报告已保存：{report}")


if __name__ == "__main__":
    main()
