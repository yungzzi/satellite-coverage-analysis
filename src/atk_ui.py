# -*- coding: utf-8 -*-
"""ATK 引擎的 GUI 面板助手：定义（类型+参数+指向）+ 约束（基本+光照）+ 全局/逐星持久化。

loader_app ⑤页 ATK 分支使用；与 ATK 软件的“左侧树 + 右侧属性页”交互对齐。
"""
from __future__ import annotations

import re
from pathlib import Path

import streamlit as st

import satellite_loader as sl

# 约束元数据：名称 → (中文名, 控件类型, 单位, (默认Min, 默认Max), 步长)
# 控件类型：minmax=开关+Min/Max；onoff=开关+On/Off；single=开关+单值（规避角）
# 类型清单与默认值对齐 ATK「约束配置」文档（基本/光照），时间/通信约束暂不实现。
CONSTRAINT_META = {
    "AzimuthAngle": ("方位角", "minmax", "°", (-180.0, 180.0), 1.0),
    "ElevationAngle": ("仰角", "minmax", "°", (0.0, 90.0), 1.0),
    "Range": ("距离", "minmax", "m", (0.0, 4e7), 1000.0),
    "AzimuthRate": ("方位角变化率", "minmax", "°/s", (-5.0, 5.0), 0.1),
    "ElevationRate": ("仰角变化率", "minmax", "°/s", (-5.0, 5.0), 0.1),
    "RangeRate": ("距离变化率", "minmax", "m/s", (-5000.0, 5000.0), 10.0),
    "AngularRate": ("角变化率", "minmax", "°/s", (0.0, 5.0), 0.1),
    "Altitude": ("高度", "minmax", "m", (0.0, 2e8), 1000.0),
    "PropagationDelay": ("传播延时", "minmax", "s", (0.0, 3600.0), 1.0),
    "LineOfSight": ("视线", "onoff", "", (0.0, 1.0), 1.0),
    "fieldofview": ("视场", "onoff", "", (0.0, 1.0), 1.0),
    "TerrainMask": ("地形", "onoff", "", (0.0, 1.0), 1.0),
    "SunElevationAngle": ("太阳仰角", "minmax", "°", (0.0, 90.0), 1.0),
    "SunGroundElevAngle": ("太阳地平仰角", "minmax", "°", (0.0, 90.0), 1.0),
    "LunarElevationAngle": ("月球仰角", "minmax", "°", (0.0, 90.0), 1.0),
    "LOSSunExclusion": ("视线太阳规避角", "single", "°", (0.0, 180.0), 1.0),
    "LOSLunarExclusion": ("视线月球规避角", "single", "°", (0.0, 180.0), 1.0),
    "BSSunExclusion": ("视轴太阳规避角", "single", "°", (0.0, 180.0), 1.0),
    "BSLunarExclusion": ("视轴月球规避角", "single", "°", (0.0, 180.0), 1.0),
}
CONSTRAINT_GROUPS = [
    ("基本约束", ["AzimuthAngle", "ElevationAngle", "Range", "AzimuthRate",
                 "ElevationRate", "RangeRate", "AngularRate", "Altitude",
                 "PropagationDelay", "LineOfSight", "fieldofview", "TerrainMask"]),
    ("光照约束", ["SunElevationAngle", "SunGroundElevAngle", "LunarElevationAngle",
                "LOSSunExclusion", "LOSLunarExclusion",
                "BSSunExclusion", "BSLunarExclusion"]),
]

# 各类约束的参数含义（界面悬浮提示）
CONSTRAINT_HELP = {
    "AzimuthAngle": "方位角约束：目标相对平台在水平面上的投影方向角；取值须落在 Min~Max 区间内（ATK 要求同时填写）。",
    "ElevationAngle": "仰角约束：目标相对平台的高度角（俯仰角）；例如地面站对卫星的最小仰角要求。",
    "Range": "距离约束：两对象之间的直线距离（m），须落在 Min~Max 区间内。",
    "AzimuthRate": "方位角变化率（°/s）：方位角随时间的变化速率，用于链路稳定性判断。",
    "ElevationRate": "仰角变化率（°/s）：仰角随时间的变化速率。",
    "RangeRate": "距离变化率（m/s）：距离随时间的变化速率（与多普勒频移相关）。",
    "AngularRate": "角变化率（°/s）：目标相对平台的旋转速度，判断链路稳定性。",
    "Altitude": "高度约束（m）：访问对象相对地面的高度，须落在 Min~Max 区间内。",
    "PropagationDelay": "传播延时（s）：两对象间信号传输所需时间，须落在 Min~Max 区间内。",
    "LineOfSight": "视线约束：On=要求两对象间视线不被中心天体（地球）遮挡；Off=不检查。",
    "fieldofview": "视场约束：On=目标必须落在传感器视场内（与传感器类型参数一致）；Off=不检查。",
    "TerrainMask": "地形约束：On=考虑地形高程对视线的遮挡；Off=不考虑。",
    "SunElevationAngle": "太阳仰角（°）：太阳相对分析对象的高度角约束。",
    "SunGroundElevAngle": "太阳地平仰角（°）：太阳相对地面当地水平面的高度角，用于白昼/夜间判断。",
    "LunarElevationAngle": "月球仰角（°）：月球相对分析对象的高度角约束。",
    "LOSSunExclusion": "视线太阳规避角（°）：视线与太阳方向的夹角下限，实际夹角小于该值时访问无效。",
    "LOSLunarExclusion": "视线月球规避角（°）：视线与月球方向的夹角下限。",
    "BSSunExclusion": "视轴太阳规避角（°）：传感器视轴与太阳方向的夹角下限（光学传感器避光）。",
    "BSLunarExclusion": "视轴月球规避角（°）：传感器视轴与月球方向的夹角下限。",
}


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
        c1, c2 = st.columns(2)
        mel = c1.number_input("最小入射仰角(°)", value=float(ip[0]) if len(ip) > 0 else 12.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_mel")
        xel = c2.number_input("最大入射仰角(°)", value=float(ip[1]) if len(ip) > 1 else 70.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_xel")
        track = st.checkbox(
            "跟踪父高度（用卫星实际高度）",
            value=bool(ip[5]) if len(ip) > 5 else False,
            key=f"{prefix}_sar_track",
            help="SAR 足印由仰角范围+天线离地高度换算。勾选=每个时刻用卫星实际轨道高度"
                 "（本版本 ATK 开关不生效，软件按该星 TLE 平均轨道高度近似）；"
                 "取消=用下面固定的父高度。")
        if track:
            alt = 550000.0
            st.caption("已跟踪父高度：下面的父高度输入被忽略。")
            st.caption("说明：本版本 ATK 的“跟踪父高度”开关不生效，"
                       "软件已自动改用该星 TLE 平均轨道高度近似计算（更贴近真实）；"
                       "待官方修复后自动切换为真跟踪。")
        else:
            alt = st.number_input("父高度(m)", value=float(ip[4]) if len(ip) > 4 else 550000.0,
                                  min_value=0.0, step=1000.0, key=f"{prefix}_alt",
                                  help="SAR 地面足印换算用的天线离地高度；勾选“跟踪父高度”时此项被忽略。")
        c4, c5 = st.columns(2)
        fwd = c4.number_input("前向排除角(°)", value=float(ip[2]) if len(ip) > 2 else 30.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_fwd")
        aft = c5.number_input("后向排除角(°)", value=float(ip[3]) if len(ip) > 3 else 30.0,
                              min_value=0.0, max_value=89.0,
                              step=1.0, key=f"{prefix}_aft")
        sparams = [mel, xel, fwd, aft, float(alt), 1 if track else 0]
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
         "Targeted 目标指向", "Spinning 旋转"],
        key=f"{prefix}_pt_mode",
        help="Fixed=固定姿态偏置（侧摆，支持 YPR/欧拉/四元数/AzEl/矩阵）；"
             "GrazingAlt=入射高度指向；"
             "Targeted=始终指向目标点（凝视观测，XML 写法已由 GUI 场景确认）；"
             "Spinning=旋转扫描（同实例内可复现，跨实例初始相位不锚定，"
             "启用时全部分析在单个 ATK 实例中计算）。")
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
    elif mode == "Targeted 目标指向":
        aim = st.selectbox("瞄准模式", ["追踪（推荐）", "固定指向"],
                           key=f"{prefix}_pt_aim",
                           help="追踪=实时指向目标；固定指向=相对目标方向叠加固定偏置姿态。")
        if aim.startswith("追踪"):
            trk = st.selectbox("追踪模式", ["接收", "发射", "转发"],
                               key=f"{prefix}_pt_trk",
                               help="按信号方向调整指向；对几何覆盖三种模式都指向目标，转发多用于中继。")
            tm = {"接收": 0, "发射": 1, "转发": 2}[trk]
            return {"type": "Targeted", "target_mode": 0, "track_mode": tm}
        om = st.selectbox("偏置姿态类型", ["YPR", "Euler", "Quaternion", "AzEl", "matrix"],
                          key=f"{prefix}_pt_om",
                          help="与 Fixed 指向同款姿态方法（欧拉角/四元数等）；在目标方向基础上叠加该偏移。")
        op = fixed_method_inputs(om, prefix=f"{prefix}_pt_of")
        if op is not None:
            return {"type": "Targeted", "target_mode": 1,
                    "offset_method": om, "offset_params": op}
        st.warning("请完成偏置姿态参数输入。")
    elif mode == "Spinning 旋转":
        st.caption("说明：旋转扫描在同一次分析（单个 ATK 实例）内相位确定、结果可复现；"
                   "跨实例初始相位不锚定，同参数两次运行相位可能不同，结果视为一次代表性实现。")
        c1, c2, c3 = st.columns(3)
        az = c1.number_input("方位角(°)", value=0.0, min_value=-180.0, max_value=360.0,
                             step=1.0, key=f"{prefix}_sp_az",
                             help="旋转轴指向的方位角（相对参考方向）")
        el = c2.number_input("高度角(°)", value=-90.0, min_value=-90.0, max_value=90.0,
                             step=1.0, key=f"{prefix}_sp_el",
                             help="旋转轴指向的高度角（-90=指向天底）")
        cone = c3.number_input("锥角(°)", value=30.0, min_value=0.1, max_value=90.0,
                               step=1.0, key=f"{prefix}_sp_cone",
                               help="旋转扫描的锥角（扫描范围）")
        c4, c5, c6 = st.columns(3)
        scan = c4.selectbox("扫描模式", ["Continuous", "Unidirectional", "Bidirectional"],
                            key=f"{prefix}_sp_scan",
                            help="Continuous=匀速连续旋转；Unidirectional=单向扫描；"
                                 "Bidirectional=来回扫描")
        rate = c5.number_input("转速(°/s)", value=1.0, min_value=0.001, step=0.1,
                               key=f"{prefix}_sp_rate")
        offset = c6.number_input("初始偏差角(°)", value=0.0, min_value=0.0, max_value=360.0,
                                 step=1.0, key=f"{prefix}_sp_off",
                                 help="旋转初始角；跨实例不锚定，同参数两次运行相位可能不同。")
        parallel = st.checkbox(
            "旋转星分片并行（各片相位独立随机，更快）",
            value=False, key=f"{prefix}_sp_parallel",
            help="默认关闭：所有旋转星聚为单个 ATK 实例，相位一致、可复现（旋转星多时会慢）；"
                 "开启后旋转星也按普通分片并行，各片相位独立随机，结果视为代表性实现。")
        sa, ea = 0.0, 360.0
        if scan != "Continuous":
            c7, c8 = st.columns(2)
            sa = c7.number_input("开始角(°)", value=0.0, min_value=0.0, max_value=360.0,
                                 step=1.0, key=f"{prefix}_sp_sa")
            ea = c8.number_input("结束角(°)", value=360.0, min_value=0.0, max_value=360.0,
                                 step=1.0, key=f"{prefix}_sp_ea")
        return {"type": "Spinning", "azimuth": float(az), "elevation": float(el),
                "cone_angle": float(cone), "scan": scan,
                "start_angle": float(sa), "stop_angle": float(ea),
                "rate": float(rate), "offset": float(offset),
                "parallel": bool(parallel)}
    return None


def _parse_constraint_state(name: str, params: str) -> dict:
    """把已保存的 {name, params} 还原为控件状态 {en,min,max,onoff,val,extra}。"""
    s = (params or "").strip()
    st0 = {"en": True, "min": None, "max": None, "onoff": "On", "val": None, "extra": ""}
    if not s:
        return st0
    if s in ("On", "Off"):
        st0["onoff"] = s
        return st0
    m = re.search(r"Min\s+([-\d.eE]+)", s)
    if m:
        st0["min"] = float(m.group(1))
    m = re.search(r"Max\s+([-\d.eE]+)", s)
    if m:
        st0["max"] = float(m.group(1))
    if "ExcludeIntervals" in s:
        st0["extra"] = "ExcludeIntervals"
    elif "IncludeIntervals" in s:
        st0["extra"] = "IncludeIntervals"
    if st0["min"] is None and st0["max"] is None:
        m = re.match(r"\s*([-\d.eE]+)\s*$", s)
        if m:
            st0["val"] = float(m.group(1))
    return st0


def _fmt_num(v) -> str:
    """数字转普通十进制字符串（避免 4e+07 这类科学计数法）。"""
    s = f"{float(v):f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _build_constraint_params(name: str, st0: dict) -> str:
    """把控件状态还原为 SetConstraint 参数串（与 ATK 文档一致）。"""
    kind = CONSTRAINT_META[name][1]
    if kind == "onoff":
        return st0.get("onoff") or "On"
    if kind == "single":
        v = st0.get("val")
        return "0" if v is None else _fmt_num(v)
    parts = []
    if st0.get("min") is not None:
        parts.append(f"Min {_fmt_num(st0['min'])}")
    if st0.get("max") is not None:
        parts.append(f"Max {_fmt_num(st0['max'])}")
    if name == "AzimuthAngle" and len(parts) < 2:
        parts = ["Min 0", "Max 360"]  # ATK 要求方位角必须同时给 Min/Max
    if not parts and not st0.get("extra"):
        return ""
    params = " ".join(parts)
    if st0.get("extra"):
        params = (params + " " + st0["extra"]).strip()
    return params


def constraints_ui(prefix: str, allow_facility: bool,
                   initial_constraints=None) -> list:
    """约束输入（仿 ATK 约束页）：按作用对象分页签，每类约束=启用开关+输入框。"""
    scopes = ([("facility", "目标点"), ("sensor", "每颗星传感器"), ("satellite", "每颗星")]
              if allow_facility else [("sensor", "该星传感器"), ("satellite", "该星")])
    init = {}
    for c in initial_constraints or []:
        obj = c.get("object", "sensor")
        name = c.get("name", "")
        if name in CONSTRAINT_META:
            init[(obj, name)] = _parse_constraint_state(name, c.get("params", ""))
    if not initial_constraints:
        # 保持旧默认：目标点（逐星面板=该星传感器）太阳地平仰角 ≥10°
        d_obj = "facility" if allow_facility else "sensor"
        init[(d_obj, "SunGroundElevAngle")] = _parse_constraint_state(
            "SunGroundElevAngle", "Min 10")

    rows = []
    with st.expander("约束配置（基本 + 光照，默认折叠）", expanded=False):
        st.caption("通用含义：Min=下限、Max=上限，访问仅当参数落在 [Min, Max] 区间内才有效；"
                   "开关类选 On/Off；规避角只填一个最小夹角。角度单位 °、距离 m、时间 s；"
                   "方位角必须同时填 Min 和 Max。")
        tabs = st.tabs([lab for _, lab in scopes])
        for tab, (scope, _label) in zip(tabs, scopes):
            with tab:
                for group_title, names in CONSTRAINT_GROUPS:
                    st.markdown(f"**{group_title}**")
                    for name in names:
                        label, kind, unit, defaults, step = CONSTRAINT_META[name]
                        base = f"{prefix}_c_{scope}_{name}"
                        s0 = init.get((scope, name), {})
                        title = label + (f"（{unit}）" if unit else "")
                        help_txt = CONSTRAINT_HELP.get(name, "")
                        if kind == "minmax":
                            c0, c1, c2, c3 = st.columns([1.25, 1.0, 1.0, 0.85])
                            en = c0.checkbox(title, value=bool(s0.get("en", False)),
                                             key=f"{base}_en", help=help_txt)
                            mn = c1.number_input(
                                "Min" + (f"（{unit}）" if unit else ""),
                                value=s0.get("min") if s0.get("min") is not None else defaults[0],
                                step=step, disabled=not en, key=f"{base}_min",
                                help="下限：参数须 ≥ 此值（只填一边表示单侧限制）")
                            mx = c2.number_input(
                                "Max" + (f"（{unit}）" if unit else ""),
                                value=s0.get("max") if s0.get("max") is not None else defaults[1],
                                step=step, disabled=not en, key=f"{base}_max",
                                help="上限：参数须 ≤ 此值（只填一边表示单侧限制）")
                            ex_opts = ["", "ExcludeIntervals", "IncludeIntervals"]
                            ex_i = ex_opts.index(s0["extra"]) if s0.get("extra") in ex_opts else 0
                            ex = c3.selectbox("附加", ex_opts, index=ex_i,
                                              disabled=not en, key=f"{base}_extra",
                                              help="ExcludeIntervals=排除该区间内的时间片；"
                                                   "IncludeIntervals=只保留该区间内的时间片。")
                            if en:
                                p = _build_constraint_params(
                                    name, {"min": float(mn), "max": float(mx), "extra": ex})
                                if p:
                                    rows.append({"object": scope, "name": name, "params": p})
                        elif kind == "onoff":
                            c0, c1 = st.columns([1.25, 0.85])
                            en = c0.checkbox(title, value=bool(s0.get("en", False)),
                                             key=f"{base}_en", help=help_txt)
                            oo = c1.selectbox("状态", ["On", "Off"],
                                              index=0 if s0.get("onoff", "On") == "On" else 1,
                                              disabled=not en, key=f"{base}_onoff",
                                              help="On=启用该项检查；Off=不检查。")
                            if en:
                                rows.append({"object": scope, "name": name, "params": oo})
                        else:  # single：规避角
                            c0, c1 = st.columns([1.25, 0.85])
                            en = c0.checkbox(title, value=bool(s0.get("en", False)),
                                             key=f"{base}_en", help=help_txt)
                            val = c1.number_input(
                                "最小夹角（°）",
                                value=s0.get("val") if s0.get("val") is not None else defaults[0],
                                step=step, disabled=not en, key=f"{base}_val",
                                help="最小允许夹角：实际夹角小于该值即不满足。")
                            if en:
                                rows.append({"object": scope, "name": name,
                                             "params": _build_constraint_params(
                                                 name, {"val": float(val)})})
        st.caption("时间约束（开始/结束/时长/时段）与通信约束暂未实现；"
                   "通信约束仅适用于发射/接收对象，本软件不涉及。")
        st.caption(f"当前启用 {len(rows)} 条约束。")
    return rows


def attitude_ui(prefix: str, initial=None) -> dict | None:
    """整星姿态（标准姿态族，AttGenType=EVeProfile 0-4 + 偏移四元数）。"""
    initial = initial or {}
    opts = ["不设置", "固连系 VVLH（默认）", "惯性系 VVLH",
            "ECI 对速度定向", "ECF 对速度定向", "太阳对齐 + 天底约束"]
    _map = {"固连系 VVLH（默认）": 0, "惯性系 VVLH": 1,
            "ECI 对速度定向": 2, "ECF 对速度定向": 3,
            "太阳对齐 + 天底约束": 4}
    _init = int(initial.get("profile", -1)) if initial.get("profile") is not None else -1
    _lab = next((k for k, v in _map.items() if v == _init), "不设置")
    mode = st.selectbox("姿态类型", opts, index=opts.index(_lab),
                        key=f"{prefix}_mode",
                        help="整星姿态：决定卫星本体在空间的指向（对地定向/速度定向等），"
                             "传感器指向相对星体生效。0=固连系VVLH（默认）；1=惯性系VVLH；"
                             "2=ECI速度定向；3=ECF速度定向；4=太阳对齐。")
    if mode == "不设置":
        return None
    profile = _map[mode]
    ip = initial.get("offset") or []
    st.caption("在基准姿态上叠加的固定旋转偏移（默认全 0 = 不偏移）。")
    _init_type = "四元数（默认）"
    if isinstance(ip, dict):
        _init_type = {"euler": "欧拉角", "ypr": "YPR 角",
                      "quaternion": "四元数（默认）"}.get(
            str(ip.get("type") or "").lower(), "四元数（默认）")
    off_label = st.selectbox("偏置形式", ["四元数（默认）", "欧拉角", "YPR 角"],
                             index=["四元数（默认）", "欧拉角", "YPR 角"].index(_init_type),
                             key=f"{prefix}_off_type",
                             help="ATK 偏置形式三选一：四元数(qx,qy,qz)、欧拉角(绕X/Y/Z连续旋转)、YPR角(先偏航/再俯仰/后滚转)")
    if off_label == "四元数（默认）":
        _v = ip if isinstance(ip, list) else []
        c1, c2, c3 = st.columns(3)
        qx = c1.number_input("qx", value=float(_v[0]) if len(_v) > 0 else 0.0,
                             min_value=-1.0, max_value=1.0, step=0.01, key=f"{prefix}_qx")
        qy = c2.number_input("qy", value=float(_v[1]) if len(_v) > 1 else 0.0,
                             min_value=-1.0, max_value=1.0, step=0.01, key=f"{prefix}_qy")
        qz = c3.number_input("qz", value=float(_v[2]) if len(_v) > 2 else 0.0,
                             min_value=-1.0, max_value=1.0, step=0.01, key=f"{prefix}_qz")
        offset = [float(qx), float(qy), float(qz)]
    elif off_label == "欧拉角":
        _v = ip.get("params") if isinstance(ip, dict) and ip.get("params") else [0.0, 0.0, 0.0]
        _seq = ip.get("seq", "123") if isinstance(ip, dict) else "123"
        c1, c2 = st.columns([1, 2])
        seq = c1.text_input("转序(如 123/321)", value=_seq, key=f"{prefix}_eu_seq",
                            help="intrinsic 连续旋转：'123'=先绕X、再绕Y、最后绕Z")
        e1, e2, e3 = c2.columns(3)
        r1 = e1.number_input("角度1(°)", value=float(_v[0]), step=1.0, key=f"{prefix}_eu_r1")
        r2 = e2.number_input("角度2(°)", value=float(_v[1]), step=1.0, key=f"{prefix}_eu_r2")
        r3 = e3.number_input("角度3(°)", value=float(_v[2]), step=1.0, key=f"{prefix}_eu_r3")
        offset = {"type": "Euler", "seq": seq, "params": [float(r1), float(r2), float(r3)]}
    else:
        _v = ip.get("params") if isinstance(ip, dict) and ip.get("params") else [0.0, 0.0, 0.0]
        _seq = str(ip.get("seq", "YPR")) if isinstance(ip, dict) else "YPR"
        c1, c2 = st.columns([1, 2])
        seq = c1.text_input("转序(Y/P/R)", value=_seq, key=f"{prefix}_ypr_seq",
                            help="默认 YPR=先偏航(绕Z)、再俯仰(绕Y)、最后滚转(绕X)")
        y1, y2, y3 = c2.columns(3)
        yaw = y1.number_input("偏航(°)", value=float(_v[0]), step=1.0, key=f"{prefix}_ypr_yaw")
        pit = y2.number_input("俯仰(°)", value=float(_v[1]), step=1.0, key=f"{prefix}_ypr_pitch")
        rol = y3.number_input("滚转(°)", value=float(_v[2]), step=1.0, key=f"{prefix}_ypr_roll")
        offset = {"type": "YPR", "seq": seq, "params": [float(yaw), float(pit), float(rol)]}
    return {"profile": int(profile), "offset": offset}


def panel(prefix: str, allow_facility: bool, initial: dict | None = None) -> dict:
    """全局/逐星共用配置面板：左栏=定义（类型+参数+指向），右栏=约束（基本+光照）。

    ATK 原生样式：左侧"定义"，右侧"约束"；自研与 ATK 引擎共用同一面板，保证口径一致。
    """
    initial = initial or {}
    left, right = st.columns(2, gap="medium")
    with left:
        st.markdown("**定义**")
        stype, sparams = sensor_type_ui(prefix + "_d",
                                        initial.get("sensor_type", "SimpleCone"),
                                        initial.get("sensor_params"))
        st.markdown("**卫星姿态（可选）**")
        attitude = attitude_ui(prefix + "_a", initial.get("attitude"))
        pointing = pointing_ui(prefix + "_p")
        if pointing is None and initial.get("pointing"):
            pointing = dict(initial["pointing"])
            st.caption(f"未改动，保留已保存指向：{initial['pointing']}")
        elif pointing is None:
            pointing = None
    with right:
        st.markdown("**约束（基本 + 光照）**")
        constraints = constraints_ui(prefix + "_c", allow_facility,
                                     initial.get("constraints"))
        if not constraints and initial.get("constraints"):
            st.caption(f"已保存约束 {len(initial['constraints'])} 条（清空表格即可删除）。")
    return {"sensor_type": stype, "sensor_params": sparams, "attitude": attitude,
            "pointing": pointing, "constraints": constraints}


def cfg_file() -> Path:
    p = sl.PROJECT_ROOT / "data" / "atk_sat_config.toml"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _tq(s) -> str:
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _cfg_to_toml_lines(cfg: dict, path: str) -> list:
    """返回配置表（path 如 'global' / 'sats.28890'）的 TOML 行。

    persist() 负责先写 [path] 表头；标量键写在当前表下，
    约束数组表用绝对路径 [[path.constraints]]，避免 TOML 表头逃逸到顶层。
    """
    lines = []
    if cfg.get("sensor_type"):
        lines.append(f"sensor_type = {_tq(cfg['sensor_type'])}")
    if cfg.get("sensor_params"):
        lines.append("sensor_params = ["
                     + ", ".join(repr(float(x)) for x in cfg["sensor_params"])
                     + "]")
    if cfg.get("min_elevation_deg") is not None:
        lines.append(f"min_elevation_deg = {float(cfg['min_elevation_deg']):g}")
    if cfg.get("pointing"):
        pt = cfg["pointing"]
        inner = []
        for k, v in pt.items():
            if isinstance(v, list):
                inner.append(f"{k} = [" + ", ".join(repr(float(x)) for x in v) + "]")
            elif isinstance(v, bool):
                inner.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                inner.append(f"{k} = {_tq(v)}")
            else:
                inner.append(f"{k} = {repr(float(v))}")
        lines.append("pointing = { " + ", ".join(inner) + " }")
    if cfg.get("attitude"):
        at = cfg["attitude"]
        inner = []
        for k, v in at.items():
            if isinstance(v, list):
                inner.append(f"{k} = [" + ", ".join(repr(float(x)) for x in v) + "]")
            elif isinstance(v, bool):
                inner.append(f"{k} = {'true' if v else 'false'}")
            elif isinstance(v, str):
                inner.append(f"{k} = {_tq(v)}")
            else:
                inner.append(f"{k} = {int(v) if isinstance(v, (int, float)) and float(v).is_integer() else repr(float(v))}")
        lines.append("attitude = { " + ", ".join(inner) + " }")
    for c in cfg.get("constraints") or []:
        lines.append(f"[[{path}.constraints]]")
        lines.append(f"object = {_tq(c['object'])}")
        lines.append(f"name = {_tq(c['name'])}")
        lines.append(f"params = {_tq(c['params'])}")
    return lines


def persist(global_cfg=None, sat_cfgs=None) -> None:
    g = dict(global_cfg) if global_cfg is not None else (st.session_state.get("atk_global_cfg") or {})
    s = dict(sat_cfgs) if sat_cfgs is not None else (st.session_state.get("atk_sat_cfgs") or {})
    lines = ["# ATK 传感器配置（程序维护，可手改）", ""]
    lines.append("[global]")
    lines += _cfg_to_toml_lines(g, "global")
    lines.append("")
    lines.append("[sats]")
    for nid, c in s.items():
        lines.append(f"[sats.{nid}]")
        lines += _cfg_to_toml_lines(c, f"sats.{nid}")
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
