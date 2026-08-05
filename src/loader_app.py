# -*- coding: utf-8 -*-
"""
loader_app.py —— 卫星数据加载器图形界面（Streamlit，阶段 1.5）

只做"卫星数据导入"这一部分的 GUI，三个页签：
  ① 搜索与收藏：按名字/所属国/发射年份/高度搜索，勾选后加入收藏夹
  ② 收藏夹管理：新建/改名/删除/移动条目/复制内置到我的/导入文件
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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import streamlit as st

import satellite_loader as sl
import validate_loader


st.set_page_config(page_title="卫星数据加载器", page_icon="🛰️", layout="wide")

st.title("🛰️ 卫星数据加载器")
st.caption("搜索 / 收藏夹 / 下载 —— 只负责卫星数据导入，覆盖分析（coverage_analysis.py）不动。")

with st.sidebar:
    st.header("说明")
    st.caption(f"缓存目录：`{sl.CACHE_DIR}`")
    st.caption(f"用户收藏：`{sl.USER_CATALOG}`")
    st.caption("CLI 入口保留：`python src\\satellite_loader.py`")
    st.divider()
    if st.button("停止并退出程序", key="btn_exit", icon=":material/stop:"):
        os._exit(0)   # 结束 Streamlit 服务进程（关闭浏览器标签页不会停止程序）
    st.caption("提示：关闭浏览器标签页不会停止程序，请点上面的按钮退出。")

tab_search, tab_collections, tab_download = st.tabs(
    ["① 搜索与收藏", "② 收藏夹管理", "③ 下载与自检"])


# ==================== ① 搜索与收藏 ====================

with tab_search:
    st.subheader("搜索卫星（CelesTrak，免费、无 token）")
    c1, c2, c3 = st.columns([2, 1, 1])
    keyword = c1.text_input("名字关键字", key="kw",
                            placeholder="如 YAOGAN / GF-1 / STARLINK")
    country = c2.text_input("所属国（可选）", key="ctry", placeholder="如 PRC / USA")
    launch_year = c3.text_input("发射年份（可选）", key="ly", placeholder="如 2023")
    c4, c5 = st.columns(2)
    min_alt = c4.number_input("最低高度 km（可选）", min_value=0.0, value=0.0, key="minalt")
    max_alt = c5.number_input("最高高度 km（可选，0=不限）", min_value=0.0, value=0.0,
                              key="maxalt")

    if st.button("搜索", key="btn_search"):
        if not keyword.strip():
            st.warning("请先输入名字关键字。")
        else:
            with st.spinner("正在搜索并下载缓存……"):
                rows = sl.search_metadata(
                    keyword.strip(), max_results=500,
                    country=country.strip() or None,
                    launch_year=launch_year.strip() or None,
                    min_alt_km=min_alt or None,
                    max_alt_km=max_alt or None,
                    offline=False)
            st.session_state["search_full"] = rows
            st.session_state.pop("search_multi", None)   # 新搜索清空选择

    rows = st.session_state.get("search_full")
    if rows is None:
        st.info("先在上方输入关键字点“搜索”，结果会出现在这里。")
    elif not rows:
        st.info("没有找到匹配的卫星，换个关键字试试。")
    else:
        df = pd.DataFrame(rows)
        df["序号"] = range(1, len(df) + 1)
        df = df.rename(columns={
            "name": "卫星名", "norad_id": "编号", "country": "所属",
            "launch_date": "发射日期", "altitude_km": "高度km",
            "inclination": "倾角°", "object_type": "类型"})

        st.markdown("**结果内筛选**（只影响显示与勾选，不重新搜索）")
        f1, f2 = st.columns(2)
        countries = sorted({x for x in df["所属"].tolist() if x})
        f_country = f1.multiselect("按所属国筛选", countries, key="f_country")
        alts = [a for a in df["高度km"].tolist() if a is not None]
        if alts:
            lo, hi = int(min(alts)), int(max(alts))
            if hi <= lo:
                hi = lo + 1
            f_alt = f2.slider("高度范围（km）", lo, hi, (lo, hi), key="f_alt")
        else:
            f_alt = None

        view = df.copy()
        if f_country:
            view = view[view["所属"].isin(f_country)]
        if f_alt is not None:
            view = view[view["高度km"].between(f_alt[0], f_alt[1])]

        st.dataframe(view[["序号", "卫星名", "编号", "所属", "发射日期",
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
        st.caption(f"筛选后 {len(view)} 条，当前已选 {len(selected)} 颗。")

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
        if mode.startswith("名字规则") and selected:
            st.caption("注意：名字规则会包含关键字匹配的全部卫星，当前勾选不会单独生效；"
                       "想只要勾选的星，请选“编号清单”。")
        if st.button("加入收藏夹", key="btn_add"):
            target = col_target
            if new_name.strip():
                if sl.create_collection(new_name.strip()):
                    target = new_name.strip()
                else:
                    target = new_name.strip()  # 已存在同名夹则直接使用
            if not target:
                st.error("请先选择或新建一个收藏夹。")
            elif mode.startswith("名字规则"):
                if sl.add_entry(target, "name_filter", keyword.strip()):
                    st.success(f"已把名字规则「{keyword}」加入「{target}」")
                    st.rerun()
                else:
                    st.warning("该收藏夹里已经有相同的名字规则，未重复添加。")
            else:
                if not sel_ids:
                    st.error("请先用多选框选择至少一颗星（或点“全选当前筛选结果”）。")
                elif sl.add_entry(target, "norad_list", " ".join(map(str, sel_ids))):
                    st.success(f"已把 {len(sel_ids)} 颗星（编号清单）加入「{target}」")
                    st.rerun()
                else:
                    st.warning("该收藏夹里已经有相同的编号清单，未重复添加。")
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

with tab_collections:
    st.subheader("收藏夹管理")
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
        if c["entries"]:
            ent_df = pd.DataFrame([
                {"序号": i + 1, "类型": sl._entry_label(e).split(" = ")[0],
                 "值": e["value"]} for i, e in enumerate(c["entries"])])
            st.dataframe(ent_df, hide_index=True, width="stretch")
        else:
            st.caption("（空收藏夹）")

        st.markdown("**管理收藏夹**")
        m1, m2 = st.columns(2)
        with m1:
            new_name = st.text_input("新建收藏夹名称", key="new_col_name")
            if st.button("新建", key="btn_new_col"):
                if new_name.strip():
                    sl.create_collection(new_name.strip())
                    st.rerun()
            if not c.get("builtin"):
                rename_to = st.text_input("改名为", key="rename_to")
                if st.button("改名", key="btn_rename"):
                    if rename_to.strip():
                        sl.rename_collection(c["name"], rename_to.strip())
                        st.rerun()
                confirm_del = st.checkbox("确认删除「当前夹」（不可撤销）", key="del_confirm")
                if st.button("删除", key="btn_del"):
                    if confirm_del:
                        sl.delete_collection(c["name"])
                        st.rerun()
                    else:
                        st.warning("请先勾选确认。")
        with m2:
            builtin = sl.load_builtin_collections()
            if builtin:
                src_name = st.selectbox("复制内置夹到我的",
                                        [b["name"] for b in builtin], key="copy_src")
                if st.button("复制", key="btn_copy"):
                    sl.copy_builtin_to_user(src_name)
                    st.rerun()

        st.markdown("**管理条目**")
        e1, e2 = st.columns(2)
        with e1:
            if not c.get("builtin"):
                etype = st.selectbox("添加条目类型",
                                     ["name_filter", "norad_list"], key="add_type")
                evalue = st.text_input(
                    "条目值", key="add_value",
                    placeholder="名字规则：YAOGAN；编号清单：25544 31490")
                if st.button("添加", key="btn_add_entry"):
                    if evalue.strip():
                        sl.add_entry(c["name"], etype, evalue.strip())
                        st.rerun()
        with e2:
            if not c.get("builtin") and c["entries"]:
                ridx = st.number_input("移除条目序号", min_value=1,
                                       max_value=len(c["entries"]), value=1, key="rm_idx")
                if st.button("移除", key="btn_rm_entry"):
                    sl.remove_entry(c["name"], int(ridx) - 1)
                    st.rerun()
                midx = st.number_input("移动条目序号", min_value=1,
                                       max_value=len(c["entries"]), value=1, key="mv_idx")
                others = [x["name"] for x in sl.load_user_collections()
                          if x["name"] != c["name"]]
                if others:
                    dst = st.selectbox("移动到", others, key="mv_dst")
                    if st.button("移动", key="btn_mv"):
                        sl.move_entry(c["name"], int(midx) - 1, dst)
                        st.rerun()

        st.markdown("**导入文件（TLE / 编号清单）**")
        f_up = st.file_uploader("选择文件（.txt / .tle）", type=["txt", "tle"], key="file_up")
        if f_up is not None and st.button("导入并加入当前收藏夹", key="btn_import"):
            if c.get("builtin"):
                st.error("内置夹只读，请先复制到我的收藏再导入。")
            else:
                sl._ensure_dirs()
                dst = sl.USER_FILES_DIR / f_up.name
                if dst.exists():
                    dst = sl.USER_FILES_DIR / f"{Path(f_up.name).stem}_{int(time.time())}{Path(f_up.name).suffix}"
                dst.write_bytes(f_up.getvalue())
                sl.add_entry(c["name"], "file", str(dst))
                st.success(f"已导入并复制到 {dst.name}")
                st.rerun()


# ==================== ③ 下载与自检 ====================

with tab_download:
    st.subheader("下载解析（多夹合并去重）")
    names = [x["name"] for x in sl.load_all_collections()]
    sel_names = st.multiselect("选择要解析的收藏夹", names, key="resolve_sel")
    if st.button("解析下载", key="btn_resolve"):
        if not sel_names:
            st.warning("请至少选择一个收藏夹。")
        else:
            with st.spinner("正在下载/读取 TLE……"):
                sats = sl.resolve_collections_to_satellites(sel_names)
            st.success(f"解析完成：{len(sats)} 颗卫星（按 NORAD 编号去重）")
            if sats:
                st.dataframe(pd.DataFrame(
                    [{"卫星名": s.name, "编号": s.norad_id} for s in sats[:200]]),
                    hide_index=True, width="stretch")
            st.caption("缓存目录：" + str(sl.CACHE_DIR) +
                       "；同批卫星在过期前不会重复下载。")

    st.markdown("---")
    st.subheader("自检（联网部分需要网络）")
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
