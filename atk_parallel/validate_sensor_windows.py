# -*- coding: utf-8 -*-
"""ATK 复杂传感器覆盖窗口验证主脚本（L1 口径对拍 + L2 三重验证）。

用法：python validate_sensor_windows.py
依赖同目录 validate_sensor_worker.py（每个场景一个子进程，绕开 ATK Connect 的 Winsock 副作用）。

场景设计（24 颗中国遥感 LEO × 8h × 杭州点 30.25,120.17，2026-08-12 00:00-08:00 UTC）：
  A  无约束：SimpleCone45 / Conical(0,45) / Conical(20,45) / SAR(12,70,30,30,550000) /
      SAR(0,90,0,0,550000) / HalfPower(0.5,0.35) / HalfPower(14.5,1) / Rectangular(45,45) / Spinning
  B  SimpleCone45 + 地面站约束 ElevationAngle Min 10   （对拍自研 fov45/min_elev10）
  C  SimpleCone45 + 地面站约束 ElevationAngle Min 30   （对拍自研 fov45/min_elev30）
  D  SimpleCone80（无约束）                            （对拍自研 fov80/min_elev0）
  E  SimpleCone80 + 地面站约束 Min 30                  （决定性测试：对拍自研 fov80/min_elev30）

结果：窗口数/总覆盖/退化一致/物理子集/自研匹配统计；原始报告与 JSON 存 %TEMP%。
"""
import os, re, subprocess, sys, tempfile, time, json, datetime
from collections import defaultdict

WS = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # 工作树根（本文件在 atk_parallel/ 下）
VENV_PY = os.path.join(os.path.dirname(WS), "卫星覆盖性分析", ".venv", "Scripts", "python.exe")
if not os.path.exists(VENV_PY):
    VENV_PY = sys.executable
sys.path.insert(0, os.path.join(WS, "atk_parallel"))
sys.path.insert(0, os.path.join(WS, "src"))
from tle_utils import read_tle, to_two_line_file

T0_ATK = "12 Aug 2026 00:00:00.000"; T1_ATK = "12 Aug 2026 08:00:00.000"
T0 = "2026-08-12 00:00:00"; T1 = "2026-08-12 08:00:00"
LAT, LON, R = 30.25, 120.17, 5.0
PORT = 6655
HERE = os.path.dirname(os.path.abspath(__file__))

import coverage_analysis as ca

def self_windows(recs, fov, min_elev):
    cfg = dict(ca.CONFIG)
    cfg.update({"fov_half_angle_deg": fov, "min_elevation_deg": min_elev,
                "region_mode": "circle", "target": {"lat": LAT, "lon": LON, "radius_km": R},
                "step_minutes": 1, "coarse_step_seconds": 10, "refine_step_seconds": 1,
                "refine_margin_minutes": 3})
    region = {"mode": "circle", "lat": LAT, "lon": LON, "radius_km": R}
    import satellite_loader as sl
    from skyfield.api import EarthSatellite
    ts = sl.load.timescale(builtin=True)
    t, dt_list = ca.make_time_array(ts, T0, T1, 1)
    t1_true = dt_list[-1] + datetime.timedelta(seconds=60)
    out = {}
    for rec in recs:
        try:
            orbit = EarthSatellite(rec.l1, rec.l2, rec.name, ts)
            sat = sl.Satellite(orbit, rec.name, int(rec.norad))
            wins = ca.analyze_one_satellite(sat, t, dt_list, region, cfg, period_end=t1_true)
            out[rec.norad] = [(w[0].strftime("%Y-%m-%d %H:%M:%S"), w[1].strftime("%Y-%m-%d %H:%M:%S"), float(w[2]))
                              for w in wins]
        except Exception:
            pass
    return out

def pick_free_port(base=6655, max_scan=200):
    """挑一个当前空闲端口（bind 探测），避免与其他软件端口冲突。"""
    import socket as _s
    for p in range(base, base + max_scan):
        s = _s.socket()
        try:
            s.bind(("127.0.0.1", p))
            s.close()
            return p
        except OSError:
            pass
        finally:
            try:
                s.close()
            except Exception:
                pass
    raise RuntimeError("无空闲端口")

def kill_port_owner(port):
    """杀监听端口的进程树（ATK 是启动器+子进程结构，按端口找真身）。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, errors="replace").stdout
    except Exception:
        return
    pids = set()
    for ln in out.splitlines():
        if f":{port}" in ln and "LISTENING" in ln:
            parts = ln.split()
            if parts:
                pids.add(parts[-1])
    for pid in pids:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", pid], capture_output=True)
        except Exception:
            pass
        try:
            os.kill(int(pid), 9)
        except Exception:
            pass
    if pids:
        time.sleep(1.5)

def run_worker(scenario, variants, facility_constraint=None):
    tle_path = os.path.join(tempfile.gettempdir(), "val_subset.tle")
    port = pick_free_port()
    cfg = {"port": port, "scenario": scenario, "tle": tle_path.replace("\\", "/"),
           "t0": T0_ATK, "t1": T1_ATK, "lat": LAT, "lon": LON,
           "expected": SUBSET_N, "variants": variants,
           "facility_constraint": facility_constraint,
           "out": os.path.join(tempfile.gettempdir(), f"val_{scenario}.json")}
    cpath = os.path.join(tempfile.gettempdir(), f"cfg_{scenario}.json")
    with open(cpath, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    last = None
    for attempt in range(3):
        kill_port_owner(port)
        subprocess.run([VENV_PY, os.path.join(HERE, "validate_sensor_worker.py"), cpath],
                       cwd=WS, timeout=900)
        if os.path.exists(cfg["out"]):
            with open(cfg["out"], encoding="utf-8") as f:
                last = json.load(f)
            if last.get("ok"):
                return last
        print(f"  [{scenario}] 尝试 {attempt + 1} 失败，重试", flush=True)
        time.sleep(3)
    raise RuntimeError(f"场景 {scenario} 连续失败: {last}")

def parse_asset(asset):
    m2 = re.match(r"tle-(\d+)([A-Z]?)-([A-Z0-9]+)", asset)
    return (m2.group(1), m2.group(3)) if m2 else (None, None)

def by_sat(windows, vname):
    out = defaultdict(list)
    for w in windows:
        norad, vn = parse_asset(w["asset"])
        if vn == vname:
            out[norad].append(w)
    return out

def win_summary(wl):
    return {"n": len(wl), "total_s": round(sum(w["duration_s"] for w in wl), 1),
            "max_s": round(max((w["duration_s"] for w in wl), default=0), 1)}

def same_windows(a, b, tol_s=30):
    a = sorted(a, key=lambda w: (w["start"], w["end"]))
    b = sorted(b, key=lambda w: (w["start"], w["end"]))
    if len(a) != len(b):
        return False, None
    diffs = []
    for x, y in zip(a, b):
        d0 = abs((datetime.datetime.strptime(x["start"], "%Y-%m-%d %H:%M:%S.%f")
                  - datetime.datetime.strptime(y["start"], "%Y-%m-%d %H:%M:%S.%f")).total_seconds())
        d1 = abs((datetime.datetime.strptime(x["end"], "%Y-%m-%d %H:%M:%S.%f")
                  - datetime.datetime.strptime(y["end"], "%Y-%m-%d %H:%M:%S.%f")).total_seconds())
        diffs.append(max(d0, d1))
    return all(d <= tol_s for d in diffs), max(diffs)

def match_self(atk_by_sat, self_wl, tol_s=45):
    n_self = sum(len(v) for v in self_wl.values())
    n_atk = sum(len(v) for v in atk_by_sat.values())
    n_match = 0; diffs = []
    for norad, wl in atk_by_sat.items():
        sw = sorted(self_wl.get(norad, []))
        for a in wl:
            ad = datetime.datetime.strptime(a["start"][:19], "%Y-%m-%d %H:%M:%S")
            best = None
            for cand in sw:
                d0 = abs((datetime.datetime.strptime(cand[0], "%Y-%m-%d %H:%M:%S") - ad).total_seconds())
                if best is None or d0 < best[0]:
                    best = (d0, cand)
            if best and best[0] <= tol_s:
                d1 = abs((datetime.datetime.strptime(a["end"][:19], "%Y-%m-%d %H:%M:%S")
                          - datetime.datetime.strptime(best[1][1], "%Y-%m-%d %H:%M:%S")).total_seconds())
                if d1 <= tol_s:
                    n_match += 1
                    diffs.append(max(best[0], d1))
    return n_atk, n_self, n_match, round(max(diffs, default=0), 1), round(sum(diffs) / len(diffs), 1) if diffs else 0.0

def containment(small, big, tol_s=30):
    total_small = 0; covered = 0
    for w in small:
        total_small += w["duration_s"]
        b0 = datetime.datetime.strptime(w["start"], "%Y-%m-%d %H:%M:%S.%f")
        b1 = datetime.datetime.strptime(w["end"], "%Y-%m-%d %H:%M:%S.%f")
        hit = any(datetime.datetime.strptime(u["start"], "%Y-%m-%d %H:%M:%S.%f") - datetime.timedelta(seconds=tol_s) <= b0
                  and datetime.datetime.strptime(u["end"], "%Y-%m-%d %H:%M:%S.%f") + datetime.timedelta(seconds=tol_s) >= b1
                  for u in big)
        if hit:
            covered += w["duration_s"]
    return (covered / total_small * 100) if total_small else 100.0

# ---------------- 准备 ----------------
recs_all = read_tle(os.path.join(WS, "data", "test_data", "china_remote_sensing_all.tle"))
print("全量遥感:", len(recs_all), flush=True)
w0_all = self_windows(recs_all, 45.0, 0.0)
ranked = sorted(((r, sum(w[2] for w in w0_all.get(r.norad, []))) for r in recs_all), key=lambda x: -x[1])
subset = [r for r, d in ranked[:24] if d > 0]
SUBSET_N = len(subset)
print("子集:", SUBSET_N, flush=True)
tle_path = os.path.join(tempfile.gettempdir(), "val_subset.tle")
to_two_line_file(subset, tle_path)

self45_0 = self_windows(subset, 45.0, 0.0)
self45_10 = self_windows(subset, 45.0, 10.0)
self45_30 = self_windows(subset, 45.0, 30.0)
self80_0 = self_windows(subset, 80.0, 0.0)
self80_30 = self_windows(subset, 80.0, 30.0)
print("自研基线:", {k: sum(len(v) for v in d.values())
                    for k, d in (("45/0", self45_0), ("45/10", self45_10), ("45/30", self45_30),
                                 ("80/0", self80_0), ("80/30", self80_30))}, flush=True)

variantsA = [
    ("S45",   "SimpleCone 45", None),
    ("SC0",   "Conical 0 45 0 360", None),
    ("SC20",  "Conical 20 45 0 360", None),
    ("SAR12", "SAR 12 70 30 30 550000", None),
    ("SAR90", "SAR 0 90 0 0 550000", None),
    ("HP30",  "HalfPower 0.5 0.35", None),
    ("HP14",  "HalfPower 14.5 1", None),
    ("RECT",  "Rectangular 45 45", None),
    ("SPIN",  "SimpleCone 20", ["Point", "Spinning 0 -90 30 Continuous 6 0"]),
]
A = run_worker("A", variantsA)
B = run_worker("B", [["S45", "SimpleCone 45", None]], "ElevationAngle Min 10")
C = run_worker("C", [["S45", "SimpleCone 45", None]], "ElevationAngle Min 30")
D = run_worker("D80", [["S80", "SimpleCone 80", None]])
E = run_worker("E80", [["S80", "SimpleCone 80", None]], "ElevationAngle Min 30")

# ---------------- 汇总 ----------------
agg = {}
for w in A["windows"]:
    norad, vn = parse_asset(w["asset"])
    agg.setdefault(vn, []).append(w)
print("\n===== 场景A 变体汇总（24 星 × 8h，杭州点，无约束） =====", flush=True)
for vn in [v[0] for v in variantsA]:
    s = win_summary(agg.get(vn, []))
    print(f"  {vn:7s} 窗口 {s['n']:4d}  总覆盖 {s['total_s']:9.1f}s  最长 {s['max_s']:8.1f}s", flush=True)

s45 = by_sat(A["windows"], "S45"); sc0 = by_sat(A["windows"], "SC0")
allmatch = True; maxdiff = 0
for norad in s45:
    ok, d = same_windows(s45[norad], sc0.get(norad, []))
    if not ok:
        allmatch = False
        print("  SC0/S45 不一致:", norad)
    maxdiff = max(maxdiff, d or 0)
print(f"退化一致 Conical(0,45) vs SimpleCone45: {'一致' if allmatch else '不一致'} 最大边界差 {maxdiff:.2f}s", flush=True)

results = {"scenarioA": {k: win_summary(v) for k, v in agg.items()},
           "conical_deg": allmatch}
r = match_self(s45, self45_0)
results["l1_45_no_constraint"] = r
print(f"L1a S45(无约束) vs 自研 fov45/min0: ATK {r[0]} / 自研 {r[1]} / 匹配 {r[2]} / 最大 {r[3]}s / 平均 {r[4]}s", flush=True)
for tag, worker, selfw in (("B", B, self45_10), ("C", C, self45_30)):
    r = match_self(by_sat(worker["windows"], "S45"), selfw)
    results[f"l1_{tag}"] = r
    print(f"L1{tag} 地面站约束 vs 自研: ATK {r[0]} / 自研 {r[1]} / 匹配 {r[2]} / 最大 {r[3]}s / 平均 {r[4]}s", flush=True)
rD = match_self(by_sat(D["windows"], "S80"), self80_0)
rE = match_self(by_sat(E["windows"], "S80"), self80_30)
results["l1_80_no_constraint"] = rD
results["l1_80_min30"] = rE
print(f"L1D fov80(无约束) vs 自研 fov80/min0: ATK {rD[0]} / 自研 {rD[1]} / 匹配 {rD[2]} / 最大 {rD[3]}s / 平均 {rD[4]}s", flush=True)
print(f"L1E fov80+约束30 vs 自研 fov80/min30: ATK {rE[0]} / 自研 {rE[1]} / 匹配 {rE[2]} / 最大 {rE[3]}s / 平均 {rE[4]}s", flush=True)

sc20 = by_sat(A["windows"], "SC20"); spin = by_sat(A["windows"], "SPIN")
c_ann = containment([w for wl in sc20.values() for w in wl], [w for wl in s45.values() for w in wl])
c_spin = containment([w for wl in spin.values() for w in wl], [w for wl in s45.values() for w in wl])
results["contain"] = {"SC20_in_S45": round(c_ann, 1), "SPIN_in_S45": round(c_spin, 1)}
print(f"物理: SC20 被 S45 覆盖 {c_ann:.1f}%  | SPIN 被 S45 覆盖 {c_spin:.1f}%", flush=True)

with open(os.path.join(tempfile.gettempdir(), "val_results_final.json"), "w", encoding="utf-8") as f:
    json.dump(results, f, ensure_ascii=False, indent=1)
print("done", flush=True)
