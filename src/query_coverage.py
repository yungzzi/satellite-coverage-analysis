# -*- coding: utf-8 -*-
"""
卫星覆盖查询工具（交互模式 + 参数模式 + Excel 导出）
====================================================
数据来源：先运行主程序 src/coverage_analysis.py，生成 output/ 下的
coverage_windows.csv / coverage_state.csv / coverage_gaps.csv /
coverage_summary.csv / analysis_meta.json，本工具只读这些文件。

用法：
  交互模式（菜单循环，可反复查询，0/q 退出）：
    python src/query_coverage.py

  参数模式（一次给全参数，供脚本调用，自动跑完退出）：
    python src/query_coverage.py --start "2026-08-03 12:00" --end "14:00"
    python src/query_coverage.py --export xlsx                     # 导出整体
    python src/query_coverage.py --start ... --end ... --export xlsx --out 我的结果.xlsx

时间统一用 UTC（北京时间 = UTC+8）。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coverage_analysis as ca

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(PROJ, "output")
CSV_ENCODING = "utf-8-sig"


def _as_utc(series):
    """把时间列统一成 UTC 带时区（CSV 里有的带 +00:00、有的不带）"""
    if series.dt.tz is None:
        return series.dt.tz_localize("UTC")
    return series.dt.tz_convert("UTC")


# ==================== 数据读取 ====================

def load_all():
    """读主程序生成的元信息 + 各张表，返回 (meta, windows, state, summary, gaps)"""
    meta_path = os.path.join(OUT, "analysis_meta.json")
    if not os.path.exists(meta_path):
        print("未找到 output/analysis_meta.json，请先运行主程序：")
        print("  .venv\\Scripts\\python src\\coverage_analysis.py")
        sys.exit(1)
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    windows = pd.read_csv(os.path.join(OUT, "coverage_windows.csv"), encoding=CSV_ENCODING,
                          parse_dates=["window_start_utc", "window_end_utc"])
    windows["window_start_utc"] = _as_utc(windows["window_start_utc"])
    windows["window_end_utc"] = _as_utc(windows["window_end_utc"])
    state = pd.read_csv(os.path.join(OUT, "coverage_state.csv"), encoding=CSV_ENCODING,
                        parse_dates=["start_utc", "end_utc"])
    state["start_utc"] = _as_utc(state["start_utc"])
    state["end_utc"] = _as_utc(state["end_utc"])
    summary = pd.read_csv(os.path.join(OUT, "coverage_summary.csv"), encoding=CSV_ENCODING)
    gaps_path = os.path.join(OUT, "coverage_gaps.csv")
    gaps = (pd.read_csv(gaps_path, encoding=CSV_ENCODING,
                        parse_dates=["start_utc", "end_utc"])
            if os.path.exists(gaps_path) else pd.DataFrame())
    if not gaps.empty:
        gaps["start_utc"] = _as_utc(gaps["start_utc"])
        gaps["end_utc"] = _as_utc(gaps["end_utc"])
    return meta, windows, state, summary, gaps


# ==================== 时间解析 ====================

def parse_time(s, default_date):
    """解析用户输入的时间。支持 '2026-08-03 12:00' 或 '12:00'（默认取分析开始日），UTC"""
    s = s.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%H:%M:%S", "%H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            break
        except ValueError:
            continue
    else:
        raise ValueError(f"无法解析「{s}」，支持 2026-08-03 12:00 或 12:00 格式")
    if dt.tzinfo is None:
        if fmt.startswith("%H"):
            dt = dt.replace(year=default_date.year, month=default_date.month,
                            day=default_date.day)
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def analysis_bounds(meta):
    t0 = datetime.strptime(meta["start_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(meta["end_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return t0, t1


# ==================== 查询 ====================

def _sort_state_satellites(state, windows):
    """把状态表每段名单按"该范围（查询段）内每星覆盖总时长"降序重排"""
    if state.empty or windows.empty:
        return state
    totals = windows.groupby("satellite")["duration_s"].sum().to_dict()

    def key(n):
        return (-totals.get(n, 0), n)

    out = state.copy()
    out["satellites"] = out["satellites"].apply(
        lambda s: ", ".join(sorted([x.strip() for x in str(s).split(",") if x.strip()],
                                   key=key)))
    return out


def query_time_range(start, end, windows, state, gaps):
    """把各张表裁剪到 [start, end]，返回 {"windows","state","gaps"} 三个 DataFrame"""
    def clip(df, s_col, e_col):
        if df.empty:
            return df.copy()
        out = df[(df[e_col] >= start) & (df[s_col] <= end)].copy()
        if out.empty:
            return out
        out[s_col] = out[s_col].clip(lower=start)
        out[e_col] = out[e_col].clip(upper=end)
        out["duration_s"] = (out[e_col] - out[s_col]).dt.total_seconds().astype(int)
        out = out[out["duration_s"] > 0]   # 去掉只擦到查询边界的零长片段
        return out

    w = clip(windows, "window_start_utc", "window_end_utc")
    s = _sort_state_satellites(clip(state, "start_utc", "end_utc"), w)
    return {
        "windows": w,
        "state": s,
        "gaps": clip(gaps, "start_utc", "end_utc") if not gaps.empty else gaps.copy(),
    }


def summary_from_windows(w, meta, period_seconds):
    """由窗口 DataFrame 重新聚合卫星汇总（供导出查询段时使用，按查询段口径算覆盖率）"""
    cfg = {"step_minutes": meta["step_minutes"],
           "refine_step_seconds": meta["refine_step_seconds"]}
    tuples = [(r.satellite, r.window_start_utc, r.window_end_utc, r.max_covered_ratio)
              for r in w.itertuples()]
    return ca.summarize_windows(tuples, cfg, period_seconds)


def show_query_result(res, meta):
    """控制台展示一次查询的结果"""
    s, w, g = res["state"], res["windows"], res["gaps"]
    print("\n" + "-" * 66)
    print(f"查询结果（UTC）：{len(s)} 个状态段，{len(w)} 个覆盖窗口，{len(g)} 段空窗")
    if s.empty:
        print("该时段内没有任何覆盖。")
    else:
        print(s[["start_utc", "end_utc", "n_satellites", "satellites"]].to_string(index=False))
    if not g.empty:
        print("该时段内的空窗（前 10 段）：")
        print(g[["start_utc", "end_utc", "duration_s"]].head(10).to_string(index=False))
    print("-" * 66)


# ==================== Excel 导出 ====================

def export_excel(meta, state, windows, summary, gaps, out_path, scope_label):
    """把各张表打包成一个 Excel（多 sheet），给不懂程序的用户看"""
    def _strip_tz(df):
        """Excel 不支持时区，导出前把 UTC 时区去掉（墙钟时间不变）"""
        out = df.copy()
        for col in out.columns:
            if isinstance(out[col].dtype, pd.DatetimeTZDtype):
                out[col] = out[col].dt.tz_localize(None)
        return out

    with pd.ExcelWriter(out_path, engine="openpyxl") as writer:
        info = pd.DataFrame({
            "项目": ["分析地区", "分析时段(UTC)", "时间步长(分钟)", "边界精化(秒)",
                     "视场半角(°)", "最小仰角(°)", "网格间距(°)",
                     "参与卫星数", "有覆盖卫星数", "生成时间(UTC)",
                     "导出范围", "口径说明"],
            "内容": [meta["region_name"],
                     f"{meta['start_utc']} ~ {meta['end_utc']}",
                     meta["step_minutes"], meta["refine_step_seconds"],
                     meta["fov_half_angle_deg"], meta["min_elevation_deg"],
                     meta["grid_spacing_deg"], meta["n_satellites_total"],
                     meta["n_satellites_covered"], meta["generated_utc"],
                     scope_label,
                     "覆盖 = 至少 1 个网格代表点在视场圆内且仰角≥10°；时间为 UTC（北京时间 = UTC+8）；"
                     "卫星汇总 coverage_pct = 该星总覆盖时长 ÷ 本表时间范围"],
        })
        info.to_excel(writer, sheet_name="说明", index=False)
        _strip_tz(state).to_excel(writer, sheet_name="状态表", index=False)
        _strip_tz(windows).to_excel(writer, sheet_name="窗口明细", index=False)
        summary.to_excel(writer, sheet_name="卫星汇总", index=False)
        if gaps is not None and not gaps.empty:
            _strip_tz(gaps).to_excel(writer, sheet_name="空窗表", index=False)
        else:
            # 空窗表始终生成：没有空窗也明确写出来，避免用户误以为漏了
            pd.DataFrame({"提示": ["该时段无空窗：全程至少一颗星可覆盖"]}).to_excel(
                writer, sheet_name="空窗表", index=False)
    return out_path


def default_xlsx_path(scope):
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(OUT, f"coverage_{scope}_{stamp}.xlsx")


# ==================== 交互模式 ====================

def ask_range(meta):
    """让用户输入查询时间段的起止（UTC），校验后返回 (start, end)"""
    t0, t1 = analysis_bounds(meta)
    while True:
        s = input(f"开始时间（UTC，如 {t0:%Y-%m-%d %H:%M}；直接回车=分析开始）：").strip()
        e = input(f"结束时间（UTC，如 {t1:%Y-%m-%d %H:%M}；直接回车=分析结束）：").strip()
        try:
            st = parse_time(s, t0) if s else t0
            et = parse_time(e, t0) if e else t1
        except ValueError as ex:
            print(f"  输入有误：{ex}")
            continue
        st, et = max(st, t0), min(et, t1)
        if et <= st:
            print("  结束时间必须晚于开始时间（且落在分析时段内），请重输。")
            continue
        return st, et


def interactive_menu(meta, windows, state, summary, gaps):
    """交互模式：菜单循环，可反复查询，0/q 退出"""
    t0, t1 = analysis_bounds(meta)
    last_query = None
    print("=" * 60)
    print("卫星覆盖查询工具（交互模式）")
    print(f"地区：{meta['region_name']}；分析时段：{t0} ~ {t1}（UTC）")
    print("时间均为 UTC（北京时间 = UTC+8）。")
    print("=" * 60)
    while True:
        print("\n请选择：")
        print("  [1] 查询时间段（可反复查询）")
        print("  [2] 导出 Excel（当前查询段 / 整个时段）")
        print("  [3] 查看整体文字摘要")
        print("  [0] 退出")
        choice = input("> ").strip()
        if choice in ("0", "q", "Q", "退出"):
            print("已退出。")
            break
        if choice == "1":
            st, et = ask_range(meta)
            res = query_time_range(st, et, windows, state, gaps)
            last_query = (st, et, res)
            show_query_result(res, meta)
        elif choice == "2":
            scope = input("导出范围：[1]当前查询段  [2]整个时段  [0]取消 > ").strip()
            if scope == "1" and last_query is not None:
                st, et, res = last_query
                frames = res
                frames["summary"] = summary_from_windows(
                    res["windows"], meta, (et - st).total_seconds())
                label = f"查询段 {st:%Y-%m-%d %H:%M} ~ {et:%Y-%m-%d %H:%M}"
                out = default_xlsx_path("query")
            elif scope == "2":
                frames = {"state": state, "windows": windows,
                          "summary": summary, "gaps": gaps}
                label = "整个时段"
                out = default_xlsx_path("all")
            elif scope == "0":
                continue
            else:
                print("  尚未查询过，或输入无效；已取消导出。")
                continue
            export_excel(meta, frames["state"], frames["windows"], frames["summary"],
                         frames.get("gaps", pd.DataFrame()), out, label)
            print(f"  已导出：{out}")
        elif choice == "3":
            txt_path = os.path.join(OUT, "report_summary.txt")
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8") as f:
                    print("\n" + f.read())
            else:
                print("  未找到 report_summary.txt，请先运行主程序。")
        else:
            print("  无效输入，请输入 0~3。")


# ==================== 参数模式 ====================

def run_params(meta, windows, state, summary, gaps, args):
    t0, t1 = analysis_bounds(meta)
    if args.start or args.end:
        st = parse_time(args.start, t0) if args.start else t0
        et = parse_time(args.end, t0) if args.end else t1
        st, et = max(st, t0), min(et, t1)
        if et <= st:
            print("错误：结束时间不晚于开始时间。")
            sys.exit(2)
        res = query_time_range(st, et, windows, state, gaps)
        show_query_result(res, meta)
        scope_label = f"查询段 {st:%Y-%m-%d %H:%M} ~ {et:%Y-%m-%d %H:%M}"
        out_frames = {"state": res["state"], "windows": res["windows"],
                      "summary": summary_from_windows(res["windows"], meta,
                                                      (et - st).total_seconds()),
                      "gaps": res["gaps"]}
        default_scope = "query"
    else:
        scope_label = "整个时段"
        out_frames = {"state": state, "windows": windows,
                      "summary": summary, "gaps": gaps}
        default_scope = "all"
        print(f"整体概况：共 {len(windows)} 个窗口，{len(state)} 个状态段，"
              f"{len(gaps)} 段空窗。")

    if args.export == "xlsx":
        out = args.out or default_xlsx_path(default_scope)
        export_excel(meta, out_frames["state"], out_frames["windows"],
                     out_frames["summary"], out_frames["gaps"], out, scope_label)
        print(f"已导出 Excel：{out}")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="卫星覆盖查询工具：交互菜单，或 --start/--end 参数模式（供脚本调用）")
    parser.add_argument("--start", help="开始时间（UTC，如 2026-08-03 12:00 或 12:00）")
    parser.add_argument("--end", help="结束时间（UTC）")
    parser.add_argument("--export", choices=["xlsx"], help="导出 Excel")
    parser.add_argument("--out", help="Excel 导出路径（默认 output/ 下自动命名）")
    args = parser.parse_args(argv)

    meta, windows, state, summary, gaps = load_all()
    if args.start or args.end or args.export:
        run_params(meta, windows, state, summary, gaps, args)
    else:
        interactive_menu(meta, windows, state, summary, gaps)


if __name__ == "__main__":
    main()
