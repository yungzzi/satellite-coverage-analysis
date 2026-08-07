# -*- coding: utf-8 -*-
"""
validate_selection.py —— 选星模块回归自检

Q104 教训：AppTest 只能渲染默认第一个页签、点不到⑤页按钮，之前仿真脚本又自己
手写了 windows_by_sat，导致"测试通过但界面 NameError"。本自检统一走
cov.analyze_collection_pipeline（⑤页按钮调用的同一个函数），堵住这个漏洞。

运行：.venv/Scripts/python src\validate_selection.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coverage_analysis as cov
import satellite_loader as sl
import satellite_selection as ss

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}{('  ' + detail) if detail else ''}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


def jilin_recs():
    cols = sl.load_builtin_collections()
    jl = next(c for c in cols if "吉林一号全量" in c["name"])
    return sl.resolve_collection(jl)


def make_cfg(region_name="杭州5km"):
    return {
        "region_name": region_name,
        "region_mode": "circle",
        "tle_file": "自检（吉林一号测试快照）",
        "fov_half_angle_deg": 30.0,
        "min_elevation_deg": 10.0,
        "step_minutes": 1,
        "refine_step_seconds": 10,
        "grid_spacing_deg": 0.2,
        "target": {"lat": 30.25, "lon": 120.15, "radius_km": 5.0},
        "save_gantt": False,
        "save_plots": False,
        "max_gantt_rows": 15,
    }


def main():
    print("== 1. 覆盖分析共用入口（⑤页按钮同款路径，防 Q104 回归）==")
    recs = jilin_recs()
    cfg = make_cfg()
    region = {"mode": "circle", "lat": 30.25, "lon": 120.15, "radius_km": 5.0}
    result = cov.analyze_collection_pipeline(
        recs, region, cfg, "2026-08-03 00:00:00", "2026-08-04 00:00:00")
    check("入口返回结果", result is not None)
    check("windows_by_sat 与卫星数一致",
          result is not None and len(result["windows_by_sat"]) == result["n_sats"] == len(recs),
          f"{len(result['windows_by_sat']) if result else 0} vs {len(recs)}")
    check("窗口数与明细一致",
          result is not None and result["n_win"] == sum(
              len(w) for w in result["windows_by_sat"]),
          f"{result['n_win'] if result else 0}")
    check("四件套输出齐全",
          result is not None and len(result["dfs"]) == 4)

    print("== 2. 选星：贪心 vs 整数规划（可满足需求，MRT=180）==")
    if result:
        sel = ss.run_selection(result["sats"], result["windows_by_sat"],
                               result["t0"], result["t1"],
                               step_minutes=1, k_min=1, mrt_minutes=180,
                               method="both", time_limit=120)
        g = sel["greedy"]; i = sel["ilp"]
        check("贪心满足需求", g["eval"]["satisfied"],
              f"MRT={g['eval']['mrt_actual_minutes']:.0f}分")
        check("ILP 证明最优", i is not None and i["status"] == "Optimal",
              i["status"] if i else "无")
        check("两算法数量一致", len(g["selected"]) == len(i["selected"]),
              f"贪心 {len(g['selected'])} vs ILP {len(i['selected'])}")
        check("采用 ILP 结果", sel["algo"] == "ilp" and sel["eval"]["satisfied"])

        print("== 3. 需求不可满足时如实报告（MRT=30）==")
        hard = ss.run_selection(result["sats"], result["windows_by_sat"],
                                result["t0"], result["t1"],
                                step_minutes=1, k_min=1, mrt_minutes=30,
                                method="both", time_limit=60)
        check("不硬凑：如实报未满足", not hard["eval"]["satisfied"],
              f"MRT={hard['eval']['mrt_actual_minutes']:.0f}分")
        check("空窗表非空", len(hard["gaps"]) > 0)

        print("== 4. 选星 Excel 导出 ==")
        out = os.path.join(tempfile.gettempdir(), "sel_validate.xlsx")
        ss.export_selection_xlsx(sel, out, region_name="杭州5km")
        check("Excel 非空", os.path.getsize(out) > 0, f"{os.path.getsize(out)} B")

    print("========================================")
    print(f"结果：{PASS} 项通过，{FAIL} 项失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())