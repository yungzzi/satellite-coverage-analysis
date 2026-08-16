# -*- coding: utf-8 -*-
"""单片 worker：连一个 headless ATK 实例，逐传感器配置（类型/参数/指向/约束），算覆盖窗口。

用法：python -m atk_parallel.worker --config <config.json>
config 字段：
  port / tle / expected / out / t0 / t1 / lat / lon
  fov            全局默认 SimpleCone 半角（度）
  min_elev       目标点最小仰角（度，地面站级，全局）
  sensor_type / sensor_params / pointing / constraints
                 全局默认（constraints 中 object=facility 作用于目标点、sensor 作用于每颗星传感器、
                 satellite 作用于每颗星）
  sat_cfgs       逐星配置列表（与 TLE 导入顺序一一对应；None=用全局）：
                 {"sensor_type", "sensor_params", "fov_half_angle_deg",
                  "pointing", "constraints"(该星传感器级), "sat_constraints"(该星卫星级)}
  angles         兼容旧接口：SimpleCone 逐星半角列表（仅全局 SimpleCone 且未配 sat_cfgs 时用）

路线：
  - 无 SAR/HalfPower 时走 Define 路线（一个实例，逐传感器 Define/Point/SetConstraint）；
  - 有 SAR/HalfPower 时走 XML 路线（实例1 建 SimpleCone 占位 → Save → 逐传感器改 Pattern
    → 实例2 Load → 逐传感器 Point/SetConstraint → 计算）。两类传感器可混在同一个实例里。

注意：不调用 atkClose（堆损坏会丢结果），收尾按端口杀 ATK 进程树。
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tempfile, time

from .headless import get_atk_root, launch_headless

ATK_ROOT = get_atk_root()
PYMOD = os.path.join(ATK_ROOT, "IntegratingWithATK", "connect", "Python")
BS = 250

sys.path.insert(0, PYMOD)
import ATKConnectModule as C

from .report_parser import decode_report, parse_coverage_report

XML_ROUTE_TYPES = ("SAR", "HalfPower")
SENSOR_TYPE_INDEX = {"SimpleCone": 0, "Rectangular": 1, "Conical": 3,
                     "HalfPower": 4, "SAR": 5}


def kill_port_owner(port: int) -> None:
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
    return f"{sensor_type} " + " ".join(str(x) for x in params)


def build_point_cmd(pointing) -> str:
    t = pointing.get("type", "Fixed")
    if t == "Fixed":
        method = pointing.get("method", "YPR")
        params = pointing.get("params") or []
        return f"Fixed {method} " + " ".join(str(x) for x in params)
    if t == "Targeted":
        return f"Targeted Tracking */Facility/Hangzhou " \
               + str(pointing.get("track_mode", 2))
    if t == "GrazingAlt":
        return f"GrazingAlt {pointing['azimuth']} {pointing['altitude']}"
    if t == "External":
        return f'External "{pointing["file"]}"'
    raise ValueError(f"暂不支持的指向类型：{t}")


def constraint_cmds(constraints, facility="*/Facility/Hangzhou", sat=None, sensor=None):
    out = []
    for c in constraints or []:
        obj = c.get("object", "facility")
        if obj == "facility":
            path = facility
        elif obj == "satellite" and sat:
            path = sat
        elif obj == "sensor" and sensor:
            path = sensor
        else:
            continue
        params = c.get("params", "")
        out.append(f"{path} {c['name']} {params}".rstrip())
    return out


def build_pattern_block(sensor_type: str, params) -> str:
    """构造 XML <Pattern> 块（五种类型全支持；结构来自 Connect 建场景 Save 导出 + §7.1）。"""
    idx = SENSOR_TYPE_INDEX[sensor_type]
    if sensor_type == "SimpleCone":
        return (f"<Pattern>\n<SensorType>{idx}</SensorType>\n<SimpleCone>\n"
                f"<HalfAng>{params[0]}</HalfAng>\n</SimpleCone>\n</Pattern>")
    if sensor_type == "Rectangular":
        v, h = float(params[0]), float(params[1])
        return (f"<Pattern>\n<SensorType>{idx}</SensorType>\n<Rect>\n"
                f"<HorizontalHalfAng>{h}</HorizontalHalfAng>\n"
                f"<VerticalHalfAng>{v}</VerticalHalfAng>\n</Rect>\n</Pattern>")
    if sensor_type == "Conical":
        i, o, mc, xc = (float(params[k]) for k in range(4))
        return (f"<Pattern>\n<SensorType>{idx}</SensorType>\n<Conical>\n"
                f"<InnerConeAngle>{i}</InnerConeAngle>\n<OuterConeAngle>{o}</OuterConeAngle>\n"
                f"<MinClockAngle>{mc}</MinClockAngle>\n<MaxClockAngle>{xc}</MaxClockAngle>\n"
                f"</Conical>\n</Pattern>")
    if sensor_type == "HalfPower":
        freq, diam = float(params[0]), float(params[1])
        lam = 299792458.0 / (freq * 1e9) if freq > 0 else 0.0
        half = round(70.0 * lam / diam / 2.0, 6) if diam > 0 else 0.0
        return (f"<Pattern>\n<SensorType>{idx}</SensorType>\n<HalfPwr>\n"
                f"<AntFrequency>{freq}</AntFrequency>\n<AntDiameter>{diam}</AntDiameter>\n"
                f"<halfAngle>{half}</halfAngle>\n</HalfPwr>\n</Pattern>")
    if sensor_type == "SAR":
        mel, xel, fwd, aft = (float(params[k]) for k in range(4))
        alt = float(params[4]) if len(params) > 4 else 0.0
        track = int(params[5]) if len(params) > 5 else 0
        return (f"<Pattern>\n<SensorType>{idx}</SensorType>\n<SAR>\n"
                f"<MinElevAngle>{mel}</MinElevAngle>\n<MaxElevAngle>{xel}</MaxElevAngle>\n"
                f"<FwdExclAngle>{fwd}</FwdExclAngle>\n<AftExclAngle>{aft}</AftExclAngle>\n"
                f"<Altitude>{alt}</Altitude>\n<TrackParentAlt>{track}</TrackParentAlt>\n"
                f"</SAR>\n</Pattern>")
    raise ValueError(f"未知传感器类型：{sensor_type}")


def patch_patterns_per_sensor(xml_path: str, blocks: dict, out_path: str) -> str:
    """按传感器索引逐个替换 <Pattern>（blocks: {idx: Pattern块字符串}）。"""
    with open(xml_path, "rb") as f:
        b = f.read()
    t = b.decode("gbk", errors="replace")
    pat = re.compile(r'(?s)(<Sensor Name="S(\d+)"[^>]*>.*?<Pattern>).*?(</Pattern>)')

    def repl(m):
        idx = int(m.group(2))
        blk = blocks.get(idx)
        if blk is None:
            return m.group(0)
        inner = blk[len("<Pattern>"):-len("</Pattern>")] \
            if blk.startswith("<Pattern>") and blk.endswith("</Pattern>") else blk
        return m.group(1) + inner + m.group(3)

    t = pat.sub(repl, t)
    with open(out_path, "wb") as f:
        f.write(t.encode("gbk", errors="replace"))
    return out_path.replace("\\", "/")


def patch_pointing_targeted(xml_path: str, scenario: str, facility: str,
                           targets: dict, out_path: str) -> str:
    """把指定传感器（S{idx}）的 <Pointing> 改为目标指向（PointType=2）。

    正确写法来自 GUI 场景传感器2（§10.26）：AssignedTargets 内是
    <ObjectsPath Path="/Scenario/<场景名>/Facility/<地面站>"/>，不是内联文本路径。"""
    with open(xml_path, "rb") as f:
        b = f.read()
    t = b.decode("gbk", errors="replace")
    pat = re.compile(r'(?s)(<Sensor Name="S(\d+)"[^>]*>.*?<Pointing>).*?(</Pointing>)')

    def repl(m):
        idx = int(m.group(2))
        tm = targets.get(idx)
        if tm is None:
            return m.group(0)
        blk = m.group(0)
        blk = re.sub(r"<PointType>\d+</PointType>", "<PointType>2</PointType>", blk, count=1)
        new_tgt = (
            "<Targeted>\n<TargetMode>0</TargetMode>\n<Sequence>323</Sequence>\n"
            f"<TrackingMode>{int(tm)}</TrackingMode>\n<AssignedTargets>\n"
            f'<ObjectsPath Path="/Scenario/{scenario}/Facility/{facility}"/>\n'
            "</AssignedTargets>\n</Targeted>")
        blk = re.sub(r"(?s)<Targeted>.*?</Targeted>", new_tgt, blk, count=1)
        return blk

    t = pat.sub(repl, t)
    with open(out_path, "wb") as f:
        f.write(t.encode("gbk", errors="replace"))
    return out_path.replace("\\", "/")


def eff_sat(i: int, cfg: dict) -> dict:
    """第 i 颗星的有效配置（逐星覆盖全局）。"""
    sc = cfg.get("sat_cfgs") or []
    s = sc[i] if i < len(sc) and sc[i] else {}
    stype = s.get("sensor_type") or cfg.get("sensor_type", "SimpleCone")
    sparams = s.get("sensor_params") if s.get("sensor_params") is not None \
        else cfg.get("sensor_params")
    if stype == "SimpleCone" and sparams is None:
        if s.get("fov_half_angle_deg") is not None:
            sparams = [s["fov_half_angle_deg"]]
        elif cfg.get("angles") and i < len(cfg["angles"]):
            sparams = [cfg["angles"][i]]
        else:
            sparams = [cfg.get("fov", 45.0)]
    pointing = s.get("pointing") or cfg.get("pointing")
    cons = list(cfg.get("constraints") or []) + list(s.get("constraints") or [])
    sat_cons = list(s.get("sat_constraints") or [])
    return {"type": stype, "params": sparams, "pointing": pointing,
            "constraints": cons, "sat_constraints": sat_cons}


def build_and_patch_xml(cfg, port: int, tle: str, t0: str, t1: str) -> str:
    """实例 1：建 SimpleCone 占位 → Save → 逐传感器改 Pattern（XML 路线）。"""
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
    _scn = re.match(r"/Scenario/([^/]+)", str(r))
    scenario = _scn.group(1).strip() if _scn else "Scenario1"
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
    blocks = {}
    tgt = {}
    for i in range(len(names)):
        eff = eff_sat(i, cfg)
        if eff["type"] in XML_ROUTE_TYPES:
            blocks[i] = build_pattern_block(eff["type"], eff["params"])
        pt = eff.get("pointing") or {}
        if pt.get("type") == "Targeted":
            tgt[i] = pt.get("track_mode", 2)
    try:
        out_path = raw
        if blocks:
            out_path = patch_patterns_per_sensor(
                raw, blocks, os.path.join(tempfile.gettempdir(), f"xmlroute_patch_{port}.atk"))
        if tgt:
            out_path = patch_pointing_targeted(
                out_path, scenario, "Hangzhou", tgt,
                os.path.join(tempfile.gettempdir(), f"xmlroute_tgt_{port}.atk"))
        return out_path
    finally:
        kill_port_owner(port)


def run_xml_route(cfg, port: int, tle: str, t0: str, t1: str, result: dict):
    """实例 2：Load → 目标点约束 → 逐传感器 Point/约束 → Assign → 计算。"""
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
    if cfg.get("min_elev"):
        cmd("SetConstraint", f"*/Facility/Hangzhou ElevationAngle Min {float(cfg['min_elev'])}")
    for cc in constraint_cmds(cfg.get("constraints")):
        cmd("SetConstraint", cc)
    r = cmd("AllInstanceNames", "/")
    names = [n for n in re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", r) if n.strip()]
    result["sats"] = len(names)
    if result["expected"] and len(names) != result["expected"]:
        result["error"] = f"卫星数校验失败: 预期 {result['expected']}, 实际 {len(names)}"
        kill_port_owner(port)
        return
    for i, nm in enumerate(names):
        eff = eff_sat(i, cfg)
        sp = f'"*/Satellite/{nm}/Sensor/S{i}"'
        if eff["pointing"] and eff["pointing"].get("type") != "Targeted":
            cmd("Point", f"{sp} {build_point_cmd(eff['pointing'])}")
        for cc in constraint_cmds(eff["constraints"], sensor=f"*/Satellite/{nm}/Sensor/S{i}"):
            cmd("SetConstraint", cc)
        for cc in constraint_cmds(eff["sat_constraints"], sat=f"*/Satellite/{nm}"):
            cmd("SetConstraint", cc)
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
    t0, t1 = cfg["t0"], cfg["t1"]
    out = cfg["out"]
    timing = {}
    t_all = time.time()
    result = {
        "port": port, "ok": False, "expected": expected, "sats": 0,
        "windows": [], "asset_order": [], "timing": timing, "error": None,
    }
    need_xml = any(
        (eff_sat(i, cfg)["type"] in XML_ROUTE_TYPES
         or (eff_sat(i, cfg).get("pointing") or {}).get("type") == "Targeted")
        for i in range(expected or 0)) or \
        cfg.get("sensor_type", "SimpleCone") in XML_ROUTE_TYPES or \
        (cfg.get("pointing") or {}).get("type") == "Targeted"
    if need_xml:
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
    cmd("Animate", "* Reset")
    cmd("ImportTLEFile", f'* "{tle}"')
    r = cmd("AllInstanceNames", "/")
    names = [n for n in re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", str(r)) if n.strip()]
    result["sats"] = len(names)
    result["asset_order"] = names
    if expected and len(names) != expected:
        result["error"] = f"卫星数校验失败: 预期 {expected}, 实际 {len(names)}"
        _finish(con, result, out, port)
        return

    t0_ = time.time()
    for i, nm in enumerate(names):
        eff = eff_sat(i, cfg)
        sp = f'"*/Satellite/{nm}/Sensor/S{i}"'
        cmd("New", f'/ "*/Satellite/{nm}/Sensor" S{i}')
        cmd("Define", f"{sp} {build_define(eff['type'], eff['params'])}")
        if eff["pointing"]:
            cmd("Point", f"{sp} {build_point_cmd(eff['pointing'])}")
        for cc in constraint_cmds(eff["constraints"], sensor=f"*/Satellite/{nm}/Sensor/S{i}"):
            cmd("SetConstraint", cc)
        for cc in constraint_cmds(eff["sat_constraints"], sat=f"*/Satellite/{nm}"):
            cmd("SetConstraint", cc)
    timing["sensor"] = round(time.time() - t0_, 1)

    cmd("New", "/ Facility Hangzhou")
    cmd("SetPosition", f"*/Facility/Hangzhou Geodetic {float(cfg['lat'])} {float(cfg['lon'])} 0.0")
    if cfg.get("min_elev"):
        cmd("SetConstraint", f"*/Facility/Hangzhou ElevationAngle Min {float(cfg['min_elev'])}")
    for cc in constraint_cmds(cfg.get("constraints")):
        cmd("SetConstraint", cc)
    cmd("Animate", "* Start")
    cmd("Animate", "* Reset")

    t0_ = time.time()
    cmd("CovMulti", "/ Objects */Facility/Hangzhou")
    for b in range(0, len(names), BS):
        lo, hi = b + 1, min(b + BS, len(names))
        assets = " ".join(f"*/Satellite/{names[i - 1]}/Sensor/S{i - 1}" for i in range(lo, hi + 1))
        cmd("CovMulti", f"/ Assets {assets}")
        cmd("CovMulti", f'/ Access Compute "{t0}" "{t1}"')
    timing["cov"] = round(time.time() - t0_, 1)
    r = C.atkConnect(con, "Cov_RM", f'*/Facility/Hangzhou Access Compute "Coverage" "{t0}" "{t1}"')
    text = decode_report(r if isinstance(r, str) else str(r))
    timing["report"] = round(time.time() - t0_, 1)
    result["windows"] = parse_coverage_report(text)
    result["timing"]["total"] = round(time.time() - t_all, 1)
    result["ok"] = True
    _finish(con, result, out, port)


def _finish(con, result, out, port):
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps({k: result[k] for k in (
        "port", "ok", "expected", "sats", "timing", "error")}, ensure_ascii=False), flush=True)
    kill_port_owner(port)


if __name__ == "__main__":
    main()
