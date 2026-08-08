# -*- coding: utf-8 -*-
"""
loader_app.py —— 卫星覆盖分析工作台图形界面（Streamlit）

只做"卫星数据导入"这一部分的 GUI，三个页签：
  ① 搜索与收藏：按名字/所属国/发射年份/高度搜索，勾选后加入收藏夹
  ② 收藏夹管理：自动规则（名字规则）+ 手动卫星（编号清单可单颗删除/追加/归纳规则）/ 导入文件
  ③ 下载与自检：多夹合并解析（下载 TLE 缓存）+ 一键自检

设计原则（与 CLI 一致）：
- 核心逻辑全在 satellite_loader.py，本文件只是"壳"；
- CLI（satellite_loader.py 交互菜单）保留，两者共用同一套函数；
- coverage_analysis.py 完全不动。

启动：.venv\\Scripts\\streamlit run src\\loader_app.py
（第一次使用前先执行 .venv\\Scripts\\pip install streamlit）
"""

from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

import coverage_analysis as cov
import query_coverage as qc
import satellite_loader as sl
import satellite_selection as ss
import validate_loader

# 覆盖分析的输出写相对路径 "output/"，把工作目录固定到项目根，避免写错位置
os.chdir(str(sl.PROJECT_ROOT))


def flash(msg: str, kind: str = "success"):
    """把反馈消息存进会话状态，跨 rerun 显示（st.rerun 不会把它清掉）。"""
    st.session_state["flash"] = {"kind": kind, "msg": msg}


def show_flash():
    """在页面顶部显示上一次操作的反馈（显示一次后自动清除）。"""
    f = st.session_state.pop("flash", None)
    if f:
        if f["kind"] == "success":
            st.success(f["msg"])
        else:
            st.warning(f["msg"])


# ---------- 撤销 / 重做（收藏夹文件快照栈，仅本次会话内有效） ----------

def _catalog_text() -> str:
    """读当前用户收藏夹文件内容（文件不存在时返回空串）。"""
    try:
        return sl.USER_CATALOG.read_text(encoding="utf-8")
    except OSError:
        return ""


def _snapshot():
    """在修改收藏夹【之前】调用：把当前文件内容压入撤销栈，并清空重做栈。"""
    st.session_state.setdefault("undo_stack", [])
    st.session_state["redo_stack"] = []
    st.session_state["undo_stack"].append(_catalog_text())
    if len(st.session_state["undo_stack"]) > 50:   # 防止无限增长
        st.session_state["undo_stack"].pop(0)


def _restore_catalog(text: str):
    """把用户收藏夹文件恢复成某次快照的文本。"""
    sl._ensure_dirs()
    sl.USER_CATALOG.write_text(text, encoding="utf-8")


def _do_undo():
    """撤销上一步：当前状态进重做栈，恢复上一次快照。"""
    if not st.session_state.get("undo_stack"):
        flash("没有可撤销的操作。", kind="warning")
        return
    current = _catalog_text()
    prev = st.session_state["undo_stack"].pop()
    st.session_state.setdefault("redo_stack", []).append(current)
    _restore_catalog(prev)
    flash("已撤销上一步操作。")


def _do_redo():
    """重做下一步：当前状态进撤销栈，恢复被撤销掉的状态。"""
    if not st.session_state.get("redo_stack"):
        flash("没有可重做的操作。", kind="warning")
        return
    current = _catalog_text()
    nxt = st.session_state["redo_stack"].pop()
    st.session_state.setdefault("undo_stack", []).append(current)
    _restore_catalog(nxt)
    flash("已重做下一步操作。")


def _slider_bounds(values: list):
    """根据数值列表计算滑块的全范围 (lo, hi)，上限向上取整避免截断丢星。"""
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    lo, hi = int(min(vals)), int(max(vals)) + 1
    if hi <= lo:
        hi = lo + 1
    return (lo, hi)


def _name_search(keyword: str) -> list:
    """按名字搜索（附带界面上的 所属国/发射年份/高度 过滤），搜索与重置共用。"""
    return sl.search_metadata(
        keyword,
        country=str(st.session_state.get("ctry", "")).strip() or None,
        launch_year=str(st.session_state.get("ly", "")).strip() or None,
        min_alt_km=st.session_state.get("minalt", 0.0) or None,
        max_alt_km=st.session_state.get("maxalt", 0.0) or None,
        offline=False)


def _collection_label(x: dict) -> str:
    """收藏夹在下拉/多选框里的显示名：星座收藏夹带 📡 标识。"""
    if x.get("builtin"):
        return f"{x['name']}（内置，只读）"
    if x.get("kind") == "constellation":
        return f"📡 {x['name']}（星座）"
    if x.get("kind") == "virtual_constellation":
        return f"⭐ {x['name']}（虚拟星座，来自选星）"
    return f"{x['name']}（我的）"


def _strip_collection_label(n: str) -> str:
    """把带标识的显示名还原成收藏夹原名（③⑤页解析前调用）。"""
    n = n[len("📡 "):] if n.startswith("📡 ") else n
    if n.endswith("（星座）"):
        n = n[:-len("（星座）")]
    if n.endswith("（内置，只读）"):
        n = n[:-len("（内置，只读）")]
    if n.endswith("（我的）"):
        n = n[:-len("（我的）")]
    return n


def _reset_filters():
    """重置结果内筛选（on_click 回调）：递增"筛选版本号"让所有筛选控件换新 key
    重建（自动回到默认值），并重新拉取最新结果。

    用版本号而不是 pop/设值，可以同时避免：①新版 Streamlit 显示不回位；
    ②"widget 同时有默认值和 session_state 值"的警告。
    """
    st.session_state["filter_ver"] = st.session_state.get("filter_ver", 0) + 1
    st.session_state.pop("search_multi", None)
    kw = str(st.session_state.get("kw", "")).strip()
    if kw:
        rows = _name_search(kw)
        st.session_state["search_full"] = rows


st.set_page_config(page_title="卫星覆盖分析工作台", page_icon="🛰️", layout="wide")

st.title("🛰️ 卫星覆盖分析工作台")
st.caption("卫星导入 / 星座 / 覆盖分析 / 选星决策 —— 一体化工作台")
show_flash()

# 拦截 Streamlit 的页面级快捷键（c=清空缓存、r=重新运行），
# 否则在页面空白处按 Ctrl+C 会弹"清空缓存"确认框而不是复制文字。
# 输入框/文本域内不拦截，Ctrl+C 复制照常可用。
st.html(
    """
    <script>
    (function () {
      if (window.__stCovShortcutsBlocked) return;
      window.__stCovShortcutsBlocked = true;
      function block(e) {
        var k = (e.key || '').toLowerCase();
        if ((e.ctrlKey || e.metaKey) && k === 'c') {
          // Ctrl+C / Cmd+C 是复制手势：拦截掉 Streamlit 的快捷键，但不阻止浏览器复制
          e.stopPropagation();
          e.stopImmediatePropagation();
          return;
        }
        if (k !== 'c' && k !== 'r') return;
        var t = e.target;
        var tag = t && (t.tagName || '').toLowerCase();
        var editable = tag === 'input' || tag === 'textarea' ||
                       (t && t.isContentEditable);
        if (!editable) {
          e.stopPropagation();
          e.stopImmediatePropagation();
        }
      }
      window.addEventListener('keydown', block, true);
      document.addEventListener('keydown', block, true);
    })();
    </script>
    """,
    unsafe_allow_javascript=True,
)

with st.sidebar:
    st.header("说明")
    st.caption(f"缓存目录：`{sl.CACHE_DIR}`")
    st.caption(f"用户收藏：`{sl.USER_CATALOG}`")
    st.caption("CLI 入口保留：`python src\\satellite_loader.py`")
    st.divider()
    if st.button("停止并退出程序", key="btn_exit", icon=":material/stop:"):
        # 先尝试让浏览器关闭标签页（受浏览器安全限制，可能不成功），再停服务
        st.html("<script>window.close();</script>", unsafe_allow_javascript=True)
        # 再铺一层纯白全屏遮罩：盖住所有按钮，只显示退出提示
        st.html(
            """
            <style>
              #exit-overlay {
                position: fixed; inset: 0; z-index: 999999;
                background: #ffffff; display: flex; align-items: center;
                justify-content: center; text-align: center;
                font-family: "Microsoft YaHei", sans-serif; font-size: 22px;
                color: #333333;
              }
            </style>
            <div id="exit-overlay">程序已退出，请关闭此标签页</div>
            """,
        )
        threading.Timer(1.5, os._exit, args=(0,)).start()
        st.stop()
    st.caption("提示：浏览器不允许网页自己关闭标签页；退出后页面会变成白屏提示，手动关即可。")

tab_search, tab_collections, tab_constellations, tab_download, tab_analysis = st.tabs(
    ["① 搜索与收藏", "② 收藏夹管理", "③ 星座导入", "④ 下载与自检", "⑤ 覆盖分析与选星决策"],
    key="main_tabs", on_change="rerun")


# ==================== ① 搜索与收藏 ====================

if tab_search.open:
    st.subheader("搜索卫星（CelesTrak，免费、无 token）")
    c1, c2 = st.columns([2, 1])
    keyword = c1.text_input("名字关键字", key="kw",
                            placeholder="如 YAOGAN / GF-1 / STARLINK")
    norad_input = c2.text_input("NORAD 编号（精确，可选）", key="norad",
                                placeholder="如 25544")
    c3, c4, c5 = st.columns(3)
    intdes_input = c3.text_input("国际编号（可选）", key="intdes",
                                 placeholder="如 1998-067A（一次发射的全部星）")
    country = c4.text_input("所属国（可选）", key="ctry", placeholder="如 PRC / USA")
    launch_year = c5.text_input("发射年份（可选）", key="ly", placeholder="如 2023")
    c6, c7 = st.columns(2)
    min_alt = c6.number_input("最低高度 km（可选）", min_value=0.0, value=0.0, key="minalt")
    max_alt = c7.number_input("最高高度 km（可选，0=不限）", min_value=0.0, value=0.0,
                              key="maxalt")

    if st.button("搜索", key="btn_search"):
        norad_str = norad_input.strip()
        intdes_str = intdes_input.strip()
        kw = keyword.strip()
        if norad_str and not norad_str.isdigit():
            st.warning("NORAD 编号需为纯数字（如 25544）。")
        elif norad_str:
            with st.spinner("正在按编号搜索……"):
                rows = sl.search_metadata_by_norad(int(norad_str))
            st.session_state["search_kind"] = "norad"
            st.session_state["search_note"] = ("数据下载失败（可能被数据源限流），请稍后重试。"
                                               if rows is None else "")
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)   # 新搜索清空选择
        elif intdes_str:
            with st.spinner("正在按国际编号搜索……"):
                rows = sl.search_metadata_by_intdes(intdes_str)
            st.session_state["search_kind"] = "intdes"
            st.session_state["search_note"] = ("数据下载失败（可能被数据源限流），请稍后重试。"
                                               if rows is None else "")
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)
        elif kw:
            note = ""
            with st.spinner("正在搜索并下载缓存……"):
                rows = _name_search(kw)
            if rows is None:
                note = "数据下载失败（可能被数据源限流），请稍后重试。"
                rows = []
            elif not rows:
                # 变体重查：连字符 <-> 空格（Q78 拍板方案，标注来源）
                variant = kw.replace("-", " ").strip()
                if variant and variant != kw:
                    rows_v = _name_search(variant)
                    if rows_v:
                        note = f"原名「{kw}」未找到，已按变体「{variant}」查到 {len(rows_v)} 条。"
                        rows = rows_v
                if not rows:
                    # 拼写纠错提示：本地名字表编辑距离匹配（只用已有缓存，不触发下载）
                    sug = sl.suggest_similar_names(kw, n=3, offline=True)
                    if sug:
                        note = f"「{kw}」未找到。你是不是想找：{'、'.join(sug)}？"
            st.session_state["search_note"] = note
            st.session_state["search_kind"] = "name"
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)
        else:
            st.warning("请至少填写一个搜索条件：名字关键字 / NORAD 编号 / 国际编号。")

    rows = st.session_state.get("search_full")
    note = st.session_state.get("search_note")
    if note:
        st.warning(note)
    if rows is None:
        if not note:
            st.info("先在上方填写搜索条件（名字 / NORAD 编号 / 国际编号）点“搜索”，结果会出现在这里。")
    elif not rows:
        if not note:
            st.info("没有找到匹配的卫星，换个关键字试试。")
    else:
        df = pd.DataFrame(rows)
        df["序号"] = range(1, len(df) + 1)
        df = df.rename(columns={
            "name": "卫星名", "norad_id": "NORAD 编号", "object_id": "国际编号",
            "country": "所属",
            "launch_date": "发射日期", "altitude_km": "高度km",
            "inclination": "倾角°", "object_type": "类型"})

        st.markdown("**结果内筛选**（只影响显示与勾选，不重新搜索）")
        ver = st.session_state.get("filter_ver", 0)   # 重置时递增，控件换新 key 回到默认
        f1, f2 = st.columns(2)
        countries = sorted({x for x in df["所属"].tolist() if x})
        f_country = f1.multiselect("按所属国筛选", countries, key=f"f_country_{ver}")
        years = sorted({str(x)[:4] for x in df["发射日期"].tolist()
                        if str(x)[:4].isdigit()})
        f_year = f2.multiselect("按发射年份筛选", years, key=f"f_year_{ver}")
        strict = st.checkbox(
            "严格匹配：只显示名字以关键字开头的星（减少混入的无关星）",
            key=f"f_strict_{ver}",
            help="CelesTrak 按名字搜索是'包含匹配'。例如搜 GF 会匹配到 CYGFM（长光一号）等；"
                 "勾选后只保留名字以 GF 开头的星。")
        # 注意：不设 help，避免悬停提示框长时间停留（Streamlit 无法配置提示时长）
        st.button("重置筛选", key="btn_reset_filters", on_click=_reset_filters)
        f3, f4 = st.columns(2)
        bounds = _slider_bounds(df["高度km"].tolist())
        if bounds:
            f_alt = f3.slider("高度范围（km）", bounds[0], bounds[1], bounds,
                              key=f"f_alt_{ver}")
        else:
            f_alt = None
        bounds = _slider_bounds(df["倾角°"].tolist())
        if bounds:
            f_inc = f4.slider("倾角范围（°）", bounds[0], bounds[1], bounds,
                              key=f"f_inc_{ver}")
        else:
            f_inc = None

        # 逐个筛选，并记录每个筛掉多少条（便于排查是哪个筛选框在起作用）
        steps = []
        mask = pd.Series(True, index=df.index)
        if strict and keyword.strip():
            kw = keyword.strip().lower()
            m = df["卫星名"].str.lower().str.startswith(kw)
            if int((~m).sum()):
                steps.append(f"严格匹配(去{int((~m).sum())})")
            mask &= m
        if f_country:
            m = df["所属"].isin(f_country)
            if int((~m).sum()):
                steps.append(f"所属国{list(f_country)}(去{int((~m).sum())})")
            mask &= m
        if f_year:
            m = df["发射日期"].str[:4].isin(f_year)
            if int((~m).sum()):
                steps.append(f"发射年份{list(f_year)}(去{int((~m).sum())})")
            mask &= m
        if f_alt is not None:
            # 空值（元数据缺失）不参与过滤：未知不算"不符合"
            m = df["高度km"].isna() | df["高度km"].between(f_alt[0], f_alt[1])
            if int((~m).sum()):
                steps.append(f"高度[{f_alt[0]}~{f_alt[1]}](去{int((~m).sum())})")
            mask &= m
        if f_inc is not None:
            m = df["倾角°"].isna() | df["倾角°"].between(f_inc[0], f_inc[1])
            if int((~m).sum()):
                steps.append(f"倾角[{f_inc[0]}~{f_inc[1]}](去{int((~m).sum())})")
            mask &= m
        view = df[mask].copy()
        view["序号"] = range(1, len(view) + 1)   # 筛选后重新连续编号
        if steps:
            st.caption("当前生效的筛选：" + " ｜ ".join(steps))
        else:
            st.caption("当前无筛选，显示全部搜索结果。")

        st.dataframe(view[["序号", "卫星名", "NORAD 编号", "国际编号", "所属", "发射日期",
                           "高度km", "倾角°", "类型"]], hide_index=True)

        options = [f"{i}. {name} (NORAD {rid})" for i, name, rid in
                   zip(view["序号"], view["卫星名"], view["NORAD 编号"])]
        b1, b2 = st.columns(2)
        if b1.button("全选当前筛选结果", key="btn_sel_all"):
            st.session_state["search_multi"] = list(options)
            st.rerun()
        if b2.button("清空选择", key="btn_sel_none"):
            st.session_state["search_multi"] = []
            st.rerun()
        selected = st.multiselect("选择卫星（配合上方全选/清空按钮）", options,
                                  key="search_multi")
        st.caption(f"结果共 {len(df)} 条，筛选后 {len(view)} 条，当前已选 {len(selected)} 颗。")
        if len(df) > 500:
            st.caption("结果较多（>500 条），建议用④星座导入一键收藏，或直接存为名字规则，避免逐颗勾选。")

        sel_ids = []
        for opt in selected:
            try:
                rid = int(opt.rsplit("NORAD ", 1)[1].rstrip(")"))
            except (ValueError, IndexError):
                continue
            if rid not in sel_ids:
                sel_ids.append(rid)

        st.markdown("**保存或直接使用**")
        user_names = [x["name"] for x in sl.load_user_collections()]
        col_target = st.selectbox(
            "存入收藏夹", user_names, key="target_col") if user_names else None
        new_name = st.text_input("快速新建收藏夹（留空则用上面的）", key="newcol",
                                 placeholder="如 我的遥感")
        mode = st.radio("保存方式",
                        ["名字规则（活，自动纳入新星；包含关键字匹配的全部卫星，与勾选无关）",
                         "编号清单（固定，只保存勾选的星）"],
                        key="save_mode")
        search_kind = st.session_state.get("search_kind", "name")
        if search_kind != "name":
            st.caption("本次是「按编号 / 国际编号」搜索结果，只能保存为“编号清单”；"
                       "想要“名字规则”请用名字关键字重新搜索。")
        if mode.startswith("名字规则") and selected:
            st.caption("注意：名字规则会包含关键字匹配的全部卫星，当前勾选不会单独生效；"
                       "想只要勾选的星，请选“编号清单”。")
        if strict and mode.startswith("名字规则"):
            st.caption("提示：名字规则解析时仍按“名字包含关键字”取星（严格匹配只影响勾选），"
                       "可能再次混入同名其他系列（如 GAOFEN 会带回吉林一号高分）；"
                       "要锁定当前严格筛选出的这批星，请改用“编号清单”保存。")
        if st.button("加入收藏夹", key="btn_add"):
            target = col_target
            if new_name.strip():
                _snapshot()
                sl.create_collection(new_name.strip())
                target = new_name.strip()
            if not target:
                st.error("请先选择或新建一个收藏夹。")
            elif mode.startswith("名字规则"):
                if st.session_state.get("search_kind", "name") != "name":
                    st.error("按编号 / 国际编号搜索的结果不能保存为名字规则，请改用“编号清单”保存。")
                else:
                    _snapshot()
                    if sl.add_entry(target, "name_filter", keyword.strip()):
                        flash(f"已把名字规则「{keyword}」加入「{target}」")
                    else:
                        flash("该收藏夹里已经有相同的名字规则，未重复添加。", kind="warning")
                    st.rerun()
            else:
                if not sel_ids:
                    st.error("请先用多选框选择至少一颗星（或点“全选当前筛选结果”）。")
                else:
                    _snapshot()
                    if sl.add_entry(target, "norad_list", " ".join(map(str, sel_ids))):
                        flash(f"已把 {len(sel_ids)} 颗星（编号清单）加入「{target}」")
                    else:
                        flash("该收藏夹里已经有相同的编号清单，未重复添加。", kind="warning")
                    st.rerun()
        if st.button("直接解析所选（不入收藏夹）", key="btn_resolve_sel"):
            if not sel_ids:
                st.error("请先选择至少一颗星（或点“全选当前筛选结果”）。")
            else:
                with st.spinner("正在下载/读取 TLE……"):
                    temp = {"entries": [{"type": "norad_list",
                                         "value": " ".join(map(str, sel_ids))}]}
                    recs = sl.resolve_collection(temp, offline=False)
                st.success(f"已解析 {len(recs)} 颗卫星（不入收藏夹，TLE 已缓存）")
                if recs:
                    st.dataframe(pd.DataFrame(
                        [{"卫星名": r["name"], "NORAD 编号": r["norad_id"]} for r in recs[:200]]),
                        hide_index=True)


# ==================== ② 收藏夹管理 ====================

if tab_collections.open:
    st.subheader("收藏夹管理")
    u1, u2, u3 = st.columns([1, 1, 3])
    u1.button("撤销上一步", key="btn_undo", on_click=_do_undo, icon=":material/undo:")
    u2.button("重做下一步", key="btn_redo", on_click=_do_redo, icon=":material/redo:")
    u3.caption("撤销/重做只对本次打开软件期间的收藏夹操作有效，关闭页面后历史清空。")
    st.caption("一个收藏夹分两部分：**自动规则**（名字规则，活）和 **手动卫星**（固定清单）；"
               "解析下载时自动合并、按编号去重。")
    all_c = sl.load_all_collections()
    if not all_c:
        st.info("暂无收藏夹。")
    else:
        labels = [_collection_label(x) for x in all_c]
        idx = st.selectbox("选择收藏夹", range(len(all_c)),
                           format_func=lambda i: labels[i], key="col_sel")
        c = all_c[idx]
        tag = ("（📡 星座收藏夹）" if c.get("kind") == "constellation"
               else "（⭐ 虚拟星座，来自选星）" if c.get("kind") == "virtual_constellation" else "")
        st.markdown(f"**{c['name']}**{tag} —— {c.get('description') or '（无备注）'}")
        if c.get("builtin"):
            st.caption("内置夹只读，不能直接修改；想要自己的版本，请新建收藏夹后手动添加同样的规则或编号。")

        # ---------- 分区 1：自动规则（名字规则） ----------
        st.markdown("### ① 自动规则（名字规则）")
        st.caption("解析时自动匹配名字含关键字的全部在轨卫星；新发射的同名卫星会自动纳入，不用手动更新。")
        rule_entries = [e for e in c["entries"] if e["type"] == "name_filter"]
        if not rule_entries:
            st.caption("（暂无自动规则）")
        for i, e in enumerate(c["entries"]):
            if e["type"] != "name_filter":
                continue
            r1, r2 = st.columns([5, 1])
            r1.markdown(f"名字规则：**{e['value']}**")
            if not c.get("builtin") and r2.button("删除", key=f"del_rule_{c['name']}_{i}"):
                _snapshot()
                sl.remove_entry(c["name"], i)
                flash(f"已删除名字规则「{e['value']}」")
                st.rerun()
        if not c.get("builtin"):
            a1, a2 = st.columns([4, 1])
            new_rule = a1.text_input("新增名字规则（关键字，如 YAOGAN）", key=f"new_rule_{c['name']}")
            if a2.button("添加规则", key=f"btn_new_rule_{c['name']}"):
                if not new_rule.strip():
                    st.warning("请先输入关键字。")
                else:
                    _snapshot()
                    ok = sl.add_entry(c["name"], "name_filter", new_rule.strip())
                    flash(f"已添加名字规则「{new_rule.strip()}」" if ok
                          else "已有相同的名字规则，未重复添加。",
                          kind="success" if ok else "warning")
                    st.rerun()

        st.divider()

        # ---------- 分区 2：手动卫星（固定清单） ----------
        st.markdown("### ② 手动卫星（固定清单）")
        st.caption("手动勾选或输入编号保存的星，固定不变（新发射的星不会自动进来）；"
                   "可单颗删除、追加编号，或把整批归纳成自动规则。")
        list_entries = [e for e in c["entries"] if e["type"] == "norad_list"]
        file_entries = [e for e in c["entries"] if e["type"] == "file"]
        if not list_entries and not file_entries:
            st.caption("（暂无手动卫星）")
        else:
            declared_ids = sorted({int(x) for e in list_entries
                                   for x in e["value"].replace(",", " ").split()
                                   if x.strip().isdigit()})
            if declared_ids:
                with st.spinner("读取编号清单中的卫星……"):
                    list_recs = sl.resolve_collection(
                        {"entries": [dict(e) for e in list_entries]}, offline=False)
                by_id = {r["norad_id"]: r for r in list_recs}
                rows = []
                for rid in declared_ids:
                    r = by_id.get(rid)
                    rows.append({"卫星名": r["name"] if r else f"（编号 {rid}：未取到 TLE）",
                                 "NORAD 编号": rid})
                # 把"行号 -> NORAD 编号"存进会话状态，供行内删除回调按行定位
                st.session_state[f"manual_ids_{c['name']}"] = [r["NORAD 编号"] for r in rows]

                def _del_sat(collection_name):
                    # ButtonColumn 点击触发：行号在 session_state[key] 里（{"row","label"}）
                    click = st.session_state.get(f"btn_del_sat_{collection_name}")
                    if click is None:
                        return
                    ids = st.session_state.get(f"manual_ids_{collection_name}", [])
                    row = click["row"]
                    if not 0 <= row < len(ids):
                        return
                    rid = ids[row]
                    _snapshot()
                    if sl.remove_norad_from_collection(collection_name, rid):
                        flash(f"已从「{collection_name}」清单中删除卫星 NORAD {rid}")
                    else:
                        flash(f"删除失败：编号 {rid} 不在收藏夹中。", kind="warning")

                df_manual = pd.DataFrame(rows)
                if c.get("builtin"):
                    st.dataframe(df_manual, hide_index=True, width="stretch")
                else:
                    df_manual["删除"] = ":material/delete:"
                    st.dataframe(
                        df_manual, hide_index=True, width="stretch",
                        column_config={
                            "删除": st.column_config.ButtonColumn(
                                "删除", on_click=_del_sat, args=(c["name"],),
                                key=f"btn_del_sat_{c['name']}"),
                        })

                if not c.get("builtin"):
                    # 追加编号
                    b1, b2 = st.columns([4, 1])
                    append_ids = b1.text_input(
                        "追加编号（空格或逗号分隔，如 25544 31490）",
                        key=f"append_ids_{c['name']}")
                    if b2.button("追加", key=f"btn_append_{c['name']}"):
                        ids = [int(x) for x in append_ids.replace(",", " ").split()
                               if x.strip().isdigit()]
                        if not ids:
                            st.warning("请输入有效的 NORAD 编号。")
                        else:
                            _snapshot()
                            sl.add_norad_to_collection(c["name"], ids)
                            flash(f"已向「{c['name']}」追加 {len(ids)} 颗星（重复编号自动去重）")
                            st.rerun()

                    # 归纳成自动规则：这批星有公共名字前缀时给出建议
                    names = [r["name"] for r in by_id.values() if r.get("name")]
                    prefix = sl.longest_common_prefix(names) if len(names) >= 2 else ""
                    if len(prefix) >= 3:
                        k1, k2 = st.columns([5, 1])
                        k1.markdown(f"这批星的名字都带 **{prefix}** —— 可转成自动规则（以后自动纳入所有同名新星）：")
                        if k2.button(f"转为自动规则「{prefix}」", key=f"btn_to_rule_{c['name']}"):
                            _snapshot()
                            ok = sl.add_entry(c["name"], "name_filter", prefix)
                            flash(f"已添加名字规则「{prefix}」" if ok
                                  else "已有相同名字规则，未重复添加。",
                                  kind="success" if ok else "warning")
                            st.rerun()

            # 导入文件条目：只读展示，可整条移除
            for fi, e in enumerate(file_entries):
                try:
                    text = Path(e["value"]).read_text(encoding="ascii", errors="replace")
                    f_recs = sl.parse_tle_records(text)
                    f_n = len(f_recs) if f_recs else len(
                        [ln for ln in text.splitlines() if ln.strip().isdigit()])
                except Exception:
                    f_n = "?"
                f1, f2 = st.columns([5, 1])
                f1.markdown(f"📄 导入文件：**{Path(e['value']).name}**（{f_n} 颗，只读）")
                if not c.get("builtin") and f2.button("移除文件条目", key=f"del_file_{c['name']}_{fi}"):
                    fidx = next((i for i, x in enumerate(c["entries"])
                                 if x["type"] == "file" and x["value"] == e["value"]), None)
                    if fidx is not None:
                        _snapshot()
                        sl.remove_entry(c["name"], fidx)
                        flash(f"已移除导入文件条目「{Path(e['value']).name}」")
                        st.rerun()

        st.divider()

        # ---------- 查看本夹全部卫星（完整信息，只查看不保存） ----------
        with st.expander("查看本夹全部卫星（完整信息，只查看不保存）"):
            if st.button("查看本夹全部卫星", key="btn_show_col_sats"):
                if not c["entries"]:
                    st.warning("这个收藏夹是空的，先添加条目。")
                else:
                    bar = st.progress(0.0, text="开始解析……")
                    def cb(done, total, msg):
                        if total:
                            bar.progress(min(done / total, 1.0), text=msg)
                        else:
                            bar.progress(0.0, text=msg)
                    try:
                        rows_meta = sl.resolve_collection_with_meta(c, progress=cb)
                    except Exception as ex:
                        st.error(f"解析失败：{ex}")
                    else:
                        if not rows_meta:
                            st.warning("未能获取卫星数据：缓存被清空且数据源下载被拒（限流/网络异常）时会出现；"
                                       "请稍后重试，或到③页重新解析。")
                        st.success(f"本夹共 {len(rows_meta)} 颗卫星")
                        st.caption("需要保存成 TLE 文件请到 ③下载与自检 页“解析下载”。")
                        n_incomplete = sum(1 for x in rows_meta if not x.get("meta_complete"))
                        if n_incomplete:
                            st.caption(f"其中 {n_incomplete} 颗星的所属/发射日期等档案未联网补全"
                                       "（星数较多，避免被数据源拒绝）；高度/倾角为轨道近似值。")
                        if rows_meta:
                            df_all = pd.DataFrame(rows_meta)
                            df_all["类型"] = df_all["object_type"].map(sl._object_type_label)
                            df_all = df_all.replace("", "—").fillna("—")
                            st.dataframe(df_all.rename(columns={
                                "name": "卫星名", "norad_id": "NORAD 编号", "country": "所属",
                                "launch_date": "发射日期", "altitude_km": "高度km",
                                "inclination": "倾角°"})[
                                ["卫星名", "NORAD 编号", "所属", "发射日期", "高度km", "倾角°", "类型"]],
                                hide_index=True)

        # ---------- 管理收藏夹 ----------
        st.markdown("### 管理收藏夹")
        m1, m2 = st.columns(2)
        with m1:
            new_name = st.text_input("新建收藏夹名称", key="new_col_name")
            if st.button("新建", key="btn_new_col"):
                if new_name.strip():
                    _snapshot()
                    ok = sl.create_collection(new_name.strip())
                    flash("已新建收藏夹：" + new_name.strip() if ok
                          else f"已存在同名收藏夹：{new_name.strip()}，未新建。",
                          kind="success" if ok else "warning")
                st.rerun()
            if not c.get("builtin"):
                rename_to = st.text_input("改名为", key="rename_to")
                if st.button("改名", key="btn_rename"):
                    if rename_to.strip():
                        _snapshot()
                        ok = sl.rename_collection(c["name"], rename_to.strip())
                        flash(f"已改名：{c['name']} -> {rename_to.strip()}" if ok
                              else "改名失败（可能重名）。",
                              kind="success" if ok else "warning")
                        st.rerun()
                confirm_del = st.checkbox("确认删除「当前夹」（不可撤销）", key="del_confirm")
                if st.button("删除", key="btn_del"):
                    if confirm_del:
                        _snapshot()
                        ok = sl.delete_collection(c["name"])
                        flash(f"已删除收藏夹：{c['name']}" if ok else "删除失败。",
                              kind="success" if ok else "warning")
                        st.rerun()
                    else:
                        st.warning("请先勾选确认。")
        with m2:
            st.caption("导入本地文件（TLE 或编号清单），程序会复制到 data/user_files/ 统一管理：")
            f_up = st.file_uploader(
                "选择文件（.txt / .tle）", type=["txt", "tle"], key="file_up",
                help="TLE：标准两行轨道根数，每颗星 3 行（名称行 + 两行数字），可多颗；"
                     "编号清单：每行一个 NORAD 编号。推荐流程：①页搜索卫星 → 加入收藏夹 → "
                     "④页'下载与自检'自动下载；也可从 CelesTrak（celestrak.org）、"
                     "spacemapper.cn、Space-Track（需注册）获取文件后手动导入。"
                     "建议用分析日期前 2 天内的 TLE。")
            if f_up is not None and st.button("导入并加入当前收藏夹", key="btn_import"):
                if c.get("builtin"):
                    st.error("内置夹只读，请新建收藏夹后再导入。")
                else:
                    sl._ensure_dirs()
                    dst = sl.USER_FILES_DIR / f_up.name
                    if dst.exists():
                        dst = sl.USER_FILES_DIR / f"{Path(f_up.name).stem}_{int(time.time())}{Path(f_up.name).suffix}"
                    dst.write_bytes(f_up.getvalue())
                    _snapshot()
                    ok = sl.add_entry(c["name"], "file", str(dst))
                    flash(f"已导入并复制到 {dst.name}" if ok
                          else "已有相同条目，未重复添加。",
                          kind="success" if ok else "warning")
                    st.rerun()


# ==================== ③ 下载与自检 ====================

if tab_download.open:
    st.subheader("下载解析（合并多个收藏夹 → 生成 TLE 文件）")
    st.caption("用途：把选中的收藏夹合并、去重，生成成品 TLE 文件；"
               "只想查看某个夹的内容请用 ②页的“查看本夹全部卫星”。")
    all_cols = sl.load_all_collections()
    names = [_collection_label(x) for x in all_cols]
    sel_names = st.multiselect("选择要解析的收藏夹", names, key="resolve_sel")
    r1, r2 = st.columns(2)
    if r1.button("解析下载", key="btn_resolve"):
        real = [_strip_collection_label(n) for n in sel_names]
        if not real:
            st.warning("请至少选择一个收藏夹。")
        else:
            bar = st.progress(0.0, text="开始解析……")
            def cb(done, total, msg):
                if total:
                    bar.progress(min(done / total, 1.0), text=msg)
                else:
                    bar.progress(0.0, text=msg)
            recs = sl.resolve_collections_to_records(real, progress=cb)
            path = sl.save_resolved_tle(" + ".join(real), recs)
            if not recs:
                st.warning("没有解析到卫星：缓存被清空且数据源下载被拒（限流）时会出现；请稍后重试。")
            st.success(f"解析完成：{len(recs)} 颗卫星（按 NORAD 编号去重）")
            st.caption(f"已合并为一个文件：`{path}`（下次解析直接复用缓存，不重复下载）")
            if recs:
                st.dataframe(pd.DataFrame(
                    [{"卫星名": r["name"], "NORAD 编号": r["norad_id"]} for r in recs[:200]]),
                    hide_index=True)
    if r2.button("强制更新（重下全集与星座缓存）", key="btn_force_refresh",
                 help="忽略缓存，重新下载全集 active.tle 与选中星座分组；编号清单随全集自动同步"):
        real = [_strip_collection_label(n) for n in sel_names]
        with st.spinner("正在强制更新（联网）……"):
            n_req = sl.force_refresh_collections(real)
        flash(f"强制更新完成：本次请求 {n_req} 次（全集 + 选中星座分组），编号清单已随全集同步。")
        st.rerun()

    st.markdown("---")
    st.subheader("缓存管理（data/tle_cache/）")
    cache_rows = []
    for p in sorted(sl.CACHE_DIR.rglob("*")):
        if p.is_file():
            rel = p.relative_to(sl.CACHE_DIR)
            cache_rows.append({
                "文件": str(rel),
                "大小 KB": round(p.stat().st_size / 1024, 1),
                "更新于(小时前)": round((time.time() - p.stat().st_mtime) / 3600, 1),
            })
    total_files = len(cache_rows)
    total_kb = round(sum(r["大小 KB"] for r in cache_rows), 1)
    st.caption(f"共 {total_files} 个缓存文件，合计约 {total_kb} KB。")
    with st.expander("查看详细缓存列表（默认折叠）"):
        if cache_rows:
            st.dataframe(pd.DataFrame(cache_rows), hide_index=True)
            st.caption("文件说明：active.tle=全集TLE缓存；meta/…=搜索结果元数据；"
                       "name/…=按名字搜索的TLE；norad/…=单星TLE；"
                       "resolved/…=每个收藏夹合并的TLE（重点看这个）")
        else:
            st.info("暂无缓存文件。")
    confirm_clear = st.checkbox(
        "确认清空全部缓存（下次需全部重新下载；CelesTrak 有 2 小时更新窗口和频率限制，"
        "刚清空时可能下载失败，建议仅在缓存损坏时清空）", key="clear_confirm")
    if st.button("清空缓存", key="btn_clear_cache"):
        if not confirm_clear:
            st.warning("请先勾选确认。")
        else:
            for item in sl.CACHE_DIR.iterdir():
                if item.is_dir():
                    shutil.rmtree(item)
                elif item.is_file():
                    item.unlink()
            flash("已清空缓存，下次会自动重新下载。")
            st.rerun()

    st.markdown("---")
    st.subheader("自检（联网部分需要网络）")
    with st.expander("自检是什么？为什么要自检？"):
        st.markdown(
            "**自检 = 自动检查“卫星覆盖分析工作台”数据环节是否正常**（43 项，7 组）。\n\n"
            "**为什么要做**：本软件的所有卫星数据都来自 CelesTrak 网站，中间还要经过本地缓存、"
            "收藏夹、解析、合并去重等环节。网络波动、缓存损坏、程序更新都可能让某一环悄悄出错，"
            "自检能把这些环节逐项过一遍，确保进入覆盖分析的卫星数据是可靠的。\n\n"
            "**自检会做什么**：\n"
            "1. TLE 解析（离线）——确认能读懂 TLE 文件、算得出星下点；\n"
            "2. 编目搜索（联网）——确认能搜到卫星档案（名字/国家/轨道参数）；\n"
            "3. 缓存行为——确认第二次查询不再重复下载；\n"
            "4. 收藏夹增删改移——用临时测试夹验证改名/删除/移动（自动清理，不碰你的收藏夹）；\n"
            "5. 收藏夹解析——名字规则 / 编号清单 / 导入文件三种方式都能解析成卫星；\n"
            "6. 多夹合并去重——确认重复的星只算一次；\n"
            "7. 星座导入与搜索增强——星座整组下载、名字规则黑名单过滤（星链不再被截断）、拼写纠错提示。\n\n"
            "**什么时候建议自检**：\n"
            "1. 第一次使用、或软件/代码更新之后；\n"
            "2. 网络不好、下载报过错（比如缓存下载失败）之后；\n"
            "3. 手动动过缓存或收藏夹文件之后；\n"
            "4. 要导入大量卫星（几百上千颗）之前；\n"
            "5. 覆盖分析结果出现异常、想排查是不是数据环节出问题时。\n\n"
            "**注意**：自检会联网下载少量数据（走缓存，不重复请求）；"
            "不会改动你的真实收藏夹，也不会影响覆盖分析结果。")
    if st.button("运行自检", key="btn_selftest"):
        with st.spinner("正在运行自检（约 10 秒）……"):
            results = validate_loader.run_all()
        ok_n = sum(1 for _, ok, _ in results if ok)
        st.metric("通过", f"{ok_n} / {len(results)}")
        for name, ok, detail in results:
            if ok:
                st.success(f"{name}　{detail}")
            else:
                st.error(f"{name}　{detail}")


# ==================== ④ 星座导入 ====================

if tab_constellations.open:
    st.subheader("星座 / 常用卫星导入")
    st.caption("按用途选一个星座（或常用单星，会标注“单星”），一键加入收藏夹（自动建夹），"
               "之后到③页解析下载整组 TLE；遥感家族没有现成 GROUP 分组的走名字规则，"
               "新发射的同名星会自动纳入。")
    cons = sl.load_constellations()
    if not cons:
        st.info("暂无星座目录（data/constellations.toml 缺失或为空）。")
    else:
        purposes = list(dict.fromkeys(c["purpose"] for c in cons))
        p1, p2 = st.columns([2, 3])
        purpose = p1.segmented_control("用途分类", purposes,
                                       key="cons_purpose", default=purposes[0], persist_state="session")
        cands = [c for c in cons if c["purpose"] == purpose]
        sel_name = p2.selectbox("星座", [c["name"] for c in cands], key="cons_sel", persist_state="session")
        c = next(x for x in cands if x["name"] == sel_name)
        src = c["source"]
        sing = "（单星）" if c.get("singleton") else ""
        st.markdown(f"**{c['name']}**{sing} —— {c.get('description') or '（暂无简介）'}")
        st.caption("数据来源：" + ("CelesTrak 整组下载（GROUP=" + src["value"] + "）"
                                   if src["type"] == "celestrak_group"
                                   else "名字规则（关键字 " + src["value"] + "）"))
        b1, b2 = st.columns(2)
        if b1.button("预览成员数", key="btn_cons_preview"):
            with st.spinner("正在下载整组 TLE（首次稍慢，之后走缓存）……"):
                recs = sl.resolve_collection(
                    {"entries": [{"type": src["type"], "value": src["value"]}]},
                    offline=False)
            if not recs:
                st.warning("未取到该星座数据：缓存被清空且数据源下载被拒（限流）时会出现；请稍后重试。")
            st.session_state["cons_preview"] = {"name": c["name"], "recs": recs}
            st.rerun()
        if b2.button("加入收藏夹（自动建夹）", key="btn_cons_add"):
            _snapshot()
            if sl.find_collection(sl.load_user_collections(), c["name"]) is None:
                sl.create_collection(c["name"], kind="constellation")
            ok = sl.add_entry(c["name"], src["type"], src["value"])
            flash(f"已把「{c['name']}」加入收藏夹（📡 星座收藏夹）" if ok
                  else "该收藏夹里已有相同条目，未重复添加。",
                  kind="success" if ok else "warning")
            st.rerun()
        prev = st.session_state.get("cons_preview")
        if prev and prev.get("name") == c["name"]:
            recs = prev["recs"]
            st.success(f"「{c['name']}」解析到 {len(recs)} 颗卫星（按 NORAD 编号去重）")
            st.dataframe(pd.DataFrame(
                [{"卫星名": r["name"], "NORAD 编号": r["norad_id"]} for r in recs[:100]]),
                hide_index=True)


# ==================== ⑤ 覆盖分析 ====================

if tab_analysis.open:
    st.subheader("覆盖分析")
    st.caption("选好收藏夹（卫星来源）后，设置目标区域与时间直接开算；"
               "覆盖判定参数沿用已拍板默认值（视场半角 30°、最小仰角 10°、1 分钟步长 + 10 秒精化）。")
    all_cols = sl.load_all_collections()
    cov_names = [_collection_label(x) for x in all_cols]

    c1, c2 = st.columns(2)
    with c1:
        cov_mode = st.radio("目标区域", ["点+半径", "地区多边形"], key="cov_mode",
                            horizontal=True)
        grid_spacing = 0.2
        if cov_mode == "点+半径":
            _locs = sl.load_locations()
            _loc_label = lambda x: (f"{x['name']} ({x['lat']:.4f}, {x['lon']:.4f})"
                                    + ("（内置）" if x.get("source") == "builtin" else "（用户）"))
            _loc_names = ["（手动输入）"] + [_loc_label(x) for x in _locs]
            cov_loc = st.selectbox("常用地点（内置+用户）", _loc_names, key="cov_loc")
            _loc = None
            if cov_loc != "（手动输入）":
                _loc = next((x for x in _locs if _loc_label(x) == cov_loc), None)
                if _loc and st.session_state.get("cov_loc_applied") != cov_loc:
                    st.session_state["cov_lat"] = float(_loc["lat"])
                    st.session_state["cov_lon"] = float(_loc["lon"])
                    st.session_state["cov_rad"] = float(_loc.get("radius_km", 5.0))
                    st.session_state["cov_loc_applied"] = cov_loc
                    st.rerun()
            la, lo, ra = st.columns(3)
            cov_lat = la.number_input("纬度", value=30.25, step=0.01, key="cov_lat")
            cov_lon = lo.number_input("经度", value=120.15, step=0.01, key="cov_lon")
            cov_rad = ra.number_input("半径(km)", value=5.0, min_value=0.1, step=0.5,
                                      key="cov_rad")
            if _loc is not None and _loc.get("source") != "builtin":
                if st.session_state.get("cov_loc_del") == cov_loc:
                    dc1, dc2 = st.columns(2)
                    if dc1.button("确认删除该地点", key="btn_cov_loc_del_yes"):
                        sl.delete_user_location(_loc["name"])
                        st.session_state.pop("cov_loc_del", None)
                        st.session_state.pop("cov_loc_applied", None)
                        flash(f"已删除常用地点：{_loc['name']}", kind="success")
                        st.rerun()
                    if dc2.button("取消", key="btn_cov_loc_del_no"):
                        st.session_state.pop("cov_loc_del", None)
                        st.rerun()
                elif st.button("删除该用户地点", key="btn_cov_loc_del"):
                    st.session_state["cov_loc_del"] = cov_loc
                    st.rerun()
            sv1, sv2 = st.columns([3, 1])
            new_loc_name = sv1.text_input("保存当前坐标为常用地点（留空不保存）", key="cov_loc_new")
            if sv2.button("保存地点", key="btn_cov_loc_save"):
                if new_loc_name.strip():
                    _same_builtin = any(b["name"] == new_loc_name.strip()
                                        for b in _locs if b.get("source") == "builtin")
                    sl.save_user_location(new_loc_name.strip(), float(cov_lat),
                                          float(cov_lon), float(cov_rad))
                    st.session_state.pop("cov_loc_applied", None)
                    flash(f"已保存常用地点：{new_loc_name.strip()}"
                          + ("（与内置同名，下拉中以“（用户）”标记区分）" if _same_builtin else ""),
                          kind="success")
                    st.rerun()
                else:
                    flash("请先输入地点名称再保存。", kind="warning")
        else:
            geo_dir = sl.PROJECT_ROOT / "data" / "geo"
            geo_files = {p.stem: str(p) for p in sorted(geo_dir.glob("*.json"))}
            cov_geo = st.selectbox("预设地区", list(geo_files), key="cov_geo")
            cov_grid = st.number_input("网格间距(°)", value=0.2, min_value=0.05,
                                       step=0.05, key="cov_grid")
            grid_spacing = float(cov_grid)
        d1, d2 = st.columns(2)
        cov_t0 = d1.datetime_input("开始时间 (UTC)", value=datetime(2026, 8, 3, 0, 0,
                                                                    tzinfo=timezone.utc),
                                   key="cov_t0")
        cov_t1 = d2.datetime_input("结束时间 (UTC)", value=datetime(2026, 8, 3, 8, 0,
                                                                    tzinfo=timezone.utc),
                                   key="cov_t1")
        cov_sats = st.multiselect("卫星来源（收藏夹）", cov_names, key="cov_cols")
        up = st.file_uploader(
            "上传 TLE 文件（可选，作为卫星来源）", type=["tle", "txt"], key="cov_tle_up",
            help="标准两行轨道根数（TLE）文件：每颗星 3 行（名称行 + 两行数字），可含多颗星，"
                 "纯文本 .txt/.tle。推荐流程：①页搜索卫星 → 加入收藏夹 → ④页'下载与自检'"
                 "（自动下载 TLE 缓存）→ 本页分析；也可从 CelesTrak（celestrak.org，免费无需注册）、"
                 "spacemapper.cn、Space-Track（需注册）获取 TLE 文件后手动上传。"
                 "建议使用分析日期前 2 天内更新的 TLE，过期 TLE 外推误差会变大。")
        with st.expander("TLE 文件格式示例（点击展开）"):
            st.code("YAOGAN-41\n"
                    "1 58582U 23197A   26216.04232449 -.00000351  00000+0  00000+0 0  9993\n"
                    "2 58582   3.0457 313.8823 0014312 202.7838 294.2930  1.00266763  9805")
            st.caption("每颗星 3 行：第 1 行名称（可中文），第 2~3 行为两行轨道根数；"
                       "多颗星按此格式依次排列，一个文件可包含全部卫星。")
        if up is not None:
            ukey = f"{up.name}|{up.size}"
            if st.session_state.get("cov_tle_up_done") != ukey:
                stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                dest = sl.USER_FILES_DIR / f"upload_{stamp}_{up.name}"
                dest.write_bytes(up.getvalue())
                col_name = f"上传TLE-{Path(up.name).stem}-{datetime.now():%H%M%S}"
                if sl.create_collection(col_name, description=f"上传的 TLE 文件：{up.name}", kind=""):
                    sl.add_entry(col_name, "file", str(dest))
                    st.session_state["cov_tle_up_done"] = ukey
                    st.session_state["cov_cols"] = [_collection_label({"name": col_name, "kind": ""})]
                    st.rerun()
                else:
                    st.warning(f"同名收藏夹已存在：{col_name}，请改名后再上传。")
    with c2:
        p1, p2 = st.columns(2)
        cov_fov = p1.number_input("视场半角(°)", value=30.0, min_value=1.0, max_value=89.0,
                                  step=1.0, key="cov_fov")
        cov_elev = p2.number_input("最小仰角(°)", value=10.0, min_value=0.0, max_value=85.0,
                                   step=1.0, key="cov_elev")
        p3, p4 = st.columns(2)
        cov_step = p3.number_input("时间步长(分钟)", value=1, min_value=1, step=1,
                                   key="cov_step")
        cov_refine = p4.number_input("边界精化(秒)", value=10, min_value=0, step=5,
                                     key="cov_refine")
        st.caption("时间步长越小越准但越慢（1 分钟 + 边界精化是已验证的默认）。"
                   "结果会同时保存到 output/ 目录。")

    if st.button("开始覆盖分析", key="btn_cov_run", type="primary"):
        real_cols = [_strip_collection_label(n) for n in cov_sats]
        if not real_cols:
            st.warning("请至少选择一个收藏夹作为卫星来源。")
        elif cov_t1 <= cov_t0:
            st.warning("结束时间需晚于开始时间。")
        else:
            bar = st.progress(0.0, text="解析卫星（走缓存，不重复下载）……")
            def cb(done, total, msg):
                bar.progress(min(done / total, 1.0), text=msg)
            recs = sl.resolve_collections_to_records(real_cols, progress=cb)
            if not recs:
                st.warning("没有解析到卫星（可能缓存缺失且数据源不可用），请先到③页解析。")
            else:
                ts = sl.load.timescale(builtin=True)
                t, dt_list = cov.make_time_array(
                    ts, cov_t0.strftime("%Y-%m-%d %H:%M:%S"),
                    cov_t1.strftime("%Y-%m-%d %H:%M:%S"), int(cov_step))
                cfg = {
                    "region_name": "",
                    "region_mode": "circle" if cov_mode == "点+半径" else "polygon",
                    "tle_file": "GUI 收藏夹解析（合并 TLE）",
                    "fov_half_angle_deg": float(cov_fov),
                    "min_elevation_deg": float(cov_elev),
                    "step_minutes": int(cov_step),
                    "refine_step_seconds": int(cov_refine),
                    "grid_spacing_deg": grid_spacing,
                    "save_gantt": False,
                    "save_plots": False,
                    "max_gantt_rows": 15,
                }
                if cov_mode == "点+半径":
                    region = {"mode": "circle", "lat": float(cov_lat),
                              "lon": float(cov_lon), "radius_km": float(cov_rad)}
                    cfg["target"] = {"lat": float(cov_lat), "lon": float(cov_lon),
                                     "radius_km": float(cov_rad)}
                    cfg["region_name"] = f"圆心({cov_lat:.4f},{cov_lon:.4f}) 半径{cov_rad}km"
                else:
                    polygon = cov.load_region_polygon(geo_files[cov_geo])
                    grid_lats, grid_lons = cov.make_grid_points(polygon, grid_spacing)
                    region = {"mode": "polygon", "lats": grid_lats, "lons": grid_lons}
                    cfg["region_name"] = cov_geo
                cov_result = cov.analyze_collection_pipeline(
                    recs, region, cfg,
                    cov_t0.strftime("%Y-%m-%d %H:%M:%S"),
                    cov_t1.strftime("%Y-%m-%d %H:%M:%S"),
                    progress=lambda frac, msg: bar.progress(min(frac, 1.0), text=msg))
                if cov_result is None:
                    st.warning("该时段内没有卫星覆盖目标区域（无窗口）。可尝试：更长时段、"
                               "更大的目标区域，或更大的视场角。")
                else:
                    st.session_state["cov_result"] = cov_result
                    st.session_state.pop("sel_result", None)
                    st.session_state.pop("cov_xlsx", None)

    res = st.session_state.get("cov_result")
    if res:
        dfs = res["dfs"]
        df_windows, df_summary, df_state, df_gaps = dfs
        st.success(f"分析完成：卫星池 {res['n_sats']} 颗（全部导入）参与分析，"
                   f"{len(df_summary)} 颗有覆盖，共 {res['n_win']} 个窗口"
                   f"（目标：{res['cfg']['region_name']}）")
        if "cov_xlsx" not in st.session_state:
            meta = {
                "region_name": res["cfg"].get("region_name", ""),
                "region_mode": res["cfg"].get("region_mode", "circle"),
                "start_utc": str(df_state["start_utc"].iloc[0])[:19],
                "end_utc": str(df_state["end_utc"].iloc[-1])[:19],
                "step_minutes": res["cfg"].get("step_minutes", 1),
                "refine_step_seconds": res["cfg"].get("refine_step_seconds", 10),
                "fov_half_angle_deg": res["cfg"].get("fov_half_angle_deg", 30.0),
                "min_elevation_deg": res["cfg"].get("min_elevation_deg", 10.0),
                "grid_spacing_deg": res["cfg"].get("grid_spacing_deg", 0.2),
                "n_satellites_total": res["n_sats"],
                "n_satellites_covered": len(df_summary),
            }
            out_dir = sl.PROJECT_ROOT / "output"
            out_dir.mkdir(exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = str(out_dir / f"coverage_gui_{stamp}.xlsx")
            qc.export_excel(meta, df_state, df_windows, df_summary, df_gaps,
                            out_path, "整个分析时段")
            st.session_state["cov_xlsx"] = {"data": Path(out_path).read_bytes(),
                                            "name": Path(out_path).name,
                                            "path": out_path}
        xlsx = st.session_state["cov_xlsx"]
        st.download_button(
            "下载覆盖 Excel", data=xlsx["data"], file_name=xlsx["name"],
            key="btn_cov_dl",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        st.caption(f"已保存：{xlsx['path']}")
        with st.expander("卫星汇总（每颗星覆盖时长/覆盖率，按时长排名）"):
            st.dataframe(df_summary, hide_index=True, width="stretch")
        with st.expander("整体状态表（每分钟几颗星在覆盖）"):
            st.dataframe(df_state, hide_index=True, width="stretch")
        with st.expander("窗口明细（卫星视角，开始/结束/时长）"):
            st.dataframe(df_windows, hide_index=True, width="stretch")
        with st.expander("空窗表（连续无覆盖时间段）"):
            if not df_gaps.empty:
                st.dataframe(df_gaps, hide_index=True, width="stretch")
            else:
                st.info("无空窗：全时段持续有覆盖。")


        with st.expander("选星决策（虚拟星座）：基于本次覆盖结果挑最少卫星满足需求", expanded=st.session_state.get("sel_result") is not None):
            st.info("说明：覆盖分析与选星均包含所有导入星（卫星池全部参与计算），"
                       "未覆盖的星不会出现在推荐名单。选星任务的人话问法见下拉下方。")
            t1c, t2c = st.columns([2, 2])
            sel_task = t1c.selectbox(
                "选星任务",
                ["① 覆盖率 ≥P% · 最少颗数（P=100 即连续覆盖，推荐）",
                 "② 重访 · 最少颗数",
                 "③ 给定 N 颗 · 覆盖率最大",
                 "④ 给定 N 颗 · 最长空窗最小（MMRT）",
                 "⑤ 给定 N 颗 · 平均空窗最小（MART）"],
                key="sel_task")
            st.caption({
                "① 覆盖率 ≥P% · 最少颗数（P=100 即连续覆盖，推荐）":
                    "问：至少覆盖 P% 的时间，最少需要几颗星？（P=100 即全程无空窗）",
                "② 重访 · 最少颗数":
                    "问：每 X 分钟至少拍到一次，最少需要几颗星？",
                "③ 给定 N 颗 · 覆盖率最大":
                    "问：只有 N 颗星的名额，最多能拍到多少时间？",
                "④ 给定 N 颗 · 最长空窗最小（MMRT）":
                    "问：只有 N 颗星的名额，最长空窗能压到多短？（结果可读：能否连续 / 满足重访）",
                "⑤ 给定 N 颗 · 平均空窗最小（MART）":
                    "问：只有 N 颗星的名额，平均空窗能压到多短？",
            }[sel_task])
            sel_k = t2c.number_input("K（最少同时可见）", value=1, min_value=1, step=1, key="sel_k")
            if sel_task.startswith("②"):
                sel_preset = st.selectbox(
                    "重访预设", ["标准（MRT=180 分钟）", "宽松（MRT=360 分钟）",
                                "严格（MRT=60 分钟）", "自定义"], key="sel_preset")
                if sel_preset == "自定义":
                    sel_mrt = st.number_input("MRT（最大重访间隔,分钟）", value=180.0, min_value=1.0,
                                              step=5.0, key="sel_mrt_custom")
                else:
                    sel_mrt = {"标准（MRT=180 分钟）": 180.0, "宽松（MRT=360 分钟）": 360.0,
                               "严格（MRT=60 分钟）": 60.0}[sel_preset]
            else:
                sel_mrt = 1.0
            if sel_task.startswith(("③", "④", "⑤")):
                sel_n = st.number_input("给定卫星数 N", value=10, min_value=1, step=1, key="sel_n")
            else:
                sel_n = None
            if sel_task.startswith("①"):
                sel_p = st.number_input("覆盖率要求 P（%）", value=100.0, min_value=1.0,
                                        max_value=100.0, step=1.0, key="sel_p")
            else:
                sel_p = 100.0
            sel_min_ev = st.number_input("单次最短窗口(分钟, 0=不限)", value=0.0, min_value=0.0,
                                         step=1.0, key="sel_min_ev")
            sel_method = st.selectbox("算法", ["两者对照（推荐）", "贪心（快）", "整数规划（精确）"],
                                      key="sel_method")
            if st.button("开始选星", key="btn_sel_run", type="primary"):
                task_map = {
                    "① 覆盖率 ≥P% · 最少颗数（P=100 即连续覆盖，推荐）": ("coverage_pct", "min_sats"),
                    "② 重访 · 最少颗数": ("revisit", "min_sats"),
                    "③ 给定 N 颗 · 覆盖率最大": ("any", "max_coverage"),
                    "④ 给定 N 颗 · 最长空窗最小（MMRT）": ("any", "min_max_gap"),
                    "⑤ 给定 N 颗 · 平均空窗最小（MART）": ("any", "min_avg_gap"),
                }
                _req, _obj = task_map[sel_task]
                sel_res = ss.run_selection(
                    res["sats"], res["windows_by_sat"], res["t0"], res["t1"],
                    step_minutes=res["cfg"].get("step_minutes", 1),
                    k_min=int(sel_k), mrt_minutes=float(sel_mrt),
                    min_event_minutes=float(sel_min_ev),
                    method={"两者对照（推荐）": "both", "贪心（快）": "greedy",
                            "整数规划（精确）": "ilp"}[sel_method],
                    req_mode=_req, objective=_obj,
                    n_sats_limit=int(sel_n) if sel_n else None,
                    coverage_pct=float(sel_p))
                sel_res["region_name"] = res["cfg"].get("region_name", "")
                sel_res["fov"] = res["cfg"].get("fov_half_angle_deg", 30.0)
                sel_res["elev"] = res["cfg"].get("min_elevation_deg", 10.0)
                sel_res["refine"] = res["cfg"].get("refine_step_seconds", 10)
                st.session_state["sel_result"] = sel_res
                st.session_state.pop("sel_xlsx", None)
                st.rerun()
            sel_res = st.session_state.get("sel_result")
            if sel_res:
                req_lbl = {"continuous": "连续覆盖（无空窗）",
                           "revisit": f"重访 MRT≤{sel_res['mrt_minutes']:.0f} 分",
                           "coverage_pct": f"覆盖率 ≥{sel_res.get('coverage_threshold_pct') or 100:.0f}%",
                           "any": "不限（只看指标）"}[sel_res["req_mode"]]
                obj_lbl = {
                    "min_sats": "最少颗数满足需求",
                    "max_coverage": f"给定 {sel_res.get('n_sats_limit') or 0} 颗最大化覆盖率",
                    "min_max_gap": f"给定 {sel_res.get('n_sats_limit') or 0} 颗最小化最大空窗",
                    "min_avg_gap": f"给定 {sel_res.get('n_sats_limit') or 0} 颗最小化平均空窗",
                }[sel_res["objective"]]
                if sel_res["req_mode"] == "any":
                    st.success(f"选星完成：卫星池 {sel_res['n_sats_total']} 颗 → 选中 "
                               f"{len(sel_res['selected'])} 颗（任务：{obj_lbl}），"
                               f"覆盖率 {sel_res['eval']['coverage_pct']:.1f}%，"
                               f"最大空窗 {sel_res['eval']['mrt_actual_minutes']:.0f} 分，"
                               f"平均空窗 {sel_res['eval']['art_actual_minutes']:.0f} 分")
                else:
                    st.success(f"选星完成：卫星池 {sel_res['n_sats_total']} 颗 → 推荐 "
                               f"{len(sel_res['selected'])} 颗（任务：{req_lbl} + {obj_lbl}），"
                               f"需求{'满足' if sel_res['eval']['satisfied'] else '未满足'}")
                if not sel_res["eval"]["satisfied"]:
                    if sel_res["req_mode"] == "continuous":
                        _fb = sel_res.get("feasibility", {})
                        st.error(_fb.get("message", "连续覆盖无法满足：见空窗表。"))
                        if _fb.get("gaps"):
                            with st.expander("物理上限：全部卫星一起上的空窗表（连续覆盖不可行的原因）"):
                                st.dataframe(pd.DataFrame(_fb["gaps"]), hide_index=True,
                                             width="stretch")
                        st.info("该组合已用最少卫星把空窗压到该池子能做到的最小（尽力而为）。")
                    elif sel_res["req_mode"] == "revisit":
                        st.warning("需求无法完全满足：即使所有卫星一起上仍有超长空窗（见空窗表）。"
                                   "建议放宽 MRT、减小 K，或更换/延长时段。")
                        st.info("该组合已用最少卫星把空窗压到该池子能做到的最小（尽力而为）。")
                    elif sel_res["req_mode"] == "coverage_pct":
                        _full_cov = sel_res.get("full", {}).get("coverage_pct", 0.0)
                        _thr = sel_res.get("coverage_threshold_pct") or 100.0
                        st.error(f"覆盖率要求未达到：该卫星池最高也只有 {_full_cov:.1f}% 覆盖率"
                                 f"（阈值 {_thr:.0f}%）。")
                        st.info("该组合已用最少卫星达到该池子能做到的最大覆盖率（尽力而为）。")
                if (sel_res["eval"]["satisfied"] and sel_res["req_mode"] == "continuous"
                        and len(sel_res["selected"]) == 1):
                    st.info(f"说明：{sel_res['selected_names'][0]} 对目标区域全程 0 空窗覆盖，"
                            "1 颗即满足连续覆盖（常见于同步/高轨卫星）；如需冗余可调高 K。")
                if sel_res["req_mode"] == "any":
                    st.caption("说明：本任务为预算型（给定 N 颗），只报告覆盖率/空窗指标，"
                               "不做达标判定；覆盖率为“被覆盖时间占比”。")
                if sel_res["objective"] == "min_max_gap":
                    st.caption(f"解读：最长空窗 = {sel_res['eval']['mrt_actual_minutes']:.0f} 分钟；"
                               "若为 0 → 这 N 颗可实现连续覆盖；"
                               "≤ 你的重访要求 X → 满足“每 X 分钟至少一次”。")
                with st.expander("推荐卫星（选中清单，按覆盖时长排名）", expanded=True):
                    st.dataframe(pd.DataFrame(sel_res["detail_rows"]), hide_index=True,
                                 width="stretch")
                with st.expander("全池 vs 选中对比"):
                    st.dataframe(pd.DataFrame(sel_res["compare_rows"]), hide_index=True,
                                 width="stretch")
                with st.expander("组合覆盖曲线（每分钟被选星覆盖数）"):
                    df_state = pd.DataFrame(sel_res["state_rows"])
                    df_state["时间(UTC)"] = pd.to_datetime(df_state["时间(UTC)"])
                    st.line_chart(df_state.set_index("时间(UTC)")["覆盖数"])
                with st.expander("空窗表（连续无覆盖/不足 K 颗的时间段）"):
                    if sel_res["gaps"]:
                        st.dataframe(pd.DataFrame(sel_res["gaps"]), hide_index=True,
                                     width="stretch")
                    else:
                        st.info("无空窗：需求已完全满足。")
                if sel_res.get("optimal_solutions"):
                    with st.expander(f"全部最优方案（{len(sel_res['optimal_solutions'])} 种）"):
                        _opt = pd.DataFrame([{
                            "方案": f"方案{i+1}", "卫星数": s["n"],
                            "覆盖率(%)": s["coverage_pct"],
                            "卫星清单": "、".join(s["sat_names"]),
                        } for i, s in enumerate(sel_res["optimal_solutions"])])
                        st.dataframe(_opt, hide_index=True, width="stretch")
                        if sel_res.get("optimal_note"):
                            st.caption(sel_res["optimal_note"])
                with st.expander("算法与需求摘要"):
                    st.text(sel_res["summary"])
                if "sel_xlsx" not in st.session_state:
                    out_dir = sl.PROJECT_ROOT / "output"
                    out_dir.mkdir(exist_ok=True)
                    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = str(out_dir / f"selection_{stamp}.xlsx")
                    ss.export_selection_xlsx(sel_res, out_path,
                                             region_name=sel_res["region_name"],
                                             extra_meta={
                                                 "视场半角(°)": sel_res.get("fov", 30.0),
                                                 "最小仰角(°)": sel_res.get("elev", 10.0),
                                                 "边界精化(秒)": sel_res.get("refine", 10),
                                             })
                    st.session_state["sel_xlsx"] = {"data": Path(out_path).read_bytes(),
                                                    "name": Path(out_path).name,
                                                    "path": out_path}
                xlsx = st.session_state["sel_xlsx"]
                st.download_button(
                    "下载选星 Excel", data=xlsx["data"], file_name=xlsx["name"],
                    key="btn_sel_dl",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
                st.caption(f"已保存：{xlsx['path']}")
                st.divider()
                vc_name = st.text_input(
                    "虚拟星座名称（保存为收藏夹，带 ⭐ 标识）",
                    value=f"虚拟星座-{datetime.now():%Y%m%d-%H%M%S}",
                    key="sel_vc_name")
                if st.button("保存为收藏夹（虚拟星座）", key="btn_sel_save"):
                    ids = [sel_res["sats"][j].norad_id for j in sel_res["selected"]]
                    desc = (f"选星虚拟星座：{sel_res.get('region_name', '')} "
                            f"{sel_res['t0']:%Y-%m-%d %H:%M}~{sel_res['t1']:%H:%M} UTC "
                            f"K={sel_res['k_min']} MRT={sel_res['mrt_minutes']:.0f} 分 "
                            f"（{sel_res['algo']}，{len(ids)} 颗）")
                    if sl.create_collection(vc_name, description=desc,
                                            kind="virtual_constellation"):
                        sl.add_entry(vc_name, "norad_list", " ".join(str(i) for i in ids))
                        flash(f"已保存虚拟星座「{vc_name}」（{len(ids)} 颗，⭐ 来自选星标识），"
                              f"可在②收藏夹管理查看。", kind="success")
                    else:
                        flash(f"同名收藏夹已存在：「{vc_name}」", kind="warning")
                    st.rerun()
