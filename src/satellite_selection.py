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
        "satisfied": m > 0 and coverage_pct > 0 and mrt_actual <= mrt_minutes,
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

def ilp_select(sat_cov, k_min, mrt_minutes, step_minutes, time_limit=180, exclude=None):
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
    for _ex in (exclude or []):
        prob += pulp.lpSum(x[j] for j in _ex) <= len(_ex) - 1
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


def select_max_coverage(sat_cov, n_limit, k_min, step_minutes, t0, t1):
    """目标模式②：给定 N 颗，贪心最大化组合覆盖率。

    每轮从候选里挑“使覆盖时间片增加最多”的一颗（最大边际收益），
    直到选满 N 颗或无法再增加覆盖。返回 (selected, counts)。
    说明：覆盖率为“被覆盖时间片占比”，与“需求是否达标”是两种口径。
    """
    n_sat = len(sat_cov)
    m = len(sat_cov[0]) if n_sat else 0
    n_limit = max(0, min(int(n_limit), n_sat))
    if m == 0 or n_limit == 0:
        return [], np.zeros(m, dtype=int)
    indices = [np.flatnonzero(sat_cov[j]) for j in range(n_sat)]
    usable = [j for j in range(n_sat) if indices[j].size > 0]
    counts = np.zeros(m, dtype=int)
    selected = []
    selected_set = set()
    covered_total = 0
    for _ in range(n_limit):
        best_j, best_gain = -1, 0
        for j in usable:
            if j in selected_set:
                continue
            ids = indices[j]
            gain = int((counts[ids] == k_min - 1).sum())
            if gain > best_gain:
                best_gain, best_j = gain, j
                if best_gain == m - covered_total:
                    break
        if best_j < 0 or best_gain <= 0:
            break
        selected.append(best_j)
        selected_set.add(best_j)
        ids = indices[best_j]
        counts[ids] += 1
        covered_total += best_gain
    # 已达 100% 覆盖但名额未满时，按单星覆盖片数降序补足（满足“给定 N 颗”的冗余需求）
    if len(selected) < n_limit:
        rest = [j for j in usable if j not in selected_set]
        rest.sort(key=lambda j: -int(indices[j].size))
        for j in rest:
            if len(selected) >= n_limit:
                break
            selected.append(j)
            selected_set.add(j)
            _ids = indices[j]
            counts[_ids] += 1
    return selected, counts


def select_min_gap(sat_cov, n_limit, k_min, step_minutes, t0, t1, mode="max"):
    """给定 N 颗，贪心最小化空窗（MMRT=mode"max" / MART=mode"avg"）。

    每轮从“覆盖当前目标空窗段”的候选星里，选使目标指标（最大/平均空窗）最短的那颗；
    直到选满 N 颗或空窗不再缩短。返回 (selected, counts)。
    """
    n_sat = len(sat_cov)
    m = len(sat_cov[0]) if n_sat else 0
    n_limit = max(0, min(int(n_limit), n_sat))
    if m == 0 or n_limit == 0:
        return [], np.zeros(m, dtype=int)
    sat_arr = np.asarray(sat_cov, dtype=bool)
    selected = []
    selected_set = set()
    counts = np.zeros(m, dtype=int)
    k = k_min

    def _gaps(cnts):
        covered = cnts >= k
        gaps = []
        i = 0
        while i < m:
            if not covered[i]:
                j = i
                while j < m and not covered[j]:
                    j += 1
                gaps.append((i, j))
                i = j
            else:
                i += 1
        return gaps

    for _ in range(n_limit):
        gaps = _gaps(counts)
        if not gaps:
            break
        if mode == "max":
            target = max(g[1] - g[0] for g in gaps)
            cand_mask = np.zeros(m, dtype=bool)
            for a, b in gaps:
                if b - a == target:
                    cand_mask[a:b] = True
        else:
            cand_mask = ~(counts >= k)
        cand = [j for j in range(n_sat) if j not in selected_set
                and sat_arr[j].any() and bool((sat_arr[j] & cand_mask).any())]
        if not cand:
            break
        best_j, best_score = -1, None
        for j in cand:
            trial = counts.copy()
            trial[sat_arr[j]] += 1
            g2 = _gaps(trial)
            if not g2:
                best_j, best_score = j, 0.0
                break
            if mode == "max":
                score = float(max(x[1] - x[0] for x in g2))
            else:
                score = float(np.mean([x[1] - x[0] for x in g2]))
            if best_score is None or score < best_score:
                best_score, best_j = score, j
        if best_j < 0:
            break
        selected.append(best_j)
        selected_set.add(best_j)
        counts[sat_arr[best_j]] += 1
    # 目标（0 空窗/不可再缩短）已达成但名额未满时，按覆盖片数降序补足（与 max_coverage 一致）
    if len(selected) < n_limit:
        rest = [j for j in range(n_sat) if j not in selected_set and sat_arr[j].any()]
        rest.sort(key=lambda j: -int(sat_arr[j].sum()))
        for j in rest:
            if len(selected) >= n_limit:
                break
            selected.append(j)
            selected_set.add(j)
            counts[sat_arr[j]] += 1
    return selected, counts


def _state_rows(sats, sat_cov, counts, sel_set, dt_list):
    """组合状态表：每个时间片的覆盖数 + 覆盖卫星名单。"""
    rows = []
    for i, dt in enumerate(dt_list):
        names = [sats[j].name for j in sorted(sel_set) if sat_cov[j][i]]
        rows.append({
            "时间(UTC)": dt.strftime("%Y-%m-%d %H:%M:%S"),
            "覆盖数": int(counts[i]),
            "覆盖卫星": "、".join(names),
        })
    return rows


def enumerate_optimal_solutions(sat_cov, sats, k_min, mrt_minutes, step_minutes,
                                max_solutions=10, time_limit=60):
    """列出全部最优方案（目标=最少颗数满足需求）。

    策略：
    1) 存在 >= K 颗“全程星”（单颗即 100% 覆盖）时，全部最优 = 任选 K 颗全程星的组合
       （例：杭州 + 遥感系列 K=1 → 4 种；K=2 → 6 种）；
    2) 否则用整数规划 + “排除已得解”（no-good cut）迭代收集，最多 max_solutions 个。
    返回 (solutions, note)：
      solutions: [{"sat_names": [...], "n": k, "coverage_pct": float}, ...]
    """
    n_sat = len(sat_cov)
    m = len(sat_cov[0]) if n_sat else 0
    if not sats:
        sats = [type("S", (), {"name": f"SAT-{j}"})() for j in range(n_sat)]
    _t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _t1 = _t0 + timedelta(minutes=max(1, m * max(1, step_minutes)))

    def _sol(sel):
        ev = evaluate_selection(sat_cov, sel, k_min, mrt_minutes, step_minutes, _t0, _t1)
        return {"sat_names": [sats[j].name for j in sel],
                "n": len(sel), "coverage_pct": round(ev["coverage_pct"], 2)}

    full_sats = [j for j in range(n_sat) if sat_cov[j].all()]
    if len(full_sats) >= k_min:
        import itertools
        solutions = []
        for combo in itertools.combinations(full_sats, k_min):
            solutions.append(_sol(list(combo)))
            if len(solutions) >= max_solutions:
                break
        note = (f"存在 {len(full_sats)} 颗全程星（单颗即 100% 覆盖）；"
                f"K={k_min} 时全部最优方案 = 任选 {k_min} 颗全程星的组合"
                f"（共 {len(solutions)} 种）")
        return solutions, note

    try:
        import pulp
    except ImportError:
        g_sel, _ = greedy_select(sat_cov, k_min, mrt_minutes, step_minutes)
        return [_sol(g_sel)], "未安装 pulp，仅给出一个贪心方案"
    if n_sat > 1500:
        g_sel, _ = greedy_select(sat_cov, k_min, mrt_minutes, step_minutes)
        return [_sol(g_sel)], "卫星数>1500，最优枚举成本过高，仅给出一个贪心方案"

    banned = []
    solutions = []
    for _ in range(max_solutions):
        sel, _cnt, status, _n2 = ilp_select(sat_cov, k_min, mrt_minutes,
                                            step_minutes, time_limit=time_limit,
                                            exclude=banned)
        if sel is None or status != "Optimal" or not sel:
            break
        if solutions and len(sel) != solutions[0]["n"]:
            break
        solutions.append(_sol(sel))
        banned.append(sel)
    note = (f"整数规划迭代收集到 {len(solutions)} 个最优方案"
            + ("（可能还有更多）" if len(solutions) >= max_solutions else ""))
    return solutions, note


def run_selection(sats, windows_by_sat, t0, t1, step_minutes=1, k_min=1,
                  mrt_minutes=30, min_event_minutes=0.0, method="both",
                  time_limit=180, req_mode="continuous", objective="min_sats",
                  n_sats_limit=None, max_solutions=10):
    """选星总入口：算覆盖矩阵 → 贪心/ILP → 统计 → 对比全池。

    req_mode: "continuous"=连续覆盖（无空窗，每个时间片都达标）；
              "revisit"=重访（允许最长 MRT 分钟无覆盖）。
    objective: "min_sats"=最少颗数满足需求（默认）；
               "max_coverage"=给定 n_sats_limit 颗最大化组合覆盖率。
    返回 dict（供 GUI/CLI 展示与导出），含：
      req_mode / objective / mrt_minutes（用户口径）/ effective_mrt_minutes（实际判定用）
      full（全池基线）/ feasibility（连续覆盖物理上限）/ optimal_solutions（全部最优方案）
      selected / selected_names / detail_rows / gaps / state_rows / compare_rows / summary
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

    eff_mrt = 0.0 if req_mode in ("continuous", "any") else float(mrt_minutes)
    res = {
        "t0": t0, "t1": t1, "dt_list": dt_list,
        "k_min": int(k_min),
        "mrt_minutes": float(mrt_minutes),
        "effective_mrt_minutes": eff_mrt,
        "req_mode": req_mode,
        "objective": objective,
        "n_sats_limit": int(n_sats_limit) if n_sats_limit else None,
        "step_minutes": int(step_minutes),
        "min_event_minutes": float(min_event_minutes),
        "method": method, "n_sats_total": n_sat,
        "sats": sats, "sat_cov": sat_cov,
        "windows_by_sat": windows_by_sat,
    }

    # 全池基线（不选星、全部参与能达到的覆盖水平 = 物理上限）
    full = evaluate_selection(sat_cov, list(range(n_sat)), k_min, eff_mrt,
                              step_minutes, t0, t1)
    res["full"] = full
    if req_mode == "continuous" and not full["satisfied"]:
        res["feasibility"] = {
            "possible": False,
            "full_coverage_pct": round(full["coverage_pct"], 2),
            "full_max_gap_minutes": round(full["mrt_actual_minutes"], 1),
            "gaps": full["gaps"],
            "message": ("连续覆盖（全程无空窗）在该卫星池下物理不可行："
                        "即使全部卫星一起上，最长空窗仍达 "
                        f"{full['mrt_actual_minutes']:.0f} 分钟，覆盖率仅 "
                        f"{full['coverage_pct']:.1f}%。"
                        "这是星座轨道设计问题，不是选星算法问题。"
                        "可选出路：①候选池加入同步/高轨遥感星（如高分四号、遥感四十一号，"
                        "对东半球目标 1 颗即可）；②缩小目标区域或缩短时段；"
                        "③改用“重访（每 X 分钟至少一次）”需求模式。"),
        }
    else:
        res["feasibility"] = {"possible": full["satisfied"]}

    # ---- 目标模式②：预算型任务（给定 N 颗：覆盖率最大 / 最大空窗最小 / 平均空窗最小）----
    if objective in ("max_coverage", "min_max_gap", "min_avg_gap"):
        n_lim = int(n_sats_limit) if n_sats_limit else min(10, n_sat)
        if objective == "max_coverage":
            sel_mc, counts_mc = select_max_coverage(sat_cov, n_lim, k_min, step_minutes, t0, t1)
            algo_note = (f"目标：给定 {n_lim} 颗最大化组合覆盖率"
                         f"（贪心最大边际收益；覆盖率=被覆盖时间片占比）")
        elif objective == "min_max_gap":
            sel_mc, counts_mc = select_min_gap(sat_cov, n_lim, k_min, step_minutes, t0, t1, mode="max")
            algo_note = f"目标：给定 {n_lim} 颗最小化最大空窗（MMRT，贪心）"
        else:
            sel_mc, counts_mc = select_min_gap(sat_cov, n_lim, k_min, step_minutes, t0, t1, mode="avg")
            algo_note = f"目标：给定 {n_lim} 颗最小化平均空窗（MART，贪心）"
        res["greedy"] = None
        res["ilp"] = None
        res["selected"] = sel_mc
        res["selected_names"] = [sats[j].name for j in sel_mc]
        res["eval"] = evaluate_selection(sat_cov, sel_mc, k_min, eff_mrt,
                                         step_minutes, t0, t1)
        res["algo"] = "greedy-budget"
        res["algo_note"] = algo_note
        res["detail_rows"] = _sat_detail(sats, windows_by_sat, sel_mc,
                                         step_minutes, period_minutes)
        counts = res["eval"]["counts"]
        sel_set = set(sel_mc)
        res["state_rows"] = _state_rows(sats, sat_cov, counts, sel_set, dt_list)
        res["gaps"] = res["eval"]["gaps"]
        res["compare_rows"] = [
            {"口径": "全池（不选星）", "卫星数": n_sat,
             "覆盖率(%)": round(full["coverage_pct"], 2),
             "MRT(分)": round(full["mrt_actual_minutes"], 1),
             "ART(分)": round(full["art_actual_minutes"], 1),
             "需求满足": "—"},
            {"口径": f"选中（目标 {n_lim} 颗）", "卫星数": len(sel_mc),
             "覆盖率(%)": round(res["eval"]["coverage_pct"], 2),
             "MRT(分)": round(res["eval"]["mrt_actual_minutes"], 1),
             "ART(分)": round(res["eval"]["art_actual_minutes"], 1),
             "需求满足": "—"},
        ]
        res["optimal_solutions"] = []
        res["optimal_note"] = "预算型任务（给定 N 颗）不做“最少颗数最优方案”枚举。"
        res["summary"] = _summary_text(res)
        return res

    # ---- 目标模式①：最少颗数满足需求 ----
    g_sel, g_counts = greedy_select(sat_cov, k_min, eff_mrt, step_minutes)
    res["greedy"] = {
        "selected": g_sel,
        "eval": evaluate_selection(sat_cov, g_sel, k_min, eff_mrt, step_minutes, t0, t1),
        "note": "",
    }
    ilp = None
    if method in ("both", "ilp"):
        sel_i, counts_i, status_i, note_i = ilp_select(
            sat_cov, k_min, eff_mrt, step_minutes, time_limit=time_limit)
        if sel_i is not None:
            ilp = {
                "selected": sel_i,
                "eval": evaluate_selection(sat_cov, sel_i, k_min, eff_mrt,
                                          step_minutes, t0, t1),
                "status": status_i, "note": note_i,
            }
    res["ilp"] = ilp

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

    res["detail_rows"] = _sat_detail(sats, windows_by_sat, final["selected"],
                                     step_minutes, period_minutes)
    counts = final["eval"]["counts"]
    sel_set = set(final["selected"])
    res["state_rows"] = _state_rows(sats, sat_cov, counts, sel_set, dt_list)
    res["gaps"] = final["eval"]["gaps"]
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

    # 全部最优方案（仅最少颗数目标；无全程星时用 ILP no-good cut 收集）
    opt_sols, opt_note = enumerate_optimal_solutions(
        sat_cov, sats, k_min, eff_mrt, step_minutes,
        max_solutions=max_solutions, time_limit=time_limit)
    res["optimal_solutions"] = opt_sols
    res["optimal_note"] = opt_note

    res["summary"] = _summary_text(res)
    return res


def _summary_text(res):
    ev = res["eval"]
    if res["req_mode"] == "continuous":
        req_label = "连续覆盖（无空窗）"
    elif res["req_mode"] == "revisit":
        req_label = (f"重访：每 {res['mrt_minutes']:.0f} 分钟内至少一次"
                     f"（允许最长 {res['mrt_minutes']:.0f} 分钟无覆盖）")
    else:
        req_label = "不限（只看指标）"
    obj_map = {
        "min_sats": "最少颗数满足需求",
        "max_coverage": f"给定 {res.get('n_sats_limit') or 0} 颗最大化覆盖率",
        "min_max_gap": f"给定 {res.get('n_sats_limit') or 0} 颗最小化最大空窗（MMRT）",
        "min_avg_gap": f"给定 {res.get('n_sats_limit') or 0} 颗最小化平均空窗（MART）",
    }
    obj_label = obj_map.get(res["objective"], res["objective"])
    lines = [
        f"需求模式：{req_label}（K={res['k_min']}，步长 {res['step_minutes']} 分钟）",
        f"目标模式：{obj_label}",
        f"卫星池 {res['n_sats_total']} 颗 → 选中 {len(res['selected'])} 颗（算法：{res['algo']}）",
        f"组合覆盖率 {ev['coverage_pct']:.1f}%，最大空窗 {ev['mrt_actual_minutes']:.1f} 分钟，"
        f"平均空窗 {ev['art_actual_minutes']:.1f} 分钟",
        f"同时可见：平均 {ev['n_sim_mean']:.1f} 颗，最多 {ev['n_sim_max']} 颗",
    ]
    if res["req_mode"] == "any":
        lines.append("达标判定：不适用（本任务只看覆盖率/空窗指标）")
    else:
        lines.append("需求满足：是" if ev["satisfied"] else "需求未完全满足：以下空窗段超限")
    if not ev["satisfied"] and res.get("feasibility") and not res["feasibility"].get("possible", True):
        _msg = res["feasibility"].get("message", "")
        if _msg:
            lines.append("[物理上限] " + _msg)
    if not ev["satisfied"] and res["req_mode"] != "any":
        if res["objective"] == "min_sats":
            lines.append("[尽力而为] 该组合已用最少卫星把空窗压到该池子能做到的最小。")
        else:
            lines.append("[尽力而为] 该组合已是该预算下的最优选择（覆盖率/空窗已尽量优化）。")
    if res.get("algo_note"):
        lines.append(res["algo_note"])
    if res.get("optimal_note"):
        lines.append("[全部最优方案] " + res["optimal_note"])
    return "\n".join(lines)


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
    ap.add_argument("--req-mode", choices=["continuous", "revisit", "any"], default="continuous",
                    help="需求模式：continuous=连续覆盖；revisit=重访；any=不限（只看指标）")
    ap.add_argument("--objective", choices=["min_sats", "max_coverage", "min_max_gap", "min_avg_gap"],
                    default="min_sats",
                    help="目标模式：min_sats=最少颗数；max_coverage=给定 N 颗最大化覆盖率；"
                         "min_max_gap=给定 N 颗最小化最大空窗；min_avg_gap=给定 N 颗最小化平均空窗")
    ap.add_argument("--n-sats", type=int, default=None,
                    help="预算型目标的给定颗数 N")
    ap.add_argument("--list-optimal", action="store_true",
                    help="在命令行打印全部最优方案")
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
                        method=args.method, req_mode=args.req_mode,
                        objective=args.objective, n_sats_limit=args.n_sats,
                        max_solutions=10)
    print(res["summary"])
    print()
    print("推荐卫星：", "、".join(res["selected_names"]) or "（无）")
    if args.list_optimal and res.get("optimal_solutions"):
        print()
        print("全部最优方案：")
        for _k, _sol in enumerate(res["optimal_solutions"], 1):
            print(f"  方案{_k}（{len(_sol['sat_names'])} 颗，覆盖率 {_sol['coverage_pct']}%）："
                  f"{'、'.join(_sol['sat_names'])}")
        if res.get("optimal_note"):
            print("  注：" + res["optimal_note"])
    if res["gaps"]:
        print("空窗：")
        for g in res["gaps"]:
            print(f"  {g['start_utc']} ~ {g['end_utc']}  共 {g['duration_minutes']} 分钟")
    else:
        print("空窗：无（满足需求）")
    return 0


def export_selection_xlsx(res, path, region_name="", extra_meta=None):
    """把选星结果导出为 Excel：说明 / 推荐卫星 / 组合状态 / 空窗表 / 对比 / 全部最优方案。"""
    import pandas as pd
    if res["req_mode"] == "continuous":
        req_label = "连续覆盖（无空窗）"
    elif res["req_mode"] == "revisit":
        req_label = f"重访（MRT≤{res['mrt_minutes']:.0f} 分钟）"
    else:
        req_label = "不限（只看指标）"
    obj_map = {
        "min_sats": "最少颗数满足需求",
        "max_coverage": f"给定 {res.get('n_sats_limit') or 0} 颗最大化覆盖率",
        "min_max_gap": f"给定 {res.get('n_sats_limit') or 0} 颗最小化最大空窗（MMRT）",
        "min_avg_gap": f"给定 {res.get('n_sats_limit') or 0} 颗最小化平均空窗（MART）",
    }
    obj_label = obj_map.get(res["objective"], res["objective"])
    meta = {
        "分析地区": region_name or "",
        "分析时段(UTC)": f"{res['t0']:%Y-%m-%d %H:%M:%S} ~ {res['t1']:%Y-%m-%d %H:%M:%S}",
        "时间步长(分钟)": res["step_minutes"],
        "需求模式": req_label,
        "目标模式": obj_label,
        "需求-K(最少同时可见)": res["k_min"],
        "需求-MRT(最大重访间隔,分)": ("0（连续覆盖）" if res["req_mode"] == "continuous"
                                       else ("不限" if res["req_mode"] == "any"
                                             else res["mrt_minutes"])),
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
    opt_df = pd.DataFrame(res.get("optimal_solutions") or [])
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([{"项目": k, "内容": v} for k, v in meta.items()]).to_excel(
            writer, sheet_name="说明", index=False)
        pd.DataFrame(res["detail_rows"]).to_excel(writer, sheet_name="推荐卫星", index=False)
        pd.DataFrame(res["state_rows"]).to_excel(writer, sheet_name="组合状态", index=False)
        pd.DataFrame(res["gaps"]).to_excel(writer, sheet_name="空窗表", index=False)
        pd.DataFrame(res["compare_rows"]).to_excel(writer, sheet_name="对比", index=False)
        if opt_df.empty:
            pd.DataFrame([{"说明": res.get("optimal_note", "无多个最优方案")}]).to_excel(
                writer, sheet_name="全部最优方案", index=False)
        else:
            _o = opt_df.copy()
            _o["卫星清单"] = _o["sat_names"].apply(lambda x: "、".join(x))
            _o = _o[["n", "coverage_pct", "卫星清单"]]
            _o.columns = ["卫星数", "覆盖率(%)", "卫星清单"]
            _o.to_excel(writer, sheet_name="全部最优方案", index=False)

if __name__ == "__main__":
    sys.exit(main())
