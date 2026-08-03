# -*- coding: utf-8 -*-
"""
覆盖性分析 MVP（第一版）
========================
功能：给定 地区边界(GeoJSON) + 卫星TLE + 时间段，
     计算每颗卫星对该地区的覆盖时间窗口，输出 CSV 和甘特图。

白话流程（对应下面的每个函数）：
1. 读地区边界 -> 在地区内撒"网格代表点"（像在地图上画格子，交点就是代表点）
2. 读 TLE -> 筛选出关心的卫星（按名字里的关键字，如 YAOGAN=遥感）
3. 用 skyfield（内部就是 SGP4 算法）外推每颗卫星在时间段内每分钟的位置 -> 得到星下点
4. 判断每个时刻：星下点到任一代表点的距离是否在"覆盖圆"内，且仰角是否达标
5. 把连续覆盖的分钟合并成窗口 -> 输出表格和甘特图

依赖：skyfield, sgp4, numpy, pandas, matplotlib, shapely
用法：python src/coverage_analysis.py
"""

import json
from datetime import datetime, timedelta, timezone

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
    "tle_file": "data/raw/active_tle.txt",       # TLE 数据文件（CelesTrak 下载）
    # 第一版只分析遥感卫星（YAOGAN）对地面的覆盖。
    # 天链（TIANLIAN）是地球同步中继卫星，不直接成像，通信链路分析放到后续版本。
    "satellite_keywords": ["YAOGAN"],
    "start_utc": "2026-08-03 00:00:00",          # 分析开始时间（UTC）
    "end_utc": "2026-08-04 00:00:00",            # 分析结束时间（UTC）
    "step_minutes": 1,                            # 粗筛时间步长（分钟）
    "fov_half_angle_deg": 30.0,                   # 视场半角（度）：传感器最大能"歪着头"看多少
    "min_elevation_deg": 10.0,                    # 最小仰角（度）：太低受大气影响，不算有效覆盖
    "grid_spacing_deg": 0.2,                      # 网格间距（度，约 22 km）
    "max_gantt_rows": 15,                         # 甘特图最多画几颗星（按覆盖时长排名）
}

R_EARTH_KM = 6378.137  # 地球平均半径（km），球面近似


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
    """生成 [开始, 结束] 的等间隔时间点（datetime 列表 + skyfield 时间对象）"""
    t0 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_list = []
    t = t0
    while t <= t1:
        dt_list.append(t)
        t += timedelta(minutes=step_minutes)
    return ts.from_datetimes(dt_list), dt_list


# ==================== 4. 覆盖判定（核心几何） ====================

def footprint_max_angle_deg(altitude_km, fov_half_deg):
    """
    由轨道高度和视场半角计算"覆盖圆"的最大地心角（度）。
    地心角 = 星下点与目标点在地心处张开的夹角，0°=正下方。
    公式推导（球面近似）：
      视场半角 η、轨道半径 r=R+h、地球半径 R，
      覆盖圆边缘处目标点看卫星的仰角 ε 满足 sin(η) = (R/r)·cos(ε)
      地心角 λ = 90° - ε - η
      另外 λ 不可能超过"地平线角" acos(R/r)
    """
    r = R_EARTH_KM + altitude_km
    eta = np.radians(fov_half_deg)
    sin_eta_ratio = (r / R_EARTH_KM) * np.sin(eta)
    horizon = np.arccos(R_EARTH_KM / r)              # 地平线对应的最大地心角
    eps = np.arccos(np.clip(sin_eta_ratio, 0.0, 1.0))  # 覆盖圆边缘处的仰角
    lam = np.pi / 2 - eps - eta
    return np.degrees(np.minimum(lam, horizon))


def central_angle_deg(lat1, lon1, lat2, lon2):
    """两组经纬度之间的大圆地心角（度），向量化 haversine 公式"""
    lat1r, lat2r = np.radians(lat1), np.radians(lat2)
    dlat = lat2r - lat1r
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1r) * np.cos(lat2r) * np.sin(dlon / 2) ** 2
    return np.degrees(2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def elevation_at_deg(lam_deg, altitude_km):
    """由地心角 λ 和卫星高度 h，计算目标点看卫星的仰角（度）"""
    lam = np.radians(lam_deg)
    r = R_EARTH_KM + altitude_km
    return np.degrees(np.arctan2(np.cos(lam) - R_EARTH_KM / r, np.sin(lam)))


def analyze_one_satellite(sat, t, dt_list, grid_lats, grid_lons, cfg):
    """
    分析单颗卫星的覆盖情况，返回窗口列表：
    [(开始datetime, 结束datetime, 窗口内最大覆盖比例), ...]
    """
    # 用 SGP4 外推整段时间，得到每个时刻的星下点（纬度、经度、高度）
    geocentric = sat.at(t)
    sub = geocentric.subpoint()
    lat = sub.latitude.degrees          # 形状 (T,)
    lon = sub.longitude.degrees
    alt = sub.elevation.km

    # 每个时刻，星下点到所有网格点的地心角：形状 (T, N)
    lam = central_angle_deg(lat[:, None], lon[:, None],
                            grid_lats[None, :], grid_lons[None, :])

    # 每个网格点处的仰角：形状 (T, N)
    elev = elevation_at_deg(lam, alt[:, None])

    # 每个时刻的覆盖圆最大地心角（随高度变化）：形状 (T,)
    lam_max = footprint_max_angle_deg(alt, cfg["fov_half_angle_deg"])

    # 覆盖判定：点在覆盖圆内 且 仰角达标 -> 形状 (T, N)
    covered_grid = (lam <= lam_max[:, None]) & (elev >= cfg["min_elevation_deg"])
    covered = covered_grid.any(axis=1)          # 该时刻是否有任一代表点被覆盖
    ratio = covered_grid.mean(axis=1)           # 该时刻被覆盖的代表点比例

    # 把连续覆盖的分钟合并成窗口
    windows = []
    in_window = False
    for i, c in enumerate(covered):
        if c and not in_window:
            start = dt_list[i]
            in_window = True
        elif not c and in_window:
            windows.append((start, dt_list[i - 1], float(ratio[i - 1])))
            in_window = False
    if in_window:
        windows.append((start, dt_list[-1], float(ratio[-1])))
    return windows


# ==================== 5. 输出：CSV + 甘特图 ====================

def build_outputs(all_windows, cfg):
    """把所有卫星的窗口整理成两张表 + 甘特图"""
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
            "duration_s": int((end - start).total_seconds()) + cfg["step_minutes"] * 60,
            "max_covered_ratio": round(ratio, 4),
        })
    df_windows = pd.DataFrame(rows)

    # 汇总表（每颗卫星）
    summaries = []
    for name in sorted({w[0] for w in all_windows}):
        sub = [w for w in all_windows if w[0] == name]
        sub = sorted(sub, key=lambda w: w[1])
        durations = [(w[2] - w[1]).total_seconds() + cfg["step_minutes"] * 60 for w in sub]
        gaps = [(sub[i + 1][1] - sub[i][2]).total_seconds() for i in range(len(sub) - 1)]
        summaries.append({
            "satellite": name,
            "n_windows": len(sub),
            "total_duration_s": int(sum(durations)),
            "longest_window_s": int(max(durations)),
            "max_gap_s": int(max(gaps)) if gaps else None,
            "first_cover_utc": sub[0][1].strftime("%Y-%m-%d %H:%M:%S"),
            "last_cover_utc": sub[-1][2].strftime("%Y-%m-%d %H:%M:%S"),
        })
    df_summary = pd.DataFrame(summaries).sort_values("total_duration_s", ascending=False)

    df_windows.to_csv("output/coverage_windows.csv", index=False, encoding="utf-8-sig")
    df_summary.to_csv("output/coverage_summary.csv", index=False, encoding="utf-8-sig")
    print(f"窗口明细已保存：output/coverage_windows.csv（{len(df_windows)} 条）")
    print(f"汇总表已保存：output/coverage_summary.csv（{len(df_summary)} 颗卫星有覆盖）")

    # 甘特图：只画覆盖时长最长的前 N 颗，避免行数过多看不清
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
    print("甘特图已保存：output/gantt.png")

    # 控制台输出：按总覆盖时长排序，方便看"哪些卫星可用"
    print("\n覆盖时长排名（前 10）：")
    print(df_summary.head(10).to_string(index=False))
    return df_windows, df_summary


# ==================== 6. 主流程 ====================

def main():
    cfg = CONFIG
    # builtin=True：不联网下载时间数据，保证离线可运行
    ts = load.timescale(builtin=True)

    print("=" * 64)
    print("卫星覆盖性分析 MVP")
    print("=" * 64)

    # 1) 地区 -> 网格代表点
    polygon = load_region_polygon(cfg["region_geojson"])
    grid_lats, grid_lons = make_grid_points(polygon, cfg["grid_spacing_deg"])
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
    print(f"时间轴：{dt_list[0]} ~ {dt_list[-1]}（UTC），步长 {cfg['step_minutes']} 分钟，"
          f"共 {len(dt_list)} 步")

    # 4) 逐颗卫星分析
    all_windows = []
    for sat in sats:
        windows = analyze_one_satellite(sat, t, dt_list, grid_lats, grid_lons, cfg)
        all_windows.extend((sat.name, w[0], w[1], w[2]) for w in windows)
        if windows:
            print(f"  {sat.name}: {len(windows)} 个覆盖窗口")

    # 5) 输出
    build_outputs(all_windows, cfg)


if __name__ == "__main__":
    main()



