# -*- coding: utf-8 -*-
"""自研复杂传感器/姿态引擎（P0-P3）回归自检。

覆盖点（对应 docs/reference/自研复杂传感器理论报告.md 第 8 章）：
  1) 零回归：复杂引擎 SimpleCone+天底+ε_min 与现有引擎逐窗口一致；
  2) 退化一致：Conical(0,45,0,360) ≡ SimpleCone45；SimpleCone(θ) ⊆ Rect(θ,θ)；
     HalfPower(14.5,1) ≈ SimpleCone(0.72°)；
  3) 姿态族：profile0 vs profile4 窗口不同（端到端差异，§10.33）；
  4) 指向：Fixed 侧摆 / Targeted 凝视 / GrazingAlt 切球 / Spinning 确定性（两次一致）；
  5) 约束：最小仰角约束与现有口径一致；太阳仰角约束可跑；
  6) SAR：近天底被 MaxElev 排除、远缘被 MinElev 排除（点目标几何）。

运行：.venv/Scripts/python src\\validate_self_complex_sensor.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import coverage_analysis as cov
import satellite_loader as sl
import sensor_geometry as sg

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {name}{('  ' + detail) if detail else ''}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name}{('  ' + detail) if detail else ''}")


def test_recs():
    """中国遥感全量测试快照（与 ATK SimpleCone45 基线同一批数据源，窗口更多）。"""
    p = Path(__file__).resolve().parent.parent / "data" / "test_data" / "china_remote_sensing_all.tle"
    return sl.parse_tle_records(p.read_text(encoding="ascii", errors="replace"))


def make_cfg(**kw):
    cfg = {
        "region_name": "杭州5km",
        "region_mode": "circle",
        "tle_file": "自检（吉林一号测试快照）",
        "fov_half_angle_deg": 30.0,
        "min_elevation_deg": 10.0,
        "step_minutes": 1,
        "coarse_step_seconds": 10,
        "refine_step_seconds": 1,
        "refine_margin_minutes": 3,
        "grid_spacing_deg": 0.2,
        "target": {"lat": 30.25, "lon": 120.15, "radius_km": 5.0},
        "save_gantt": False,
        "save_plots": False,
        "max_gantt_rows": 15,
    }
    cfg.update(kw)
    return cfg


def region():
    return {"mode": "circle", "lat": 30.25, "lon": 120.15, "radius_km": 5.0}


def run_windows(recs, cfg, engine="self", complex_cfg=None, limit=6):
    ts = sl.load.timescale(builtin=True)
    t, dt_list = cov.make_time_array(ts, "2026-08-12 00:00:00", "2026-08-12 08:00:00", 1)
    t1_true = dt_list[-1] + timedelta(minutes=1)
    sats = sl.records_to_satellites(recs[:limit])
    out = []
    for sat in sats:
        sat_cfg = dict(cfg)
        if complex_cfg:
            sat_cfg.update(complex_cfg)
        if engine == "self":
            fn = None
        else:
            fn = sg.compute_covered_series_complex
        out.append(cov.analyze_one_satellite(sat, t, dt_list, region(), sat_cfg,
                                             period_end=t1_true, covered_fn=fn))
    return sats, out


def norm_windows(ws):
    return [(w[0].isoformat(), w[1].isoformat(), round(float(w[2]), 6)) for w in ws]


def main():
    recs = test_recs()
    check("测试数据解析（吉林一号 ≥ 20 颗）", len(recs) >= 20, f"n={len(recs)}")

    print("== 1. 零回归：复杂引擎 SimpleCone+天底 vs 现有引擎 ==")
    cfg = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg["sensor_type"] = "SimpleCone"
    cfg["sensor_params"] = [45.0]
    sats, w_base = run_windows(recs, cfg, engine="self", limit=24)
    _, w_cplx = run_windows(recs, cfg, engine="complex", limit=24)
    same = True
    total = 0
    for i, (a, b) in enumerate(zip(w_base, w_cplx)):
        total += len(b)
        if norm_windows(a) != norm_windows(b):
            same = False
            check(f"  星 {sats[i].name} 窗口一致", False,
                  f"base={len(a)} complex={len(b)}")
    check("所有测试星窗口逐条一致（零回归，24 星）", same and total >= 10,
          f"{len(sats)} 颗星，总窗口 {total}")

    print("== 1b. 非委托复杂几何（P0+无偏移）≈ 现有引擎（容差） ==")
    cfg_att = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg_att["sensor_type"] = "SimpleCone"
    cfg_att["sensor_params"] = [45.0]
    cfg_att["attitude"] = {"profile": 0}     # 显式 P0 触发复杂路径（不委托）
    _, w_att = run_windows(recs, cfg_att, engine="complex", limit=6)
    _, w_base6 = run_windows(recs, cfg, engine="self", limit=6)
    close = True
    for a, b in zip(w_base6, w_att):
        if len(a) != len(b):
            close = False
            break
        for wa, wb in zip(a, b):
            if abs((wa[1] - wa[0]).total_seconds() - (wb[1] - wb[0]).total_seconds()) > 30.0:
                close = False
    check("复杂 P0 几何窗口数与时长 vs 现有引擎（≤30s 容差）", close)

    print("== 2. 退化一致 ==")
    cfg2 = make_cfg()
    cfg2["sensor_type"] = "Conical"
    cfg2["sensor_params"] = [0.0, 45.0, 0.0, 360.0]
    _, w_cone = run_windows(recs, cfg2, engine="complex", limit=3)
    cfg3 = make_cfg()
    cfg3["sensor_type"] = "SimpleCone"
    cfg3["sensor_params"] = [45.0]
    _, w_simple = run_windows(recs, cfg3, engine="complex", limit=3)
    same_deg = all(norm_windows(a) == norm_windows(b) for a, b in zip(w_cone, w_simple))
    check("Conical(0,45,0,360) ≡ SimpleCone(45)（3 星逐窗口）", same_deg)

    cfg4 = make_cfg()
    cfg4["sensor_type"] = "HalfPower"
    cfg4["sensor_params"] = [14.5, 1.0]     # η≈0.72°
    _, w_hp = run_windows(recs, cfg4, engine="complex", limit=3)
    cfg5 = make_cfg()
    cfg5["sensor_type"] = "SimpleCone"
    cfg5["sensor_params"] = [0.72]
    _, w_s072 = run_windows(recs, cfg5, engine="complex", limit=3)
    same_hp = all(norm_windows(a) == norm_windows(b) for a, b in zip(w_hp, w_s072))
    check("HalfPower(14.5,1) ≈ SimpleCone(0.72°)（3 星）", same_hp)

    print("== 3. 姿态族：profile0 vs profile4 ==")
    cfg6 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg6["sensor_type"] = "SimpleCone"
    cfg6["sensor_params"] = [45.0]
    cfg6["attitude"] = {"profile": 0}
    _, w_p0 = run_windows(recs, cfg6, engine="complex", limit=3)
    cfg7 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg7["sensor_type"] = "SimpleCone"
    cfg7["sensor_params"] = [45.0]
    cfg7["attitude"] = {"profile": 4}
    _, w_p4 = run_windows(recs, cfg7, engine="complex", limit=3)
    diff = any(norm_windows(a) != norm_windows(b) for a, b in zip(w_p0, w_p4))
    check("profile0 vs profile4 窗口存在差异（姿态生效）", diff)

    print("== 4. 指向：Fixed / Targeted / GrazingAlt / Spinning 确定性 ==")
    cfg8 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg8["sensor_type"] = "SimpleCone"
    cfg8["sensor_params"] = [45.0]
    cfg8["pointing"] = {"type": "Fixed", "method": "YPR", "params": ["YPR", 0.0, 15.0, 0.0]}
    _, w_fix = run_windows(recs, cfg8, engine="complex", limit=6)
    check("Fixed YPR 侧摆 15° 可运行且产出窗口（6 星总窗口 > 0）",
          sum(len(w) for w in w_fix) > 0,
          f"总窗口={sum(len(w) for w in w_fix)}")

    cfg9 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg9["sensor_type"] = "SimpleCone"
    cfg9["sensor_params"] = [45.0]
    cfg9["pointing"] = {"type": "Targeted", "target_mode": 0, "track_mode": 2}
    _, w_tgt = run_windows(recs, cfg9, engine="complex", limit=3)
    check("Targeted 追踪可运行且产出窗口", any(len(w) > 0 for w in w_tgt))

    cfg10 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg10["sensor_type"] = "SimpleCone"
    cfg10["sensor_params"] = [45.0]
    cfg10["pointing"] = {"type": "GrazingAlt", "azimuth": 90.0, "altitude": 10000.0}
    _, w_graz = run_windows(recs, cfg10, engine="complex", limit=3)
    check("GrazingAlt 可运行且产出窗口", any(len(w) > 0 for w in w_graz))

    cfg11 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg11["sensor_type"] = "SimpleCone"
    cfg11["sensor_params"] = [45.0]
    cfg11["pointing"] = {"type": "Spinning", "azimuth": 0.0, "elevation": -90.0,
                         "cone_angle": 45.0, "scan": "Continuous",
                         "rate": 6.0, "offset": 0.0}
    _, w_spin1 = run_windows(recs, cfg11, engine="complex", limit=3)
    _, w_spin2 = run_windows(recs, cfg11, engine="complex", limit=3)
    same_spin = all(norm_windows(a) == norm_windows(b) for a, b in zip(w_spin1, w_spin2))
    check("Spinning 两次运行逐窗口一致（确定性）", same_spin
          and any(len(w) > 0 for w in w_spin1))

    print("== 5. 约束：最小仰角 / 太阳仰角 ==")
    cfg12 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg12["sensor_type"] = "SimpleCone"
    cfg12["sensor_params"] = [45.0]
    cfg12["min_elevation_deg"] = 10.0
    _, w_e10 = run_windows(recs, cfg12, engine="complex", limit=6)
    cfg12b = dict(cfg12)
    cfg12b["min_elevation_deg"] = 30.0
    _, w_e30 = run_windows(recs, cfg12b, engine="complex", limit=6)
    total10 = sum(len(w) for w in w_e10)
    total30 = sum(len(w) for w in w_e30)
    check("最小仰角 30° 窗口 ≤ 10° 窗口（约束收紧）", total30 <= total10,
          f"10°={total10} 30°={total30}")

    cfg13 = make_cfg(fov_half_angle_deg=45.0, min_elevation_deg=0.0)
    cfg13["sensor_type"] = "SimpleCone"
    cfg13["sensor_params"] = [45.0]
    cfg13["constraints"] = [{"object": "sensor", "name": "SunGroundElevAngle",
                             "params": "Min 10"}]
    try:
        _, w_sun = run_windows(recs, cfg13, engine="complex", limit=2)
        check("太阳地平仰角约束可运行", True, f"窗口数={sum(len(w) for w in w_sun)}")
    except Exception as exc:
        check("太阳地平仰角约束可运行", False, str(exc))

    print("== 6. SAR 几何点判据 ==")
    r_s = sg.R_EARTH_KM + 550.0
    S = np.array([r_s, 0.0, 0.0])
    vhat = np.array([0.0, 1.0, 0.0])

    def ground_point(dlon_deg, along_deg):
        d = np.radians(dlon_deg)
        a = np.radians(along_deg)
        return sg.R_EARTH_KM * np.array([np.cos(d) * np.cos(a), np.sin(a), np.sin(d) * np.cos(a)])

    def sar_covered(p, emin=12.0, emax=70.0, fwd=30.0, aft=30.0):
        e = sg.elevation_of_ground(p, S)
        if not (emin <= e <= emax):
            return False
        u = sg.normalize(p - S)
        ang_f = np.degrees(np.arccos(np.clip(np.dot(u, vhat), -1, 1)))
        ang_a = np.degrees(np.arccos(np.clip(np.dot(u, -vhat), -1, 1)))
        return ang_f >= fwd and ang_a >= aft

    check("SAR 近天底被 MaxElev 排除",
          not sar_covered(ground_point(1.0, 0.0)),
          f"ε={sg.elevation_of_ground(ground_point(1.0, 0.0), S):.1f}°")
    check("SAR 条带中部覆盖", sar_covered(ground_point(7.0, 0.0)))
    check("SAR 远缘被 MinElev 排除",
          not sar_covered(ground_point(18.0, 0.0)))

    print()
    print(f"通过 {PASS} 项，失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
