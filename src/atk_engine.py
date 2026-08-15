# -*- coding: utf-8 -*-
"""ATK 覆盖引擎（v1）：用本机 ATK 替代自研几何引擎计算覆盖窗口（点/圆目标）。

口径说明（与自研引擎的差异，已对拍，见 docs/ATK引擎对拍报告.md）：
- 目标只支持 circle（点+半径）：ATK 用"圆心点地面站"判定覆盖，半径仅用于展示。
  自研是"足印圆∩目标圆"，ATK 是"圆心在足印内"，通常只差目标圆边缘的短过境。
- 视场半角：SimpleCone 逐星半角（sensor_params.toml 逐星 fov 优先，否则全局）；
  幅宽按该星 TLE 平均轨道高度换算成等效半角（自研是逐时刻换算，差异很小）。
- 最小仰角：按取值分组，每组一次 ATK 计算（地面站仰角约束，与自研口径一致）。
- 复杂传感器（Rectangular/HalfPower/Conical/SAR）：需显式传 sensor_type/sensor_params，
  逐星半角不参与（用统一参数）；SAR/HalfPower 自动走 XML 双实例路线。
- ATK 安装目录：config 的 [atk] root > 环境变量 ATK_ROOT > 默认路径。

依赖：src/atk_parallel/（收编自实验仓库，ATK 官方 Connect 模块）。
"""
from __future__ import annotations

import csv
import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC_DIR = Path(__file__).resolve().parent

MU_EARTH = 398600.4418   # km^3/s^2
R_EARTH = 6371.0         # km
DEFAULT_ATK_ROOT = r"D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5"


def resolve_atk_root(atk_root=None) -> str:
    """ATK 根目录：显式参数 > 环境变量 ATK_ROOT > 默认路径。"""
    return atk_root or os.environ.get("ATK_ROOT") or DEFAULT_ATK_ROOT


def check_atk(atk_root=None) -> str:
    root = resolve_atk_root(atk_root)
    exe = Path(root) / "ATK.exe"
    if not exe.exists():
        raise RuntimeError(
            f"未找到 ATK 程序：{exe}\n"
            "ATK 引擎需要本机安装并注册 ATK（国防科技大学航天任务工具箱）。\n"
            "可在 config.toml 的 [atk] 配置 root，或设置环境变量 ATK_ROOT。"
        )
    return root


def _tle_mean_altitude_km(rec) -> float:
    """由 TLE 平运动估算平均轨道高度（km）。"""
    try:
        _l1, l2 = _rec_tle_lines(rec)
        mm_day = float(str(l2)[52:63])
        n_rad_s = mm_day * 2.0 * math.pi / 86400.0
        a = (MU_EARTH / (n_rad_s * n_rad_s)) ** (1.0 / 3.0)
        return max(0.0, a - R_EARTH)
    except Exception:
        return 600.0


def _rec_tle_lines(rec):
    """兼容 dict（line1/line2）与对象（l1/l2）两种记录形态。"""
    if isinstance(rec, dict):
        return rec.get("line1") or rec.get("l1"), rec.get("line2") or rec.get("l2")
    return (getattr(rec, "l1", None) or getattr(rec, "line1", None),
            getattr(rec, "l2", None) or getattr(rec, "line2", None))


def _rec_get(rec, key):
    """兼容 dict 与对象两种记录形态。"""
    if isinstance(rec, dict):
        return rec.get(key) or rec.get({"norad": "norad_id"}.get(key, ""))
    return getattr(rec, key, None)


def _atk_time(s: str) -> str:
    """'2026-08-12 00:00:00' → ATK 时段格式 '12 Aug 2026 00:00:00.000'。"""
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y %H:%M:%S.000")


def _write_two_line_tle(recs, path: Path) -> None:
    with open(path, "w", encoding="ascii") as f:
        for rec in recs:
            l1, l2 = _rec_tle_lines(rec)
            f.write(str(l1).strip() + "\n" + str(l2).strip() + "\n")


def run_atk_windows(recs, region, cfg, start_str, end_str, progress=None,
                    sensor_overrides=None, swath_enabled=False,
                    sensor_type="SimpleCone", sensor_params=None,
                    atk_root=None) -> dict:
    """用 ATK 算覆盖窗口，返回与 analyze_collection_pipeline 同结构的 cov_result。

    输入输出与自研引擎完全对齐（windows_by_sat 与 recs 顺序一一对应，
    每项是 [(start_datetime, end_datetime, ratio=1.0), ...]）。
    """
    if region.get("mode") != "circle":
        raise ValueError("ATK 引擎 v1 只支持点+半径（circle）目标；多边形目标请用自研引擎。")
    import coverage_analysis as cov
    import satellite_loader as sl

    root = check_atk(atk_root)
    os.environ["ATK_ROOT"] = root

    sats = sl.records_to_satellites(recs)
    # 逐星有效参数：与自研引擎同一套解析（逐星表 > 全局默认；幅宽/GEO 回退同规则）
    fovs, elevs = [], []
    sat_params, sensor_warnings = [], []
    for rec, sat in zip(recs, sats):
        norad = int(sat.model.satnum) if getattr(sat, "model", None) else int(_rec_get(rec, "norad"))
        sat_cfg, info = cov.resolve_sat_cfg(norad, sensor_overrides, cfg,
                                            swath_enabled=bool(swath_enabled))
        tag = cov.orbit_tag(sat)
        if sat_cfg.get("swath_km") and tag in ("GEO/静止", "高轨"):
            fov_back = sat_cfg.get("fov_half_angle_deg")
            sat_cfg.pop("swath_km", None)
            info["swath_km"] = 0.0
            info["fov_half_angle_deg"] = fov_back
            sensor_warnings.append(
                f"{sat.name}（NORAD {norad}，{tag}）：填了幅宽但高轨/静止卫星"
                f"没有条带成像概念，已改按视场半角 {fov_back}° 计算")
        if sat_cfg.get("swath_km"):
            alt = _tle_mean_altitude_km(rec)
            fovs.append(round(float(cov.swath_to_half_angle_deg(alt, sat_cfg["swath_km"])), 6))
        else:
            fovs.append(float(sat_cfg["fov_half_angle_deg"]))
        elevs.append(float(sat_cfg["min_elevation_deg"]))
        sat_params.append({"name": sat.name, **info})

    lat, lon = float(region["lat"]), float(region["lon"])
    step = int(cfg.get("step_minutes", 1))
    t0 = datetime.strptime(start_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    t_end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    dt_list = []
    t = t0
    while t < t_end:
        dt_list.append(t)
        t += timedelta(minutes=step)
    if not dt_list:
        dt_list = [t0]
    t1_true = dt_list[-1] + timedelta(minutes=step)   # 左闭右开，与自研一致
    t0_atk = _atk_time(dt_list[0].strftime("%Y-%m-%d %H:%M:%S"))
    t1_atk = _atk_time(t1_true.strftime("%Y-%m-%d %H:%M:%S"))

    # 按最小仰角分组，每组一次 ATK 计算
    groups = {}
    for i, e in enumerate(elevs):
        groups.setdefault(e, []).append(i)

    tmp = Path(tempfile.mkdtemp(prefix="atk_engine_"))
    windows_by_norad = {}
    try:
        for gi, (elev, idxs) in enumerate(sorted(groups.items())):
            group_recs = [recs[i] for i in idxs]
            tle_path = tmp / f"g{gi}.tle"
            angles_path = tmp / f"g{gi}_angles.json"
            out_dir = tmp / f"g{gi}_out"
            _write_two_line_tle(group_recs, tle_path)
            with open(angles_path, "w", encoding="utf-8") as f:
                json.dump([fovs[i] for i in idxs], f)
            if progress:
                progress((gi + 1) / (len(groups) + 1),
                         f"ATK 计算中（仰角 {elev}° 组 {gi + 1}/{len(groups)}）")
            cmd = [
                sys.executable, "-m", "atk_parallel.runner",
                "--tle", str(tle_path),
                "--t0", t0_atk, "--t1", t1_atk,
                "--lat", str(lat), "--lon", str(lon),
                "--fov", str(float(cfg.get("fov_half_angle_deg", 10.0))),
                "--min-elev", str(elev),
                "--sensor-type", sensor_type,
                "--angles-file", str(angles_path),
                "--per-shard", str(max(1000, len(group_recs))),
                "--max-inst", "1",
                "--out-dir", str(out_dir),
            ]
            if sensor_params is not None:
                cmd += ["--sensor-params", ",".join(str(x) for x in sensor_params)]
            p = subprocess.run(cmd, cwd=str(SRC_DIR), capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=3600)
            if p.returncode != 0:
                raise RuntimeError(
                    f"ATK 引擎运行失败（仰角 {elev}° 组）：\n"
                    f"{p.stdout[-1200:]}\n{p.stderr[-1200:]}")
            merged = out_dir / "merged_windows.csv"
            if not merged.exists():
                raise RuntimeError(f"ATK 引擎未产出窗口文件：{merged}")
            with open(merged, encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    try:
                        norad = int(row["norad"])
                    except (TypeError, ValueError):
                        continue
                    windows_by_norad.setdefault(norad, []).append((
                        datetime.strptime(str(row["start_utc"])[:19], "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=timezone.utc),
                        datetime.strptime(str(row["end_utc"])[:19], "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=timezone.utc),
                        1.0,
                    ))
    finally:
        pass  # 临时目录保留便于排查（系统 TEMP，不污染工程）

    windows_by_sat, all_windows = [], []
    for rec, sat in zip(recs, sats):
        norad = int(sat.model.satnum) if getattr(sat, "model", None) else int(_rec_get(rec, "norad"))
        ws = sorted(windows_by_norad.get(norad, []), key=lambda w: w[0])
        windows_by_sat.append(ws)
        all_windows.extend((sat.name, w[0], w[1], w[2]) for w in ws)

    if not all_windows:
        return None
    dfs = cov.build_outputs(all_windows, cfg, t0, t1_true, len(sats),
                            sat_params=sat_params)
    return {
        "dfs": dfs, "cfg": cfg, "n_sats": len(sats), "n_win": len(all_windows),
        "windows_by_sat": windows_by_sat, "sats": sats,
        "sat_params": sat_params, "sensor_warnings": sensor_warnings,
        "t0": t0, "t1": t1_true, "engine": "atk",
    }
