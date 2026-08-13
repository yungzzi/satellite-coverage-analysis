# -*- coding: utf-8 -*-
"""单片 worker：连一个 headless ATK 实例，完成导入→传感器→覆盖→报告→落盘。

用法：python -m atk_parallel.worker --config <config.json>
config.json 字段：
  port           ATK connect 端口
  tle            纯两行 TLE 文件（正斜杠路径）
  expected       预期卫星数（用于完整性校验）
  angles         与分片顺序对应的传感器半角列表（可选，缺省用 fov）
  fov            默认传感器半角（度）
  lat / lon      地面站经纬度（ATK 点目标）
  t0 / t1        分析时段（"12 Aug 2026 00:00:00.000" 格式）
  out            结果 JSON 路径
"""
from __future__ import annotations
import argparse, json, os, re, sys, time

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
PYMOD = os.path.join(ATK_ROOT, "IntegratingWithATK", "connect", "Python")
BS = 250  # CovMulti Assets 每批条数（安全上限）

sys.path.insert(0, PYMOD)
import ATKConnectModule as C

from .report_parser import decode_report, parse_coverage_report


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args()
    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    port = int(cfg["port"])
    tle = cfg["tle"].replace("\\", "/")
    expected = int(cfg.get("expected", 0))
    fov = float(cfg.get("fov", 45.0))
    angles = cfg.get("angles") or []
    lat = float(cfg.get("lat", 30.25))
    lon = float(cfg.get("lon", 120.17))
    t0, t1 = cfg["t0"], cfg["t1"]
    out = cfg["out"]

    timing = {}
    t_all = time.time()
    con = C.atkOpen("127.0.0.1", port)

    def cmd(c, p):
        try:
            return C.atkConnect(con, c, p)
        except Exception:
            return None

    cmd("New", "/ Scenario W" + str(port))
    cmd("SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
    cmd("Animate", "* Reset")

    t0_ = time.time()
    cmd("ImportTLEFile", f'* "{tle}"')
    timing["import"] = round(time.time() - t0_, 1)

    r = cmd("AllInstanceNames", "/")
    names = re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", str(r))
    names = [n for n in names if n.strip()]
    sats = len(names)

    result = {
        "port": port, "ok": False, "expected": expected, "sats": sats,
        "windows": [], "asset_order": names, "timing": timing,
        "error": None,
    }
    if expected and sats != expected:
        result["error"] = f"卫星数校验失败: 预期 {expected}, 实际 {sats}"
        _finish(con, result, out)
        return

    t0_ = time.time()
    for idx, nm in enumerate(names):
        angle = angles[idx] if idx < len(angles) else fov
        cmd("New", f'/ "*/Satellite/{nm}/Sensor" S45')
        cmd("Define", f'"*/Satellite/{nm}/Sensor/S45" SimpleCone {angle}')
    timing["sensor"] = round(time.time() - t0_, 1)

    cmd("New", "/ Facility Hangzhou")
    cmd("SetPosition", f"*/Facility/Hangzhou Geodetic {lat} {lon} 0.0")
    cmd("Animate", "* Start")
    cmd("Animate", "* Reset")

    t0_ = time.time()
    cmd("CovMulti", "/ Objects */Facility/Hangzhou")
    for b in range(0, sats, BS):
        lo, hi = b + 1, min(b + BS, sats)
        assets = " ".join(f'*/Satellite/{names[i - 1]}/Sensor/S45' for i in range(lo, hi + 1))
        cmd("CovMulti", f"/ Assets {assets}")
        cmd("CovMulti", "/ Access Compute")
    timing["cov"] = round(time.time() - t0_, 1)

    t0_ = time.time()
    r = C.atkConnect(con, "Cov_RM", f'*/Facility/Hangzhou Access Compute "Coverage" "{t0}" "{t1}"')
    text = decode_report(r if isinstance(r, str) else str(r))
    timing["report"] = round(time.time() - t0_, 1)
    result["windows"] = parse_coverage_report(text)
    result["timing"]["total"] = round(time.time() - t_all, 1)
    result["ok"] = True

    _finish(con, result, out)


def _finish(con, result, out):
    try:
        C.atkClose(con)
    except Exception:
        pass
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: result[k] for k in ("port", "ok", "expected", "sats", "timing", "error")}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
