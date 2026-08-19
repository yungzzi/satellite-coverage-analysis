# -*- coding: utf-8 -*-
"""
覆盖性分析 MVP（第一版）
========================
功能：给定 地区边界(GeoJSON) + 卫星TLE + 时间段，
     计算每颗卫星对该地区的覆盖时间窗口，输出 CSV 和甘特图。

【覆盖的物理定义】（工作口径，参数在 CONFIG 里可改）
  把地区离散成"网格代表点"。某个时刻，一颗卫星"覆盖"该地区，当且仅当
  地区内【至少一个】网格代表点同时满足两个条件：
    ① 该点在卫星的"覆盖圆"内：点与星下点的地心角 <= 覆盖圆半径角。
    覆盖圆由传感器视场半角（视线张角，默认 10°，假设值）和轨道高度决定，高度越高圆越大。
    ② 该点看卫星的仰角 >= 最小仰角（默认 10°）。
       仰角太低 = 视线斜穿大气、图像质量差，不算有效覆盖。
  把连续满足的时刻合并成"覆盖窗口"，就是输出表格里的一行。

【程序流程】（对应下面的每个函数）
1. load_region_polygon   : 读地区边界 GeoJSON -> shapely 多边形
2. make_grid_points      : 在多边形内撒网格代表点（间距约 22 km）
3. load_satellites       : 读 TLE 文件 -> 按名字关键字筛选卫星
4. make_time_array       : 生成"每分钟一个"的时间轴
5. analyze_one_satellite :
   a. skyfield（内部就是 SGP4 轨道外推算法）算出每颗星每个时刻的星下点（经纬度+高度）
   b. central_angle_deg       : 星下点到每个网格点的地心角（haversine 大圆公式）
   c. elevation_at_deg        : 由地心角+轨道高度反推该点看卫星的仰角
   d. footprint_max_angle_deg : 由高度+视场半角算覆盖圆半径角
   e. covered_grid = (地心角<=覆盖圆) & (仰角>=最低仰角)  <- 覆盖判定核心
   f. covered = 任一网格点被覆盖，合并连续时刻为窗口
6. build_outputs : 输出窗口明细表、卫星汇总表、甘特图

依赖：skyfield, sgp4, numpy, pandas, matplotlib, shapely
用法：python src/coverage_analysis.py
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone

try:
    import tomllib          # Python 3.11+ 自带，读取 TOML 配置文件
except ImportError:
    tomllib = None

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 无界面环境也能出图
import matplotlib.pyplot as plt
import os
from matplotlib import font_manager

for _f in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
    if os.path.exists(_f):
        font_manager.fontManager.addfont(_f)
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False
import matplotlib.dates as mdates
from shapely.geometry import Point, shape
from skyfield.api import EarthSatellite, load

# ==================== 0. 配置（改这里即可） ====================
CONFIG = {
    "region_name": "浙江省",            # 仅用于图表标题和提示
    "region_geojson": "data/geo/330000.json",   # 地区边界（330000=浙江，DataV GeoAtlas）
    "region_mode": "polygon",           # 目标区域模式：polygon=多边形+网格 / circle=点+半径
    "target": {"lat": 30.25, "lon": 120.15, "radius_km": 5.0},  # circle 模式：圆心+半径
    "tle_file": "data/raw/active_tle.txt",       # TLE 数据文件（CelesTrak 下载）
    # 第一版只分析遥感卫星（YAOGAN）对地面的覆盖。
    # 天链（TIANLIAN）是地球同步中继卫星，不直接成像，通信链路分析放到后续版本。
    "satellite_keywords": ["YAOGAN"],
    "start_utc": "2026-08-03 00:00:00",          # 分析开始时间（UTC）
    "end_utc": "2026-08-03 08:00:00",            # 分析结束时间（UTC）
    "step_minutes": 1,                            # 输出/选星时间步长（分钟），决策矩阵粒度（P1 解耦）
    "coarse_step_seconds": 10,                    # 检测步长（秒）：粗筛网格，短窗口不漏检（Q161/P1）
    "fov_half_angle_deg": 10.0,                   # 视场半角（度）：视线张角/天底圆锥半角（假设值，Q147）
    "min_elevation_deg": 10.0,                    # 最小仰角（度）：太低受大气影响，不算有效覆盖
    "grid_spacing_deg": 0.2,                      # 网格间距（度，约 22 km）
    "max_gantt_rows": 15,                         # 甘特图最多画几颗星（按覆盖时长排名）
    "save_gantt": False,                          # 甘特图默认不生成；需要时改 True
    "save_plots": False,                          # 两张图默认都不生成；需要演示图时改 True
    "refine_step_seconds": 1,                     # 两段式：窗口边界精化步长（秒），0=不精化（P1 改 1s）
    "refine_margin_minutes": 3,                   # 精化时向窗口两侧多扫的分钟数
}

R_EARTH_KM = 6378.137  # 地球平均半径（km），球面近似


def apply_config_file(cfg, path):
    """读取 TOML 配置文件并覆盖默认配置（命令行参数优先级更高）"""
    if tomllib is None:
        print("当前 Python 版本不支持 TOML 配置文件（需要 3.11+），请改用命令行参数。")
        sys.exit(1)
    with open(path, "rb") as f:
        raw = f.read()
    if raw.startswith(b"\xef\xbb\xbf"):      # 兼容 Windows 编辑器加的 UTF-8 BOM
        raw = raw[3:]
    data = tomllib.loads(raw.decode("utf-8"))
    # 目标区域
    if "region_mode" in data:
        cfg["region_mode"] = str(data["region_mode"])
    if "region_geojson" in data:
        cfg["region_geojson"] = str(data["region_geojson"])
    if "target_lat" in data:
        cfg["target"]["lat"] = float(data["target_lat"])
    if "target_lon" in data:
        cfg["target"]["lon"] = float(data["target_lon"])
    if "target_radius_km" in data:
        cfg["target"]["radius_km"] = float(data["target_radius_km"])
    # 分析时段
    if "start_utc" in data:
        cfg["start_utc"] = str(data["start_utc"])
    if "end_utc" in data:
        cfg["end_utc"] = str(data["end_utc"])
    if "step_minutes" in data:
        cfg["step_minutes"] = int(data["step_minutes"])
    if "coarse_step_seconds" in data:
        cfg["coarse_step_seconds"] = int(data["coarse_step_seconds"])
    # 传感器参数（全局默认；"按星分配不同参数"是后续功能，设计中）
    if "fov_half_angle_deg" in data:
        cfg["fov_half_angle_deg"] = float(data["fov_half_angle_deg"])
    if "min_elevation_deg" in data:
        cfg["min_elevation_deg"] = float(data["min_elevation_deg"])
    # 网格与输出
    if "grid_spacing_deg" in data:
        cfg["grid_spacing_deg"] = float(data["grid_spacing_deg"])
    if "save_plots" in data:
        cfg["save_plots"] = bool(data["save_plots"])
    if "save_gantt" in data:
        cfg["save_gantt"] = bool(data["save_gantt"])
    if "max_gantt_rows" in data:
        cfg["max_gantt_rows"] = int(data["max_gantt_rows"])
    print(f"已读取配置文件：{path}")


# ==================== 1. 地区边界 -> 网格代表点 ====================

def load_region_polygon(geojson_path):
    """读取地区边界 GeoJSON，返回 shapely 多边形对象"""
    with open(geojson_path, "r", encoding="utf-8-sig") as f:
        gj = json.load(f)
    if gj.get("type") == "FeatureCollection":
        geom = gj["features"][0]["geometry"]
    else:
        geom = gj["geometry"]
    return shape(geom)


def make_grid_points(polygon, spacing_deg):
    """在地区边界内按间距撒网格代表点，返回 (纬度数组, 经度数组)"""
    minx, miny, maxx, maxy = polygon.bounds
    lons = np.arange(minx, maxx + spacing_deg, spacing_deg)
    lats = np.arange(miny, maxy + spacing_deg, spacing_deg)
    grid_lats, grid_lons = [], []
    for lat in lats:
        for lon in lons:
            if polygon.contains(Point(lon, lat)):
                grid_lats.append(lat)
                grid_lons.append(lon)
    return np.array(grid_lats), np.array(grid_lons)


# ==================== 2. TLE -> 卫星列表 ====================

def load_satellites(tle_path, keywords, ts):
    """读取 TLE 文件，按名字关键字筛选卫星，返回 skyfield 卫星对象列表"""
    with open(tle_path, "r", encoding="ascii", errors="replace") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    keywords = [k.upper() for k in keywords]
    sats = []
    i = 0
    while i + 2 < len(lines):
        name = lines[i]
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            if any(k in name.upper() for k in keywords):
                try:
                    sats.append(EarthSatellite(lines[i + 1], lines[i + 2], name, ts))
                except Exception as e:
                    print(f"  跳过解析失败的卫星 {name}: {e}")
            i += 3
        else:
            i += 1
    return sats


# ==================== 3. 时间轴 ====================

def make_time_array(ts, start_str, end_str, step_minutes):
    """生成 [开始, 结束) 的等间隔时间点（左闭右开，不含终点 t1；datetime 列表 + skyfield 时间对象）。
    与选星 run_selection 的栅格一致（Q151/P3）。"""
    t0 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_list = []
    t = t0
    while t < t1:
        dt_list.append(t)
        t += timedelta(minutes=step_minutes)
    return ts.from_datetimes(dt_list), dt_list


def make_dt_series(start_dt, end_dt, step_seconds):
    """生成 [start, end] 等间隔的 UTC 时间点列表（步长：秒）"""
    out = []
    t = start_dt
    while t <= end_dt:
        out.append(t)
        t += timedelta(seconds=step_seconds)
    return out


# ==================== 4. 覆盖判定（核心几何） ====================

def footprint_max_angle_deg(altitude_km, fov_half_deg):
    """
    由轨道高度和视场半角计算"覆盖圆"的最大地心角（度）。
    地心角 = 星下点与目标点在地心处张开的夹角，0°=正下方，
    约 1° ≈ 111 km（赤道）。覆盖圆 = 以星下点为圆心、这个角为半径的圆。

    物理图景：把传感器视场看成以天底为轴、半角 η 的圆锥（η = 视场半角 / 视线张角，
    默认 10°，假设值）；圆锥与地面的交线就是覆盖圆（以星下点为圆心）。

    公式推导（球面近似）：
      在"地心 O - 卫星 S - 目标点 P"三点组成的平面三角形里：
        卫星处内角 = 视场半角 η；目标点处内角 = 90°+仰角 ε（钝角）；地心处 = λ
        内角和：η + (90°+ε) + λ = 180°  ->  λ = 90° - ε - η
        正弦定理：sin(η)/R = sin(90°+ε)/r = cos(ε)/r  ->  sin(η) = (R/r)·cos(ε)
      联立两式，先由 η 解出边缘处的仰角 ε，再得到边缘地心角 λ。
      另外覆盖圆不可能超过地平线，所以 λ 上限 = acos(R/r)（地平线角）。

    注意（已知简化）：当地面完全被视场包住时（典型如高轨 GEO，30° 锥体根本
    够不到地表），公式退化为"90°-η"的保守估计。对中小地区（如浙江）无影响，
    但做全球/大区域或 GEO 通信覆盖时需改用"可见圆盘"口径。
    """
    r = R_EARTH_KM + altitude_km
    eta = np.radians(fov_half_deg)
    sin_eta_ratio = (r / R_EARTH_KM) * np.sin(eta)
    horizon = np.arccos(R_EARTH_KM / r)              # 地平线对应的最大地心角
    eps = np.arccos(np.clip(sin_eta_ratio, 0.0, 1.0))  # 覆盖圆边缘处的仰角
    lam = np.pi / 2 - eps - eta
    return np.degrees(np.minimum(lam, horizon))


def swath_to_half_angle_deg(altitude_km, swath_km):
    """把幅宽（km）换算成等效视场半角（度）。

    语义（Q142 拍板）：把"天底条带宽 W"近似为"以星下点为圆心、半径 W/2 的
    覆盖圆"，仍走原来的圆锥几何。球面精确式：
      半地心角 λ = (W/2)/R；卫星到条带边缘的离轴角 η 满足
      tan η = R·sin λ / (r − R·cos λ)，r = R + h。
    输入支持标量或数组（数组用于逐时刻高度），返回同形状的度数。
    """
    alt = np.asarray(altitude_km, dtype=float)
    # λ = 半幅宽对应的地心角（弧度）：弧长/半径，无需再转弧度
    lam = (float(swath_km) / 2.0) / R_EARTH_KM
    r = R_EARTH_KM + alt
    denom = r - R_EARTH_KM * np.cos(lam)
    eta = np.arctan2(R_EARTH_KM * np.sin(lam), denom)
    return np.clip(np.degrees(eta), 0.0, 89.0)


def central_angle_deg(lat1, lon1, lat2, lon2):
    """两组经纬度之间的大圆地心角（度），向量化 haversine 公式"""
    lat1r, lat2r = np.radians(lat1), np.radians(lat2)
    dlat = lat2r - lat1r
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2) ** 2
    return np.degrees(2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def elevation_at_deg(lam_deg, altitude_km):
    """
    由地心角 λ 和卫星高度 h，计算目标点看卫星的仰角（度）。
    几何（球面近似，轨道半径 r=R+h）：
      tan(ε) = (cos λ - R/r) / sin λ
    直观理解：λ=0（卫星在正上方）时 ε=90°（头顶）；
    λ 越大卫星越贴近地平线、仰角越低；λ=acos(R/r) 时 ε=0（刚好在地平线上）。
    这个公式就是"仰角约束"的数学来源：为什么覆盖不只看视场圆，
    还要加一道仰角门槛（默认 10°）——太斜的视线质量差，不算有效覆盖。
    """
    lam = np.radians(lam_deg)
    r = R_EARTH_KM + altitude_km
    return np.degrees(np.arctan2(np.cos(lam) - R_EARTH_KM / r, np.sin(lam)))


def circle_overlap_ratio(d_km, r_foot_km, r_target_km):
    """
    两个圆（平面近似）重叠面积占目标圆面积的比例。
    d_km=圆心距，r_foot_km=足印半径，r_target_km=目标圆半径。
    用于 circle 模式：足印圆与 5km 目标圆的重叠程度。
    """
    if d_km >= r_foot_km + r_target_km:
        return 0.0
    if d_km <= abs(r_foot_km - r_target_km):
        # 小圆完全在大圆内
        return 1.0 if r_target_km <= r_foot_km else (r_foot_km / r_target_km) ** 2
    r1, r2, d = r_foot_km, r_target_km, d_km
    a = (r1 * r1 * np.arccos((d * d + r1 * r1 - r2 * r2) / (2 * d * r1))
         + r2 * r2 * np.arccos((d * d + r2 * r2 - r1 * r1) / (2 * d * r2))
         - 0.5 * np.sqrt(np.clip((-d + r1 + r2) * (d + r1 - r2)
                                 * (d - r1 + r2) * (d + r1 + r2), 0, None)))
    return min(1.0, a / (np.pi * r2 * r2))


def compute_covered_series(sat, t, dt_list, region, cfg):
    """
    计算该卫星在给定时间点列表上对区域的覆盖判定。
    返回 (covered, ratio)：
      covered : 每个时刻是否覆盖（任一网格点满足条件），形状 (T,)
      ratio   : 每个时刻的覆盖比例（多边形=被覆盖网格点占比；圆=目标圆被覆盖面积占比）

    region 支持两种模式：
      {"mode": "polygon", "lats": [...], "lons": [...]}   多边形 + 网格点（原有）
      {"mode": "circle",  "lat": float, "lon": float,
       "radius_km": float}                                点 + 半径（5km 圆目标，导师拍板）
    """
    # 用 SGP4 外推整段时间，得到每个时刻的星下点（纬度、经度、高度）
    geocentric = sat.at(t)
    sub = geocentric.subpoint()
    lat = sub.latitude.degrees          # 形状 (T,)
    lon = sub.longitude.degrees
    alt = sub.elevation.km

    # 每个时刻的覆盖圆最大地心角（随高度变化）：形状 (T,)
    # 若该星配置了幅宽（swath_km>0），先按当前高度换算成等效视场半角，
    # 再走同一套圆锥几何（Q142：幅宽只作换算，仍按圆锥计算）。
    swath_km = float(cfg.get("swath_km") or 0.0)
    if swath_km > 0:
        eta = swath_to_half_angle_deg(alt, swath_km)
        lam_max = footprint_max_angle_deg(alt, eta)
    else:
        lam_max = footprint_max_angle_deg(alt, cfg["fov_half_angle_deg"])

    # ---- circle 模式：目标是一个点+半径的圆 ----
    if region["mode"] == "circle":
        tlat, tlon = region["lat"], region["lon"]
        r_km = region["radius_km"]
        lam = central_angle_deg(lat, lon,
                                np.full(len(lat), tlat), np.full(len(lon), tlon))
        elev = elevation_at_deg(lam, alt)
        # 判据（导师拍板）：足印圆与目标圆【有交集】且圆心处仰角达标
        lam_t = np.degrees(r_km / R_EARTH_KM)          # 目标圆半径对应的地心角
        covered = (lam <= lam_max + lam_t) & (elev >= cfg["min_elevation_deg"])
        # 覆盖比例：目标圆被足印覆盖的面积占比（平面近似，km 尺度）
        r_foot_km = np.radians(lam_max) * R_EARTH_KM
        d_km = np.radians(lam) * R_EARTH_KM
        ratio = np.array([circle_overlap_ratio(dd, rf, r_km)
                          for dd, rf in zip(d_km, r_foot_km)])
        return covered, ratio

    # ---- polygon 模式：地区内撒网格代表点（原有逻辑） ----
    grid_lats, grid_lons = region["lats"], region["lons"]

    # 每个时刻，星下点到所有网格点的地心角：形状 (T, N)
    lam = central_angle_deg(lat[:, None], lon[:, None],
                            grid_lats[None, :], grid_lons[None, :])

    # 每个网格点处的仰角：形状 (T, N)
    elev = elevation_at_deg(lam, alt[:, None])

    # 覆盖判定：点在覆盖圆内 且 仰角达标 -> 形状 (T, N)
    covered_grid = (lam <= lam_max[:, None]) & (elev >= cfg["min_elevation_deg"])
    # 上面这行就是整个程序的核心判定：一个网格点必须【同时】满足
    #   (a) 在视场覆盖圆内（星下点为圆心，半径随高度变化）
    #   (b) 该点看卫星的仰角 >= 最小仰角（默认 10°）
    return covered_grid.any(axis=1), covered_grid.mean(axis=1)


def analyze_one_satellite(sat, t, dt_list, region, cfg, period_end=None,
                          covered_fn=None):
    """
    分析单颗卫星的覆盖情况，返回窗口列表：
    [(开始datetime, 结束datetime, 窗口内最大覆盖比例), ...]

    两段式（P1 解耦，Q161/Q168）：
      第一步 检测：按 coarse_step_seconds（默认 10s）粗网格整段扫描，找出"大致覆盖段"；
      第二步 精化：对每个粗窗口边界 ±margin 按 refine_step_seconds（默认 1s）加密，
               把起止定到秒级；横跨整个分析时段的窗口（边界=时段端点）跳过不精化（Q162 性能守卫）；
               精化时按连续覆盖段切分，被空隙隔开的两个真实窗口不会被错误合并。
    dt_list 只提供时段范围（[t0, t1) 左闭右开）与 skyfield 时间基准，不再决定检测步长。
    period_end：分析时段真实终点（默认 dt_list[-1] + step，兼容旧调用）。
    """
    t_lo = dt_list[0]
    step_s = int(cfg.get("step_minutes", 1)) * 60
    if period_end is None:
        period_end = dt_list[-1] + timedelta(seconds=step_s)
    coarse = int(cfg.get("coarse_step_seconds", 10))
    refine = int(cfg.get("refine_step_seconds", 0))

    # ---- 第一步：coarse 秒粗筛（检测步长与输出/选星步长解耦） ----
    dt_coarse = make_dt_series(t_lo, period_end, coarse)
    t_coarse = t.ts.from_datetimes(dt_coarse)
    _cov_fn = covered_fn or compute_covered_series
    covered, ratio = _cov_fn(sat, t_coarse, dt_coarse, region, cfg)
    windows = []
    in_window = False
    for i, c in enumerate(covered):
        if c and not in_window:
            start = dt_coarse[i]
            in_window = True
        elif not c and in_window:
            windows.append([start, dt_coarse[i - 1], float(ratio[i - 1])])
            in_window = False
    if in_window:
        windows.append([start, dt_coarse[-1], float(ratio[-1])])

    # ---- 第二步：窗口边界精化（两段式） ----
    margin = timedelta(minutes=cfg.get("refine_margin_minutes", 3))
    if refine and windows:
        refined = []
        for start, end, r in windows:
            # 横跨全时段（覆盖到两端）的窗口：边界就是 t0/t1，无需重算（Q162 性能守卫）
            if start - margin <= t_lo and end + margin >= period_end:
                refined.append([start, end, r])
                continue
            dt_f = make_dt_series(start - margin, end + margin, refine)
            t_f = t.ts.from_datetimes(dt_f)
            cov_f, _ = _cov_fn(sat, t_f, dt_f, region, cfg)
            idx = np.where(cov_f)[0]
            if not len(idx):
                refined.append([start, end, r])
                continue
            # 按连续覆盖段切分：间隔 ≥1 个精化步长就拆开，避免合并两个真实窗口（P1）
            seg_start = idx[0]
            prev_i = idx[0]
            for k in idx[1:]:
                if k > prev_i + 1:
                    refined.append([dt_f[seg_start], dt_f[prev_i], r])
                    seg_start = k
                prev_i = k
            refined.append([dt_f[seg_start], dt_f[prev_i], r])
        # 相邻窗口触碰/重叠才合并（间隔 ≥ refine 秒的不合并）
        refined.sort(key=lambda w: w[0])
        merged = []
        for w in refined:
            if merged and w[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], w[1])
                merged[-1][2] = max(merged[-1][2], w[2])
            else:
                merged.append(w)
        windows = [tuple(w) for w in merged]
        # 精化会向窗口两侧多扫 margin 分钟，可能越过分析时段边界，裁回 [t_lo, period_end]
        windows = [(max(s, t_lo), min(e, period_end), r) for s, e, r in windows]
        # 边界裁剪后可能出现 0 时长伪窗口（窗口实际在时段外），过滤掉（Q92 多坐标验证发现）
        windows = [w for w in windows if w[1] > w[0]]
    return windows


# ==================== 5. 输出：CSV + 甘特图 ====================

def window_duration_s(start, end, cfg):
    """窗口时长（秒）：边界精化后用起止差；纯 1 分钟粗筛时补一个步长"""
    if cfg.get("refine_step_seconds", 0):
        return int(round((end - start).total_seconds()))
    return int((end - start).total_seconds()) + cfg["step_minutes"] * 60


def summarize_windows(all_windows, cfg, period_seconds):
    """
    按卫星聚合窗口 -> 汇总 DataFrame（每颗星一行），主程序与查询工具共用。
    额外加一列 coverage_pct：该星总覆盖时长占"本表时间范围"的百分比
    （整体导出按整个分析时段，子时段导出按子时段）。
    """
    summaries = []
    for name in sorted({w[0] for w in all_windows}):
        sub = sorted([w for w in all_windows if w[0] == name], key=lambda w: w[1])
        durations = [window_duration_s(w[1], w[2], cfg) for w in sub]
        gaps = [(sub[i + 1][1] - sub[i][2]).total_seconds() for i in range(len(sub) - 1)]
        total = int(sum(durations))
        summaries.append({
            "satellite": name,
            "n_windows": len(sub),
            "total_duration_s": total,
            "coverage_pct": round(total / period_seconds * 100, 1) if period_seconds else 0.0,
            "longest_window_s": int(max(durations)),
            "max_gap_s": int(max(gaps)) if gaps else None,
            "first_cover_utc": sub[0][1].strftime("%Y-%m-%d %H:%M:%S"),
            "last_cover_utc": sub[-1][2].strftime("%Y-%m-%d %H:%M:%S"),
        })
    return pd.DataFrame(summaries).sort_values("total_duration_s", ascending=False)


def build_state_table(all_windows, t0, t1, cfg):
    """
    整体时段状态表（时间视角，1 分钟分辨率）：
    只在"覆盖情况变化"时记一行：开始 / 结束 / 覆盖星数 / 卫星名单。
    结束列是"下一状态的开始时刻"（左闭右开）。
    卫星名单按"整个分析时段内的覆盖总时长"降序排（最勤快的排前面）。
    """
    step_s = cfg["step_minutes"] * 60
    n = int(round((t1 - t0).total_seconds() / step_s))   # 左闭右开：480 片（8h）
    sats_at_min = [set() for _ in range(n)]
    for name, start, end, ratio in all_windows:
        i0 = int(np.ceil((start - t0).total_seconds() / step_s - 1e-9))
        i1 = int(np.floor((end - t0).total_seconds() / step_s + 1e-9))
        i0 = max(0, min(i0, n - 1))
        i1 = max(0, min(i1, n - 1))
        for i in range(i0, i1 + 1):
            sats_at_min[i].add(name)
    # 每颗星在整个分析时段内的覆盖总时长（名单排序依据）
    sat_total = {}
    for name, start, end, ratio in all_windows:
        sat_total[name] = sat_total.get(name, 0) + window_duration_s(start, end, cfg)

    def sat_key(name):
        return (-sat_total.get(name, 0), name)

    rows = []
    prev = None
    seg_start = 0
    for i in range(n):
        key = frozenset(sats_at_min[i])
        if key != prev:
            if prev is not None:
                rows.append((t0 + timedelta(seconds=seg_start * step_s),
                             t0 + timedelta(seconds=i * step_s),
                             len(prev), ", ".join(sorted(prev, key=sat_key)),
                             (i - seg_start) * step_s))
            prev = key
            seg_start = i
    rows.append((t0 + timedelta(seconds=seg_start * step_s),
                 t1,                                # 最后一段结束 = 分析时段结束
                 len(prev), ", ".join(sorted(prev, key=sat_key)),
                 int((t1 - (t0 + timedelta(seconds=seg_start * step_s))).total_seconds())))
    return pd.DataFrame(rows, columns=["start_utc", "end_utc", "n_satellites",
                                       "satellites", "duration_s"])


def build_gaps_table(state_df):
    """空窗表：所有"一颗星都没有"的时段，按时长降序"""
    gaps = state_df[state_df["n_satellites"] == 0].copy()
    if gaps.empty:
        return gaps
    return gaps.sort_values("duration_s", ascending=False).reset_index(drop=True)


def fmt_duration(seconds):
    """把秒数变成人话（如 1小时23分 / 45分 / 12秒）"""
    seconds = int(round(seconds))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}小时{m}分"
    if m:
        return f"{m}分{s}秒"
    return f"{s}秒"


def build_summary_text(state_df, df_summary, cfg, meta):
    """自动生成一段文字摘要（人话总结），写入 report_summary.txt"""
    t0 = state_df["start_utc"].iloc[0]
    t1 = state_df["end_utc"].iloc[-1]
    total_s = (t1 - t0).total_seconds()
    covered_s = int(state_df.loc[state_df["n_satellites"] > 0, "duration_s"].sum())
    maxc = int(state_df["n_satellites"].max())
    busiest = state_df[state_df["n_satellites"] == maxc]
    gaps = build_gaps_table(state_df)

    lines = []
    lines.append(f"卫星覆盖分析摘要（{cfg['region_name']}）")
    lines.append("=" * 52)
    lines.append(f"分析时段：{t0} ~ {t1}（UTC，北京时间 = UTC+8）")
    lines.append(f"参与卫星：共 {meta['n_satellites_total']} 颗，"
                 f"其中 {len(df_summary)} 颗在此时段内有覆盖")
    lines.append(f"至少一颗星覆盖的总时长：{fmt_duration(covered_s)}"
                 f"（占分析时段的 {covered_s / total_s * 100:.1f}%）")
    lines.append(f"同时覆盖卫星数峰值：{maxc} 颗"
                 f"（发生在 {len(busiest)} 个时段，最长一段 {fmt_duration(busiest['duration_s'].max())}）")
    if not gaps.empty:
        lines.append(f"覆盖空窗（一颗星都没有）：共 {len(gaps)} 段，"
                     f"最长 {fmt_duration(gaps['duration_s'].iloc[0])}"
                     f"（{gaps['start_utc'].iloc[0]} ~ {gaps['end_utc'].iloc[0]}）")
        lines.append("最需要关注的前 5 段空窗：")
        for _, g in gaps.head(5).iterrows():
            lines.append(f"  {g['start_utc']} ~ {g['end_utc']}（{fmt_duration(g['duration_s'])}）")
    else:
        lines.append("覆盖空窗：无（全程至少一颗星可覆盖）")
    lines.append("")
    lines.append("口径说明：覆盖 = 至少一个网格代表点在视场圆内且仰角≥10°；"
                 "1 分钟粗筛 + 窗口边界秒级精化。")
    if meta.get("sensor_override_count"):
        lines.append(f"逐星传感器参数覆盖：{meta['sensor_override_count']} 条生效；"
                     f"未覆盖星用全局默认（视场半角 {cfg.get('fov_half_angle_deg')}°、"
                     f"最小仰角 {cfg.get('min_elevation_deg')}°）。")
    return "\n".join(lines)


def plot_availability(state_df, cfg, out_png):
    """可用卫星数曲线（主图）：横轴时间，纵轴同时覆盖的卫星数"""
    t0 = state_df["start_utc"].iloc[0]
    step_s = cfg["step_minutes"] * 60
    total = int(round((state_df["end_utc"].iloc[-1] - t0).total_seconds() / step_s))
    counts = np.zeros(total, dtype=int)
    for _, r in state_df.iterrows():
        i0 = int(np.ceil((r["start_utc"] - t0).total_seconds() / step_s - 1e-9))
        i1 = int(np.floor((r["end_utc"] - t0).total_seconds() / step_s + 1e-9))
        counts[i0:i1] = r["n_satellites"]
    times = [t0 + timedelta(seconds=i * step_s) for i in range(total)]
    fig, ax = plt.subplots(figsize=(12, 5))
    ax.step(times, counts, where="post", color="steelblue")
    ax.fill_between(times, 0, counts, step="post", color="steelblue", alpha=0.25)
    ax.set_xlabel("时间（UTC）")
    ax.set_ylabel("同时覆盖的卫星数")
    ax.set_title(f"{cfg['region_name']}可用卫星数曲线（1 分钟步长）")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()
    fig.savefig(out_png, dpi=150)
    plt.close(fig)


def plot_gantt(all_windows, df_summary, cfg):
    """甘特图：只画覆盖时长最长的前 N 颗，避免行数过多看不清"""
    top = df_summary.head(cfg["max_gantt_rows"])
    sats = list(top["satellite"])
    y_index = {name: i for i, name in enumerate(sats)}
    fig, ax = plt.subplots(figsize=(12, max(4, 0.45 * len(sats) + 2)))
    for name, start, end, ratio in all_windows:
        if name not in y_index:
            continue
        x0 = mdates.date2num(start)
        width = mdates.date2num(end) - x0
        ax.broken_barh([(x0, width)], (y_index[name] - 0.35, 0.7),
                       facecolors="steelblue", edgecolor="white")
    ax.set_yticks(range(len(sats)))
    ax.set_yticklabels(sats)
    ax.set_xlabel("时间（UTC）")
    ax.set_title(f"{cfg['region_name']}覆盖甘特图（覆盖时长前 {len(sats)} 颗，"
                 f"共 {len(df_summary)} 颗有覆盖；视场半角 {cfg['fov_half_angle_deg']}°，"
                 f"最小仰角 {cfg['min_elevation_deg']}°）")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    fig.autofmt_xdate()
    plt.tight_layout()
    fig.savefig("output/gantt.png", dpi=150)
    plt.close(fig)


def build_outputs(all_windows, cfg, t0, t1, n_sats_total, sat_params=None):
    """
    把所有窗口整理成一套完整输出：
      CSV（数据底座，给程序/脚本用）：
        coverage_windows.csv  窗口明细（卫星视角）
        coverage_summary.csv  卫星汇总（卫星视角）
        coverage_state.csv    整体时段状态表（时间视角，可查"某段时间怎么样"）
        coverage_gaps.csv     空窗表（风险时段）
        analysis_meta.json    本次分析元信息（时段/参数/星数）
      TXT（人话摘要）：report_summary.txt
      图（给人看）：availability_curve.png（主图）+ gantt.png（默认生成，可关）
    """
    if not all_windows:
        print("没有任何覆盖窗口。")
        return

    # 窗口明细表
    rows = []
    for name, start, end, ratio in all_windows:
        rows.append({
            "satellite": name,
            "window_start_utc": start.strftime("%Y-%m-%d %H:%M:%S"),
            "window_end_utc": end.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_s": window_duration_s(start, end, cfg),
            "max_covered_ratio": round(ratio, 4),
        })
    df_windows = pd.DataFrame(rows)
    df_summary = summarize_windows(all_windows, cfg, (t1 - t0).total_seconds())
    if sat_params:
        by_name = {p["name"]: p for p in sat_params}
        df_summary = enrich_summary_sensor(df_summary, by_name)
    df_state = build_state_table(all_windows, t0, t1, cfg)
    df_gaps = build_gaps_table(df_state)

    os.makedirs("output", exist_ok=True)
    df_windows.to_csv("output/coverage_windows.csv", index=False, encoding="utf-8-sig")
    df_summary.to_csv("output/coverage_summary.csv", index=False, encoding="utf-8-sig")
    df_state.to_csv("output/coverage_state.csv", index=False, encoding="utf-8-sig")
    df_gaps.to_csv("output/coverage_gaps.csv", index=False, encoding="utf-8-sig")
    print(f"窗口明细已保存：output/coverage_windows.csv（{len(df_windows)} 条）")
    print(f"汇总表已保存：output/coverage_summary.csv（{len(df_summary)} 颗卫星有覆盖）")
    print(f"状态表已保存：output/coverage_state.csv（{len(df_state)} 行）")
    print(f"空窗表已保存：output/coverage_gaps.csv（{len(df_gaps)} 段）")

    # 元信息 + 文字摘要
    meta = {
        "region_name": cfg["region_name"],
        "region_mode": cfg["region_mode"],
        "region_geojson": cfg.get("region_geojson", ""),
        "target": cfg.get("target", {}),
        "tle_file": cfg["tle_file"],
        "start_utc": t0.strftime("%Y-%m-%d %H:%M:%S"),
        "end_utc": t1.strftime("%Y-%m-%d %H:%M:%S"),
        "step_minutes": cfg["step_minutes"],
        "refine_step_seconds": cfg.get("refine_step_seconds", 0),
        "fov_half_angle_deg": cfg["fov_half_angle_deg"],
        "min_elevation_deg": cfg["min_elevation_deg"],
        "grid_spacing_deg": cfg["grid_spacing_deg"],
        "sensor_override_count": sum(1 for p in (sat_params or [])
                                     if p.get("source") == "override"),
        "engine": cfg.get("engine", "self"),
        "sensor_type": cfg.get("sensor_type", "SimpleCone"),
        "sensor_params": cfg.get("sensor_params"),
        "n_satellites_total": n_sats_total,
        "n_satellites_covered": int(len(df_summary)),
    }
    with open("output/analysis_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open("output/report_summary.txt", "w", encoding="utf-8") as f:
        f.write(build_summary_text(df_state, df_summary, cfg, meta))
    print("文字摘要已保存：output/report_summary.txt")

    # 图（默认关闭；需要时在 config.toml 设 save_plots=true 才生成）
    if cfg.get("save_plots", False):
        plot_availability(df_state, cfg, "output/availability_curve.png")
        print("可用卫星数曲线已保存：output/availability_curve.png")
        if cfg.get("save_gantt", False):
            plot_gantt(all_windows, df_summary, cfg)
            print("甘特图已保存：output/gantt.png")

    # 控制台输出：按总覆盖时长排序，方便看"哪些卫星可用"
    print("\n覆盖时长排名（前 10）：")
    print(df_summary.head(10).to_string(index=False))
    print("\n整体状态表预览（前 8 行，完整见 coverage_state.csv）：")
    print(df_state.head(8).to_string(index=False))
    return df_windows, df_summary, df_state, df_gaps


def resolve_sat_cfg(norad_id, overrides, global_cfg, swath_enabled=False):
    """按 NORAD 精确匹配逐星传感器参数（文件里最后命中者覆盖）。

    返回 (sat_cfg, info)：
      sat_cfg = global_cfg 副本 + 该星覆盖值（幅宽仅在 swath_enabled=True 时优先于视场半角；
               未开启或该行未填幅宽时回退用视场半角；不给全局统一幅宽默认值（Q151））；
      info    = {"norad", "source": "override"/"default",
                 "fov_half_angle_deg", "min_elevation_deg", "swath_km"}，
                供界面/Excel 展示"这颗星实际用的参数"。
    """
    sat_cfg = dict(global_cfg)
    sat_cfg.pop("swath_km", None)
    hit = None
    for o in overrides or []:
        if o.get("norad") == norad_id:
            hit = o
    if hit is None:
        info = {"norad": norad_id, "source": "default",
                "fov_half_angle_deg": global_cfg.get("fov_half_angle_deg"),
                "min_elevation_deg": global_cfg.get("min_elevation_deg"),
                "swath_km": 0.0}
        return sat_cfg, info
    sw = hit.get("swath_km") or 0
    if swath_enabled and sw > 0:
        sat_cfg["swath_km"] = float(sw)
        fov_used = None          # 判据用幅宽，角度口径不参与
    elif hit.get("fov_half_angle_deg") is not None:
        sat_cfg["fov_half_angle_deg"] = float(hit["fov_half_angle_deg"])
        fov_used = sat_cfg["fov_half_angle_deg"]
    else:
        fov_used = sat_cfg.get("fov_half_angle_deg")
    if hit.get("min_elevation_deg") is not None:
        sat_cfg["min_elevation_deg"] = float(hit["min_elevation_deg"])
    info = {"norad": norad_id, "source": "override",
            "fov_half_angle_deg": fov_used,
            "min_elevation_deg": sat_cfg.get("min_elevation_deg"),
            "swath_km": sat_cfg.get("swath_km", 0.0)}
    return sat_cfg, info


def enrich_summary_sensor(df_summary, sat_params_by_name):
    """给卫星汇总表补"这颗星实际用的传感器参数"四列（用于 GUI/Excel 展示）。"""
    def _row(info):
        sw = float((info or {}).get("swath_km") or 0.0)
        if info is None:
            return pd.Series({"fov_used_deg": None, "min_elev_used_deg": None,
                              "swath_used_km": 0.0, "sensor_source": "默认"})
        return pd.Series({
            "fov_used_deg": None if sw > 0 else info.get("fov_half_angle_deg"),
            "min_elev_used_deg": info.get("min_elevation_deg"),
            "swath_used_km": sw,
            "sensor_source": "逐星覆盖" if info.get("source") == "override" else "默认",
        })
    cols = df_summary["satellite"].map(sat_params_by_name.get).apply(_row)
    return pd.concat([df_summary, cols], axis=1)


def orbit_tag(sat):
    """按 TLE 平运动算轨道周期，粗分轨道类型（Q152）：
    GEO/静止（周期 ≥ 1300 分钟，约一天一圈）、高轨（≥ 700 分钟）、低轨、未知。
    用于"幅宽只适用于低轨条带成像"的提示与自动回退。
    """
    try:
        no_kozai = float(sat.model.no_kozai)   # rad/min
        period_min = 2 * np.pi / no_kozai
    except Exception:
        return "未知"
    if period_min >= 1300:
        return "GEO/静止"
    if period_min >= 700:
        return "高轨"
    return "低轨"


# ==================== 6. 主流程 ====================

def analyze_collection_pipeline(recs, region, cfg, start_str, end_str, progress=None,
                                sensor_params=None, swath_enabled=None, engine="self"):
    """解析卫星 → 逐颗覆盖分析 → 组装结果（GUI 与测试共用的同一入口）。

    2026-08-07 新增：把⑤页"开始覆盖分析"的运行逻辑抽成纯函数，
    界面按钮和回归测试调用的是同一个函数，避免"测试通过但界面报错"。
    参数：
      recs        已解析的卫星记录（satellite_loader 输出）
      region      目标区域 dict（circle / polygon）
      cfg         覆盖参数（含 region_name / step_minutes 等）
      start_str / end_str  时段（UTC，"%Y-%m-%d %H:%M:%S"）
      progress    可选回调 progress(已完成比例 0~1, 说明文字)
    返回 cov_result 字典（dfs / windows_by_sat / sats / sat_params / t0 / t1），无任何窗口时返回 None。
    """
    import satellite_loader as sl  # 延迟导入，避免模块顶层互相依赖
    ts = sl.load.timescale(builtin=True)
    t, dt_list = make_time_array(ts, start_str, end_str, int(cfg.get("step_minutes", 1)))
    t1_true = dt_list[-1] + timedelta(seconds=int(cfg.get("step_minutes", 1)) * 60)
    sats = sl.records_to_satellites(recs)
    if sensor_params is None:
        sensor_params = sl.load_sensor_params()   # 文件不存在则空表 = 全用全局默认
    if swath_enabled is None:
        swath_enabled = sl.load_sensor_swath_enabled()
    if engine == "atk":
        import atk_engine as ae
        cfg["engine"] = "atk"
        _atk_cfg = cfg.get("atk") or {}
        return ae.run_atk_windows(
            recs, region, cfg, start_str, end_str, progress=progress,
            sensor_overrides=sensor_params, swath_enabled=bool(swath_enabled),
            sensor_type=cfg.get("sensor_type", "SimpleCone"),
            sensor_params=cfg.get("sensor_params"),
            atk_root=cfg.get("atk_root") or _atk_cfg.get("root"))
    all_windows = []
    windows_by_sat = []
    sat_params = []
    sensor_warnings = []
    for i, sat in enumerate(sats):
        if progress:
            progress((i + 1) / len(sats), f"分析 {sat.name}（{i + 1}/{len(sats)}）")
        sat_cfg, info = resolve_sat_cfg(sat.norad_id, sensor_params, cfg,
                                        swath_enabled=swath_enabled)
        # 复杂传感器/姿态/指向/约束：自研复杂引擎（P0-P3，docs/自研复杂传感器理论报告.md）
        complex_cfg = {}
        if engine == "self":
            import sensor_geometry as sg
            complex_cfg = sg.merge_sat_complex_cfg(sat.norad_id, cfg)
            if complex_cfg:
                sat_cfg.update(complex_cfg)
        tag = orbit_tag(sat)
        if sat_cfg.get("swath_km") and tag in ("GEO/静止", "高轨"):
            fov_back = sat_cfg.get("fov_half_angle_deg")
            sat_cfg.pop("swath_km", None)
            info["swath_km"] = 0.0
            info["fov_half_angle_deg"] = fov_back
            sensor_warnings.append(
                f"{sat.name}（NORAD {sat.norad_id}，{tag}）：填了幅宽但高轨/静止卫星"
                f"没有条带成像概念，已改按视场半角 {fov_back}° 计算")
        sat_params.append({"name": sat.name, **info})
        _cov_fn = None
        if engine == "self" and complex_cfg:
            import sensor_geometry as sg
            _cov_fn = sg.compute_covered_series_complex
        windows = analyze_one_satellite(sat, t, dt_list, region, sat_cfg,
                                        period_end=t1_true, covered_fn=_cov_fn)
        windows_by_sat.append(windows)
        all_windows.extend((sat.name, w[0], w[1], w[2]) for w in windows)
    if not all_windows:
        return None
    dfs = build_outputs(all_windows, cfg, dt_list[0], t1_true, len(sats),
                        sat_params=sat_params)
    return {
        "dfs": dfs, "cfg": cfg, "n_sats": len(sats), "n_win": len(all_windows),
        "windows_by_sat": windows_by_sat, "sats": sats,
        "sat_params": sat_params,
        "sensor_warnings": sensor_warnings,
        "t0": dt_list[0], "t1": t1_true,
    }


def main():
    # 命令行参数优先，缺省用 CONFIG 里的默认值
    parser = argparse.ArgumentParser(description="卫星覆盖性分析")
    parser.add_argument("--config", help="配置文件路径（TOML，模板见 config.example.toml）")
    parser.add_argument("--mode", choices=["polygon", "circle"], help="目标区域模式")
    parser.add_argument("--lat", type=float, help="circle 模式：圆心纬度")
    parser.add_argument("--lon", type=float, help="circle 模式：圆心经度")
    parser.add_argument("--radius", type=float, help="circle 模式：半径（km）")
    parser.add_argument("--location", help="常用地点名称（内置+用户），覆盖 --lat/--lon/--radius")
    parser.add_argument("--geojson", help="polygon 模式：地区 GeoJSON 路径")
    parser.add_argument("--start", help="分析开始时间 UTC（YYYY-MM-DD HH:MM:SS）")
    parser.add_argument("--end", help="分析结束时间 UTC")
    parser.add_argument("--fov", type=float, help="视场半角（度）")
    parser.add_argument("--min-elevation", type=float, help="最小仰角（度）")
    parser.add_argument("--sensor-params", help="逐星传感器参数表（TOML，模板见 data/sensor_params.toml）；"
                                                "不指定则自动读 data/sensor_params.toml（存在才生效）")
    args = parser.parse_args()

    cfg = dict(CONFIG)
    # 配置文件：显式 --config 优先；没指定但存在 config.toml 时自动使用
    # （这样 VSCode 点"运行 Python 文件"按钮也会用 config.toml）
    config_path = args.config
    if config_path is None and os.path.exists("config.toml"):
        config_path = "config.toml"
        print("未指定 --config，检测到 config.toml，自动使用。")
    if config_path:
        apply_config_file(cfg, config_path)
    # 优先级：命令行参数 > 配置文件（config.toml 自动加载）> CONFIG 默认
    if args.mode:
        cfg["region_mode"] = args.mode
    if args.lat is not None:
        cfg["target"] = dict(cfg["target"], lat=args.lat)
    if args.lon is not None:
        cfg["target"] = dict(cfg["target"], lon=args.lon)
    if args.radius is not None:
        cfg["target"] = dict(cfg["target"], radius_km=args.radius)
    if args.geojson:
        cfg["region_geojson"] = args.geojson
    if args.start:
        cfg["start_utc"] = args.start
    if args.end:
        cfg["end_utc"] = args.end
    if args.fov is not None:
        cfg["fov_half_angle_deg"] = args.fov
    if args.min_elevation is not None:
        cfg["min_elevation_deg"] = args.min_elevation
    # 逐星传感器参数（只按 NORAD 匹配）：显式 --sensor-params 优先，否则自动读默认文件
    import satellite_loader as sl
    _sp_path = args.sensor_params if args.sensor_params else None
    if args.sensor_params:
        sensor_overrides = sl.load_sensor_params(args.sensor_params)
        print(f"已加载逐星传感器参数表：{args.sensor_params}（{len(sensor_overrides)} 条覆盖）")
    else:
        sensor_overrides = sl.load_sensor_params()
        if sensor_overrides:
            print(f"已加载逐星传感器参数表：data/sensor_params.toml"
                  f"（{len(sensor_overrides)} 条覆盖）")
    swath_enabled = sl.load_sensor_swath_enabled(_sp_path)
    if swath_enabled:
        print("幅宽（逐星）生效开关：已开启（只对参数表中填了幅宽的星生效，"
              "且高轨/静止星会自动回退视场半角）。")
    if args.location:
        loc = next((x for x in sl.load_locations() if x["name"] == args.location), None)
        if loc is None:
            print(f"未找到常用地点：{args.location}（见 data/locations.toml）")
            return
        cfg["target"] = dict(cfg["target"], lat=float(loc["lat"]), lon=float(loc["lon"]),
                             radius_km=float(loc.get("radius_km", 5.0)))
        print(f"使用常用地点：{loc['name']} ({loc['lat']}, {loc['lon']}) 半径{loc.get('radius_km', 5.0)}km")

    # builtin=True：不联网下载时间数据，保证离线可运行
    ts = load.timescale(builtin=True)

    print("=" * 64)
    print("卫星覆盖性分析")
    print("=" * 64)

    # 1) 目标区域 -> 判定对象（多边形=网格点 / 圆=点+半径）
    if cfg["region_mode"] == "circle":
        tgt = cfg["target"]
        region = {"mode": "circle", "lat": tgt["lat"], "lon": tgt["lon"],
                  "radius_km": tgt["radius_km"]}
        cfg["region_name"] = f"圆心({tgt['lat']:.4f}, {tgt['lon']:.4f}) " \
                             f"半径{tgt['radius_km']}km"
        print(f"目标区域：{cfg['region_name']}（circle 模式：点+半径）")
    else:
        polygon = load_region_polygon(cfg["region_geojson"])
        grid_lats, grid_lons = make_grid_points(polygon, cfg["grid_spacing_deg"])
        region = {"mode": "polygon", "lats": grid_lats, "lons": grid_lons}
        print(f"地区：{cfg['region_name']}（{cfg['region_geojson']}）")
        print(f"网格代表点：{len(grid_lats)} 个（间距 {cfg['grid_spacing_deg']}°）")

    # 2) TLE -> 卫星
    sats = load_satellites(cfg["tle_file"], cfg["satellite_keywords"], ts)
    print(f"按关键字 {cfg['satellite_keywords']} 筛选到 {len(sats)} 颗卫星")
    if not sats:
        print("没有找到符合条件的卫星，请检查 TLE 文件与关键字。")
        return

    # 3) 时间轴
    t, dt_list = make_time_array(ts, cfg["start_utc"], cfg["end_utc"], cfg["step_minutes"])
    t1_true = dt_list[-1] + timedelta(seconds=int(cfg["step_minutes"]) * 60)
    print(f"时间轴：{dt_list[0]} ~ {dt_list[-1]}（UTC），步长 {cfg['step_minutes']} 分钟，"
          f"共 {len(dt_list)} 步")

    # 4) 逐颗卫星分析（逐星传感器参数仅按 NORAD 匹配，未设置用全局默认）
    all_windows = []
    sat_params = []
    sensor_warnings = []
    for sat in sats:
        norad = int(sat.model.satnum) if getattr(sat, "model", None) else None
        sat_cfg, info = resolve_sat_cfg(norad, sensor_overrides, cfg,
                                        swath_enabled=swath_enabled)
        tag = orbit_tag(sat)
        if sat_cfg.get("swath_km") and tag in ("GEO/静止", "高轨"):
            fov_back = sat_cfg.get("fov_half_angle_deg")
            sat_cfg.pop("swath_km", None)
            info["swath_km"] = 0.0
            info["fov_half_angle_deg"] = fov_back
            sensor_warnings.append(
                f"  {sat.name}（NORAD {norad}，{tag}）：幅宽不适用，"
                f"已按视场半角 {fov_back}° 计算")
        sat_params.append({"name": sat.name, **info})
        windows = analyze_one_satellite(sat, t, dt_list, region, sat_cfg,
                                        period_end=t1_true)
        all_windows.extend((sat.name, w[0], w[1], w[2]) for w in windows)
        if windows:
            print(f"  {sat.name}: {len(windows)} 个覆盖窗口")
    for w in sensor_warnings:
        print(w)

    # 5) 输出（CSV 四件套 + 文字摘要 + 图 + 元信息）
    build_outputs(all_windows, cfg, dt_list[0], t1_true, len(sats),
                  sat_params=sat_params)


if __name__ == "__main__":
    main()



