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
       覆盖圆由传感器视场半角（默认 30°）和轨道高度决定，高度越高圆越大。
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
    "save_gantt": True,                           # 甘特图默认生成；改 False 可关闭
    "refine_step_seconds": 10,                    # 两段式：窗口边界精化步长（秒），0=不精化
    "refine_margin_minutes": 3,                   # 精化时向窗口两侧多扫的分钟数
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

    物理图景：卫星不是只能拍正下方，传感器可以"歪着头"看，最多歪 fov_half_deg
    （默认 30°，即视场宽 60°）。以星下点为圆心、看得见的边界为圆，就是覆盖圆。

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


def compute_covered_series(sat, t, dt_list, grid_lats, grid_lons, cfg):
    """
    计算该卫星在给定时间点列表上对区域的覆盖判定。
    返回 (covered, ratio)：
      covered : 每个时刻是否覆盖（任一网格点满足条件），形状 (T,)
      ratio   : 每个时刻被覆盖的网格点比例，形状 (T,)
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
    # 上面这行就是整个程序的核心判定：一个网格点必须【同时】满足
    #   (a) 在视场覆盖圆内（星下点为圆心，半径随高度变化）
    #   (b) 该点看卫星的仰角 >= 最小仰角（默认 10°）
    return covered_grid.any(axis=1), covered_grid.mean(axis=1)


def analyze_one_satellite(sat, t, dt_list, grid_lats, grid_lons, cfg):
    """
    分析单颗卫星的覆盖情况，返回窗口列表：
    [(开始datetime, 结束datetime, 窗口内最大覆盖比例), ...]
    两段式：先用 1 分钟粗筛出窗口，再对每个窗口边界附近做细网格精化
    （默认 10 秒），让窗口起止精确到秒级，而不用全程细算。
    """
    # ---- 第一步：1 分钟粗筛 ----
    covered, ratio = compute_covered_series(sat, t, dt_list, grid_lats, grid_lons, cfg)
    windows = []
    in_window = False
    for i, c in enumerate(covered):
        if c and not in_window:
            start = dt_list[i]
            in_window = True
        elif not c and in_window:
            windows.append([start, dt_list[i - 1], float(ratio[i - 1])])
            in_window = False
    if in_window:
        windows.append([start, dt_list[-1], float(ratio[-1])])

    # ---- 第二步：窗口边界精化（两段式） ----
    refine = cfg.get("refine_step_seconds", 0)
    margin = cfg.get("refine_margin_minutes", 3)
    if refine and windows:
        refined = []
        for start, end, r in windows:
            dt_f = make_dt_series(start - timedelta(minutes=margin),
                                  end + timedelta(minutes=margin), refine)
            t_f = t.ts.from_datetimes(dt_f)
            cov_f, _ = compute_covered_series(sat, t_f, dt_f, grid_lats, grid_lons, cfg)
            idx = np.where(cov_f)[0]
            if len(idx):
                refined.append([dt_f[idx[0]], dt_f[idx[-1]], r])
            else:
                refined.append([start, end, r])
        # 两个粗窗口相距很近时精化区间会重叠，合并一下
        refined.sort(key=lambda w: w[0])
        merged = []
        for w in refined:
            if merged and w[0] <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], w[1])
                merged[-1][2] = max(merged[-1][2], w[2])
            else:
                merged.append(w)
        windows = [tuple(w) for w in merged]
        # 精化会向窗口两侧多扫 margin 分钟，可能越过分析时段边界，裁回分析时段内
        t_lo, t_hi = dt_list[0], dt_list[-1]
        windows = [(max(s, t_lo), min(e, t_hi), r) for s, e, r in windows]
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
    n = int(round((t1 - t0).total_seconds() / step_s)) + 1
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


def build_outputs(all_windows, cfg, t0, t1, n_sats_total):
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
        "region_geojson": cfg["region_geojson"],
        "tle_file": cfg["tle_file"],
        "start_utc": t0.strftime("%Y-%m-%d %H:%M:%S"),
        "end_utc": t1.strftime("%Y-%m-%d %H:%M:%S"),
        "step_minutes": cfg["step_minutes"],
        "refine_step_seconds": cfg.get("refine_step_seconds", 0),
        "fov_half_angle_deg": cfg["fov_half_angle_deg"],
        "min_elevation_deg": cfg["min_elevation_deg"],
        "grid_spacing_deg": cfg["grid_spacing_deg"],
        "n_satellites_total": n_sats_total,
        "n_satellites_covered": int(len(df_summary)),
    }
    with open("output/analysis_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open("output/report_summary.txt", "w", encoding="utf-8") as f:
        f.write(build_summary_text(df_state, df_summary, cfg, meta))
    print("文字摘要已保存：output/report_summary.txt")

    # 可用卫星数曲线（主图）
    plot_availability(df_state, cfg, "output/availability_curve.png")
    print("可用卫星数曲线已保存：output/availability_curve.png")

    # 甘特图（默认生成，可关）
    if cfg.get("save_gantt", True):
        plot_gantt(all_windows, df_summary, cfg)
        print("甘特图已保存：output/gantt.png")

    # 控制台输出：按总覆盖时长排序，方便看"哪些卫星可用"
    print("\n覆盖时长排名（前 10）：")
    print(df_summary.head(10).to_string(index=False))
    print("\n整体状态表预览（前 8 行，完整见 coverage_state.csv）：")
    print(df_state.head(8).to_string(index=False))
    return df_windows, df_summary, df_state, df_gaps


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

    # 5) 输出（CSV 四件套 + 文字摘要 + 图 + 元信息）
    build_outputs(all_windows, cfg, dt_list[0], dt_list[-1], len(sats))


if __name__ == "__main__":
    main()



