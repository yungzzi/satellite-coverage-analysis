# -*- coding: utf-8 -*-
"""ATK 覆盖定义（Coverage Definition）区域覆盖率模块。

标准路线（实验记录 §10.17 / §10.18 / §10.20 定案）：
    Connect 建 CD（默认网格）→ Save → XML 注入 <Grid> 区域参数 → Load
    → Assign → 带引号 Compute → Report_RM 读
    "Grid Point Visible Assets"（真实网格点）与 "Percent Coverage"（分钟级区域覆盖率）。

用途：区域百分比覆盖率（与自研引擎对拍验证过的口径，§10.20）。
逐星覆盖窗口请继续走 Facility 点路线（atk_parallel/worker.py）。

注意事项（踩坑记录）：
- TLE 必须为纯两行格式（Connect ImportTLEFile 要求，§9.10；三行格式会触发崩溃）。
- 每个场景用独立子进程/独立端口；不要在同一 Python 进程连接第二个 ATK 场景。
- 不要调用 atkClose：本环境实测偶发堆损坏（0xC0000374），一律按端口杀进程树收尾。
- XML 注入用于绕开两环境不一致的 Cov Grid 命令。
"""

from __future__ import annotations

import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"
_ATK_PYMOD = os.path.join(ATK_ROOT, "IntegratingWithATK", "connect", "Python")
if _ATK_PYMOD not in sys.path:
    sys.path.insert(0, _ATK_PYMOD)

try:
    import ATKConnectModule as C
    from atk_parallel.headless import launch_headless
    from atk_parallel.report_parser import decode_report
    from atk_parallel.worker import XML_ROUTE_TYPES, patch_sensor_patterns
except ImportError:  # 仅文档/静态检查场景
    C = None
    launch_headless = None
    decode_report = None
    XML_ROUTE_TYPES = ("SAR", "HalfPower")
    patch_sensor_patterns = None


@dataclass
class AreaCoverageResult:
    """区域覆盖率结果。

    grid_points: [(lat, lon), ...] —— ATK 网格点可见目标报告读回的真实网格点；
    minutes: [datetime, ...] —— 每分钟时间戳（UTC）；
    coverage_pct: [float, ...] —— 与 minutes 对齐的区域覆盖率（0~100）；
    assets: [str, ...] —— 参与计算的传感器（如 "tle-68196U-S0"）；
    raw_grid_report: str —— 原始报告文本（留档）；
    raw_percent_report: str —— 原始报告文本（留档）。
    """

    grid_points: list = field(default_factory=list)
    minutes: list = field(default_factory=list)
    coverage_pct: list = field(default_factory=list)
    assets: list = field(default_factory=list)
    raw_grid_report: str = ""
    raw_percent_report: str = ""


def normalize_tle(tle_path: str, out_path: Optional[str] = None) -> str:
    """把 TLE 文件规整为纯两行格式（跳过名字行），返回输出路径（正斜杠）。"""
    if out_path is None:
        fd, out_path = tempfile.mkstemp(suffix=".tle", prefix="atk_cd_")
        os.close(fd)
    kept = []
    with open(tle_path, "r", encoding="utf-8-sig", errors="replace") as f:
        for ln in f:
            s = ln.strip()
            if not s:
                continue
            if re.match(r"^\s*[12]\s+\d{5}", s):
                kept.append(s)
    with open(out_path, "w", encoding="ascii") as f:
        f.write("\n".join(kept) + "\n")
    return out_path.replace("\\", "/")


def pick_port(base: int = 9400) -> int:
    for p in range(base, base + 300):
        s = socket.socket()
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
    raise RuntimeError("no free port")


def kill_port_owner(port: int) -> None:
    """按端口找到 ATK 进程树并强制结束（避免 atkClose 的堆损坏问题）。"""
    try:
        out = subprocess.run(
            ["netstat", "-ano"], capture_output=True, text=True, errors="replace"
        ).stdout
    except Exception:
        return
    for ln in out.splitlines():
        if f":{port}" in ln and "LISTENING" in ln:
            parts = ln.split()
            if parts:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", parts[-1]],
                        capture_output=True,
                    )
                except Exception:
                    pass
    time.sleep(1.0)


def _cmd(con, name: str, params: str) -> str:
    try:
        r = C.atkConnect(con, name, params)
        return decode_report(r) if isinstance(r, str) else str(r)
    except Exception as e:  # noqa: BLE001
        return f"EXC:{e}"


def _sat_names(con) -> list:
    r = _cmd(con, "AllInstanceNames", "/")
    return re.findall(r"/Satellite/([^/]+?)(?= /Scenario/|$)", r)


def _patch_grid_xml(xml_path: str, region, granularity_deg: float) -> str:
    """把场景 XML 中 <Grid> 改为区域网格，返回补丁后路径（正斜杠）。"""
    min_lat, min_lon, max_lat, max_lon = region
    with open(xml_path, "rb") as f:
        b = f.read()
    t = b.decode("gbk", errors="replace")
    repl = {
        r"<MinLat>-?\d+(\.\d+)?</MinLat>": f"<MinLat>{min_lat}</MinLat>",
        r"<MaxLat>-?\d+(\.\d+)?</MaxLat>": f"<MaxLat>{max_lat}</MaxLat>",
        r"<MinLon>-?\d+(\.\d+)?</MinLon>": f"<MinLon>{min_lon}</MinLon>",
        r"<MaxLon>-?\d+(\.\d+)?</MaxLon>": f"<MaxLon>{max_lon}</MaxLon>",
        r"<ValueGranularity>\d+(\.\d+)?</ValueGranularity>": (
            f"<ValueGranularity>{granularity_deg}</ValueGranularity>"
        ),
    }
    for pat, new in repl.items():
        t = re.sub(pat, new, t)
    fd, out_path = tempfile.mkstemp(suffix=".atk", prefix="atk_cd_patch_")
    os.close(fd)
    with open(out_path, "wb") as f:
        f.write(t.encode("gbk", errors="replace"))
    return out_path.replace("\\", "/")


def build_cd_xml(
    tle_path: str,
    t0: str,
    t1: str,
    region,
    granularity_deg: float,
    sensor_half_angle_deg: float = 45.0,
    cd_name: str = "CD1",
    sensor_type: str = "SimpleCone",
    sensor_params: Optional[list] = None,
) -> dict:
    """新建场景 → 导入 TLE → 每星加传感器 → 建 CD → 保存 → 注入区域网格。

    sensor_type 支持 SimpleCone / Rectangular / HalfPower / Conical / SAR
    （官方 Define 语法见 Help/传感器/Define.html）；SimpleCone 缺省用
    sensor_half_angle_deg，其余类型必须给 sensor_params。
    返回 {"xml": 补丁后路径, "sats": [..], "sensors": [..], "raw": 原场景路径}。
    """
    if C is None:
        raise RuntimeError("ATKConnectModule 不可用")
    if sensor_type != "SimpleCone" and not sensor_params:
        raise ValueError(f"传感器 {sensor_type} 需要 sensor_params")
    port = pick_port()
    kill_port_owner(port)
    launch_headless(port, new_scenario=True)
    con = C.atkOpen("127.0.0.1", port)
    tle2 = normalize_tle(tle_path)
    raw_xml = os.path.join(tempfile.gettempdir(), f"atk_cd_raw_{os.getpid()}.atk").replace("\\", "/")
    try:
        _cmd(con, "SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
        _cmd(con, "Animate", "* Reset")
        _cmd(con, "ImportTLEFile", f'* "{tle2}"')
        sats = _sat_names(con)
        if not sats:
            raise RuntimeError("TLE 导入后未找到卫星")
        sensors = []
        xml_route = sensor_type in XML_ROUTE_TYPES
        for i, sat in enumerate(sats):
            sname = f"S{i}"
            if xml_route:
                # 占位用 SimpleCone，Save 后再改 XML（§7.1：SAR/HalfPower 的 Define 静默失效）
                define_type, define_params = "SimpleCone", [sensor_half_angle_deg]
            else:
                define_type = sensor_type
                define_params = sensor_params if sensor_params is not None else [sensor_half_angle_deg]
            _cmd(con, "New", f'/ "*/Satellite/{sat}/Sensor" {sname}')
            _cmd(
                con,
                "Define",
                f'"*/Satellite/{sat}/Sensor/{sname}" {define_type} '
                + " ".join(str(x) for x in define_params),
            )
            sensors.append((sat, sname))
        _cmd(con, "New", f"/ */CoverageDefinition {cd_name}")
        _cmd(con, "Cov", f'*/CoverageDefinition/{cd_name} Interval "{t0}" "{t1}"')
        for sat, sname in sensors:
            _cmd(
                con,
                "Cov",
                f"*/CoverageDefinition/{cd_name} Asset */Satellite/{sat}/Sensor/{sname} Assign",
            )
        _cmd(con, "Animate", "* Start")
        _cmd(con, "Animate", "* Reset")
        _cmd(con, "Save", f'/ * "{raw_xml}"')
    finally:
        kill_port_owner(port)
    patched = _patch_grid_xml(raw_xml.replace("\\", "/"), region, granularity_deg)
    if xml_route and patch_sensor_patterns is not None:
        fd, patched2 = tempfile.mkstemp(suffix=".atk", prefix="atk_cd_patch_")
        os.close(fd)
        patched = patch_sensor_patterns(
            patched.replace("/", "\\"), sensor_type, sensor_params, patched2)
    return {
        "xml": patched,
        "raw": raw_xml,
        "sats": sats,
        "sensors": sensors,
    }


def parse_grid_points(text: str) -> list:
    pts = []
    for ln in text.splitlines():
        m = re.match(r"\s*\d+\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", ln)
        if m:
            pts.append((float(m.group(1)), float(m.group(2))))
    return pts


def parse_percent_coverage(text: str) -> tuple:
    """解析 Percent Coverage 报告，返回 (minutes, pct)。"""
    minutes, pct = [], []
    for ln in text.splitlines():
        m = re.match(
            r"\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)",
            ln,
        )
        if m:
            minutes.append(datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S.%f"))
            pct.append(float(m.group(3)))
    return minutes, pct


def read_cd_reports(xml_path: str, t0: str, t1: str, cd_name: str = "CD1") -> AreaCoverageResult:
    """Load 注入后的场景 → Assign → Compute → 读两份报告。"""
    if C is None:
        raise RuntimeError("ATKConnectModule 不可用")
    port = pick_port()
    kill_port_owner(port)
    launch_headless(port, new_scenario=False)
    con = C.atkOpen("127.0.0.1", port)
    res = AreaCoverageResult()
    try:
        _cmd(con, "Load", f'/ Scenario "{xml_path}"')
        _cmd(con, "SetAnalysisTimePeriod", f'* "{t0}" "{t1}"')
        _cmd(con, "Animate", "* Reset")
        sats = _sat_names(con)
        allnames = _cmd(con, "AllInstanceNames", "/")
        for sat in sats:
            for sname in re.findall(rf"/Satellite/{re.escape(sat)}/Sensor/([^/]+?)(?= /|$)", allnames):
                _cmd(
                    con,
                    "Cov",
                    f"*/CoverageDefinition/{cd_name} Asset */Satellite/{sat}/Sensor/{sname} Assign",
                )
        _cmd(con, "Cov", f'*/CoverageDefinition/{cd_name} Access Compute "{t0}" "{t1}"')
        grid_rep = _cmd(
            con,
            "Report_RM",
            f'*/CoverageDefinition/{cd_name} Style "Grid Point Visible Assets" TimePeriod "{t0}" "{t1}"',
        )
        pct_rep = _cmd(
            con,
            "Report_RM",
            f'*/CoverageDefinition/{cd_name} Style "Percent Coverage" TimePeriod "{t0}" "{t1}"',
        )
        res.raw_grid_report = grid_rep
        res.raw_percent_report = pct_rep
        res.grid_points = parse_grid_points(grid_rep)
        res.minutes, res.coverage_pct = parse_percent_coverage(pct_rep)
        m = re.search(r"目标对象：\s*\n((?:\s+[^\n]+\n?)*)", pct_rep)
        if m:
            res.assets = [a.strip() for a in m.group(1).splitlines() if a.strip()]
    finally:
        kill_port_owner(port)
    return res


def analyze_area_coverage(
    tle_path: str,
    region,
    granularity_deg: float,
    t0: str,
    t1: str,
    sensor_half_angle_deg: float = 45.0,
    cd_name: str = "CD1",
    sensor_type: str = "SimpleCone",
    sensor_params: Optional[list] = None,
) -> AreaCoverageResult:
    """一站式：建 CD（XML 注入区域网格）→ 计算 → 返回网格点与分钟覆盖率。"""
    built = build_cd_xml(
        tle_path, t0, t1, region, granularity_deg, sensor_half_angle_deg, cd_name,
        sensor_type, sensor_params,
    )
    return read_cd_reports(built["xml"], t0, t1, cd_name)
