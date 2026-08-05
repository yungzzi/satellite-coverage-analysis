# -*- coding: utf-8 -*-
"""
loader_app.py —— 卫星数据加载器图形界面（Streamlit，阶段 1.5）

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

import satellite_loader as sl
import validate_loader


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
        rows = sl.search_metadata(
            kw, max_results=500,
            country=str(st.session_state.get("ctry", "")).strip() or None,
            launch_year=str(st.session_state.get("ly", "")).strip() or None,
            min_alt_km=st.session_state.get("minalt", 0.0) or None,
            max_alt_km=st.session_state.get("maxalt", 0.0) or None,
            offline=False)
        st.session_state["search_full"] = rows


st.set_page_config(page_title="卫星数据加载器", page_icon="🛰️", layout="wide")

st.title("🛰️ 卫星数据加载器")
st.caption("搜索 / 收藏夹 / 下载 —— 只负责卫星数据导入，覆盖分析（coverage_analysis.py）不动。")
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

tab_search, tab_collections, tab_download = st.tabs(
    ["① 搜索与收藏", "② 收藏夹管理", "③ 下载与自检"],
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
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)   # 新搜索清空选择
        elif intdes_str:
            with st.spinner("正在按国际编号搜索……"):
                rows = sl.search_metadata_by_intdes(intdes_str)
            st.session_state["search_kind"] = "intdes"
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)
        elif kw:
            with st.spinner("正在搜索并下载缓存……"):
                rows = sl.search_metadata(
                    kw, max_results=500,
                    country=country.strip() or None,
                    launch_year=launch_year.strip() or None,
                    min_alt_km=min_alt or None,
                    max_alt_km=max_alt or None,
                    offline=False)
            st.session_state["search_kind"] = "name"
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)
        else:
            st.warning("请至少填写一个搜索条件：名字关键字 / NORAD 编号 / 国际编号。")

    rows = st.session_state.get("search_full")
    if rows is None:
        st.info("先在上方填写搜索条件（名字 / NORAD 编号 / 国际编号）点“搜索”，结果会出现在这里。")
    elif not rows:
        st.info("没有找到匹配的卫星，换个关键字试试。")
    else:
        df = pd.DataFrame(rows)
        df["序号"] = range(1, len(df) + 1)
        df = df.rename(columns={
            "name": "卫星名", "norad_id": "编号", "object_id": "国际编号",
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

        st.dataframe(view[["序号", "卫星名", "编号", "国际编号", "所属", "发射日期",
                           "高度km", "倾角°", "类型"]], hide_index=True)

        options = [f"{i}. {name} (NORAD {rid})" for i, name, rid in
                   zip(view["序号"], view["卫星名"], view["编号"])]
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
                        [{"卫星名": r["name"], "编号": r["norad_id"]} for r in recs[:200]]),
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
        labels = [f"{x['name']}（内置，只读）" if x.get("builtin")
                  else f"{x['name']}（我的）" for x in all_c]
        idx = st.selectbox("选择收藏夹", range(len(all_c)),
                           format_func=lambda i: labels[i], key="col_sel")
        c = all_c[idx]
        st.markdown(f"**{c['name']}** —— {c.get('description') or '（无备注）'}")
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
                                 "编号": rid})
                # 把"行号 -> 编号"存进会话状态，供行内删除回调按行定位
                st.session_state[f"manual_ids_{c['name']}"] = [r["编号"] for r in rows]

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
                        st.success(f"本夹共 {len(rows_meta)} 颗卫星")
                        st.caption("需要保存成 TLE 文件请到 ③下载与自检 页“解析下载”。")
                        if rows_meta:
                            df_all = pd.DataFrame(rows_meta)
                            df_all["类型"] = df_all["object_type"].map(sl._object_type_label)
                            st.dataframe(df_all.rename(columns={
                                "name": "卫星名", "norad_id": "编号", "country": "所属",
                                "launch_date": "发射日期", "altitude_km": "高度km",
                                "inclination": "倾角°"})[
                                ["卫星名", "编号", "所属", "发射日期", "高度km", "倾角°", "类型"]],
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
            f_up = st.file_uploader("选择文件（.txt / .tle）", type=["txt", "tle"], key="file_up")
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
    names = [x["name"] for x in sl.load_all_collections()]
    sel_names = st.multiselect("选择要解析的收藏夹", names, key="resolve_sel")
    if st.button("解析下载", key="btn_resolve"):
        if not sel_names:
            st.warning("请至少选择一个收藏夹。")
        else:
            bar = st.progress(0.0, text="开始解析……")
            def cb(done, total, msg):
                if total:
                    bar.progress(min(done / total, 1.0), text=msg)
                else:
                    bar.progress(0.0, text=msg)
            recs = sl.resolve_collections_to_records(sel_names, progress=cb)
            path = sl.save_resolved_tle(" + ".join(sel_names), recs)
            st.success(f"解析完成：{len(recs)} 颗卫星（按 NORAD 编号去重）")
            st.caption(f"已合并为一个文件：`{path}`（下次解析直接复用缓存，不重复下载）")
            if recs:
                st.dataframe(pd.DataFrame(
                    [{"卫星名": r["name"], "编号": r["norad_id"]} for r in recs[:200]]),
                    hide_index=True)

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
    confirm_clear = st.checkbox("确认清空全部缓存（下次会自动重新下载）", key="clear_confirm")
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
            "**自检 = 自动检查“卫星数据加载器”的每个核心环节是否正常**（37 项，6 组）。\n\n"
            "**为什么要做**：本软件的所有卫星数据都来自 CelesTrak 网站，中间还要经过本地缓存、"
            "收藏夹、解析、合并去重等环节。网络波动、缓存损坏、程序更新都可能让某一环悄悄出错，"
            "自检能把这些环节逐项过一遍，确保进入覆盖分析的卫星数据是可靠的。\n\n"
            "**自检会做什么**：\n"
            "1. TLE 解析（离线）——确认能读懂 TLE 文件、算得出星下点；\n"
            "2. 编目搜索（联网）——确认能搜到卫星档案（名字/国家/轨道参数）；\n"
            "3. 缓存行为——确认第二次查询不再重复下载；\n"
            "4. 收藏夹增删改移——用临时测试夹验证改名/删除/移动（自动清理，不碰你的收藏夹）；\n"
            "5. 收藏夹解析——名字规则 / 编号清单 / 导入文件三种方式都能解析成卫星；\n"
            "6. 多夹合并去重——确认重复的星只算一次。\n\n"
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
