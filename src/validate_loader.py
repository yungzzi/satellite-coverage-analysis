# -*- coding: utf-8 -*-
"""
validate_loader.py —— satellite_loader.py 的自检脚本（阶段 1）

用途：在【不修改 coverage_analysis.py】的前提下，独立验证数据加载模块：
  1. TLE 解析与包装类（离线）
  2. 编目下载 / 搜索（联网，需能访问 celestrak.org）
  3. 缓存行为（不重复下载）
  4. 收藏夹增删改移（用临时文件，不污染真实用户收藏）
  5. 收藏夹解析（编号清单 / 文件快照，旧式名字规则兼容）
  6. 离线模式（有缓存时可用）
  7. 星座导入与搜索增强
  8. CLI 入口冒烟（--help / --catalog，不写数据）

运行：.venv\\Scripts\\python src\\validate_loader.py
（联网测试部分需要网络；全部通过后退出码为 0）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import satellite_loader as sl


PASS = 0
FAIL = 0
RESULTS: list = []   # 每项检查的结果 (名称, 是否通过, 说明)，供 GUI 复用


def check(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    RESULTS.append((name, ok, detail))
    if ok:
        PASS += 1
        print(f"  [PASS] {name} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def test_tle_parse_offline():
    print("\n== 1. TLE 解析与包装类（离线） ==")
    tle_path = sl.DATA_DIR / "raw" / "active_tle.txt"
    if not tle_path.exists():
        check("本地 TLE 文件存在", False, f"缺少 {tle_path}")
        return
    text = tle_path.read_text(encoding="ascii", errors="replace")
    sats = sl.parse_tle_text(text)
    yaogan = [s for s in sats if "YAOGAN" in s.name.upper()]
    check("解析出卫星", len(sats) > 5000, f"共 {len(sats)} 颗")
    check("YAOGAN 数量符合预期", len(yaogan) >= 150, f"共 {len(yaogan)} 颗（>=150）")
    if yaogan:
        s = yaogan[0]
        check("包装类有 name", bool(s.name), s.name)
        check("包装类有 norad_id", isinstance(s.norad_id, int), f"NORAD={s.norad_id}")
        check("包装类透传 .at()", callable(getattr(s, "at")), "")
        ts = sl.load.timescale(builtin=True)
        t = ts.now()
        geo = s.at(t)
        sub = geo.subpoint()
        check("透传 .at() 能算出星下点", abs(sub.latitude.degrees) <= 90
              and abs(sub.longitude.degrees) <= 180,
              f"lat={sub.latitude.degrees:.2f}, lon={sub.longitude.degrees:.2f}")
        check("sensor 字段预留", s.sensor is None, "")


def test_metadata_search_online():
    print("\n== 2. 编目搜索（联网） ==")
    rows = sl.search_metadata("YAOGAN", max_results=10000, offline=False)
    if rows is None:
        check("编目搜索成功", False, "下载失败（可能被数据源限流/禁止）")
        return
    check("编目搜索成功", len(rows) >= 150, f"共 {len(rows)} 条（在轨载荷）")
    if not rows:
        return
    r = rows[0]
    check("搜索结果有元数据", bool(r["name"]) and isinstance(r["norad_id"], int)
          and bool(r["country"]), f"{r['name']} / {r['country']}")
    check("编目不直接含 TLE（设计如此）", r["has_tle"] is False, "")
    c = sl.search_metadata("YAOGAN", country="PRC", max_results=10000)
    check("按国家筛选（PRC）", c is not None and len(c) > 0,
          f"共 {len(c) if c else 0} 条")
    a = sl.search_metadata("ISS", max_results=50)
    check("按高度筛选（ISS，约 400km）",
          a is not None and any(r.get("altitude_km") and 300 < r["altitude_km"] < 500 for r in a),
          [f"{r['name']}:{r.get('altitude_km')}" for r in (a or [])][:3])


def test_cache_behavior():
    print("\n== 3. 缓存行为 ==")
    rows = sl.search_metadata("YAOGAN", max_results=100, offline=False)
    path = sl.CACHE_DIR / "meta" / "YAOGAN_meta.csv"
    if rows is None or not path.exists():
        check("缓存文件存在", False, "首次下载失败")
        return
    mtime1 = path.stat().st_mtime
    sl.search_metadata("YAOGAN", max_results=100, offline=False)
    mtime2 = path.stat().st_mtime
    check("第二次调用不重新下载", mtime1 == mtime2, "")


def test_collections_crud():
    print("\n== 4. 收藏夹增删改移（临时文件） ==")
    tmp = Path(tempfile.mkdtemp(prefix="sl_test_")) / "user_catalog.toml"
    old = sl.USER_CATALOG
    sl.USER_CATALOG = tmp
    try:
        check("新建夹", sl.create_collection("测试夹A") and tmp.exists(), "")
        check("重复名被拒绝", sl.create_collection("测试夹A") is False, "")
        check("新建第二个夹", sl.create_collection("测试夹B"), "")
        check("加旧式名字规则（兼容）", sl.add_entry("测试夹A", "name_filter", "YAOGAN"), "")
        check("加编号清单", sl.add_entry("测试夹A", "norad_list", "25544 31490"), "")
        user = sl.load_user_collections()
        a = sl.find_collection(user, "测试夹A")
        check("夹内 2 条", a is not None and len(a["entries"]) == 2, "")
        check("移动条目", sl.move_entry("测试夹A", 0, "测试夹B"), "")
        user = sl.load_user_collections()
        a = sl.find_collection(user, "测试夹A")
        b = sl.find_collection(user, "测试夹B")
        check("移动后数量正确", len(a["entries"]) == 1 and len(b["entries"]) == 1,
              f"A={len(a['entries'])}, B={len(b['entries'])}")
        check("改名", sl.rename_collection("测试夹A", "测试夹C"), "")
        check("删除", sl.delete_collection("测试夹B"), "")
        check("删除后不存在", sl.find_collection(sl.load_user_collections(), "测试夹B") is None, "")
        builtin = sl.load_builtin_collections()
        check("内置目录可读", len(builtin) > 0, f"{len(builtin)} 个内置夹")
        if builtin:
            check("复制内置到用户", sl.copy_builtin_to_user(builtin[0]["name"], "内置副本"), "")
    finally:
        sl.USER_CATALOG = old
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_resolve():
    print("\n== 5. 收藏夹解析（编号清单 / 文件，旧式规则兼容） ==")
    tmp = Path(tempfile.mkdtemp(prefix="sl_test_")) / "user_catalog.toml"
    old = sl.USER_CATALOG
    sl.USER_CATALOG = tmp
    try:
        sl.create_collection("解析测试")
        sl.add_entry("解析测试", "name_filter", "YAOGAN")
        col = sl.find_collection(sl.load_user_collections(), "解析测试")
        recs = sl.resolve_collection(col, offline=False)
        norads = [r["norad_id"] for r in recs]
        check("旧式名字规则解析数量", len(recs) >= 150, f"共 {len(recs)} 颗")
        check("NORAD 编号去重", len(set(norads)) == len(norads), "")
        check("记录含 TLE", all(r.get("line1") and r.get("line2") for r in recs[:50]), "")
        saved = sl.save_resolved_tle("解析测试", recs)
        check("合并 TLE 文件已生成", saved.exists() and saved.suffix == ".tle",
              str(saved))
        saved.unlink(missing_ok=True)   # 自检产物不留缓存目录，避免污染用户数据

        # 编号清单：ISS（25544）
        sl.create_collection("编号测试")
        sl.add_entry("编号测试", "norad_list", "25544")
        col2 = sl.find_collection(sl.load_user_collections(), "编号测试")
        recs2 = sl.resolve_collection(col2, offline=False)
        check("编号清单解析", len(recs2) == 1 and recs2[0]["norad_id"] == 25544,
              f"{recs2[0]['name'] if recs2 else '空'}")

        # 文件导入：编号清单文件
        f = tmp.parent / "norad_list.txt"
        f.write_text("25544\n31490\n", encoding="ascii")
        sl.create_collection("文件测试")
        check("导入文件到收藏", sl.import_file_to_collection(str(f), "文件测试"), "")
        col3 = sl.find_collection(sl.load_user_collections(), "文件测试")
        recs3 = sl.resolve_collection(col3, offline=False)
        check("文件解析", len(recs3) == 2, f"共 {len(recs3)} 颗")
        check("文件已复制到 user_files", (sl.USER_FILES_DIR / "norad_list.txt").exists(), "")

        # 离线模式：缓存已存在时应能用
        recs_off = sl.resolve_collection(col, offline=True)
        check("离线模式可用（有缓存）", len(recs_off) >= 150, f"共 {len(recs_off)} 颗")
    finally:
        sl.USER_CATALOG = old
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_multi_collection_merge():
    print("\n== 6. 多夹合并去重 ==")
    tmp = Path(tempfile.mkdtemp(prefix="sl_test_")) / "user_catalog.toml"
    old = sl.USER_CATALOG
    sl.USER_CATALOG = tmp
    try:
        sl.create_collection("夹1")
        sl.create_collection("夹2")
        sl.add_entry("夹1", "norad_list", "25544")
        sl.add_entry("夹2", "norad_list", "25544 31490")
        sats = sl.resolve_collections_to_satellites(["夹1", "夹2"], offline=False)
        check("合并去重得到 2 颗", len(sats) == 2,
              f"共 {len(sats)} 颗: {[s.norad_id for s in sats]}")
        check("合并结果是包装类", all(isinstance(s, sl.Satellite) for s in sats), "")
    finally:
        sl.USER_CATALOG = old
        shutil.rmtree(tmp.parent, ignore_errors=True)


def test_search_enhancements():
    print("\n== 7. 星座导入与搜索增强（黑名单/分组/拼写） ==")
    cons = sl.load_constellations()
    check("星座目录可读", len(cons) > 0, f"{len(cons)} 个星座条目")
    recs = sl._records_by_name_filter("STARLINK", 24, offline=False)
    check("名字规则黑名单不截断星链", len(recs) >= 10000, f"共 {len(recs)} 颗")
    g = sl._records_by_group("gps-ops", 24, offline=False)
    check("星座分组下载（gps-ops）", len(g) > 0, f"共 {len(g)} 颗")
    sug = sl.suggest_similar_names("STARLNK", offline=False)
    check("拼写纠错提示", any("STARLINK" in s for s in sug), str(sug))
    sug_lower = sl.suggest_similar_names("yaogen", offline=False)
    check("拼写提示忽略大小写", "YAOGAN" in sug_lower, str(sug_lower))
    iss = next((r for r in sl.load_active_tle(offline=True)[1]
                if r.get("norad_id") == 25544), None)
    if iss:
        d = sl._tle_derived_meta(iss.get("line1", ""), iss.get("line2", ""))
        check("TLE 推导倾角/高度", bool(d["inclination"] and 50 < d["inclination"] < 53
              and d["altitude_km"] and 350 < d["altitude_km"] < 500), str(d))
    else:
        check("TLE 推导倾角/高度", False, "缓存无 ISS")


def test_cli_smoke():
    print("\n== 8. CLI 入口冒烟（--help / --catalog，不写数据） ==")
    root = Path(__file__).resolve().parent
    py = sys.executable
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for script in ("satellite_loader.py", "satellite_selection.py"):
        r = subprocess.run([py, str(root / script), "--help"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env)
        ok = (r.returncode == 0 and "Traceback" not in r.stdout
              and "usage:" in r.stdout)
        check(f"CLI {script} --help 可用", ok,
              f"exit={r.returncode}" + ("" if ok else r.stderr[-300:]))
    r = subprocess.run([py, str(root / "satellite_loader.py"), "--catalog"],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    ok = r.returncode == 0 and "Traceback" not in r.stdout and "收藏夹" in r.stdout
    check("CLI --catalog 可用", ok, f"exit={r.returncode}")


def run_all() -> list:
    """运行全部自检，返回 [(名称, 是否通过, 说明), ...]；CLI 和 GUI 共用。"""
    global PASS, FAIL, RESULTS
    PASS = 0
    FAIL = 0
    RESULTS = []
    print("satellite_loader 自检开始（联网部分需要能访问 celestrak.org）")
    test_tle_parse_offline()
    test_metadata_search_online()
    test_cache_behavior()
    test_collections_crud()
    test_resolve()
    test_multi_collection_merge()
    test_search_enhancements()
    test_cli_smoke()
    return list(RESULTS)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    run_all()
    print("\n" + "=" * 40)
    print(f"结果：{PASS} 项通过，{FAIL} 项失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
