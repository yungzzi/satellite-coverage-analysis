# -*- coding: utf-8 -*-
"""ATK 传感器验证 worker：单个场景（启动无头 ATK → 建场景 → 建传感器变体 →
Assign 逐条登记 → 一次计算 → Cov_RM 覆盖时段报告 → JSON 落盘）。

用法：python validate_sensor_worker.py <config.json>
config 字段：
  port / scenario / tle / t0 / t1 / lat / lon / expected
  variants       [[name, define命令串, [Point命令, 参数] 或 null], ...]
  facility_constraint  可空；加在【地面站】上的约束（如 "ElevationAngle Min 10"）
  out            结果 JSON 路径

要点（2026-08-13 实测踩坑）：
- CovMulti/Assets 必须用不带引号的对象路径；带引号 NACK。
- 登记用逐条 Cov ... Asset ... Assign（可靠），再只发一次 Compute + 一次 Cov_RM。
- 仰角约束加在地面站上（= 自研 min_elevation 口径）；加在传感器上是传感器本地俯仰角。
- ATK 是启动器+子进程结构，收尾按端口杀进程树，避免残留实例串场景。
"""# -*- coding: utf-8 -*-
"""ATK 验证 worker：单个场景（启动ATK→建场景→Assign登记→计算→报告→JSON）。
用法：python val_worker.py <config.json>
"""
import os, re, socket, subprocess, sys, tempfile, time, json

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
ATK_EXE = os.path.join(ATK_ROOT, "ATK.exe")
PYMOD = os.path.join(ATK_ROOT, "IntegratingWithATK", "connect", "Python")
WS = r"D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析-atk-experiment"
sys.path.insert(0, PYMOD)
sys.path.insert(0, WS)
sys.path.insert(0, os.path.join(WS, "atk_parallel"))
import ATKConnectModule as C
from report_parser import decode_report, parse_coverage_report
from atk_parallel.headless import launch_headless

def wait_port(port, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = socket.socket(); s.settimeout(0.5)
        try:
            s.connect(("127.0.0.1", port)); s.close(); return True
        except OSError:
            pass
        finally:
            s.close()
        time.sleep(0.5)
    return False

def kill_port_owner(port):
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
        time.sleep(1.0)

def main():
    with open(sys.argv[1], encoding="utf-8") as f:
        cfg = json.load(f)
    port = int(cfg["port"])
    t0, t1 = cfg["t0"], cfg["t1"]
    lat, lon = float(cfg["lat"]), float(cfg["lon"])
    tle_path = cfg["tle"].replace("\\", "/")
    variants = cfg["variants"]
    fac_constraint = cfg.get("facility_constraint")

    result = {"ok": False, "scenario": cfg["scenario"], "sats": 0, "windows": [],
              "raw_report": "", "nack": [], "error": None}
    kill_port_owner(port)
    try:
        proc = launch_headless(port, new_scenario=True)
    except RuntimeError:
        result["error"] = "ATK 端口未就绪"
        with open(cfg["out"], "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        return
    con = C.atkOpen("127.0.0.1", port)
    def cmd(c, p):
        try:
            r = C.atkConnect(con, c, p)
            return decode_report(r) if isinstance(r, str) else str(r)
        except Exception:
            return "EXC"

    cmd("New", f"/ Scenario {cfg['scenario']}")
    cmd("SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
    cmd("Animate", "* Reset")
    cmd("ImportTLEFile", f'* "{tle_path}"')
    r = cmd("AllInstanceNames", "/")
    names = [n for n in re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", r) if n.startswith("tle-")]
    result["sats"] = len(names)
    if len(names) != cfg.get("expected", 0):
        result["error"] = f"卫星数校验失败: 预期 {cfg.get('expected')} 实际 {len(names)}"

    cmd("New", "/ Facility Hangzhou")
    cmd("SetPosition", f"*/Facility/Hangzhou Geodetic {lat} {lon} 0.0")
    if fac_constraint:
        rr = cmd("SetConstraint", f"*/Facility/Hangzhou {fac_constraint}")
        if "NACK" in rr:
            result["nack"].append(("facility", fac_constraint, rr))

    nack = []
    for sat in names:
        for vname, define, point in variants:
            sp = f'"*/Satellite/{sat}/Sensor/{vname}"'
            cmd("New", f'/ "*/Satellite/{sat}/Sensor" {vname}')
            cmd("Define", f'{sp} {define}')
            if point:
                rr = cmd(point[0], f'{sp} {point[1]}')
                if "NACK" in rr:
                    nack.append((sat, vname, "Point", rr))
    result["nack"].extend(nack)

    # 新建场景必须先 Start 生成星历（仅 Reset/Step/Save-Load 都无法物化）
    rr_start = cmd("Animate", "* Start")
    rr_reset = cmd("Animate", "* Reset")
    for tag, rr in (("start", rr_start), ("reset", rr_reset)):
        if rr and "NACK" in rr:
            result["nack"].append((tag, "", rr[:200]))
    # 逐条 Assign（可靠路径）+ 一次 Compute + 一次报告
    for sat in names:
        for vname, _, _ in variants:
            cmd("Cov", f"*/Facility/Hangzhou Asset */Satellite/{sat}/Sensor/{vname} Assign")
    cmd("CovMulti", "/ Objects */Facility/Hangzhou")
    rr_compute = cmd("CovMulti", f'/ Access Compute "{t0}" "{t1}"')
    if rr_compute and "NACK" in rr_compute:
        result["nack"].append(("compute", "", rr_compute[:200]))
    r = C.atkConnect(con, "Cov_RM", f'*/Facility/Hangzhou Access Compute "Coverage" "{t0}" "{t1}"')
    text = decode_report(r if isinstance(r, str) else str(r))
    result["raw_report"] = text
    result["windows"] = parse_coverage_report(text)
    result["ok"] = True
    try:
        C.atkClose(con)
    except Exception:
        pass
    kill_port_owner(port)
    with open(cfg["out"], "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(f"[{cfg['scenario']}] sats={result['sats']} windows={len(result['windows'])} nack={len(nack)}", flush=True)

if __name__ == "__main__":
    main()
