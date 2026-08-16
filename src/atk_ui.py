# -*- coding: utf-8 -*-
"""ATK 引擎的 GUI 面板助手：定义（类型+参数+指向）+ 约束（基本+光照）+ 全局/逐星持久化。

loader_app ⑤页 ATK 分支使用；与 ATK 软件的“左侧树 + 右侧属性页”交互对齐。
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
import streamlit as st

import satellite_loader as sl

ATK_CONSTRAINT_OPTIONS = [
    "AzimuthAngle", "ElevationAngle", "Range",
    "AzimuthRate", "ElevationRate", "RangeRate", "AngularRate",
    "Altitude", "PropagationDelay", "LineOfSight", "fieldofview", "TerrainMask",
    "SunElevationAngle", "SunGroundElevAngle", "LunarElevationAngle",
    "LOSSunExclusion", "LOSLunarExclusion", "BSSunExclusion", "BSLunarExclusion",
]


def fixed_method_inputs(method: str, prefix: str = "cov_pt") -> list | None:
    """按姿态方法渲染参数输入，返回参数列表（或 None）。"""
    if method == "YPR":
        seq = st.selectbox("转序", ["YPR", "YRP", "PYR", "PRY", "RYP", "RPY"],
                           key=f"{prefix}_seq")
        c1, c2, c3 = st.columns(3)
        y = c1.number_input("偏航(°)", value=0.0, min_value=-180.0, max_value=180.0,
                            step=1.0, key=f"{prefix}_yaw")
        p = c2.number_input("俯仰(°)", value=30.0, min_value=-180.0, max_value=180.0,
                            step=1.0, key=f"{prefix}_pitch", help="俯仰=侧摆角")
        r = c3.number_input("滚转(°)", value=0.0, min_value=-180.0, max_value=180.0,
                            step=1.0, key=f"{prefix}_roll")
        return [seq, y, p, r]
    if method == "Euler":
        seq = st.selectbox("转序", ["121", "123", "131", "132", "212", "213", "231",
                                    "232", "312", "313", "321", "323"],
                           key=f"{prefix}_eseq")
        c1, c2, c3 = st.columns(3)
        r1 = c1.number_input("Rot1(°)", value=0.0, step=1.0, key=f"{prefix}_r1")
        r2 = c2.number_input("Rot2(°)", value=30.0, step=1.0, key=f"{prefix}_r2")
        r3 = c3.number_input("Rot3(°)", value=0.0, step=1.0, key=f"{prefix}_r3")
        return [seq, r1, r2, r3]
    if method == "Quaternion":
        c1, c2, c3, c4 = st.columns(4)
        q1 = c1.number_input("q1", value=0.0, step=0.01, key=f"{prefix}_q1")
        q2 = c2.number_input("q2", value=0.0, step=0.01, key=f"{prefix}_q2")
        q3 = c3.number_input("q3", value=0.0, step=0.01, key=f"{prefix}_q3")
        q4 = c4.number_input("q4（标量）", value=1.0, step=0.01, key=f"{prefix}_q4")
        return [q1, q2, q3, q4]
    if method == "AzEl":
        c1, c2 = st.columns(2)
        az = c1.number_input("方位角(°)", value=0.0, min_value=0.0, max_value=360.0,
                             step=1.0, key=f"{prefix}_az")
        el = c2.number_input("高度角(°)", value=90.0, min_value=-90.0, max_value=90.0,
                             step=1.0, key=f"{prefix}_el")
        return [az, el]
    if method == "matrix":
        c1, c2, c3 = st.columns(3)
        d11 = c1.number_input("d11", value=1.0, step=0.01, key=f"{prefix}_d11")
        d21 = c2.number_input("d21", value=0.0, step=0.01, key=f"{prefix}_d21")
        d22 = c3.number_input("d22", value=1.0, step=0.01, key=f"{prefix}_d22")
        c4, c5, c6 = st.columns(3)
        d31 = c4.number_input("d31", value=0.0, step=0.01, key=f"{prefix}_d31")
        d32 = c5.number_input("d32", value=0.0, step=0.01, key=f"{prefix}_d32")
        d33 = c6.number_input("d33", value=1.0, step=0.01, key=f"{prefix}_d33")
        return [d11, d21, d22, d31, d32, d33]
    return None


def sensor_type_ui(prefix: str, initial_type: str = "SimpleCone",
                   initial_params=None) -> tuple:
    """传感器类型+参数输入，返回 (type, params)。"""
    ip = list(initial_params) if initial_params else []
    _map = {"SimpleCone（默认）": "SimpleCone", "Rectangular（矩形）": "Rectangular",
            "HalfPower（半功率）": "HalfPower", "Conical（复杂圆锥）": "Conical",
            "SAR": "SAR"}
    _init_label = next((k for k, v in _map.items() if v == initial_type),
                       "SimpleCone（默认）")
    _lab = st.selectbox("传感器类型", list(_map), key=f"{prefix}_type",
                        index=list(_map).index(_init_label))
    stype = _map[_lab]
    sparams = list(initial_params) if initial_params else None
    if stype == "Rectangular":
        c1, c2 = st.columns(2)
        v = c1.number_input("垂直半角(°)", value=float(ip[0]) if len(ip) > 0 else 40.0,
                            min_value=0.1, max_value=89.0,
                            step=1.0, key=f"{prefix}_vh")
        h = c2.number_input("水平半角(°)", value=float(ip[1]) if len(ip) > 1 else 60.0,
                            min_value=0.1, max_value=89.0,
                            step=1.0, key=f"{prefix}_hh")
        sparams = [v, h]
    elif stype == "HalfPower":
        c1, c2 = st.columns(2)
        f = c1.number_input("频率(GHz)", value=float(ip[0]) if len(ip) > 0 else 14.5,
                            min_value=0.01, step=0.1,
                            key=f"{prefix}_freq", help="半功率波束宽度由频率与天线直径计算")
        d = c2.number_input("天线直径(m)", value=float(ip[1]) if len(ip) > 1 else 1.0,
                            min_value=0.01, step=0.1,
                            key=f"{prefix}_diam")
        sparams = [f, d]
    elif stype == "Conical":
        c1, c2, c3, c4 = st.columns(4)
        i = c1.number_input("内锥半角(°)", value=float(ip[0]) if len(ip) > 0 else 0.0,
                            min_value=0.0, max_value=89.0,
                            step=1.0, key=f"{prefix}_inner")
        o = c2.number_input("外锥半角(°)", value=float(ip[1]) if len(ip) > 1 else 45.0,
                            min_value=0.1, max_value=89.0,
                            step=1.0, key=f"{prefix}_outer")
        mc = c3.number_input("最小时钟角(°)", value=float(ip[2]) if len(ip) > 2 else 0.0,
                             min_value=0.0, max_value=360.0,
                             step=5.0, key=f"{prefix}_minc")
        xc = c4.number_input("最大时钟角(°)", value=float(ip[3]) if len(ip) > 3 else 360.0,
                             min_value=0.0, max_value=360.0,
                             step=5.0, key=f"{prefix}_maxc")
        sparams = [i, o, mc, xc]
    elif stype == "SAR":
        c1, c2, c3 = st.columns(3)
        mel = c1.number_input("最小入射仰角(°)", value=float(ip[0]) if len(ip) > 0 else 12.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_mel")
        xel = c2.number_input("最大入射仰角(°)", value=float(ip[1]) if len(ip) > 1 else 70.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_xel")
        alt = c3.number_input("父高度(m)", value=float(ip[4]) if len(ip) > 4 else 550000.0,
                              min_value=0.0, step=1000.0,
                              key=f"{prefix}_alt")
        c4, c5 = st.columns(2)
        fwd = c4.number_input("前向排除角(°)", value=float(ip[2]) if len(ip) > 2 else 30.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_fwd")
        aft = c5.number_input("后向排除角(°)", value=float(ip[3]) if len(ip) > 3 else 30.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_aft")
        sparams = [mel, xel, fwd, aft, alt]
    else:
        fv = st.number_input("视场半角(°)（SimpleCone）",
                             value=float(ip[0]) if len(ip) > 0 else 10.0, min_value=1.0,
                             max_value=89.0, step=1.0, key=f"{prefix}_fov",
                             help="SimpleCone 视场半角；逐星可覆盖")
        sparams = [fv]
    return stype, sparams


def pointing_ui(prefix: str) -> dict | None:
    """指向输入。Targeted 的 XML 写法待官方/GUI 样例复核，暂不可用。"""
    mode = st.selectbox(
        "指向模式",
        ["天底（默认，不设置）", "Fixed 固定指向", "GrazingAlt 入射高度",
         "External 外部指向文件", "Targeted 目标指向",
         "Spinning 旋转（暂不可用）"],
        key=f"{prefix}_pt_mode",
        help="Fixed=固定姿态偏置（侧摆，支持 YPR/欧拉/四元数/AzEl/矩阵）；"
             "GrazingAlt=入射高度指向；External=外部指向文件；"
             "Targeted=始终指向目标点（凝视观测，XML 写法已由 GUI 场景确认）；"
             "Spinning 初始相位跨进程随机，暂不可用。")
    if mode == "Fixed 固定指向":
        m = st.selectbox("姿态方法", ["YPR", "Euler", "Quaternion", "AzEl", "matrix"],
                         key=f"{prefix}_pt_method")
        ps = fixed_method_inputs(m, prefix=f"{prefix}_pt")
        if ps is not None:
            return {"type": "Fixed", "method": m, "params": ps}
    elif mode == "GrazingAlt 入射高度":
        c1, c2 = st.columns(2)
        az = c1.number_input("方位角(°)", value=90.0, min_value=0.0, max_value=360.0,
                             step=1.0, key=f"{prefix}_pt_az")
        alt = c2.number_input("入射高度(m)", value=10000.0, min_value=0.0, step=1000.0,
                              key=f"{prefix}_pt_alt")
        return {"type": "GrazingAlt", "azimuth": float(az), "altitude": float(alt)}
    elif mode == "External 外部指向文件":
        pf = st.file_uploader("指向文件（.sp）", type=["sp", "txt"],
                              key=f"{prefix}_pt_file",
                              help="ATK 外部指向文件（.sp）格式见 ATK 官方文档")
        if pf is not None:
            dst = sl.USER_FILES_DIR / f"pointing_{int(__import__('time').time())}_{pf.name}"
            dst.write_bytes(pf.getvalue())
            st.caption(f"已保存指向文件：{dst.name}")
            return {"type": "External", "file": str(dst).replace("\\", "/")}
    elif mode == "Targeted 目标指向":
        aim = st.selectbox("瞄准模式", ["追踪（推荐）", "固定指向（待复核，暂不可用）"],
                           key=f"{prefix}_pt_aim",
                           help="追踪=实时指向目标；固定指向=相对目标加固定姿态偏移（写法待 GUI 样例复核）。")
        if aim.startswith("追踪"):
            trk = st.selectbox("追踪模式", ["接收", "发射", "转发"],
                               key=f"{prefix}_pt_trk",
                               help="按信号方向调整指向；对几何覆盖三种模式都指向目标，转发多用于中继。")
            tm = {"接收": 0, "发射": 1, "转发": 2}[trk]
            return {"type": "Targeted", "track_mode": tm}
        st.warning("固定指向（目标点+固定姿态偏移）的 XML 写法待 GUI 样例复核，暂不可用。")
    elif "Spinning" in mode:
        st.warning("Spinning 初始相位跨进程随机，已反馈官方，暂不可用。")
    return None


def _params_to_row(c) -> dict:
    """把已保存的 {object,name,params} 尽量还原成表格行。"""
    obj = c.get("object", "sensor")
    obj_label = {"facility": "目标点", "sensor": "该星传感器", "satellite": "该星"}.get(obj, "该星传感器")
    name = c.get("name", "")
    params = (c.get("params") or "").strip()
    mn = mx = None
    extra = ""
    m = re.match(r"Min\s+([-\d.eE]+)", params)
    if m:
        mn = float(m.group(1))
    m = re.search(r"Max\s+([-\d.eE]+)", params)
    if m:
        mx = float(m.group(1))
    if name in ("LineOfSight", "fieldofview", "TerrainMask"):
        extra = params if params in ("On", "Off") else "On"
    elif params and mn is None:
        try:
            mn = float(params.split()[0])
        except (ValueError, IndexError):
            extra = params
    elif "ExcludeIntervals" in params or "IncludeIntervals" in params:
        extra = "ExcludeIntervals" if "ExcludeIntervals" in params else "IncludeIntervals"
    return {"enable": True, "object": obj_label, "type": name,
            "min": mn, "max": mx, "extra": extra}


def constraints_ui(prefix: str, allow_facility: bool,
                   initial_constraints=None) -> list:
    """约束输入（基本+光照），一行一条。"""
    st.caption("一行一条约束：作用对象 + 约束类型 + Min/Max；开关类（视线/视场/地形）在附加参数填 On/Off；"
               "排除角类把角度填在 Min 列。")
    key = f"{prefix}_cons_rows"
    if key not in st.session_state:
        if initial_constraints:
            st.session_state[key] = [_params_to_row(c) for c in initial_constraints]
        else:
            st.session_state[key] = [
                {"enable": True, "object": "目标点" if allow_facility else "该星传感器",
                 "type": "SunGroundElevAngle", "min": 10.0, "max": None, "extra": ""}]
    df = pd.DataFrame(st.session_state[key],
                      columns=["enable", "object", "type", "min", "max", "extra"])
    df["enable"] = df["enable"].fillna(True).astype(bool)
    df["object"] = df["object"].astype("string")
    df["type"] = df["type"].astype("string")
    df["min"] = pd.to_numeric(df["min"], errors="coerce")
    df["max"] = pd.to_numeric(df["max"], errors="coerce")
    df["extra"] = df["extra"].fillna("").astype("string")
    objs = (["目标点", "每颗星传感器", "每颗星"] if allow_facility
            else ["该星传感器", "该星"])
    edited = st.data_editor(
        df, key=f"{prefix}_cons_editor", num_rows="dynamic", hide_index=True,
        column_config={
            "enable": st.column_config.CheckboxColumn("启用"),
            "object": st.column_config.SelectboxColumn("作用对象", options=objs),
            "type": st.column_config.SelectboxColumn("约束类型", options=ATK_CONSTRAINT_OPTIONS),
            "min": st.column_config.NumberColumn("Min 值", format="%.3f"),
            "max": st.column_config.NumberColumn("Max 值", format="%.3f"),
            "extra": st.column_config.TextColumn(
                "附加参数", help='如 "ExcludeIntervals" / "On" / "Off"'),
        },
    )
    obj_map = {"目标点": "facility", "每颗星传感器": "sensor", "每颗星": "satellite",
               "该星传感器": "sensor", "该星": "satellite"}
    rows = []
    for _, r in edited.iterrows():
        if not r.get("enable"):
            continue
        t = (r.get("type") or "").strip()
        o = obj_map.get(r.get("object"), "sensor")
        if not t:
            continue
        mn, mx = r.get("min"), r.get("max")
        mn_s = None if mn is None or pd.isna(mn) else f"{float(mn):g}"
        mx_s = None if mx is None or pd.isna(mx) else f"{float(mx):g}"
        ex = (r.get("extra") or "").strip()
        if t == "AzimuthAngle":
            params = f"Min {mn_s} Max {mx_s}" if (mn_s and mx_s) else "Min 0 Max 360"
            if ex:
                params += " " + ex
        elif t in ("LineOfSight", "fieldofview", "TerrainMask"):
            params = ex or "On"
        elif t in ("LOSSunExclusion", "LOSLunarExclusion",
                   "BSSunExclusion", "BSLunarExclusion"):
            params = mn_s or "0"
        else:
            parts = []
            if mn_s:
                parts.append(f"Min {mn_s}")
            if mx_s:
                parts.append(f"Max {mx_s}")
            params = " ".join(parts)
            if ex:
                params += " " + ex
        rows.append({"object": o, "name": t, "params": params})
    st.caption(f"当前启用 {len(rows)} 条约束。")
    return rows


def panel(prefix: str, allow_facility: bool, initial: dict | None = None) -> dict:
    """全局/逐星共用配置面板：定义（类型+参数+指向）+ 约束（基本+光照）。"""
    initial = initial or {}
    st.markdown("**定义**")
    stype, sparams = sensor_type_ui(prefix + "_d",
                                    initial.get("sensor_type", "SimpleCone"),
                                    initial.get("sensor_params"))
    pointing = pointing_ui(prefix + "_p")
    if pointing is None and initial.get("pointing"):
        st.caption(f"已保存指向：{initial['pointing']}")
    st.markdown("**约束（基本 + 光照）**")
    constraints = constraints_ui(prefix + "_c", allow_facility,
                                 initial.get("constraints"))
    if not constraints and initial.get("constraints"):
        st.caption(f"已保存约束 {len(initial['constraints'])} 条（清空表格即可删除）。")
    return {"sensor_type": stype, "sensor_params": sparams,
            "pointing": pointing, "constraints": constraints}


def cfg_file() -> Path:
    p = sl.PROJECT_ROOT / "data" / "atk_sat_config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _tq(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cfg_to_toml_lines(cfg: dict, prefix: str) -> list:
    lines = []
    if cfg.get("sensor_type"):
        lines.append(f"{prefix}sensor_type = {_tq(cfg['sensor_type'])}")
    if cfg.get("sensor_params"):
        lines.append(f"{prefix}sensor_params = ["
                     + ", ".join(repr(float(x)) for x in cfg["sensor_params"])
                     + "]")
    if cfg.get("pointing"):
        pt = cfg["pointing"]
        inner = []
        for k, v in pt.items():
            if isinstance(v, list):
                inner.append(f"{k} = [" + ", ".join(repr(float(x)) for x in v) + "]")
            elif isinstance(v, str):
                inner.append(f"{k} = {_tq(v)}")
            else:
                inner.append(f"{k} = {repr(float(v))}")
        lines.append(f"{prefix}pointing = {{ " + ", ".join(inner) + " }")
    for c in cfg.get("constraints") or []:
        lines.append(f"{prefix}[[constraints]]")
        lines.append(f"{prefix}object = {_tq(c['object'])}")
        lines.append(f"{prefix}name = {_tq(c['name'])}")
        lines.append(f"{prefix}params = {_tq(c['params'])}")
    return lines


def persist(global_cfg=None, sat_cfgs=None) -> None:
    g = dict(global_cfg) if global_cfg is not None else (st.session_state.get("atk_global_cfg") or {})
    s = dict(sat_cfgs) if sat_cfgs is not None else (st.session_state.get("atk_sat_cfgs") or {})
    lines = ["# ATK 传感器配置（程序维护，可手改）", ""]
    lines.append("[global]")
    lines += _cfg_to_toml_lines(g, "")
    lines.append("")
    lines.append("[sats]")
    for nid, c in s.items():
        lines.append(f"[sats.{nid}]")
        lines += _cfg_to_toml_lines(c, "")
        lines.append("")
    cfg_file().write_text("\n".join(lines), encoding="utf-8")


def load_cfg() -> tuple:
    p = cfg_file()
    if not p.exists():
        return {}, {}
    import tomllib
    try:
        with open(p, "rb") as f:
            d = tomllib.load(f)
    except Exception:
        return {}, {}
    g = dict(d.get("global") or {})
    s = {str(k): dict(v) for k, v in (d.get("sats") or {}).items()}
    return g, s


def sat_pool(cov_sats) -> list:
    """从所选收藏夹去重列出卫星，标记已配置。"""
    names = []
    for n in cov_sats:
        x = str(n)
        for pre in ("📡 ", "⭐ "):
            if x.startswith(pre):
                x = x[len(pre):]
        for suf in ("（星座）", "（虚拟星座，来自选星）", "（内置，只读）", "（我的）"):
            if x.endswith(suf):
                x = x[:-len(suf)]
        names.append(x)
    if not names:
        return []
    src = sl.collection_sources_dedup(names)
    cfgs = st.session_state.get("atk_sat_cfgs") or {}
    pool = []
    for x in src:
        pool.append({"norad": int(x["norad"]), "name": x["name"],
                     "已配置": "●" if str(x["norad"]) in cfgs else ""})
    return pool
