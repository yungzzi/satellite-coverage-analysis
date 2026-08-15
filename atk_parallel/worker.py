# -*- coding: utf-8 -*-
"""单片 worker：连一个 headless ATK 实例，完成导入→传感器→覆盖→报告→落盘。

用法：python -m atk_parallel.worker --config <config.json>
config.json 字段：
  port           ATK connect 端口
  tle            纯两行 TLE 文件（正斜杠路径）
  expected       预期卫星数（用于完整性校验）
  sensor_type    传感器类型：SimpleCone / Rectangular / HalfPower / Conical / SAR
                 （默认 SimpleCone，见官方 Define 命令库）
  sensor_params  类型参数列表（SimpleCone 缺省用 fov/angles；其余类型必填）：
                   Rectangular [VerticalHalfAngle, HorizontalHalfAngle]
                   HalfPower   [Frequency, Diameter]
                   Conical     [InnerConeHalfAngle, OuterConeHalfAngle,
                                MinClockAngle, MaxClockAngle]
                   SAR         [MinElevationAngle, MaxElevationAngle,
                                ForwardExclusionAngle, AftExclusionAngle,
                                ParentAltitude]
  angles         与分片顺序对应的 SimpleCone 半角列表（可选，仅 SimpleCone，优先于 sensor_params）
  fov            默认 SimpleCone 半角（度）
  lat / lon      地面站经纬度（ATK 点目标）
  t0 / t1        分析时段（"12 Aug 2026 00:00:00.000" 格式）
  out            结果 JSON 路径
  xml_route      布尔（可选）：SAR/HalfPower 走 Save→改 XML→Load 路线（Define 在 rc.5
                 对这两种类型静默失效，§7.1）；该模式下 worker 自己管理两个 ATK 实例，
                 runner 不应预启动实例。

注意：不调用 atkClose（本环境实测偶发堆损坏 0xC0000374，会丢结果），
收尾一律按端口杀 ATK 进程树（调度器也会清理）。
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile, time

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
PYMOD = os.path.join(ATK_ROOT, "IntegratingWithATK", "connect", "Python")
BS = 250  # CovMulti Assets 每批条数（安全上限）

sys.path.insert(0, PYMOD)
import ATKConnectModule as C

from .report_parser import decode_report, parse_coverage_report
from .headless import launch_headless

XML_ROUTE_TYPES = ("SAR", "HalfPower")
# 官方 XML <SensorType> 索引（§7.1 实测）：0=SimpleCone, 3=Conical, 4=HalfPower, 5=SAR
SENSOR_TYPE_INDEX = {"HalfPower": 4, "SAR": 5}


def kill_port_owner(port: int) -> None:
    """按端口找到 ATK 进程树并强制结束（替代 atkClose）。"""
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, errors="replace").stdout
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


def build_define(sensor_type: str, params) -> str:
    """拼 Define 命令参数串（官方语法见 Help/传感器/Define.html）。"""
    return f"{sensor_type} " + " ".join(str(x) for x in params)


def build_pattern_block(sensor_type: str, params) -> str:
    """构造 XML <Pattern> 块（SAR/HalfPower 用，§7.1 / 参考 xml_sar5.atk / xml_hp4.atk）。"""
    idx = SENSOR_TYPE_INDEX[sensor_type]
    if sensor_type == "SAR":
        min_el, max_el, fwd, aft = (float(params[i]) for i in range(4))
        alt = float(params[4]) if len(params) > 4 else 0.0
        track = int(params[5]) if len(params) > 5 else 0
        return (
            f"<Pattern>\n<SensorType>{idx}</SensorType>\n<SAR>\n"
            f"<MinElevAngle>{min_el}</MinElevAngle>\n<MaxElevAngle>{max_el}</MaxElevAngle>\n"
            f"<FwdExclAngle>{fwd}</FwdExclAngle>\n<AftExclAngle>{aft}</AftExclAngle>\n"
            f"<Altitude>{alt}</Altitude>\n<TrackParentAlt>{track}</TrackParentAlt>\n"
            f"</SAR>\n</Pattern>"
        )
    freq, diam = float(params[0]), float(params[1])
    lam = 299792458.0 / (freq * 1e9) if freq > 0 else 0.0
    half_angle = round(70.0 * lam / diam / 2.0, 6) if diam > 0 else 0.0
    return (
        f"<Pattern>\n<SensorType>{idx}</SensorType>\n<HalfPwr>\n"
        f"<AntFrequency>{freq}</AntFrequency>\n<AntDiameter>{diam}</AntDiameter>\n"
        f"<halfAngle>{half_angle}</halfAngle>\n</HalfPwr>\n</Pattern>"
    )


def patch_sensor_patterns(xml_path: str, sensor_type: str, params, out_path: str) -> str:
    """把场景 XML 中所有传感器 <Pattern> 替换为目标类型，返回正斜杠路径。"""
    with open(xml_path, "rb") as f:
        b = f.read()
    t = b.decode("gbk", errors="replace")
    block = build_pattern_block(sensor_type, params)
    t = re.sub(r"(?s)<Pattern>.*?</Pattern>", lambda m: block, t)
    with open(out_path, "wb") as f:
        f.write(t.encode("gbk", errors="replace"))
    return out_path.replace("\\", "/")


def build_and_patch_xml(cfg, port: int, tle: str, t0: str, t1: str) -> str:
    """实例 1（new_scenario）：建 SimpleCone 占位场景 → Save → patch 成 SAR/HalfPower。"""
    launch_headless(port, new_scenario=True)
    con = C.atkOpen("127.0.0.1", port)

    def cmd(c, p):
        try:
            return C.atkConnect(con, c, p)
        except Exception:
            return None

    cmd("New", "/ Scenario W" + str(port))
    cmd("SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
    cmd("Animate", "* Reset")
    cmd("ImportTLEFile", f'* "{tle}"')
    r = cmd("AllInstanceNames", "/")
    names = [n for n in re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", str(r)) if n.strip()]
    fov = float(cfg.get("fov", 45.0))
    for i, nm in enumerate(names):
        cmd("New", f'/ "*/Satellite/{nm}/Sensor" S{i}')
        cmd("Define", f'"*/Satellite/{nm}/Sensor/S{i}" SimpleCone {fov}')
    cmd("New", "/ Facility Hangzhou")
    cmd("SetPosition", f"*/Facility/Hangzhou Geodetic {float(cfg['lat'])} {float(cfg['lon'])} 0.0")
    cmd("Animate", "* Start")
    cmd("Animate", "* Reset")
    raw = os.path.join(tempfile.gettempdir(), f"xmlroute_raw_{port}.atk")
    cmd("Save", f'/ * "{raw.replace(chr(92), "/")}"')
    patched = os.path.join(tempfile.gettempdir(), f"xmlroute_patch_{port}.atk")
    try:
        return patch_sensor_patterns(
            raw, cfg["sensor_type"], cfg["sensor_params"], patched)
    finally:
        kill_port_owner(port)


def run_xml_route(cfg, port: int, tle: str, t0: str, t1: str, result: dict):
    """实例 2（不带 new_scenario）：Load 补丁场景 → Assign → Compute → 报告。"""
    patched = build_and_patch_xml(cfg, port, tle, t0, t1)
    launch_headless(port, new_scenario=False)
    con = C.atkOpen("127.0.0.1", port)

    def cmd(c, p):
        try:
            r = C.atkConnect(con, c, p)
            return decode_report(r) if isinstance(r, str) else str(r)
        except Exception:
            return "EXC"

    cmd("Load", f'/ Scenario "{patched}"')
    cmd("SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
    cmd("Animate", "* Reset")
    r = cmd("AllInstanceNames", "/")
    names = [n for n in re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", r) if n.strip()]
    result["sats"] = len(names)
    if result["expected"] and len(names) != result["expected"]:
        result["error"] = f"卫星数校验失败: 预期 {result['expected']}, 实际 {len(names)}"
        kill_port_owner(port)
        return
    for i, nm in enumerate(names):
        cmd("Cov", f"*/Facility/Hangzhou Asset */Satellite/{nm}/Sensor/S{i} Assign")
    cmd("CovMulti", "/ Objects */Facility/Hangzhou")
    for b in range(0, len(names), BS):
        lo, hi = b + 1, min(b + BS, len(names))
        assets = " ".join(f"*/Satellite/{names[i - 1]}/Sensor/S{i - 1}" for i in range(lo, hi + 1))
        cmd("CovMulti", f"/ Assets {assets}")
        cmd("CovMulti", f'/ Access Compute "{t0}" "{t1}"')
    rep = cmd("Cov_RM", f'*/Facility/Hangzhou Access Compute "Coverage" "{t0}" "{t1}"')
    result["windows"] = parse_coverage_report(rep)
    result["asset_order"] = names
    result["ok"] = True
    kill_port_owner(port)


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
    sensor_type = cfg.get("sensor_type", "SimpleCone")
    sensor_params = cfg.get("sensor_params")
    lat = float(cfg.get("lat", 30.25))
    lon = float(cfg.get("lon", 120.17))
    t0, t1 = cfg["t0"], cfg["t1"]
    out = cfg["out"]

    timing = {}
    t_all = time.time()

    result = {
        "port": port, "ok": False, "expected": expected, "sats": 0,
        "sensor_type": sensor_type, "sensor_params": sensor_params,
        "windows": [], "asset_order": [], "timing": timing,
        "error": None,
    }
    xml_route = bool(cfg.get("xml_route")) or sensor_type in XML_ROUTE_TYPES
    if xml_route:
        if not sensor_params:
            result["error"] = f"传感器 {sensor_type} 需要 sensor_params（见 worker.py 头注释）"
            _finish(None, result, out, port)
            return
        t_xml = time.time()
        run_xml_route(cfg, port, tle, t0, t1, result)
        result["timing"]["xml_route"] = round(time.time() - t_xml, 1)
        result["timing"]["total"] = round(time.time() - t_all, 1)
        _finish(None, result, out, port)
        return

    con = C.atkOpen("127.0.0.1", port)

    def cmd(c, p):
        try:
            return C.atkConnect(con, c, p)
        except Exception:
            return None

    cmd("New", "/ Scenario W" + str(port))
    cmd("SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
    # 初始化动画状态；新建场景的星历物化仍需下方 Start（见注释）
    cmd("Animate", "* Reset")

    t0_ = time.time()
    cmd("ImportTLEFile", f'* "{tle}"')
    timing["import"] = round(time.time() - t0_, 1)

    r = cmd("AllInstanceNames", "/")
    names = re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", str(r))
    names = [n for n in names if n.strip()]
    sats = len(names)
    result["sats"] = sats
    result["asset_order"] = names

    if expected and sats != expected:
        result["error"] = f"卫星数校验失败: 预期 {expected}, 实际 {sats}"
        _finish(con, result, out, port)
        return

    t0_ = time.time()
    for idx, nm in enumerate(names):
        if sensor_type == "SimpleCone":
            if angles:
                params = [angles[idx] if idx < len(angles) else fov]
            elif sensor_params is None:
                params = [fov]
            else:
                params = sensor_params
        else:
            if not sensor_params:
                result["error"] = f"传感器 {sensor_type} 需要 sensor_params（见 worker.py 头注释）"
                _finish(con, result, out, port)
                return
            params = sensor_params
        sname = f"S{idx}"
        cmd("New", f'/ "*/Satellite/{nm}/Sensor" {sname}')
        cmd("Define", f'"*/Satellite/{nm}/Sensor/{sname}" {build_define(sensor_type, params)}')
    timing["sensor"] = round(time.time() - t0_, 1)

    cmd("New", "/ Facility Hangzhou")
    cmd("SetPosition", f"*/Facility/Hangzhou Geodetic {lat} {lon} 0.0")
    # 新建场景必须先 Start 生成星历（仅 Reset/Step/Save-Load 都无法物化）。
    # 注意：Spinning 相位在 Connect 新建场景下跨实例仍不可复现（离散初始化状态，
    # 2026-08-15 独立复测确认；SimpleCone/SAR/HalfPower 等非旋转传感器不受影响）。
    cmd("Animate", "* Start")
    cmd("Animate", "* Reset")

    t0_ = time.time()
    cmd("CovMulti", "/ Objects */Facility/Hangzhou")
    for b in range(0, sats, BS):
        lo, hi = b + 1, min(b + BS, sats)
        assets = " ".join(f'*/Satellite/{names[i - 1]}/Sensor/S{i - 1}' for i in range(lo, hi + 1))
        cmd("CovMulti", f"/ Assets {assets}")
        cmd("CovMulti", f'/ Access Compute "{t0}" "{t1}"')
    timing["cov"] = round(time.time() - t0_, 1)

    t0_ = time.time()
    r = C.atkConnect(con, "Cov_RM", f'*/Facility/Hangzhou Access Compute "Coverage" "{t0}" "{t1}"')
    text = decode_report(r if isinstance(r, str) else str(r))
    timing["report"] = round(time.time() - t0_, 1)
    result["windows"] = parse_coverage_report(text)
    result["timing"]["total"] = round(time.time() - t_all, 1)
    result["ok"] = True

    _finish(con, result, out, port)


def _finish(con, result, out, port):
    # 先落盘再清理：atkClose 偶发堆损坏（0xC0000374），若先关会丢结果
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: result[k] for k in (
        "port", "ok", "expected", "sats", "sensor_type", "timing", "error")},
        ensure_ascii=False), flush=True)
    kill_port_owner(port)


if __name__ == "__main__":
    main()
