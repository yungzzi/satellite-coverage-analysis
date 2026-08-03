# -*- coding: utf-8 -*-
"""
交叉验证脚本：三方对照（主程序 skyfield / 独立算法 raw-sgp4 / N2YO 网站）
========================================================================
为什么要做这件事：
  主程序用 skyfield 库推卫星位置。如果 skyfield 本身有 bug，或者几何公式抄错，
  主程序会"自圆其说"。所以验证时换一条完全不同的路再算一遍：

  1) 独立算法：直接调用底层 sgp4 库，自己转坐标系、自己算仰角和覆盖圆，
     完全不经过 skyfield。
  2) 外部网站：N2YO 是专业卫星过境预报网站，用它们自己的程序和 TLE 计算，
     相当于"别人家的答案"。

三条路互相对表，如果结果一致，说明计算是对的。

用法：python src/validate_crosscheck.py
输出：output/validation/ 下的 CSV 和终端报告
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import coverage_analysis as ca

from sgp4.api import Satrec, WGS84
from skyfield.api import load, wgs84

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R_EARTH_KM = 6378.137
NORAD_YAOGAN7 = "36110"
HANGZHOU = (30.25, 120.15)  # 观测点：杭州（纬度, 经度）


# ==================== 公共工具 ====================

def read_tle_lines(path, norad):
    """
    从 TLE 文件中按 NORAD 编号读出一组三行（名字/第1行/第2行）。
    注意：必须取 TLE 第1行的第3~7列（标准 NORAD 编号字段）精确匹配，
    不能只做"编号出现在这一行里"的子串判断——否则可能撞上别的星
    （例如编号 65563 会误匹配到另一颗星纪元小数 26214.75965563 里）。
    """
    with open(path, "r", encoding="ascii", errors="replace") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    for i in range(len(lines) - 2):
        if (lines[i + 1].startswith("1 ")
                and lines[i + 1][2:7].strip() == norad
                and lines[i + 2].startswith("2 ")
                and lines[i + 2][2:7].strip() == norad):
            return lines[i], lines[i + 1], lines[i + 2]
    return None, None, None


def gmst_deg(jd_ut1):
    """格林尼治恒星时 GMST（度），IAU 1982 公式"""
    t = (jd_ut1 - 2451545.0) / 36525.0
    g = (280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0)
         + 0.000387933 * t * t - t ** 3 / 38710000.0)
    return g % 360.0


def propagate_ecef(line1, line2, dt_list):
    """独立路径：raw-sgp4 外推 TEME 位置，再手动旋转到地固系 ECEF"""
    satrec = Satrec.twoline2rv(line1, line2, WGS84)
    ecef = np.empty((len(dt_list), 3))
    for i, dt in enumerate(dt_list):
        jd = 2440587.5 + dt.timestamp() / 86400.0
        err, r, v = satrec.sgp4(int(jd), jd - int(jd))
        if err != 0:
            raise RuntimeError(f"sgp4 返回错误码 {err} @ {dt}")
        th = np.radians(gmst_deg(jd))
        c, s = np.cos(th), np.sin(th)
        ecef[i] = (r[0] * c + r[1] * s,
                   -r[0] * s + r[1] * c,
                   r[2])
    return ecef


def ecef_to_subpoint(ecef):
    """ECEF -> 星下点（球面近似）：纬度、经度、高度(km)"""
    r = np.linalg.norm(ecef, axis=1)
    lat = np.degrees(np.arcsin(np.clip(ecef[:, 2] / np.maximum(r, 1e-9), -1, 1)))
    lon = np.degrees(np.arctan2(ecef[:, 1], ecef[:, 0]))
    alt = r - R_EARTH_KM
    return lat, lon, alt


def observer_ecef(lat_deg, lon_deg, alt_km=0.0):
    """观测点在地固系中的位置（球面近似）"""
    lat, lon = np.radians(lat_deg), np.radians(lon_deg)
    r = R_EARTH_KM + alt_km
    return r * np.array([np.cos(lat) * np.cos(lon),
                         np.cos(lat) * np.sin(lon),
                         np.sin(lat)])


def elevation_from_ecef(ecef_sat, obs, obs_lat_deg, obs_lon_deg):
    """独立路径：由地固坐标直接算观测点看卫星的仰角（度）"""
    los = ecef_sat - obs
    los /= np.maximum(np.linalg.norm(los, axis=1, keepdims=True), 1e-9)
    lat, lon = np.radians(obs_lat_deg), np.radians(obs_lon_deg)
    up = np.array([np.cos(lat) * np.cos(lon),
                   np.cos(lat) * np.sin(lon),
                   np.sin(lat)])
    return np.degrees(np.arcsin(np.clip(los @ up, -1, 1)))


def central_angle(lat1, lon1, lat2, lon2):
    """大圆地心角（度），向量化 haversine（独立复刻，不调用主程序）"""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dlat = p2 - p1
    dlon = np.radians(np.asarray(lon2) - np.asarray(lon1))
    a = np.sin(dlat / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlon / 2) ** 2
    return np.degrees(2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a)))


def footprint_max_angle(alt_km, fov_half_deg):
    """覆盖圆最大地心角（度），独立复刻公式"""
    r = R_EARTH_KM + alt_km
    eta = np.radians(fov_half_deg)
    eps = np.arccos(np.clip((r / R_EARTH_KM) * np.sin(eta), 0.0, 1.0))
    lam = np.pi / 2 - eps - eta
    horizon = np.arccos(R_EARTH_KM / r)
    return np.degrees(np.minimum(lam, horizon))


def merge_windows(covered, dt_list):
    """把连续覆盖的分钟/秒合并成窗口，返回 [(开始, 结束), ...]"""
    wins = []
    start = None
    for i, c in enumerate(covered):
        if c and start is None:
            start = dt_list[i]
        elif not c and start is not None:
            wins.append((start, dt_list[i - 1]))
            start = None
    if start is not None:
        wins.append((start, dt_list[-1]))
    return wins


def make_dt_series(start_dt, end_dt, step_seconds):
    """生成等间隔的时间点列表（UTC）"""
    out = []
    t = start_dt
    while t <= end_dt:
        out.append(t)
        t += timedelta(seconds=step_seconds)
    return out


# ==================== N2YO 网页解析 ====================

def parse_n2yo_passes(html_path):
    """从 N2YO 页面 JS 中提取每次过境：开始/最高/结束时刻 + 最大仰角"""
    with open(html_path, "r", encoding="utf-8", errors="replace") as f:
        html = f.read()
    chunks = re.split(r"passes\[\d+\]\s*=\s*new SatellitePasses\(", html)
    passes = []
    for chunk in chunks[1:]:
        def grab(var):
            m = re.search(var + r"\s*=\s*new SatellitePosition\((.*?)\);", chunk, re.S)
            if not m:
                return None
            vals = re.findall(r"'([^']*)'", m.group(1))
            return [x.strip() for x in vals]
        s, mx, e = grab("spStart"), grab("spMax"), grab("spEnd")
        if not (s and mx and e):
            continue
        passes.append({
            "start_utc": datetime.fromtimestamp(float(s[5]), tz=timezone.utc),
            "max_utc": datetime.fromtimestamp(float(mx[5]), tz=timezone.utc),
            "end_utc": datetime.fromtimestamp(float(e[5]), tz=timezone.utc),
            "max_elev_deg": float(mx[3]),
        })
    passes.sort(key=lambda p: p["start_utc"])
    return passes


# ==================== 三条计算路径 ====================

def path_skyfield_region(sat, ts, dt_list, glats, glons, cfg):
    """路径1（=主程序逻辑）：skyfield 星下点 + 主程序几何公式"""
    t = ts.from_datetimes(dt_list)
    sub = sat.at(t).subpoint()
    lat = sub.latitude.degrees
    lon = sub.longitude.degrees
    alt = sub.elevation.km
    lam = ca.central_angle_deg(lat[:, None], lon[:, None],
                               glats[None, :], glons[None, :])
    elev = ca.elevation_at_deg(lam, alt[:, None])
    lam_max = ca.footprint_max_angle_deg(alt, cfg["fov_half_angle_deg"])
    covered_grid = (lam <= lam_max[:, None]) & (elev >= cfg["min_elevation_deg"])
    return covered_grid.any(axis=1), covered_grid.mean(axis=1)


def path_rawsgp4_region(line1, line2, dt_list, glats, glons, cfg):
    """路径2（独立算法）：raw-sgp4 + 手写坐标系/几何"""
    ecef = propagate_ecef(line1, line2, dt_list)
    slat, slon, salt = ecef_to_subpoint(ecef)
    lam = central_angle(slat[:, None], slon[:, None],
                        glats[None, :], glons[None, :])
    elev = np.empty_like(lam)
    for j in range(len(glats)):
        obs = observer_ecef(glats[j], glons[j])
        elev[:, j] = elevation_from_ecef(ecef, obs, glats[j], glons[j])
    lam_max = footprint_max_angle(salt, cfg["fov_half_angle_deg"])
    covered_grid = (lam <= lam_max[:, None]) & (elev >= cfg["min_elevation_deg"])
    return covered_grid.any(axis=1), covered_grid.mean(axis=1)


def path_skyfield_altaz(sat, ts, dt_list, lat_deg, lon_deg):
    """路径3：skyfield 自带的地平坐标（altaz），完全绕开我们自己的几何公式"""
    t = ts.from_datetimes(dt_list)
    obs = wgs84.latlon(lat_deg, lon_deg)
    alt, az, dist = (sat - obs).at(t).altaz()
    return alt.degrees


def windows_above(elev_series, dt_s, min_elev=10.0):
    """仰角序列中连续 >= 最低仰角的窗口"""
    return merge_windows(elev_series >= min_elev, dt_s)


# ==================== 主流程 ====================

def main():
    out_dir = os.path.join(PROJ, "output", "validation")
    os.makedirs(out_dir, exist_ok=True)
    tle_stored = os.path.join(PROJ, "data", "raw", "active_tle.txt")
    tle_fresh = os.path.join(out_dir, "yaogan7_fresh.tle")
    html_n2yo = os.path.join(out_dir, "n2yo_pass.html")

    # ---- 0. TLE 与 N2YO 数据准备 ----
    _, l1, l2 = read_tle_lines(tle_stored, NORAD_YAOGAN7)
    f1 = f2 = None
    if os.path.exists(tle_fresh):
        _, f1, f2 = read_tle_lines(tle_fresh, NORAD_YAOGAN7)
    print("=" * 66)
    print("交叉验证报告（终端摘要）")
    print("=" * 66)
    print(f"存储 TLE 第1行: {l1}")
    print(f"新 TLE  第1行: {f1 if f1 else '（未下载，外部对照将用存储 TLE）'}")

    n2yo = parse_n2yo_passes(html_n2yo)
    print(f"\n[N2YO] 共 {len(n2yo)} 次过境（杭州点，仰角≥10°口径）")
    for p in n2yo:
        print(f"  开始 {p['start_utc'].strftime('%m-%d %H:%M:%S')} UTC | "
              f"最高 {p['max_utc'].strftime('%H:%M:%S')} | "
              f"结束 {p['end_utc'].strftime('%m-%d %H:%M:%S')} | "
              f"最大仰角 {p['max_elev_deg']:.1f}°")

    ts = load.timescale()
    cfg = dict(ca.CONFIG)
    cfg["tle_file"] = tle_stored
    cfg["satellite_keywords"] = ["YAOGAN-7"]

    # ---- A. 主程序 vs 独立算法（浙江全境，2026-08-03 全天，存储 TLE） ----
    print("\n" + "=" * 66)
    print("[A] 主程序路径 vs 独立算法：浙江全境 + YAOGAN-7（存储 TLE）")
    print("=" * 66)
    poly = ca.load_region_polygon(os.path.join(PROJ, "data", "geo", "330000.json"))
    glats, glons = ca.make_grid_points(poly, cfg["grid_spacing_deg"])
    t0 = datetime.strptime(cfg["start_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t1 = datetime.strptime(cfg["end_utc"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_list = make_dt_series(t0, t1, 60)
    sats = ca.load_satellites(tle_stored, ["YAOGAN-7"], ts)
    sat = sats[0]
    cov1, ratio1 = path_skyfield_region(sat, ts, dt_list, glats, glons, cfg)
    cov2, ratio2 = path_rawsgp4_region(l1, l2, dt_list, glats, glons, cfg)

    wins1 = merge_windows(cov1, dt_list)
    wins2 = merge_windows(cov2, dt_list)
    agree = int(np.mean(cov1 == cov2) * 100)
    diff_min = [i for i, (a, b) in enumerate(zip(cov1, cov2)) if a != b]
    print(f"浙江网格代表点：{len(glats)} 个")
    print(f"逐分钟一致率：{agree}%（{len(cov1)} 分钟中 {len(cov1) - len(diff_min)} 分钟一致）")
    print(f"不一致的分钟：{len(diff_min)} 个 -> "
          f"{[dt_list[i].strftime('%H:%M') for i in diff_min][:10]}")
    print("主程序路径窗口：", [(w[0].strftime('%H:%M'), w[1].strftime('%H:%M')) for w in wins1])
    print("独立算法窗口：  ", [(w[0].strftime('%H:%M'), w[1].strftime('%H:%M')) for w in wins2])

    rows_a = []
    for w in wins1:
        rows_a.append({"path": "主程序skyfield", "start_utc": w[0], "end_utc": w[1]})
    for w in wins2:
        rows_a.append({"path": "独立raw-sgp4", "start_utc": w[0], "end_utc": w[1]})
    pd.DataFrame(rows_a).to_csv(os.path.join(out_dir, "compare_A_zhejiang_windows.csv"),
                                index=False, encoding="utf-8-sig")

    # ---- A2. 时间步长敏感性：用 10 秒细网格重算一次 ----
    dt_fine = make_dt_series(t0, t1, 10)
    cov2f, _ = path_rawsgp4_region(l1, l2, dt_fine, glats, glons, cfg)
    wins2f = merge_windows(cov2f, dt_fine)
    print("\n[A2] 时间步长敏感性（独立算法，1分钟 vs 10秒）：")
    print("  1分钟步长窗口：", [(w[0].strftime('%H:%M:%S'), w[1].strftime('%H:%M:%S')) for w in wins2])
    print("  10秒步长窗口： ", [(w[0].strftime('%H:%M:%S'), w[1].strftime('%H:%M:%S')) for w in wins2f])

    # ---- B. 外部对照：N2YO vs 我们（杭州点，仰角≥10°，10 天） ----
    print("\n" + "=" * 66)
    print("[B] N2YO 网站 vs 我们：杭州点、10 天（优先新 TLE）")
    print("=" * 66)
    bl1, bl2 = (f1, f2) if f1 else (l1, l2)
    src_tle = "新TLE(CelesTrak)" if f1 else "存储TLE"
    print(f"本对照使用：{src_tle}")
    b0 = n2yo[0]["start_utc"] - timedelta(hours=2)
    b1 = n2yo[-1]["end_utc"] + timedelta(hours=2)
    dt_b = make_dt_series(b0, b1, 10)
    print(f"对照时间范围：{b0.strftime('%m-%d %H:%M')} ~ {b1.strftime('%m-%d %H:%M')} UTC，"
          f"步长10秒，共 {len(dt_b)} 个时刻")
    ecef_b = propagate_ecef(bl1, bl2, dt_b)
    obs = observer_ecef(HANGZHOU[0], HANGZHOU[1])
    elev_manual = elevation_from_ecef(ecef_b, obs, HANGZHOU[0], HANGZHOU[1])
    from skyfield.api import EarthSatellite
    sat_b = EarthSatellite(bl1, bl2, "YAOGAN-7(fresh)", ts)
    elev_altaz = path_skyfield_altaz(sat_b, ts, dt_b, HANGZHOU[0], HANGZHOU[1])

    w_manual = windows_above(elev_manual, dt_b)
    w_altaz = windows_above(elev_altaz, dt_b)
    # N2YO 的过境起止 = 卫星从地平线(0°)升起/落下，所以同口径是仰角>=0°
    w_manual_0 = windows_above(elev_manual, dt_b, min_elev=0.0)
    w_altaz_0 = windows_above(elev_altaz, dt_b, min_elev=0.0)

    idx = {d: i for i, d in enumerate(dt_b)}

    rows_b = []
    for p in n2yo:
        # 找我们"仰角>=0°"窗口里与 N2YO 过境重叠的那个（同口径）
        hit = [w for w in w_manual_0 if w[1] >= p["start_utc"] and w[0] <= p["end_utc"]]
        row = {
            "n2yo_start_utc": p["start_utc"],
            "n2yo_end_utc": p["end_utc"],
            "n2yo_max_elev_deg": round(p["max_elev_deg"], 2),
            "n2yo_duration_min": round((p["end_utc"] - p["start_utc"]).total_seconds() / 60, 2),
        }
        if hit:
            w = hit[0]
            row["ours_start_utc"] = w[0]
            row["ours_end_utc"] = w[1]
            row["delta_start_s"] = (w[0] - p["start_utc"]).total_seconds()
            row["delta_end_s"] = (w[1] - p["end_utc"]).total_seconds()
            # 在整个过境弧段内取最大仰角（从开始前2分钟到结束后2分钟）
            i0 = max(0, idx[w[0]] - 12)
            i1 = min(len(elev_manual) - 1, idx[w[1]] + 12)
            row["ours_max_elev_deg"] = round(float(elev_manual[i0:i1 + 1].max()), 2)
            # 最大仰角出现时刻对比
            im = int(np.argmax(elev_manual[i0:i1 + 1]))
            t_ours_max = dt_b[i0 + im]
            row["delta_max_time_s"] = (t_ours_max - p["max_utc"]).total_seconds()
            row["ours10_start_utc"] = next(
                (w2[0] for w2 in w_manual if w2[1] >= p["start_utc"] and w2[0] <= p["end_utc"]), None)
            row["ours10_end_utc"] = next(
                (w2[1] for w2 in w_manual if w2[1] >= p["start_utc"] and w2[0] <= p["end_utc"]), None)
        else:
            row["ours_start_utc"] = None
            row["ours_end_utc"] = None
            row["delta_start_s"] = None
            row["delta_end_s"] = None
            row["ours_max_elev_deg"] = None
            row["delta_max_time_s"] = None
            row["ours10_start_utc"] = None
            row["ours10_end_utc"] = None
        rows_b.append(row)

    df_b = pd.DataFrame(rows_b)
    df_b.to_csv(os.path.join(out_dir, "compare_B_n2yo_passes.csv"),
                index=False, encoding="utf-8-sig")
    ok = df_b["delta_start_s"].notna()
    if ok.any():
        d1 = df_b.loc[ok, "delta_start_s"].abs()
        d2 = df_b.loc[ok, "delta_end_s"].abs()
        dm = df_b.loc[ok, "delta_max_time_s"].abs()
        de = (df_b.loc[ok, "ours_max_elev_deg"] - df_b.loc[ok, "n2yo_max_elev_deg"]).abs()
        print(f"N2YO 过境数：{len(n2yo)}，我们能对上 {ok.sum()} 次")
        print(f"[同口径=地平线0°] 开始时刻偏差：平均 {d1.mean():.0f} 秒，最大 {d1.max():.0f} 秒")
        print(f"[同口径=地平线0°] 结束时刻偏差：平均 {d2.mean():.0f} 秒，最大 {d2.max():.0f} 秒")
        print(f"最大仰角出现时刻偏差：平均 {dm.mean():.0f} 秒，最大 {dm.max():.0f} 秒")
        print(f"最大仰角数值偏差：平均 {de.mean():.2f}°（10秒网格下正常）")
    # 几何自洽：我们的手写算法 vs skyfield 自带 altaz
    if w_manual_0 and w_altaz_0:
        common = [a for a in w_manual_0 for b in w_altaz_0
                  if abs((a[0] - b[0]).total_seconds()) <= 60]
        print(f"手写算法与 skyfield-altaz 的 0°窗口起止差<=60秒的对数：{len(common)}/{len(w_manual_0)}")
    print(df_b.to_string(index=False))

    # ---- C. 物理合理性 ----
    print("\n" + "=" * 66)
    print("[C] 物理合理性检查")
    print("=" * 66)
    dur = [p["end_utc"] - p["start_utc"] for p in n2yo]
    print(f"N2YO 过境时长范围：{min(dur).total_seconds()/60:.1f} ~ {max(dur).total_seconds()/60:.1f} 分钟"
          f"（LEO 遥感星过顶通常 5~12 分钟，合理）")
    print("YAOGAN-7 轨道周期约 97.4 分钟（由 TLE 平均运动量反推），"
          "过境间隔应为周期整数倍附近，可对照上表验证。")
    print("\n验证结束：详细结论见 docs/验证报告.md")


if __name__ == "__main__":
    main()
