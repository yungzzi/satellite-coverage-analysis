# -*- coding: utf-8 -*-
"""区域网格的逐星覆盖机会计算。

本模块是 SIRIAgent 区域流程的第二阶段底座：

* 输入已经生成好的 ``RegionGrid`` 和卫星对象；
* 对每颗卫星只传播一次时间轴，再按网格块计算逐点判定；
* 输出 ``卫星 × 网格单元`` 的稀疏覆盖窗口，而不是把空单元也存下来。

这里故意不调用旧的 ``analyze_one_satellite``：旧入口会把多边形压成
``any(axis=1)``，一旦压缩就无法支持后续区域指标和选星。本模块只新增区域旁路，
不改变旧的单点、点+半径和 GUI 路径。

覆盖窗口采用半开区间 ``[start_utc, end_utc)``。检测时间步长默认沿用旧引擎的
``coarse_step_seconds=10``；窗口边界默认先保持检测步长精度，需要时可通过
``refine_step_seconds=1`` 对已有窗口两侧做按需二分精化。这样不会为每个网格单元重复
扫描整段轨道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
from pyproj import CRS, Transformer
from skyfield.api import load

import coverage_analysis as cov
import sensor_geometry as sg
from region_grid import RegionGrid


@dataclass(frozen=True)
class AccessWindow:
    """一颗卫星对一个网格单元的一段连续覆盖机会。"""

    sat_index: int
    sat_id: int
    sat_name: str
    grid_index: int
    grid_id: str
    start_utc: datetime
    end_utc: datetime
    duration_s: float


@dataclass
class OpportunityStore:
    """区域机会结果的稀疏容器。

    ``windows`` 的 key 是 ``(sat_index, grid_index)``，只保存至少有一个窗口的组合。
    空组合不写入，因此卫星很多、城市网格较细时不会产生大块的全零矩阵。
    """

    satellites: tuple[Any, ...]
    grid: RegionGrid
    start_utc: datetime
    end_utc: datetime
    detection_step_s: int
    decision_step_s: int
    windows: dict[tuple[int, int], tuple[AccessWindow, ...]] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def n_satellites(self) -> int:
        return len(self.satellites)

    @property
    def n_cells(self) -> int:
        return len(self.grid.cells)

    @property
    def n_nonempty_pairs(self) -> int:
        return len(self.windows)

    @property
    def n_windows(self) -> int:
        return sum(len(items) for items in self.windows.values())

    def windows_for(self, sat_index: int, grid_index: int) -> tuple[AccessWindow, ...]:
        """读取一个卫星—网格组合的窗口；没有机会时返回空元组。"""

        return self.windows.get((int(sat_index), int(grid_index)), ())

    def iter_windows(self) -> Iterable[AccessWindow]:
        """按卫星、网格索引稳定遍历窗口。"""

        for key in sorted(self.windows):
            yield from self.windows[key]

    def to_rows(self) -> list[dict[str, Any]]:
        """转换为调试/导出用扁平记录；正常 skill 不必把逐网格表展示给用户。"""

        return [
            {
                "sat_index": w.sat_index,
                "sat_id": w.sat_id,
                "satellite": w.sat_name,
                "grid_index": w.grid_index,
                "grid_id": w.grid_id,
                "start_utc": w.start_utc.isoformat(),
                "end_utc": w.end_utc.isoformat(),
                "duration_s": w.duration_s,
            }
            for w in self.iter_windows()
        ]


def _as_utc(value: str | datetime) -> datetime:
    """解析 ISO/旧项目格式时间，并统一为带 UTC 时区的 datetime。"""

    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            result = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _half_open_times(start: datetime, end: datetime, step_s: int) -> list[datetime]:
    """生成 ``[start, end)`` 时间轴，避免把分析终点误算成一段覆盖。"""

    if step_s <= 0:
        raise ValueError("检测时间步长必须大于 0 秒。")
    if end <= start:
        raise ValueError("分析结束时间必须晚于开始时间。")
    values: list[datetime] = []
    current = start
    delta = timedelta(seconds=step_s)
    while current < end:
        values.append(current)
        current += delta
    return values


def _sat_id(sat: Any) -> int:
    """兼容 satellite_loader.Satellite 和原生 EarthSatellite。"""

    value = getattr(sat, "norad_id", None)
    if value is None:
        model = getattr(sat, "model", None)
        value = getattr(model, "satnum", None)
    if value is None:
        raise ValueError("卫星对象缺少 NORAD 编号（norad_id/model.satnum）。")
    return int(value)


def _sat_name(sat: Any) -> str:
    name = getattr(sat, "name", None)
    if name:
        return str(name)
    orbit = getattr(sat, "orbit", None)
    return str(getattr(orbit, "name", "") or "")


def _effective_satellite_cfg(cfg: Mapping[str, Any], sat_id: int) -> dict[str, Any]:
    """把全局 Profile 与指定 NORAD 的覆盖覆盖合并。

    区域机会计算原来整批卫星共用一份 ``cfg``。高级 Skill 允许某颗卫星
    使用自己的传感器/姿态，因此在进入几何热路径前做一次浅合并；轨道、网格
    和时间轴仍然完全复用，不会把计算退化成另一套流程。
    """

    effective = dict(cfg)
    overrides = effective.pop("sat_sensor_overrides", None) or []
    for item in overrides:
        if not isinstance(item, Mapping):
            continue
        raw_id = item.get("norad", item.get("norad_id"))
        try:
            item_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if item_id != int(sat_id):
            continue
        for key in (
            "sensor_type",
            "sensor_params",
            "min_elevation_deg",
            "pointing",
            "attitude",
            "constraints",
        ):
            if key in item:
                effective[key] = item[key]
        break
    return effective


def _is_targeted(cfg: Mapping[str, Any]) -> bool:
    """判断某份逐星配置是否采用区域 Targeted 指向。"""

    pointing = cfg.get("pointing") or {}
    return str(pointing.get("type") or "").lower() == "targeted"


def _targeted_grid_centroid(grid: RegionGrid) -> dict[str, Any]:
    """计算 Targeted 区域口径使用的面积加权网格代表点形心。

    形心在与网格相同的等面积米制投影中计算，再反投影到 WGS84，并按自研几何
    使用的球面地球半径转成 ECEF。这样不会直接平均经纬度，也不会把 5 km
    网格单元误当成等面积；每个代表点的权重就是其裁剪单元面积。
    """

    if not grid.cells:
        raise ValueError("RegionGrid 为空，无法确定 Targeted 目标。")
    areas = np.asarray([cell.area_m2 for cell in grid.cells], dtype=float)
    total_area = float(np.sum(areas))
    if not np.isfinite(total_area) or total_area <= 0.0:
        raise ValueError("RegionGrid 面积权重无效，无法确定 Targeted 目标。")
    xs = np.asarray([cell.representative_x_m for cell in grid.cells], dtype=float)
    ys = np.asarray([cell.representative_y_m for cell in grid.cells], dtype=float)
    x_centroid = float(np.sum(areas * xs) / total_area)
    y_centroid = float(np.sum(areas * ys) / total_area)
    to_wgs84 = Transformer.from_crs(
        CRS.from_user_input(grid.projected_crs),
        CRS.from_user_input("EPSG:4326"),
        always_xy=True,
    )
    lon, lat = to_wgs84.transform(x_centroid, y_centroid)
    lat_rad = np.radians(float(lat))
    lon_rad = np.radians(float(lon))
    target_ecef = sg.R_EARTH_KM * np.asarray(
        [
            np.cos(lat_rad) * np.cos(lon_rad),
            np.cos(lat_rad) * np.sin(lon_rad),
            np.sin(lat_rad),
        ],
        dtype=float,
    )
    return {
        "definition": "area_weighted_representative_point_centroid",
        "latitude_deg": float(lat),
        "longitude_deg": float(lon),
        "projected_x_m": x_centroid,
        "projected_y_m": y_centroid,
        "ecef_km": [float(value) for value in target_ecef],
        "weight_area_m2": total_area,
    }


def _grid_arrays(grid: RegionGrid) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """取出几何热路径使用的代表点经纬度、面积和编号数组。"""

    cells = grid.cells
    if not cells:
        raise ValueError("RegionGrid 为空，无法计算覆盖机会。")
    lats = np.asarray([cell.representative_lat for cell in cells], dtype=float)
    lons = np.asarray([cell.representative_lon for cell in cells], dtype=float)
    areas = np.asarray([cell.area_m2 for cell in cells], dtype=float)
    return lats, lons, areas


def _is_simple_nadir(cfg: Mapping[str, Any]) -> bool:
    """判断能否走已验证的覆盖圆路径。"""

    sensor_type = cfg.get("sensor_type") or "SimpleCone"
    return (
        sensor_type == "SimpleCone"
        and not cfg.get("pointing")
        and not cfg.get("attitude")
        and not cfg.get("constraints")
    )


def _prepare_ecef_geometry(
    sat: Any,
    t: Any,
    dt_list: Sequence[datetime],
    cfg: Mapping[str, Any],
    *,
    targeted: np.ndarray | None = None,
) -> dict[str, Any]:
    """为复杂传感器路径准备一次卫星轨道、姿态和统一视轴。"""

    jd = np.asarray(t.ut1, dtype=float)
    jdi = np.floor(jd)
    jdf = jd - jdi
    err, r_teme, v_teme = sat.model.sgp4_array(jdi, jdf)
    if np.any(err):
        raise RuntimeError(f"SGP4 传播失败（NORAD {_sat_id(sat)}）：错误码 {err}")
    gmst = np.radians(np.asarray(t.gmst, dtype=float) * 15.0)
    r = sg.rot_z_array(gmst, r_teme)
    v_in = sg.rot_z_array(gmst, v_teme)
    v = v_in - sg.cross_omega(r)
    dt_sec = np.asarray(
        [(item - dt_list[0]).total_seconds() for item in dt_list], dtype=float
    )

    attitude = cfg.get("attitude") or {}
    profile = int(attitude.get("profile", 0))
    sun = None
    if profile == 4:
        sun = sg.rot_z_array(gmst, sg.sun_vector_eci(jd))
    C = sg.body_dcm(
        r,
        v,
        v_in,
        sun=sun,
        profile=profile,
        offset=attitude.get("offset"),
    )

    pointing = cfg.get("pointing") or {}
    if _is_targeted(cfg) and targeted is None:
        raise ValueError("区域 Targeted 指向缺少自动生成的网格形心目标。")
    b = sg.pointing_boresight_ecef(
        cfg.get("pointing"),
        r,
        v,
        C,
        target=targeted,
        dt_sec=dt_sec,
        jd=jd,
    )
    axes = sg.sensor_frame_axes(b, v)
    return {
        "r": r,
        "v": v,
        "b": b,
        "axes": axes,
        "dt_sec": dt_sec,
        "jd": jd,
        "gmst": gmst,
        "sun": sun,
    }


def _complex_pattern_grid(
    u_s: np.ndarray,
    eps_ground: np.ndarray,
    u: np.ndarray,
    v: np.ndarray,
    cfg: Mapping[str, Any],
) -> np.ndarray:
    """把 sensor_geometry.pattern_covered 的不等式扩展到 ``(T, N)``。"""

    sensor_type = cfg.get("sensor_type") or "SimpleCone"
    params = cfg.get("sensor_params") or []
    z = u_s[:, :, 2]
    x = u_s[:, :, 0]
    y = u_s[:, :, 1]
    if sensor_type == "SimpleCone":
        eta = float(params[0]) if params else 45.0
        ok = z >= np.cos(np.radians(eta))
    elif sensor_type == "Rectangular":
        vertical = float(params[0]) if params else 40.0
        horizontal = float(params[1]) if len(params) > 1 else 60.0
        ok = (
            (z > 0.0)
            & (np.abs(np.degrees(np.arctan2(y, z))) <= vertical)
            & (np.abs(np.degrees(np.arctan2(x, z))) <= horizontal)
        )
    elif sensor_type == "Conical":
        inner = float(params[0]) if params else 0.0
        outer = float(params[1]) if len(params) > 1 else 45.0
        cmin = float(params[2]) if len(params) > 2 else 0.0
        cmax = float(params[3]) if len(params) > 3 else 360.0
        theta = np.degrees(np.arccos(np.clip(z, -1.0, 1.0)))
        clock = np.degrees(np.arctan2(x, y)) % 360.0
        ok = (theta >= inner) & (theta <= outer) & (clock >= cmin) & (clock <= cmax)
    elif sensor_type == "HalfPower":
        freq_ghz = float(params[0]) if params else 14.5
        diameter_m = float(params[1]) if len(params) > 1 else 1.0
        wavelength_m = sg.C_LIGHT / (freq_ghz * 1e9) if freq_ghz > 0 else 0.0
        eta = 70.0 * wavelength_m / diameter_m / 2.0 if diameter_m > 0 else 45.0
        ok = z >= np.cos(np.radians(eta))
    elif sensor_type == "SAR":
        emin = float(params[0]) if params else 12.0
        emax = float(params[1]) if len(params) > 1 else 70.0
        fwd = float(params[2]) if len(params) > 2 else 30.0
        aft = float(params[3]) if len(params) > 3 else 30.0
        ok = (eps_ground >= emin) & (eps_ground <= emax)
        vhat = sg.normalize(v)
        ang_f = np.degrees(
            np.arccos(np.clip(np.sum(u * vhat[:, None, :], axis=2), -1.0, 1.0))
        )
        ang_a = np.degrees(
            np.arccos(np.clip(np.sum(u * (-vhat[:, None, :]), axis=2), -1.0, 1.0))
        )
        ok &= (ang_f >= fwd) & (ang_a >= aft)
    else:
        raise ValueError(f"未知传感器类型：{sensor_type}")

    # 复杂引擎的共同物理基线：目标不能在地平线以下。
    ok &= eps_ground >= 0.0
    min_elev = float(cfg.get("min_elevation_deg", 0.0) or 0.0)
    if sensor_type != "SAR" and min_elev > 0.0:
        ok &= eps_ground >= min_elev
    return ok


def _complex_state_for_points(
    prepared: Mapping[str, Any],
    lats: np.ndarray,
    lons: np.ndarray,
    cfg: Mapping[str, Any],
) -> np.ndarray:
    """对一个网格块返回 ``(时间, 网格点)`` 布尔覆盖状态。"""

    r = prepared["r"]
    v = prepared["v"]
    axes = prepared["axes"]
    T = len(r)
    lat_r = np.radians(lats)
    lon_r = np.radians(lons)
    points = sg.R_EARTH_KM * np.stack(
        [np.cos(lat_r) * np.cos(lon_r), np.cos(lat_r) * np.sin(lon_r), np.sin(lat_r)],
        axis=1,
    )
    point_up = sg.normalize(points)
    # u 是卫星指向地面点；一次广播处理 T×N×3，内存只受当前块大小控制。
    u = sg.normalize(points[None, :, :] - r[:, None, :])
    u_s = np.einsum("tij,tnj->tni", np.swapaxes(axes, 1, 2), u)
    # u 是“卫星→目标”，而仰角定义使用“地面点→卫星”；方向相反，必须取 -u。
    # 这与 sensor_geometry.elevation_of_ground(P, r) 的方向约定保持一致。
    eps_ground = np.degrees(
        np.arcsin(np.clip(np.sum((-u) * point_up[None, :, :], axis=2), -1.0, 1.0))
    )
    covered = _complex_pattern_grid(u_s, eps_ground, u, v, cfg)

    # 约束暂按网格点循环，避免改变已经验证的 check_constraints 语义；
    # 视场本身仍是整块向量化，后续可针对约束类型再做专项向量化。
    constraints = cfg.get("constraints") or []
    if constraints:
        sun = prepared["sun"]
        if sun is None:
            sun = sg.rot_z_array(prepared["gmst"], sg.sun_vector_eci(prepared["jd"]))
        moon = sg.rot_z_array(prepared["gmst"], sg.moon_vector_eci(prepared["jd"]))
        for index, point in enumerate(points):
            covered[:, index] &= sg.check_constraints(
                constraints,
                point,
                r,
                v,
                prepared["b"],
                u[:, index, :],
                sun,
                moon,
                prepared["dt_sec"],
            )
    return covered


def _simple_state_for_points(
    sat: Any,
    t: Any,
    lats: np.ndarray,
    lons: np.ndarray,
    cfg: Mapping[str, Any],
    *,
    prepared: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """沿用旧 SimpleCone 地心角+仰角判定，但保留每个网格列。"""

    if prepared is None:
        geocentric = sat.at(t)
        sub = geocentric.subpoint()
        sat_lat = np.asarray(sub.latitude.degrees, dtype=float)
        sat_lon = np.asarray(sub.longitude.degrees, dtype=float)
        altitude = np.asarray(sub.elevation.km, dtype=float)
        prepared = {"sat_lat": sat_lat, "sat_lon": sat_lon, "altitude": altitude}
    sat_lat = prepared["sat_lat"]
    sat_lon = prepared["sat_lon"]
    altitude = prepared["altitude"]
    lam = cov.central_angle_deg(
        sat_lat[:, None], sat_lon[:, None], lats[None, :], lons[None, :]
    )
    # SimpleCone + 天底仍沿用旧引擎口径；若调用方用 sensor_params[0] 指定半角，
    # 语义要和 sensor_geometry 的退化路径一致，不能悄悄退回全局半角。
    sensor_params = cfg.get("sensor_params") or []
    fov_half_angle = float(
        sensor_params[0]
        if sensor_params and sensor_params[0] is not None
        else cfg.get("fov_half_angle_deg", 10.0)
    )
    swath_km = float(cfg.get("swath_km") or 0.0)
    if swath_km > 0:
        eta = cov.swath_to_half_angle_deg(altitude, swath_km)
        lam_max = cov.footprint_max_angle_deg(altitude, eta)
    else:
        lam_max = cov.footprint_max_angle_deg(
            altitude, fov_half_angle
        )
    elev = cov.elevation_at_deg(lam, altitude[:, None])
    return (lam <= lam_max[:, None]) & (
        elev >= float(cfg.get("min_elevation_deg", 0.0))
    )


def _prepare_simple_geometry(sat: Any, t: Any) -> dict[str, np.ndarray]:
    """一次取得旧覆盖圆路径所需的星下点和高度。"""

    sub = sat.at(t).subpoint()
    return {
        "sat_lat": np.asarray(sub.latitude.degrees, dtype=float),
        "sat_lon": np.asarray(sub.longitude.degrees, dtype=float),
        "altitude": np.asarray(sub.elevation.km, dtype=float),
    }


def compute_satellite_grid_state(
    sat: Any,
    grid: RegionGrid,
    t: Any,
    dt_list: Sequence[datetime],
    cfg: Mapping[str, Any] | None = None,
    *,
    cell_start: int = 0,
    cell_stop: int | None = None,
    _prepared: Mapping[str, Any] | None = None,
) -> np.ndarray:
    """计算一颗卫星对网格块的逐时刻状态。

    返回布尔数组 ``(T, N_block)``；每一列只对应一个网格单元代表点。
    ``cell_start/cell_stop`` 用于内存分块，默认处理整个区域。
    """

    cfg = dict(cfg or {})
    lats, lons, _ = _grid_arrays(grid)
    start = int(cell_start)
    stop = len(lats) if cell_stop is None else int(cell_stop)
    if not (0 <= start < stop <= len(lats)):
        raise ValueError("网格块范围不合法。")
    if not dt_list:
        raise ValueError("时间轴不能为空。")
    if _is_simple_nadir(cfg):
        prepared = _prepared or _prepare_simple_geometry(sat, t)
        return _simple_state_for_points(
            sat, t, lats[start:stop], lons[start:stop], cfg, prepared=prepared
        )
    if _prepared is not None:
        prepared = _prepared
    else:
        target_ecef = (
            np.asarray(_targeted_grid_centroid(grid)["ecef_km"], dtype=float)
            if _is_targeted(cfg)
            else None
        )
        prepared = _prepare_ecef_geometry(
            sat,
            t,
            dt_list,
            cfg,
            targeted=target_ecef,
        )
    return _complex_state_for_points(
        prepared, lats[start:stop], lons[start:stop], cfg
    )


def _windows_from_state(
    states: np.ndarray,
    dt_list: Sequence[datetime],
    end_utc: datetime,
    *,
    sat_index: int,
    sat_id: int,
    sat_name: str,
    grid: RegionGrid,
    cell_start: int,
) -> dict[tuple[int, int], tuple[AccessWindow, ...]]:
    """把一个时间×网格块布尔矩阵压缩成稀疏窗口。"""

    result: dict[tuple[int, int], tuple[AccessWindow, ...]] = {}
    for local_index in range(states.shape[1]):
        active = np.flatnonzero(states[:, local_index])
        if len(active) == 0:
            continue
        windows: list[AccessWindow] = []
        seg_start = int(active[0])
        previous = int(active[0])
        for current_raw in active[1:]:
            current = int(current_raw)
            if current != previous + 1:
                windows.append(
                    _make_window(
                        sat_index,
                        sat_id,
                        sat_name,
                        cell_start + local_index,
                        grid,
                        dt_list[seg_start],
                        dt_list[previous + 1] if previous + 1 < len(dt_list) else end_utc,
                    )
                )
                seg_start = current
            previous = current
        windows.append(
            _make_window(
                sat_index,
                sat_id,
                sat_name,
                cell_start + local_index,
                grid,
                dt_list[seg_start],
                dt_list[previous + 1] if previous + 1 < len(dt_list) else end_utc,
            )
        )
        if windows:
            result[(sat_index, cell_start + local_index)] = tuple(windows)
    return result


def _make_window(
    sat_index: int,
    sat_id: int,
    sat_name: str,
    grid_index: int,
    grid: RegionGrid,
    start: datetime,
    end: datetime,
) -> AccessWindow:
    duration = max(0.0, (end - start).total_seconds())
    return AccessWindow(
        sat_index=sat_index,
        sat_id=sat_id,
        sat_name=sat_name,
        grid_index=grid_index,
        grid_id=grid.cells[grid_index].grid_id,
        start_utc=start,
        end_utc=end,
        duration_s=duration,
    )


def _refine_transition(
    state_at: Callable[[datetime], bool],
    lower: datetime,
    upper: datetime,
    *,
    entering: bool,
    tolerance_s: float,
) -> datetime | None:
    """用二分法把一次 0↔1 转换定位到 ``tolerance_s`` 秒以内。

    ``entering=True`` 时要求 ``lower`` 为未覆盖、``upper`` 为覆盖，返回已知的
    覆盖侧；退出转换则相反，返回已知的覆盖侧。这样输出窗口不会因为二分误差
    被人为扩大。若两端状态不是预期的转换，返回 ``None``，让调用方保留原窗口。
    """

    if upper <= lower or tolerance_s <= 0.0:
        return None
    lower_state = bool(state_at(lower))
    upper_state = bool(state_at(upper))
    if entering:
        if lower_state or not upper_state:
            return None
    elif not lower_state or upper_state:
        return None

    while (upper - lower).total_seconds() > tolerance_s:
        middle = lower + (upper - lower) / 2
        middle_state = bool(state_at(middle))
        if entering:
            if middle_state:
                upper = middle
            else:
                lower = middle
        elif middle_state:
            lower = middle
        else:
            upper = middle
    return upper if entering else lower


def refine_regional_opportunity_windows(
    store: OpportunityStore,
    *,
    refine_step_seconds: float = 1.0,
    timescale: Any | None = None,
    cfg: Mapping[str, Any] | None = None,
) -> OpportunityStore:
    """对已有区域窗口的起止边界做按需二分精化。

    粗检测仍由 ``store.detection_step_s`` 决定；本函数只在粗窗口两侧重新计算
    单个卫星—网格代表点，不重新扫描整座城市。进入边界取“已覆盖侧”，退出边界
    取“仍覆盖侧”，因此误差不超过 ``refine_step_seconds`` 且不会凭空扩大覆盖。
    ``refine_step_seconds=0`` 直接返回原对象；已精化的对象不会重复计算。
    """

    tolerance_s = float(refine_step_seconds)
    if tolerance_s < 0.0:
        raise ValueError("refine_step_seconds 不能为负数。")
    if tolerance_s == 0.0 or not store.windows:
        return store
    if store.metadata.get("windows_refined"):
        return store
    if store.detection_step_s <= 0:
        raise ValueError("OpportunityStore.detection_step_s 必须大于 0。")
    if timescale is None:
        timescale = load.timescale(builtin=True)
    base_cfg = dict(cfg or store.metadata.get("config") or {})
    target_info = _targeted_grid_centroid(store.grid) if _is_targeted(base_cfg) or any(
        _is_targeted(item) for item in (base_cfg.get("sat_sensor_overrides") or [])
    ) else None
    target_ecef = (
        np.asarray(target_info["ecef_km"], dtype=float) if target_info is not None else None
    )
    detection_delta = timedelta(seconds=store.detection_step_s)
    state_cache: dict[tuple[int, int, datetime], bool] = {}

    def state_at(sat_index: int, grid_index: int, when: datetime) -> bool:
        key = (int(sat_index), int(grid_index), when)
        if key in state_cache:
            return state_cache[key]
        sat = store.satellites[sat_index]
        effective_cfg = _effective_satellite_cfg(base_cfg, _sat_id(sat))
        dt_list = [when]
        t = timescale.from_datetimes(dt_list)
        prepared = (
            _prepare_simple_geometry(sat, t)
            if _is_simple_nadir(effective_cfg)
            else _prepare_ecef_geometry(
                sat,
                t,
                dt_list,
                effective_cfg,
                targeted=target_ecef if _is_targeted(effective_cfg) else None,
            )
        )
        state = compute_satellite_grid_state(
            sat,
            store.grid,
            t,
            dt_list,
            effective_cfg,
            cell_start=grid_index,
            cell_stop=grid_index + 1,
            _prepared=prepared,
        )
        value = bool(state[0, 0])
        state_cache[key] = value
        return value

    refined_windows: dict[tuple[int, int], tuple[AccessWindow, ...]] = {}
    for (sat_index, grid_index), windows in sorted(store.windows.items()):
        refined_for_pair: list[AccessWindow] = []
        for window in windows:
            start = window.start_utc
            end = window.end_utc
            # 粗窗口若从分析起点开始，边界本来就已知，无需额外计算。
            if start > store.start_utc:
                lower = max(store.start_utc, start - detection_delta)
                refined_start = _refine_transition(
                    lambda when, si=sat_index, gi=grid_index: state_at(si, gi, when),
                    lower,
                    start,
                    entering=True,
                    tolerance_s=tolerance_s,
                )
                if refined_start is not None:
                    start = refined_start
            # 以分析终点结束的窗口同理不需要外推。
            if end < store.end_utc:
                upper = min(store.end_utc, end + detection_delta)
                refined_end = _refine_transition(
                    lambda when, si=sat_index, gi=grid_index: state_at(si, gi, when),
                    end - detection_delta,
                    upper,
                    entering=False,
                    tolerance_s=tolerance_s,
                )
                if refined_end is not None:
                    end = refined_end
            if end > start:
                refined_for_pair.append(
                    _make_window(
                        sat_index,
                        window.sat_id,
                        window.sat_name,
                        grid_index,
                        store.grid,
                        start,
                        end,
                    )
                )
        if refined_for_pair:
            refined_windows[(sat_index, grid_index)] = tuple(refined_for_pair)

    metadata = dict(store.metadata)
    metadata.update(
        {
            "windows_refined": True,
            "window_precision_s": tolerance_s,
            "window_refinement": "binary_transition",
        }
    )
    return OpportunityStore(
        satellites=store.satellites,
        grid=store.grid,
        start_utc=store.start_utc,
        end_utc=store.end_utc,
        detection_step_s=store.detection_step_s,
        decision_step_s=store.decision_step_s,
        windows=refined_windows,
        metadata=metadata,
    )


def compute_regional_opportunities(
    satellites: Sequence[Any],
    grid: RegionGrid,
    start_utc: str | datetime,
    end_utc: str | datetime,
    cfg: Mapping[str, Any] | None = None,
    *,
    decision_step_s: int = 60,
    cell_chunk_size: int = 256,
    progress: Callable[[float, str], None] | None = None,
    timescale: Any | None = None,
) -> OpportunityStore:
    """计算整个城市的稀疏区域覆盖机会。

    参数
    ----
    satellites:
        ``satellite_loader.records_to_satellites`` 返回的对象，或原生 Skyfield
        ``EarthSatellite`` 列表。
    grid:
        ``region_grid.build_clipped_grid`` 的结果。
    start_utc/end_utc:
        支持 ``2026-08-30 00:00:00`` 和 ISO ``...Z``。
    cfg:
        覆盖参数。默认从旧引擎口径取 ``fov_half_angle_deg=10``、
        ``min_elevation_deg=10``、``coarse_step_seconds=10``；复杂 Profile 字段
        （sensor_type、sensor_params、pointing、attitude、constraints）可直接传入；
        也可用 ``sat_sensor_overrides`` 按 ``norad`` 给单颗卫星覆盖这些字段；
        可选 ``refine_step_seconds=1`` 启用窗口边界精化。
    decision_step_s:
        记录给后续选星的时间片粒度；本阶段窗口检测使用 cfg 的粗检测步长。
    cell_chunk_size:
        每次进入几何热路径的网格数，防止大城市一次分配过大的 T×N×3 数组。
    """

    cfg = dict(cfg or {})
    start = _as_utc(start_utc)
    end = _as_utc(end_utc)
    detection_step_s = int(cfg.get("regional_detection_step_seconds", cfg.get("coarse_step_seconds", 10)))
    if detection_step_s <= 0 or decision_step_s <= 0:
        raise ValueError("检测步长和决策步长必须大于 0 秒。")
    if cell_chunk_size <= 0:
        raise ValueError("cell_chunk_size 必须大于 0。")
    dt_list = _half_open_times(start, end, detection_step_s)
    if timescale is None:
        timescale = load.timescale(builtin=True)
    t = timescale.from_datetimes(dt_list)
    satellites = tuple(satellites)
    target_info = _targeted_grid_centroid(grid) if _is_targeted(cfg) or any(
        _is_targeted(item) for item in (cfg.get("sat_sensor_overrides") or [])
    ) else None
    target_ecef = (
        np.asarray(target_info["ecef_km"], dtype=float) if target_info is not None else None
    )
    n_cells = len(grid.cells)
    if not satellites:
        return OpportunityStore(
            satellites=(),
            grid=grid,
            start_utc=start,
            end_utc=end,
            detection_step_s=detection_step_s,
            decision_step_s=int(decision_step_s),
            metadata={"config": cfg, "targeting": target_info},
        )

    windows: dict[tuple[int, int], tuple[AccessWindow, ...]] = {}
    for sat_index, sat in enumerate(satellites):
        sat_id = _sat_id(sat)
        sat_name = _sat_name(sat)
        effective_cfg = _effective_satellite_cfg(cfg, sat_id)
        # 同一颗星的轨道和姿态只准备一次；块循环只改变网格列，避免每块重复 SGP4。
        prepared = (
            _prepare_simple_geometry(sat, t)
            if _is_simple_nadir(effective_cfg)
            else _prepare_ecef_geometry(
                sat,
                t,
                dt_list,
                effective_cfg,
                targeted=target_ecef if _is_targeted(effective_cfg) else None,
            )
        )
        for cell_start in range(0, n_cells, int(cell_chunk_size)):
            cell_stop = min(cell_start + int(cell_chunk_size), n_cells)
            states = compute_satellite_grid_state(
                sat,
                grid,
                t,
                dt_list,
                effective_cfg,
                cell_start=cell_start,
                cell_stop=cell_stop,
                _prepared=prepared,
            )
            windows.update(
                _windows_from_state(
                    states,
                    dt_list,
                    end,
                    sat_index=sat_index,
                    sat_id=sat_id,
                    sat_name=sat_name,
                    grid=grid,
                    cell_start=cell_start,
                )
            )
        if progress:
            progress(
                (sat_index + 1) / len(satellites),
                f"区域机会：{sat_name}（{sat_index + 1}/{len(satellites)}）",
            )

    store = OpportunityStore(
        satellites=satellites,
        grid=grid,
        start_utc=start,
        end_utc=end,
        detection_step_s=detection_step_s,
        decision_step_s=int(decision_step_s),
        windows=windows,
        metadata={
            "config": cfg,
            "n_grid_cells": n_cells,
            "n_nonempty_pairs": len(windows),
            "targeting": target_info,
        },
    )
    refine_step_s = float(cfg.get("refine_step_seconds", 0.0) or 0.0)
    if refine_step_s < 0.0:
        raise ValueError("refine_step_seconds 不能为负数。")
    if refine_step_s > 0.0:
        store = refine_regional_opportunity_windows(
            store,
            refine_step_seconds=refine_step_s,
            timescale=timescale,
            cfg=cfg,
        )
    return store


__all__ = [
    "AccessWindow",
    "OpportunityStore",
    "compute_satellite_grid_state",
    "compute_regional_opportunities",
    "refine_regional_opportunity_windows",
]
