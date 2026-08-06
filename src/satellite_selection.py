# -*- coding: utf-8 -*-
"""
satellite_selection.py —— 选星决策（虚拟星座）核心模块

任务：给定卫星池 + 每颗卫星的覆盖窗口（由 coverage_analysis 算出），
根据"需求"（最大重访间隔 MRT、最少同时可见数 K）挑选一组尽量少的卫星，
使组合后的覆盖满足需求。输出：推荐卫星清单 + 组合覆盖统计 + 空窗表。

需求口径（白话）：
  - 把分析时段切成时间片（默认 1 分钟一片）；
  - 每个时间片，被选中的卫星里至少有 K 颗在覆盖（K 默认 1）；
  - 任何一段连续"不足 K 颗"的时间（空窗）不能超过 MRT 分钟（默认 30）。
  例：MRT=30、K=1 → "每 30 分钟内至少有一颗星能覆盖目标"；
      MRT=1、K=1 → "每一分钟都有覆盖"（连续覆盖）。

两个算法：
  1. 贪心（默认，秒级）：每步挑"能补上最多缺口时间片"的卫星，选完后做
     "能删就删"的局部改进，工程上通常接近最优；
  2. 整数规划 ILP（精确最优）：把选星写成 0/1 数学问题交给 CBC 求解器
     （依赖 pulp；装不上时自动退回贪心）。
     两者对照：一致即放心交付；不一致以 ILP 为准。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))


# ==================== 1. 覆盖矩阵 ====================

def build_slice_cover(sats, windows_by_sat, dt_list, min_event_minutes=0.0):
    """把每颗星的窗口转成"时间片 × 卫星"的覆盖矩阵。

    参数：
      sats         卫星对象列表（须有 .name）
      windows_by_sat  与 sats 一一对应，每项是该星窗口列表
                      [(开始datetime, 结束datetime, 最大覆盖比例), ...]
      dt_list      时间片列表（UTC datetime，固定步长）
      min_event_minutes  单次窗口最短时长（分钟），0=不限；短于它的窗口不算覆盖
    返回：sat_cov 列表，第 j 项是 bool 数组（长度=时间片数），表示卫星 j 覆盖哪些片。
    """
    t_sec = np.array([x.timestamp() for x in dt_list], dtype=float)
    sat_cov = []
    min_sec = float(min_event_minutes) * 60.0
    for windows in windows_by_sat:
        mask = np.zeros(len(dt_list), dtype=bool)
        for start, end, _ratio in windows:
            if min_sec > 0 and (end - start).total_seconds() < min_sec:
                continue  # 窗口太短，不算有效覆盖
            lo = int(np.searchsorted(t_sec, start.timestamp(), side="left"))
            hi = int(np.searchsorted(t_sec, end.timestamp(), side="right")) - 1
            if hi >= lo:
                mask[lo:hi + 1] = True
        sat_cov.append(mask)
    return sat_cov


def _deficient_runs(covered, mrt_steps):
    """返回所有"超长空窗"的片段（(起点, 终点)），空窗长度 > mrt_steps 才记录。

    covered: bool 数组（达标=有 >=K 颗覆盖）
    mrt_steps: 允许的最大连续不达标时间片数（MRT 分钟 ÷ 步长）
    """
    runs = []
    m = len(covered)
    i = 0
    while i < m:
        if not covered[i]:
            j = i
            while j < m and not covered[j]:
                j += 1
            if (j - i) > mrt_steps:
                runs.append((i, j))
            i = j
        else:
            i += 1
    return runs


def _counts_of(sat_cov, selected):
    """被选卫星在每个时间片的覆盖数（int 数组）。"""
    m = len(sat_cov[0]) if sat_cov else 0
    counts = np.zeros(m, dtype=int)
    for j in selected:
        counts = counts + sat_cov[j]
    return counts


def evaluate_selection(sat_cov, selected, k_min, mrt_minutes, step_minutes, t0, t1):
    """对一组被选卫星做统计评估。

    返回 dict：覆盖数数组、空窗表、MRT/ART、覆盖率、同时可见数、是否满足需求。
    """
    counts = _counts_of(sat_cov, selected)
    m = len(counts)
    mrt_steps = max(0, int(round(mrt_minutes / step_minutes)))
    covered = counts >= k_min if m else np.zeros(0, dtype=bool)

    # 空窗：连续不达标的时间片（含时段首尾），start=第一片起点，end=最后一片终点（不含）
    gaps = []
    i = 0
    while i < m:
        if not covered[i]:
            j = i
            while j < m and not covered[j]:
                j += 1
            gap_min = (j - i) * step_minutes
            gaps.append({
                "start_utc": (t0 + timedelta(minutes=step_minutes * i)).strftime("%Y-%m-%d %H:%M:%S"),
                "end_utc": (t0 + timedelta(minutes=step_minutes * j)).strftime("%Y-%m-%d %H:%M:%S"),
                "duration_minutes": round(gap_min, 1),
            })
            i = j
        else:
            i += 1

    mrt_actual = max((g["duration_minutes"] for g in gaps), default=0.0)
    art_actual = float(np.mean([g["duration_minutes"] for g in gaps])) if gaps else 0.0
    coverage_pct = float(covered.mean() * 100.0) if m else 0.0
    return {
        "counts": counts,
        "covered": covered,
        "gaps": gaps,
        "mrt_actual_minutes": mrt_actual,
        "art_actual_minutes": art_actual,
        "coverage_pct": coverage_pct,
        "n_sim_mean": float(counts.mean()) if m else 0.0,
        "n_sim_max": int(counts.max()) if m else 0,
        "satisfied": m > 0 and mrt_actual <= mrt_minutes,
    }


def _feasible(sat_cov, selected, k_min, mrt_minutes, step_minutes):
    """判断一组被选卫星是否满足需求（用于贪心的'能删就删'改进）。"""
    counts = _counts_of(sat_cov, selected)
    covered = counts >= k_min
    mrt_steps = max(0, int(round(mrt_minutes / step_minutes)))
    return len(_deficient_runs(covered, mrt_steps)) == 0


# ==================== 2. 算法一：贪心 + 局部改进 ====================

def greedy_select(sat_cov, k_min, mrt_minutes, step_minutes):
    """贪心选星：每步挑"补上最多缺口时间片"的卫星，最后做'能删就删'改进。

    返回 (selected_indices, counts)。
    """
    n_sat = len(sat_cov)
    m = len(sat_cov[0]) if n_sat else 0
    mrt_steps = max(0, int(round(mrt_minutes / step_minutes)))
    selected: list[int] = []
    counts = np.zeros(m, dtype=int)

    for _ in range(n_sat):
        covered = counts >= k_min
        # 需求 = 处在"超长空窗"里的时间片（长度 > mrt）
        demand = np.zeros(m, dtype=bool)
        for a, b in _deficient_runs(covered, mrt_steps):
            demand[a:b] = True
        if not demand.any():
            break
        best, best_score = None, 0
        for j in range(n_sat):
            if j in selected:
                continue
            score = int(np.count_nonzero(demand & sat_cov[j]))
            if score > best_score:
                best, best_score = j, score
        if best is None or best_score == 0:
            break  # 剩下缺口没有任何星能补，需求无法完全满足
        selected.append(best)
        counts = counts + sat_cov[best]

    # 局部改进：能删就删（贪心往往多选，逐个尝试删掉仍满足的卫星）
    improved = True
    while improved:
        improved = False
        for j in list(selected):
            trial = [x for x in selected if x != j]
            if _feasible(sat_cov, trial, k_min, mrt_minutes, step_minutes):
                selected = trial
                improved = True
                break
    counts = _counts_of(sat_cov, selected)
    return selected, counts


# ==================== 3. 算法二：整数规划（精确最优） ====================

def ilp_select(sat_cov, k_min, mrt_minutes, step_minutes, time_limit=180):
    """整数规划精确选星（依赖 pulp + CBC）。

    数学形式（白话版见文件头注释）：
      变量 x_j ∈ {0,1}：是否选卫星 j；
      变量 y_i ∈ {0,1}：时间片 i 是否"达标"（覆盖数 >= K）；
      目标：min Σ x_j（卫星最少）；
      约束1：覆盖数 - K·y_i >= 0（y=1 时必须有 K 颗覆盖）；
      约束2：覆盖数 - M·y_i <= K-1（覆盖数达 K 时强制 y=1；M=卫星总数）；
      约束3：任意连续 (MRT/步长+1) 个时间片里至少 1 片达标（空窗 <= MRT）。
    返回 (selected, counts, status, note)；求解器缺失时 status="unsupported"。
    """
    try:
        import pulp
    except ImportError:
        return None, None, "unsupported", "未安装 pulp（pip install pulp），自动退回贪心。"
    n_sat = len(sat_cov)
    m = len(sat_cov[0]) if n_sat else 0
    if n_sat > 1500:
        return None, None, "skipped", "卫星数过多（>1500），整数规划太慢，自动退回贪心。"
    if m == 0:
        return [], np.zeros(0, dtype=int), "Optimal", ""
    mrt_steps = max(0, int(round(mrt_minutes / step_minutes)))
    W = mrt_steps + 1
    prob = pulp.LpProblem("satellite_selection", pulp.LpMinimize)
    x = [pulp.LpVariable(f"x{j}", 0, 1, cat=pulp.LpBinary) for j in range(n_sat)]
    y = [pulp.LpVariable(f"y{i}", 0, 1, cat=pulp.LpBinary) for i in range(m)]
    prob += pulp.lpSum(x)
    M = n_sat
    for i in range(m):
        cov_sum = pulp.lpSum(x[j] for j in range(n_sat) if sat_cov[j][i])
        prob += cov_sum - k_min * y[i] >= 0
        prob += cov_sum - M * y[i] <= k_min - 1
    for t in range(m - W + 1):
        prob += pulp.lpSum(y[t + k] for k in range(W)) >= 1
    prob.solve(pulp.PULP_CBC_CMD(msg=False, timeLimit=time_limit))
    status = pulp.LpStatus[prob.status]
    obj = pulp.value(prob.objective)
    if status in ("Optimal", "Not Solved") and obj is not None:
        selected = [j for j in range(n_sat) if pulp.value(x[j]) > 0.5]
        counts = _counts_of(sat_cov, selected)
        note = "最优（已证明）" if status == "Optimal" else "到达时间上限，给出当前最优可行解"
        return selected, counts, status, note
    return [], np.zeros(m, dtype=int), status, f"求解状态：{status}（可能需求本身无法满足）"


# ==================== 4. 汇总与报告 ====================

def _sat_detail(sats, windows_by_sat, selected, step_minutes, period_minutes):
    """选中卫星的明细表（名字/窗口数/总可见时长/覆盖率）。"""
    rows = []
    for j in selected:
        wins = [w for w in windows_by_sat[j]]
        total_s = sum((e - s).total_seconds() for s, e, _ in wins)
        rows.append({
            "卫星名": sats[j].name,
            "窗口数": len(wins),
            "总可见时长(分)": round(total_s / 60.0, 1),
            "覆盖率(%)": round(total_s / 60.0 / period_minutes * 100.0, 2) if period_minutes else 0.0,
        })
    return rows


def run_selection(sats, windows_by_sat, t0, t1, step_minutes=1, k_min=1,
                  mrt_minutes=30, min_event_minutes=0.0, method="both",
                  time_limit=180):
    """选星总入口：算覆盖矩阵 → 贪心/ILP → 统计 → 对比全池。

    返回 dict（供 GUI/CLI 展示与导出）：
      selected / selected_names / detail_rows / gaps / state_rows /
      summary 文本 / 全池对比 / 需求参数等。
    """
    dt_list = []
    t = t0
    while t < t1:
        dt_list.append(t)
        t += timedelta(minutes=step_minutes)
    if not dt_list:
        dt_list = [t0]
    sat_cov = build_slice_cover(sats, windows_by_sat, dt_list, min_event_minutes)
    n_sat = len(sats)
    period_minutes = max(1.0, (t1 - t0).total_seconds() / 60.0)

    res = {
        "t0": t0, "t1": t1, "dt_list": dt_list,
        "k_min": int(k_min), "mrt_minutes": float(mrt_minutes),
        "step_minutes": int(step_minutes),
        "min_event_minutes": float(min_event_minutes),
        "method": method, "n_sats_total": n_sat,
        "sats": sats, "sat_cov": sat_cov,
        "windows_by_sat": windows_by_sat,
    }

    # 全池基线（不选星、全部参与能达到的覆盖水平）
    full = evaluate_selection(sat_cov, list(range(n_sat)), k_min, mrt_minutes,
                              step_minutes, t0, t1)
    res["full"] = full

    # 贪心（始终可用）
    g_sel, g_counts = greedy_select(sat_cov, k_min, mrt_minutes, step_minutes)
    res["greedy"] = {
        "selected": g_sel,
        "eval": evaluate_selection(sat_cov, g_sel, k_min, mrt_minutes, step_minutes, t0, t1),
        "note": "",
    }

    # 整数规划（可选）
    ilp = None
    if method in ("both", "ilp"):
        sel_i, counts_i, status_i, note_i = ilp_select(
            sat_cov, k_min, mrt_minutes, step_minutes, time_limit=time_limit)
        if sel_i is not None:
            ilp = {
                "selected": sel_i,
                "eval": evaluate_selection(sat_cov, sel_i, k_min, mrt_minutes,
                                          step_minutes, t0, t1),
                "status": status_i, "note": note_i,
            }
    res["ilp"] = ilp

    # 最终采用：both 时优先 ILP（若可用），否则贪心
    final = None
    if ilp is not None and ilp["eval"]["satisfied"]:
        final = ilp
        final["algo"] = "ilp"
    else:
        final = res["greedy"]
        final["algo"] = "greedy"
        if ilp is not None and ilp["status"] not in ("Optimal", "Not Solved"):
            final["note"] = "整数规划不可行/超时，已用贪心结果。"
    res["selected"] = final["selected"]
    res["selected_names"] = [sats[j].name for j in final["selected"]]
    res["eval"] = final["eval"]
    res["algo"] = final["algo"]
    res["algo_note"] = final.get("note", "")

    # 选中卫星明细
    res["detail_rows"] = _sat_detail(sats, windows_by_sat, final["selected"],
                                     step_minutes, period_minutes)
    # 组合状态表（每分钟覆盖数 + 覆盖卫星名单）
    counts = final["eval"]["counts"]
    sel_set = set(final["selected"])
    state_rows = []
    for i, dt in enumerate(dt_list):
        names = [sats[j].name for j in sorted(sel_set) if sat_cov[j][i]]
        state_rows.append({
            "时间(UTC)": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "覆盖数": int(counts[i]),
            "覆盖卫星": "、".join(names),
        })
    res["state_rows"] = state_rows
    # 空窗表
    res["gaps"] = final["eval"]["gaps"]
    # 对比表（全池 vs 选中）
    res["compare_rows"] = [
        {"口径": "全池（不选星）", "卫星数": n_sat,
         "覆盖率(%)": round(full["coverage_pct"], 2),
         "MRT(分)": round(full["mrt_actual_minutes"], 1),
         "ART(分)": round(full["art_actual_minutes"], 1),
         "需求满足": "是" if full["satisfied"] else "否"},
        {"口径": f"选中（{res['algo']}）", "卫星数": len(final["selected"]),
         "覆盖率(%)": round(final["eval"]["coverage_pct"], 2),
         "MRT(分)": round(final["eval"]["mrt_actual_minutes"], 1),
         "ART(分)": round(final["eval"]["art_actual_minutes"], 1),
         "需求满足": "是" if final["eval"]["satisfied"] else "否"},
    ]
    res["summary"] = _summary_text(res)
    return res


def _summary_text(res):
    ev = res["eval"]
    lines = [
        f"需求：每 {res['mrt_minutes']:.0f} 分钟内至少 {res['k_min']} 颗星覆盖"
        f"（K={res['k_min']}，MRT≤{res['mrt_minutes']:.0f} 分钟，步长 {res['step_minutes']} 分钟）",
        f"卫星池 {res['n_sats_total']} 颗 → 选中 {len(res['selected'])} 颗（算法：{res['algo']}）",
        f"组合覆盖率 {ev['coverage_pct']:.1f}%，最大空窗 {ev['mrt_actual_minutes']:.1f} 分钟，"
        f"平均空窗 {ev['art_actual_minutes']:.1f} 分钟",
        f"同时可见：平均 {ev['n_sim_mean']:.1f} 颗，最多 {ev['n_sim_max']} 颗",
        "需求满足：是" if ev["satisfied"] else "需求未完全满足：以下空窗段超限",
    ]
    if res.get("algo_note"):
        lines.append(res["algo_note"])
    return "\n".join(lines)


# ==================== 5. CLI ====================

def main(argv=None):
    ap = argparse.ArgumentParser(description="选星决策：从卫星池里挑最少卫星满足覆盖需求")
    ap.add_argument("--collection", required=True, help="收藏夹名（卫星池来源）")
    ap.add_argument("--mode", choices=["circle", "polygon"], default="circle")
    ap.add_argument("--lat", type=float, default=30.25)
    ap.add_argument("--lon", type=float, default=120.15)
    ap.add_argument("--radius", type=float, default=5.0)
    ap.add_argument("--geojson", default=None, help="地区 GeoJSON 路径（polygon 模式）")
    ap.add_argument("--start", required=True, help="开始时间 UTC，如 2026-08-03 00:00:00")
    ap.add_argument("--end", required=True, help="结束时间 UTC")
    ap.add_argument("--step", type=int, default=1, help="时间步长（分钟）")
    ap.add_argument("--fov", type=float, default=30.0, help="视场半角（度）")
    ap.add_argument("--elev", type=float, default=10.0, help="最小仰角（度）")
    ap.add_argument("--grid", type=float, default=0.2, help="网格间距（度）")
    ap.add_argument("--k-min", type=int, default=1, help="最少同时可见数 K")
    ap.add_argument("--mrt", type=float, default=30.0, help="最大重访间隔（分钟）")
    ap.add_argument("--min-event", type=float, default=0.0, help="单次窗口最短时长（分钟，0=不限）")
    ap.add_argument("--method", choices=["greedy", "ilp", "both"], default="both")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "output"), help="输出目录")
    args = ap.parse_args(argv)

    import coverage_analysis as cov
    import satellite_loader as sl

    ts = sl.load.timescale(builtin=True)
    t, dt_list = cov.make_time_array(ts, args.start, args.end, args.step)
    recs = sl.resolve_collections_to_records([args.collection])
    if not recs:
        print("收藏夹没有解析到卫星。")
        return 1
    sats = sl.records_to_satellites(recs)
    cfg = {
        "region_mode": args.mode,
        "fov_half_angle_deg": args.fov,
        "min_elevation_deg": args.elev,
        "step_minutes": args.step,
        "refine_step_seconds": 10,
        "grid_spacing_deg": args.grid,
    }
    if args.mode == "circle":
        region = {"mode": "circle", "lat": args.lat, "lon": args.lon,
                  "radius_km": args.radius}
        cfg["target"] = {"lat": args.lat, "lon": args.lon, "radius_km": args.radius}
    else:
        polygon = cov.load_region_polygon(args.geojson or "data/geo/330000.json")
        glats, glons = cov.make_grid_points(polygon, args.grid)
        region = {"mode": "polygon", "lats": glats, "lons": glons}

    windows_by_sat = []
    for sat in sats:
        windows_by_sat.append(cov.analyze_one_satellite(sat, t, dt_list, region, cfg))
    t0 = dt_list[0]
    t1 = dt_list[-1]
    res = run_selection(sats, windows_by_sat, t0, t1,
                        step_minutes=args.step, k_min=args.k_min,
                        mrt_minutes=args.mrt, min_event_minutes=args.min_event,
                        method=args.method)
    print(res["summary"])
    print()
    print("推荐卫星：", "、".join(res["selected_names"]) or "（无）")
    if res["gaps"]:
        print("空窗：")
        for g in res["gaps"]:
            print(f"  {g['start_utc']} ~ {g['end_utc']}  共 {g['duration_minutes']} 分钟")
    else:
        print("空窗：无（满足需求）")
    return 0


def export_selection_xlsx(res, path, region_name="", extra_meta=None):
    """把选星结果导出为 Excel：说明 / 推荐卫星 / 组合状态 / 空窗表 / 对比。"""
    import pandas as pd
    meta = {
        "分析地区": region_name or "",
        "分析时段(UTC)": f"{res['t0']:%Y-%m-%d %H:%M:%S} ~ {res['t1']:%Y-%m-%d %H:%M:%S}",
        "时间步长(分钟)": res["step_minutes"],
        "需求-K(最少同时可见)": res["k_min"],
        "需求-MRT(最大重访间隔,分)": res["mrt_minutes"],
        "单次最短窗口(分)": res["min_event_minutes"],
        "算法": res["algo"] + (f"（{res['algo_note']}）" if res.get("algo_note") else ""),
        "卫星池规模": res["n_sats_total"],
        "选中卫星数": len(res["selected"]),
        "组合覆盖率(%)": round(res["eval"]["coverage_pct"], 2),
        "最大空窗(分)": round(res["eval"]["mrt_actual_minutes"], 1),
        "平均空窗(分)": round(res["eval"]["art_actual_minutes"], 1),
        "需求满足": "是" if res["eval"]["satisfied"] else "否",
        "推荐卫星": "、".join(res["selected_names"]),
    }
    if extra_meta:
        meta.update(extra_meta)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"项目": k, "内容": v} for k, v in meta.items()]).to_excel(
            writer, sheet_name="说明", index=False)
        pd.DataFrame(res["detail_rows"]).to_excel(writer, sheet_name="推荐卫星", index=False)
        pd.DataFrame(res["state_rows"]).to_excel(writer, sheet_name="组合状态", index=False)
        pd.DataFrame(res["gaps"]).to_excel(writer, sheet_name="空窗表", index=False)
        pd.DataFrame(res["compare_rows"]).to_excel(writer, sheet_name="对比", index=False)
if __name__ == "__main__":
    sys.exit(main())
