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
                               method="both", time_limit=120, req_mode="revisit")
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
                                method="both", time_limit=60, req_mode="revisit")
        check("不硬凑：如实报未满足", not hard["eval"]["satisfied"],
              f"MRT={hard['eval']['mrt_actual_minutes']:.0f}分")
        check("空窗表非空", len(hard["gaps"]) > 0)

        print("== 4. 选星 Excel 导出 ==")
        out = os.path.join(tempfile.gettempdir(), "sel_validate.xlsx")
        ss.export_selection_xlsx(sel, out, region_name="杭州5km")
        check("Excel 非空", os.path.getsize(out) > 0, f"{os.path.getsize(out)} B")
        import openpyxl
        _wb = openpyxl.load_workbook(out, read_only=True)
        _sheets = _wb.sheetnames
        check("Excel 含全部最优方案表", "全部最优方案" in _sheets, str(_sheets))
        check("Excel 说明表含需求模式", any(
            "需求模式" in str(c.value) for row in _wb["说明"].iter_rows() for c in row if c.value))
        _wb.close()

    print("== 5. 需求双模式 + 目标模式② + 全部最优方案 ==")
    if result:
        # 连续覆盖（吉林一号纯低轨 → 物理不可行，如实报告上限）
        cont = ss.run_selection(result["sats"], result["windows_by_sat"],
                                result["t0"], result["t1"],
                                step_minutes=1, k_min=1, mrt_minutes=30,
                                method="greedy", req_mode="continuous")
        check("连续模式：物理不可行如实报告",
              cont["req_mode"] == "continuous" and not cont["eval"]["satisfied"]
              and cont.get("feasibility") is not None
              and not cont["feasibility"]["possible"],
              f"possible={cont.get('feasibility', {}).get('possible')}")
        check("连续模式：给出物理上限数字",
              cont.get("feasibility", {}).get("full_max_gap_minutes", 0) > 0,
              str(cont.get("feasibility", {}).get("full_max_gap_minutes")))
        # 重访模式兼容（原有语义）
        rev = ss.run_selection(result["sats"], result["windows_by_sat"],
                               result["t0"], result["t1"],
                               step_minutes=1, k_min=1, mrt_minutes=180,
                               method="greedy", req_mode="revisit")
        check("重访模式：字段与判定正常",
              rev["req_mode"] == "revisit" and rev["effective_mrt_minutes"] == 180.0
              and rev["eval"]["satisfied"])
        # 目标模式②：给定 N 颗最大化覆盖率
        mc = ss.run_selection(result["sats"], result["windows_by_sat"],
                              result["t0"], result["t1"],
                              step_minutes=1, k_min=1, mrt_minutes=30,
                              method="greedy", req_mode="continuous",
                              objective="max_coverage", n_sats_limit=5)
        check("目标模式②：选中数≤N 且覆盖率合理",
              mc["objective"] == "max_coverage"
              and 0 < len(mc["selected"]) <= 5
              and 0 < mc["eval"]["coverage_pct"] <= cont["full"]["coverage_pct"] + 1e-9,
              f"选 {len(mc['selected'])} 颗，覆盖率 {mc['eval']['coverage_pct']:.2f}%")
        # 全部最优方案（合成 4 颗全程星）
        import numpy as np
        _m = 10
        _sc = [np.ones(_m, dtype=bool) for _ in range(4)]
        _ss = [type("S", (), {"name": f"FULL-{j}"})() for j in range(4)]
        _s1, _n1 = ss.enumerate_optimal_solutions(_sc, _ss, 1, 0, 1)
        _s2, _n2 = ss.enumerate_optimal_solutions(_sc, _ss, 2, 0, 1)
        check("全部最优方案 K=1 → 4 种", len(_s1) == 4, str(len(_s1)))
        check("全部最优方案 K=2 → 6 种", len(_s2) == 6, str(len(_s2)))

    print("========================================")
    print(f"结果：{PASS} 项通过，{FAIL} 项失败")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())