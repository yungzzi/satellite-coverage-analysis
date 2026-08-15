# -*- coding: utf-8 -*-
"""卫星覆盖分析工作台统一入口（sat 命令 / 交付版启动器）。

双击 exe（无参数）→ 弹出选择框：GUI / CLI。
命令行用法（短命令，全部可用 --help 看细节）：
  sat gui                启动网页版（默认浏览器自动打开）
  sat menu               进入交互菜单（不用记命令）
  sat search 关键字       搜索卫星（含变体重查与拼写提示）
  sat norad 25544        按 NORAD 编号查询
  sat catalog            列出收藏夹
  sat create 名称         新建收藏夹
  sat rm 名称             删除收藏夹
  sat add 夹 编号...      往夹里加星（编号清单）
  sat del 夹 编号...      从夹里删星
  sat resolve 夹         解析并下载 TLE（存到 data/tle_cache/resolved/）
  sat import 路径 [夹]    导入 TLE/编号文件（不写夹名则自动建夹）
  sat cons 星座 [夹]      星座预览 / 导入为编号快照
  sat copy 源夹 目标夹     复制收藏夹条目（重复跳过）
  sat move 源夹 序号 目标夹 移动条目（序号从 1 起）
  sat locations          列出常用地点
  sat loc 名称 纬度 经度 半径km  保存常用地点
  sat sensors            列出逐星传感器参数表
  sat sensor 编号 [--fov X] [--elev X] [--swath X]
  sat update [夹...]     强制更新全集与星座缓存
  sat check              自检（联网）
  sat select 夹 [参数]    选星（内部自动跑覆盖分析，参数同 GUI ⑤页）
  sat run 夹 [参数]       一键流程：覆盖 → 连续覆盖选星 → Excel → 虚拟星座
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import satellite_loader as sl
import satellite_selection as ss


# PyInstaller 打包后 streamlit/config.py 的 __file__ 不含 "site-packages"，
# 会被误判为开发模式（端口横幅显示 3000、CORS 警告、根路径 404）。
# 必须在导入 streamlit 之前强制关闭开发模式。
os.environ.setdefault("STREAMLIT_GLOBAL_DEVELOPMENT_MODE", "false")


PROG = "sat"


# ==================== 图形选择框（双击 exe 时用） ====================

def _choose_mode_dialog() -> str:
    try:
        import tkinter as tk
    except Exception:
        print("无法打开图形选择框，直接进入命令行交互菜单。")
        return "cli"
    root = tk.Tk()
    root.title("卫星覆盖分析工作台")
    root.geometry("560x260")
    root.resizable(False, False)
    result = {"mode": "cli"}

    def pick(mode: str):
        result["mode"] = mode
        root.destroy()

    tk.Label(root, text="请选择交互方式", font=("Microsoft YaHei", 15)
             ).pack(pady=(26, 14))
    tk.Button(root, text="GUI 网页版（强烈推荐，需要电脑有浏览器）",
              font=("Microsoft YaHei", 12), width=48, height=2,
              command=lambda: pick("gui")).pack(pady=6)
    tk.Button(root, text="CLI 命令行（交互菜单）",
              font=("Microsoft YaHei", 12), width=48, height=2,
              command=lambda: pick("cli")).pack(pady=6)
    root.mainloop()
    return result["mode"]


# ==================== GUI 启动 ====================

def cmd_gui() -> int:
    banner = (
        "============================================================\n"
        "  卫星覆盖分析工作台\n"
        "  启动后浏览器自动打开（或手动访问 http://localhost:8501）\n"
        "\n"
        "  提示：\n"
        "   - 关闭浏览器标签页【不会】停止程序，重新打开上面的网址即可回来；\n"
        "   - 彻底停止：点界面左侧\"停止并退出程序\"按钮，或按 Ctrl+C。\n"
        "============================================================\n")
    print(banner)
    if getattr(sys, "frozen", False):
        app_path = Path(sys._MEIPASS) / "loader_app.py"
    else:
        app_path = sl.PROJECT_ROOT / "src" / "loader_app.py"
    if not app_path.exists():
        print(f"找不到网页程序文件：{app_path}")
        return 1
    import streamlit.web.cli as stcli
    sys.argv = ["streamlit", "run", str(app_path)]
    stcli.main()
    return 0


# ==================== 交互菜单（不用记命令） ====================

def _menu_search():
    print("\n===== 搜索卫星 =====")
    kind = input("1 名字关键字   2 NORAD 编号   （0 返回）> ").strip()
    if kind == "1":
        kw = input("关键字（如 GAOFEN-1，留空返回）> ").strip()
        if kw:
            sl.main(["--search", kw])
    elif kind == "2":
        n = input("NORAD 编号（如 25544，留空返回）> ").strip()
        if n.isdigit():
            sl.main(["--norad", n])
    else:
        return


def _menu_collections():
    while True:
        print("\n===== 收藏夹管理 =====")
        print("  1. 列出所有收藏夹")
        print("  2. 新建收藏夹")
        print("  3. 往夹里加星（编号清单）")
        print("  4. 从夹里删星")
        print("  5. 解析夹并下载 TLE")
        print("  0. 返回")
        ch = input("> ").strip()
        if ch == "0":
            return
        if ch == "1":
            sl.main(["--catalog"])
        elif ch == "2":
            name = input("收藏夹名称（留空取消）> ").strip()
            if name:
                sl.main(["--create", name])
        elif ch == "3":
            col = input("收藏夹名 > ").strip()
            ids = input("NORAD 编号（空格分隔，如 25544 41194）> ").strip()
            if col and ids:
                sl.main(["--add-entry", "norad_list", "--collection", col,
                         "--value", ids])
        elif ch == "4":
            col = input("收藏夹名 > ").strip()
            ids = input("要删的 NORAD 编号（空格分隔）> ").strip()
            if col and ids:
                sl.main(["--remove-norad", col] + ids.split())
        elif ch == "5":
            col = input("收藏夹名 > ").strip()
            if col:
                sl.main(["--resolve", col])
        else:
            print("  无效输入")


def _menu_constellations():
    print("\n===== 星座导入 =====")
    cons = sl.load_constellations()
    if not cons:
        print("  星座目录为空。")
        return
    for i, c in enumerate(cons, 1):
        sing = "（单星）" if c.get("singleton") else ""
        print(f"  {i}. {c['name']}{sing}")
    sel = input(f"选择要预览的星座（1~{len(cons)}，0 返回）> ").strip()
    if not sel.isdigit() or not 1 <= int(sel) <= len(cons):
        return
    name = cons[int(sel) - 1]["name"]
    sl.main(["--constellation", name])
    col = input("输入目标收藏夹名导入为编号快照（留空只预览不导入）> ").strip()
    if col:
        sl.main(["--constellation", name, "--collection", col])


def _menu_select():
    print("\n===== 选星（覆盖分析 + 选星决策） =====")
    col = input("收藏夹名（0 返回）> ").strip()
    if not col or col == "0":
        return
    t0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                            microsecond=0)
    t1 = t0 + timedelta(hours=8)
    start = input(f"开始时间 UTC（回车默认 {t0:%Y-%m-%d %H:%M:%S}）> ").strip()
    end = input(f"结束时间 UTC（回车默认 {t1:%Y-%m-%d %H:%M:%S}）> ").strip()
    print("需求：1 连续覆盖（无空窗）  2 重访（MRT≤X）  3 覆盖率≥P%  0 取消")
    req = input("> ").strip()
    argv = ["--collection", col, "--start", start or f"{t0:%Y-%m-%d %H:%M:%S}",
            "--end", end or f"{t1:%Y-%m-%d %H:%M:%S}", "--method", "both",
            "--list-optimal"]
    if req == "1":
        argv += ["--req-mode", "continuous"]
    elif req == "2":
        argv += ["--req-mode", "revisit"]
        mrt = input("MRT（最大重访间隔，分钟，默认 180）> ").strip()
        argv += ["--mrt", mrt or "180"]
    elif req == "3":
        argv += ["--req-mode", "coverage_pct"]
        p = input("覆盖率要求 P%（默认 100）> ").strip()
        argv += ["--coverage-pct", p or "100"]
    else:
        return
    loc = input("常用地点名（可选，如 杭州，留空用默认坐标）> ").strip()
    if loc:
        argv += ["--location", loc]
    xlsx = input("导出选星 Excel？y/n（默认 n）> ").strip().lower()
    if xlsx in ("y", "yes"):
        argv += ["--xlsx"]
    save = input("保存为虚拟星座收藏夹？输入名称（留空不保存）> ").strip()
    if save:
        argv += ["--save-as", save]
    ss.main(argv)


def _menu_download():
    print("\n===== 下载与自检 =====")
    print("  1. 强制更新（重下全集与星座缓存）")
    print("  2. 自检（43 项，联网部分需要网络）")
    print("  0. 返回")
    ch = input("> ").strip()
    if ch == "1":
        cols = input("要更新的收藏夹名（空格分隔，留空只更新全集）> ").strip()
        sl.main(["--force-update"] + (cols.split() if cols else []))
    elif ch == "2":
        sl.main(["--selfcheck"])


def cmd_menu() -> int:
    print("卫星覆盖分析工作台 —— 命令行交互菜单（输入数字选择，0 退出）")
    while True:
        print("\n请选择：")
        print("  1. 搜索卫星")
        print("  2. 收藏夹管理")
        print("  3. 星座导入")
        print("  4. 选星（覆盖分析 + 选星决策）")
        print("  5. 下载与自检")
        print("  0. 退出")
        ch = input("> ").strip()
        if ch in ("0", "q", "Q", "退出"):
            print("已退出。")
            return 0
        if ch == "1":
            _menu_search()
        elif ch == "2":
            _menu_collections()
        elif ch == "3":
            _menu_constellations()
        elif ch == "4":
            _menu_select()
        elif ch == "5":
            _menu_download()
        else:
            print("  无效输入")


# ==================== 参数解析与分发 ====================

def _add_select_args(sp, run: bool = False):
    """select/run 共用的选星参数（run 带一键默认）。"""
    sp.add_argument("collection", help="收藏夹名（卫星池来源）")
    sp.add_argument("--location", default=None, help="常用地点名称（覆盖经纬度/半径）")
    sp.add_argument("--lat", type=float, default=None, help="纬度（默认杭州 30.25）")
    sp.add_argument("--lon", type=float, default=None, help="经度（默认杭州 120.15）")
    sp.add_argument("--radius", type=float, default=None, help="半径 km（默认 5）")
    sp.add_argument("--geojson", default=None, help="地区 GeoJSON 路径（多边形模式）")
    sp.add_argument("--grid", type=float, default=None, help="网格间距（度）")
    sp.add_argument("--start", default=None, help="开始时间 UTC（run 默认今天 00:00）")
    sp.add_argument("--end", default=None, help="结束时间 UTC（run 默认开始+8 小时）")
    sp.add_argument("--step", type=int, default=None, help="时间步长（分钟，默认 1）")
    sp.add_argument("--fov", type=float, default=None, help="视场半角（度，默认 10）")
    sp.add_argument("--elev", type=float, default=None, help="最小仰角（度，默认 10）")
    sp.add_argument("--sensor-params", default=None, help="逐星传感器参数表路径")
    sp.add_argument("--k-min", type=int, default=None, help="最少同时可见数 K（默认 1）")
    sp.add_argument("--mrt", type=float, default=None, help="最大重访间隔（分钟）")
    sp.add_argument("--min-event", type=float, default=None, help="单次窗口最短时长(分钟)")
    sp.add_argument("--method", default=None, choices=["greedy", "ilp", "both"],
                    help="算法（run 默认两者对照）")
    sp.add_argument("--req-mode", default=None,
                    choices=["continuous", "revisit", "coverage_pct", "any"],
                    help="需求（run 默认 continuous=连续覆盖）")
    sp.add_argument("--coverage-pct", type=float, default=None,
                    help="coverage_pct 模式的覆盖率阈值（%）")
    sp.add_argument("--objective", default=None,
                    choices=["min_sats", "max_coverage", "min_max_gap",
                             "min_avg_gap"])
    sp.add_argument("--n-sats", type=int, default=None, help="给定颗数 N")
    sp.add_argument("--decision-step-seconds", type=int, default=None,
                    help="选星决策粒度（秒）：10=精细（仅贪心）")
    sp.add_argument("--engine", default=None, choices=["self", "atk"],
                    help="覆盖引擎：self=自研（默认）；atk=本机 ATK（需安装注册，"
                         "仅支持点+半径目标）")
    sp.add_argument("--list-optimal", action="store_true", help="打印全部最优方案")
    sp.add_argument("--no-xlsx", action="store_true",
                    help="run 默认导出 Excel，本开关关闭")
    sp.add_argument("--save-as", default=None, help="保存为虚拟星座收藏夹的名称")
    sp.add_argument("--out", default=None, help="输出目录（默认 output/）")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description="卫星覆盖分析工作台统一入口：搜索/收藏夹/星座/覆盖分析/选星。"
                    "无参数时弹出交互方式选择框。")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("gui", help="启动网页版（默认浏览器自动打开）")
    sub.add_parser("menu", help="进入交互菜单（不用记命令）")
    sp = sub.add_parser("search", help="搜索卫星（名字，含变体重查与拼写提示）")
    sp.add_argument("keyword")
    sp.add_argument("--country", default=None, help="按所属国筛选（如 PRC）")
    sp.add_argument("--launch-year", default=None, help="按发射年份筛选")
    sp.add_argument("--min-alt", type=float, default=None, help="最低高度 km")
    sp.add_argument("--max-alt", type=float, default=None, help="最高高度 km")
    sp.add_argument("--offline", action="store_true", help="只用本地缓存")
    sp = sub.add_parser("norad", help="按 NORAD 编号查询")
    sp.add_argument("norad", type=int)
    sub.add_parser("catalog", help="列出所有收藏夹")
    sp = sub.add_parser("create", help="新建收藏夹")
    sp.add_argument("name")
    sp = sub.add_parser("rm", help="删除收藏夹")
    sp.add_argument("name")
    sp = sub.add_parser("add", help="往夹里加星（编号清单）")
    sp.add_argument("collection")
    sp.add_argument("ids", nargs="+")
    sp = sub.add_parser("del", help="从夹里删星")
    sp.add_argument("collection")
    sp.add_argument("ids", nargs="+")
    sp = sub.add_parser("resolve", help="解析夹并下载 TLE")
    sp.add_argument("collection")
    sp = sub.add_parser("import", help="导入 TLE/编号文件（不写夹名则自动建夹）")
    sp.add_argument("path")
    sp.add_argument("collection", nargs="?")
    sp = sub.add_parser("cons", help="星座预览 / 导入为编号快照")
    sp.add_argument("constellation")
    sp.add_argument("collection", nargs="?")
    sp = sub.add_parser("copy", help="复制收藏夹条目（重复跳过）")
    sp.add_argument("src")
    sp.add_argument("dst")
    sp = sub.add_parser("move", help="移动条目（序号从 1 起）")
    sp.add_argument("src")
    sp.add_argument("index")
    sp.add_argument("dst")
    sub.add_parser("locations", help="列出常用地点")
    sp = sub.add_parser("loc", help="保存常用地点")
    sp.add_argument("name")
    sp.add_argument("lat", type=float)
    sp.add_argument("lon", type=float)
    sp.add_argument("radius", type=float)
    sub.add_parser("sensors", help="列出逐星传感器参数表")
    sp = sub.add_parser("sensor", help="设置/更新一颗星的传感器参数")
    sp.add_argument("norad", type=int)
    sp.add_argument("--fov", type=float, default=None)
    sp.add_argument("--elev", type=float, default=None)
    sp.add_argument("--swath", type=float, default=None)
    sp = sub.add_parser("update", help="强制更新全集与星座缓存")
    sp.add_argument("collections", nargs="*")
    sub.add_parser("check", help="自检（43 项，联网部分需要网络）")
    sp = sub.add_parser("select", help="选星（内部自动跑覆盖分析）")
    _add_select_args(sp, run=False)
    sp = sub.add_parser("run", help="一键流程：覆盖→连续覆盖选星→Excel→虚拟星座")
    _add_select_args(sp, run=True)
    return p


def _select_argv(a, xlsx: bool, save_as: str | None,
                 start: str, end: str) -> list:
    argv = ["--collection", a.collection, "--start", start, "--end", end,
            "--method", a.method or "both"]
    if a.location:
        argv += ["--location", a.location]
    if a.lat is not None:
        argv += ["--lat", str(a.lat)]
    if a.lon is not None:
        argv += ["--lon", str(a.lon)]
    if a.radius is not None:
        argv += ["--radius", str(a.radius)]
    if a.geojson:
        argv += ["--mode", "polygon", "--geojson", a.geojson]
    if a.grid is not None:
        argv += ["--grid", str(a.grid)]
    if a.step is not None:
        argv += ["--step", str(a.step)]
    if a.fov is not None:
        argv += ["--fov", str(a.fov)]
    if a.elev is not None:
        argv += ["--elev", str(a.elev)]
    if a.sensor_params:
        argv += ["--sensor-params", a.sensor_params]
    if a.k_min is not None:
        argv += ["--k-min", str(a.k_min)]
    if a.mrt is not None:
        argv += ["--mrt", str(a.mrt)]
    if a.min_event is not None:
        argv += ["--min-event", str(a.min_event)]
    if a.req_mode:
        argv += ["--req-mode", a.req_mode]
    if a.coverage_pct is not None:
        argv += ["--coverage-pct", str(a.coverage_pct)]
    if a.objective:
        argv += ["--objective", a.objective]
    if a.n_sats is not None:
        argv += ["--n-sats", str(a.n_sats)]
    if a.decision_step_seconds:
        argv += ["--decision-step-seconds", str(a.decision_step_seconds)]
    if a.list_optimal:
        argv += ["--list-optimal"]
    if xlsx:
        argv += ["--xlsx"]
    if save_as:
        argv += ["--save-as", save_as]
    if a.engine:
        argv += ["--engine", a.engine]
    if a.out:
        argv += ["--out", a.out]
    return argv


def run_args(args: argparse.Namespace) -> int:
    cmd = args.cmd
    if cmd == "gui":
        return cmd_gui()
    if cmd == "menu":
        return cmd_menu()
    if cmd == "search":
        argv = ["--search", args.keyword]
        for flag, val in (("--country", args.country),
                          ("--launch-year", args.launch_year),
                          ("--min-alt", args.min_alt),
                          ("--max-alt", args.max_alt)):
            if val is not None:
                argv += [flag, str(val)]
        if args.offline:
            argv += ["--offline"]
        return sl.main(argv)
    if cmd == "norad":
        return sl.main(["--norad", str(args.norad)])
    if cmd == "catalog":
        return sl.main(["--catalog"])
    if cmd == "create":
        return sl.main(["--create", args.name])
    if cmd == "rm":
        return sl.main(["--delete", args.name])
    if cmd == "add":
        return sl.main(["--add-entry", "norad_list", "--collection",
                        args.collection, "--value", " ".join(args.ids)])
    if cmd == "del":
        return sl.main(["--remove-norad", args.collection] + args.ids)
    if cmd == "resolve":
        return sl.main(["--resolve", args.collection])
    if cmd == "import":
        argv = ["--import-file", args.path]
        if args.collection:
            argv += ["--collection", args.collection]
        return sl.main(argv)
    if cmd == "cons":
        argv = ["--constellation", args.constellation]
        if args.collection:
            argv += ["--collection", args.collection]
        return sl.main(argv)
    if cmd == "copy":
        return sl.main(["--copy", args.src, args.dst])
    if cmd == "move":
        return sl.main(["--move-entry", args.src, args.index, args.dst])
    if cmd == "locations":
        return sl.main(["--list-locations"])
    if cmd == "loc":
        return sl.main(["--save-location", args.name, str(args.lat),
                        str(args.lon), str(args.radius)])
    if cmd == "sensors":
        return sl.main(["--sensor-list"])
    if cmd == "sensor":
        argv = ["--sensor-set", str(args.norad)]
        if args.fov is not None:
            argv += ["--fov", str(args.fov)]
        if args.elev is not None:
            argv += ["--elev", str(args.elev)]
        if args.swath is not None:
            argv += ["--swath", str(args.swath)]
        return sl.main(argv)
    if cmd == "update":
        return sl.main(["--force-update"] + args.collections)
    if cmd == "check":
        return sl.main(["--selfcheck"])
    if cmd == "select":
        if not args.start or not args.end:
            print("select 需要 --start 和 --end（UTC）；或用 run 走默认时段。")
            return 2
        return ss.main(_select_argv(args, xlsx=not args.no_xlsx,
                                    save_as=args.save_as,
                                    start=args.start, end=args.end))
    if cmd == "run":
        t0 = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
        t1 = t0 + timedelta(hours=8)
        start = args.start or f"{t0:%Y-%m-%d %H:%M:%S}"
        end = args.end or f"{t1:%Y-%m-%d %H:%M:%S}"
        save_as = args.save_as or f"虚拟星座-{datetime.now():%Y%m%d-%H%M%S}"
        req = args.req_mode or "continuous"
        a = argparse.Namespace(**vars(args))
        a.req_mode = req
        if req == "revisit" and a.mrt is None:
            a.mrt = 180.0
        if req == "coverage_pct" and a.coverage_pct is None:
            a.coverage_pct = 100.0
        return ss.main(_select_argv(a, xlsx=not args.no_xlsx, save_as=save_as,
                                    start=start, end=end))
    print(f"未知命令：{cmd}")
    return 2


def main(argv: list | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        mode = _choose_mode_dialog()
        return cmd_gui() if mode == "gui" else cmd_menu()
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        build_parser().print_help()
        return 2
    return run_args(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
