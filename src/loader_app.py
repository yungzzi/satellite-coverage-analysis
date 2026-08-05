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


def _reset_filters():
    """重置结果内筛选（on_click 回调，在组件重建前执行），并重新拉取最新结果。"""
    for k in ("f_country", "f_year", "f_alt", "f_inc"):
        st.session_state.pop(k, None)
    st.session_state["f_strict"] = False   # 显式取消勾选（比 pop 更可靠）
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
        years = sorted({str(x)[:4] for x in df["发射日期"].tolist()
                        if str(x)[:4].isdigit()})
        f_year = f2.multiselect("按发射年份筛选", years, key="f_year")
        strict = st.checkbox(
            "严格匹配：只显示名字以关键字开头的星（减少混入的无关星）",
            key="f_strict",
            help="CelesTrak 按名字搜索是'包含匹配'。例如搜 GF 会匹配到 CYGFM（长光一号）等；"
                 "勾选后只保留名字以 GF 开头的星。")
        # 注意：不设 help，避免悬停提示框长时间停留（Streamlit 无法配置提示时长）
        st.button("重置筛选", key="btn_reset_filters", on_click=_reset_filters)
        f3, f4 = st.columns(2)
        alts = [a for a in df["高度km"].tolist() if a is not None]
        if alts:
            # 上限用 int(max)+1（向上取整），避免截断把边界值（如 98.46°）排除
            lo, hi = int(min(alts)), int(max(alts)) + 1
            if hi <= lo:
                hi = lo + 1
            f_alt = f3.slider("高度范围（km）", lo, hi, (lo, hi), key="f_alt")
        else:
            f_alt = None
        incs = [i for i in df["倾角°"].tolist() if i is not None]
        if incs:
            ilo, ihi = int(min(incs)), int(max(incs)) + 1
            if ihi <= ilo:
                ihi = ilo + 1
            f_inc = f4.slider("倾角范围（°）", ilo, ihi, (ilo, ihi), key="f_inc")
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
                sl.create_collection(new_name.strip())
                target = new_name.strip()
            if not target:
                st.error("请先选择或新建一个收藏夹。")
            elif mode.startswith("名字规则"):
                if sl.add_entry(target, "name_filter", keyword.strip()):
                    flash(f"已把名字规则「{keyword}」加入「{target}」")
                else:
                    flash("该收藏夹里已经有相同的名字规则，未重复添加。", kind="warning")
                st.rerun()
            else:
                if not sel_ids:
                    st.error("请先用多选框选择至少一颗星（或点“全选当前筛选结果”）。")
                elif sl.add_entry(target, "norad_list", " ".join(map(str, sel_ids))):
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

        st.markdown("**查看本夹全部卫星（完整信息，只查看不保存）**")
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

        st.markdown("**管理收藏夹**")
        m1, m2 = st.columns(2)
        with m1:
            new_name = st.text_input("新建收藏夹名称", key="new_col_name")
            if st.button("新建", key="btn_new_col"):
                if new_name.strip():
                    ok = sl.create_collection(new_name.strip())
                    flash("已新建收藏夹：" + new_name.strip() if ok
                          else f"已存在同名收藏夹：{new_name.strip()}，未新建。",
                          kind="success" if ok else "warning")
                st.rerun()
            if not c.get("builtin"):
                rename_to = st.text_input("改名为", key="rename_to")
                if st.button("改名", key="btn_rename"):
                    if rename_to.strip():
                        ok = sl.rename_collection(c["name"], rename_to.strip())
                        flash(f"已改名：{c['name']} -> {rename_to.strip()}" if ok
                              else "改名失败（可能重名）。",
                              kind="success" if ok else "warning")
                        st.rerun()
                confirm_del = st.checkbox("确认删除「当前夹」（不可撤销）", key="del_confirm")
                if st.button("删除", key="btn_del"):
                    if confirm_del:
                        ok = sl.delete_collection(c["name"])
                        flash(f"已删除收藏夹：{c['name']}" if ok else "删除失败。",
                              kind="success" if ok else "warning")
                        st.rerun()
                    else:
                        st.warning("请先勾选确认。")
        with m2:
            builtin = sl.load_builtin_collections()
            if builtin:
                src_name = st.selectbox("复制内置夹到我的",
                                        [b["name"] for b in builtin], key="copy_src")
                if st.button("复制", key="btn_copy"):
                    ok = sl.copy_builtin_to_user(src_name)
                    flash(f"已复制「{src_name}」到我的收藏" if ok
                          else "复制失败（可能重名）。",
                          kind="success" if ok else "warning")
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
                        ok = sl.add_entry(c["name"], etype, evalue.strip())
                        flash(f"已添加条目：{etype} = {evalue.strip()}" if ok
                              else "已有相同条目，未重复添加。",
                              kind="success" if ok else "warning")
                        st.rerun()
        with e2:
            if not c.get("builtin") and c["entries"]:
                ridx = st.number_input("移除条目序号", min_value=1,
                                       max_value=len(c["entries"]), value=1, key="rm_idx")
                if st.button("移除", key="btn_rm_entry"):
                    ok = sl.remove_entry(c["name"], int(ridx) - 1)
                    flash(f"已移除第 {ridx} 条" if ok else "移除失败。",
                          kind="success" if ok else "warning")
                    st.rerun()
                midx = st.number_input("移动条目序号", min_value=1,
                                       max_value=len(c["entries"]), value=1, key="mv_idx")
                others = [x["name"] for x in sl.load_user_collections()
                          if x["name"] != c["name"]]
                if others:
                    dst = st.selectbox("移动到", others, key="mv_dst")
                    if st.button("移动", key="btn_mv"):
                        ok = sl.move_entry(c["name"], int(midx) - 1, dst)
                        flash(f"已把条目移动到「{dst}」" if ok else "移动失败。",
                              kind="success" if ok else "warning")
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
                ok = sl.add_entry(c["name"], "file", str(dst))
                flash(f"已导入并复制到 {dst.name}" if ok
                      else "已有相同条目，未重复添加。",
                      kind="success" if ok else "warning")
                st.rerun()


# ==================== ③ 下载与自检 ====================

with tab_download:
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
