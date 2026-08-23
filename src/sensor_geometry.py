# -*- coding: utf-8 -*-
"""自研引擎复杂传感器/姿态几何（P0-P3，对应 docs/自研复杂传感器理论报告.md）。

核心思想：覆盖判定 = 把目标方向矢量变换到传感器坐标系，再做一组"视场不等式"检查。
全部公式为解析式、确定性、可复现（Spinning 相位显式锚定分析起点，优于 ATK）。

坐标系约定（全部在 ECEF 坐标内计算）：
  - r/v：由 SGP4(TEME) + GMST 旋转得到的地固系位置/速度；
  - v_in：惯性速度在地固系坐标（= R3(GMST)·v_TEME），用于"惯性系"姿态族；
  - 体坐标系：EVeProfile 0-4（2.2 节）+ 偏移四元数；
  - 传感器系：视轴 +z，x 为速度在 ⊥z 平面投影（沿迹向），y 补右手。

视场判定（第 4 章）：
  SimpleCone    θ ≤ η 且 ε_ground ≥ ε_min
  Rectangular   |atan2(x,z)| ≤ H 且 |atan2(y,z)| ≤ V（相机视锥）
  Conical       θ∈[inner,outer] 且 钟角∈[min,max]
  HalfPower     η = 35λ/D，按 SimpleCone
  SAR           ε_ground∈[min,max] 且 前/后排除角 ≥ ψ_f/ψ_a
"""
from __future__ import annotations

import math

import numpy as np

R_EARTH_KM = 6378.137
MU_EARTH = 398600.4418
OMEGA_EARTH = 7.2921158553e-5     # rad/s（WGS84）
C_LIGHT = 299792458.0              # m/s


# ==================== 基础矢量工具 ====================

def normalize(v, axis=-1):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v, axis=axis, keepdims=True)
    return v / np.maximum(n, 1e-12)


def proj_perp(v, n):
    """v 在垂直于单位向量 n 的平面上的投影，归一化（支持 (3,) 与 (T,3)）。"""
    v = np.asarray(v, dtype=float)
    n = np.asarray(n, dtype=float)
    p = v - np.sum(v * n, axis=-1, keepdims=True) * n
    return normalize(p)


def rot_z(theta):
    c, s = math.cos(theta), math.sin(theta)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def rot_z_array(theta, v):
    """逐时刻 TEME→ECEF 旋转：v'(t) = R3(-theta[t]) · v[t]（r_ECEF = R3(-GMST)·r_TEME）。
    符号与项目已验证的独立路径（validate_crosscheck.py 的 propagate_ecef）一致。"""
    c = np.cos(theta)
    s = np.sin(theta)
    x = c * v[:, 0] + s * v[:, 1]
    y = -s * v[:, 0] + c * v[:, 1]
    return np.stack([x, y, v[:, 2]], axis=1)


def cross_omega(r):
    """ω_⊕ × r（r 为 ECEF 坐标，(T,3)）。"""
    return np.stack([-OMEGA_EARTH * r[:, 1], OMEGA_EARTH * r[:, 0],
                     np.zeros_like(r[:, 0])], axis=1)


# ==================== 四元数 / 欧拉 / YPR ====================

def quat_to_dcm(q):
    """q=(qs,qx,qy,qz) → 3×3 旋转矩阵。"""
    qs, qx, qy, qz = (float(x) for x in q)
    return np.array([
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qs * qz), 2 * (qx * qz + qs * qy)],
        [2 * (qx * qy + qs * qz), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qs * qx)],
        [2 * (qx * qz - qs * qy), 2 * (qy * qz + qs * qx), 1 - 2 * (qx * qx + qy * qy)],
    ])


def offset_quat(offset):
    """ATK 偏移四元数 (qx,qy,qz)，标量由单位化隐含 → (qs,qx,qy,qz)。"""
    qx, qy, qz = (float(x) for x in (offset or [])[:3])
    n2 = qx * qx + qy * qy + qz * qz
    if n2 > 1.0:
        k = math.sqrt(1.0 / n2)
        qx, qy, qz = qx * k, qy * k, qz * k
        n2 = 1.0
    qs = math.sqrt(max(0.0, 1.0 - n2))
    return (qs, qx, qy, qz)


def dcm_to_quat(R):
    """3×3 旋转矩阵 → 单位四元数 (qs,qx,qy,qz)。标准 Shepperd 算法，qs≥0。"""
    tr = R[0, 0] + R[1, 1] + R[2, 2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        qs = 0.25 * s
        qx = (R[2, 1] - R[1, 2]) / s
        qy = (R[0, 2] - R[2, 0]) / s
        qz = (R[1, 0] - R[0, 1]) / s
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = math.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2.0
        qs = (R[2, 1] - R[1, 2]) / s
        qx = 0.25 * s
        qy = (R[0, 1] + R[1, 0]) / s
        qz = (R[0, 2] + R[2, 0]) / s
    elif R[1, 1] > R[2, 2]:
        s = math.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2.0
        qs = (R[0, 2] - R[2, 0]) / s
        qx = (R[0, 1] + R[1, 0]) / s
        qy = 0.25 * s
        qz = (R[1, 2] + R[2, 1]) / s
    else:
        s = math.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2.0
        qs = (R[1, 0] - R[0, 1]) / s
        qx = (R[0, 2] + R[2, 0]) / s
        qy = (R[1, 2] + R[2, 1]) / s
        qz = 0.25 * s
    return (qs, qx, qy, qz)


def offset_dcm(offset):
    """把姿态偏移（ATK 偏移形式）转成 3×3 旋转矩阵，作用于基准体轴。

    支持三种 ATK 偏置形式：
      - 四元数 [qx,qy,qz]（默认，标量由单位化隐含）或 dict {"type":"Quaternion","params":[qx,qy,qz]}
      - 欧拉角 dict {"type":"Euler","seq":"123","params":[r1,r2,r3]}（intrinsic 连续旋转）
      - YPR 角 dict {"type":"YPR","seq":"YPR","params":[yaw,pitch,roll]}
    返回 3×3 旋转矩阵（列向量作用于体轴）。"""
    if isinstance(offset, dict):
        t = str(offset.get("type") or "Quaternion").lower()
        params = offset.get("params") or offset.get("offset") or []
        try:
            if t == "euler":
                return dcm_from_euler(offset.get("seq", "123"), *[float(x) for x in params[:3]])
            if t == "ypr":
                return dcm_from_ypr(offset.get("seq", "YPR"), *[float(x) for x in params[:3]])
            if t == "quaternion":
                return quat_to_dcm(offset_quat([float(x) for x in params[:3]]))
        except (TypeError, ValueError):
            pass
        # 未知类型回退为单位阵
        return np.eye(3)
    return quat_to_dcm(offset_quat(offset))


def offset_to_quat(offset):
    """把任意姿态偏移转成 ATK 偏移四元数 (qx,qy,qz)（标量由单位化隐含，qs≥0）。

    用于把 GUI 里的欧拉/YPR 偏置换算成 XML 的 LHAttOffsetQuatx/y/z。
    返回 [qx,qy,qz]；不支持时返回 [0,0,0]。"""
    try:
        R = offset_dcm(offset)
    except Exception:
        return [0.0, 0.0, 0.0]
    _, qx, qy, qz = dcm_to_quat(R)
    return [qx, qy, qz]


def rotate_vec(q, v):
    """四元数旋转向量（支持 v 为 (3,) 或 (T,3)）。"""
    qs, qx, qy, qz = q
    qv = np.array([qx, qy, qz])
    t = 2.0 * np.cross(qv, v)
    return v + qs * t + np.cross(qv, t)


def dcm_from_euler(seq, r1, r2, r3):
    """本体连续旋转（intrinsic）：'123' = 先绕 x 转 r1，再绕 y 转 r2，最后绕 z 转 r3。
    R = R3(r3)·R2(r2)·R1(r1)。"""
    a1, a2, a3 = math.radians(r1), math.radians(r2), math.radians(r3)
    mats = {1: np.array([[1, 0, 0], [0, math.cos(a1), -math.sin(a1)], [0, math.sin(a1), math.cos(a1)]]),
            2: np.array([[math.cos(a2), 0, math.sin(a2)], [0, 1, 0], [-math.sin(a2), 0, math.cos(a2)]]),
            3: np.array([[math.cos(a3), -math.sin(a3), 0], [math.sin(a3), math.cos(a3), 0], [0, 0, 1]])}
    order = [int(ch) for ch in str(seq)]
    R = np.eye(3)
    for axis in order:                       # intrinsic：按序左乘
        R = mats[axis] @ R
    return R


def dcm_from_ypr(seq, yaw, pitch, roll):
    """YPR 转序（intrinsic）：'YPR' = 先偏航(绕z)、再俯仰(绕y)、最后滚转(绕x)。
    R = Rx(roll)·Ry(pitch)·Rz(yaw)。"""
    y, p, r = (math.radians(x) for x in (yaw, pitch, roll))
    Rz = np.array([[math.cos(y), -math.sin(y), 0], [math.sin(y), math.cos(y), 0], [0, 0, 1]])
    Ry = np.array([[math.cos(p), 0, math.sin(p)], [0, 1, 0], [-math.sin(p), 0, math.cos(p)]])
    Rx = np.array([[1, 0, 0], [0, math.cos(r), -math.sin(r)], [0, math.sin(r), math.cos(r)]])
    seq = str(seq).upper()
    R = np.eye(3)
    for ch in seq:
        R = {"Y": Rz, "P": Ry, "R": Rx}[ch] @ R
    return R


def fixed_rotation(method, params):
    """Fixed 指向的姿态方法 → 3×3 旋转矩阵（作用于基准视轴 (0,0,1)）。"""
    method = (method or "YPR").lower()
    if method == "ypr":
        seq, yaw, pitch, roll = params[0], float(params[1]), float(params[2]), float(params[3])
        return dcm_from_ypr(seq, yaw, pitch, roll)
    if method == "euler":
        seq = params[0]
        return dcm_from_euler(seq, float(params[1]), float(params[2]), float(params[3]))
    if method == "quaternion":
        q1, q2, q3, q4 = (float(x) for x in params[:4])
        return quat_to_dcm((q4, q1, q2, q3))
    if method == "matrix":
        # ATK Connect 六元素：d11 d21 d22 d31 d32 d33，按对称矩阵重建后正交化（待校准，3.8 节）
        d11, d21, d22, d31, d32, d33 = (float(x) for x in params[:6])
        M = np.array([[d11, d21, d31], [d21, d22, d32], [d31, d32, d33]])
        u, _, vt = np.linalg.svd(M)
        return u @ vt
    if method == "azel":
        az, el = math.radians(float(params[0])), math.radians(float(params[1]))
        return np.array([[math.cos(el) * math.cos(az), -math.sin(az), math.cos(el) * math.sin(az)],
                         [math.cos(el) * math.sin(az), math.cos(az), math.sin(el) * math.sin(az)],
                         [-math.sin(el), 0.0, math.cos(el)]])
    raise ValueError(f"不支持的姿态方法：{method}")


# ==================== 姿态族（EVeProfile 0-4） ====================

def body_dcm(r, v_ecf, v_in, sun=None, profile=0, offset=None):
    """体坐标系 DCM（列 = 体轴在地固系坐标），形状 (T,3,3)。

    profile（ATK EVeProfile）：
      0 固连系VVLH：z=-r̂，x=ECF 速度在水平面投影
      1 惯性系VVLH：z=-r̂，x=ECI 速度在水平面投影
      2 ECI 速度定向 + 天底约束：x=v̂_in，z=proj(-r̂ ⊥ x)
      3 ECF 速度定向 + 天底约束：x=v̂_ecf，z=proj(-r̂ ⊥ x)
      4 太阳对齐 + 天底约束：x=ŝ，z=proj(-r̂ ⊥ x)
    offset = (qx,qy,qz) 偏移四元数（2.4 节）。
    """
    rhat = normalize(r)
    z = -rhat
    if profile in (0, 1):
        vel = v_ecf if profile == 0 else v_in
        x = proj_perp(vel, z)
    elif profile in (2, 3):
        vel = v_ecf if profile == 3 else v_in
        x = normalize(vel)
        z = proj_perp(z, x)
    elif profile == 4:
        if sun is None:
            raise ValueError("P4 太阳对齐需要太阳方向 sun")
        x = normalize(sun)
        z = proj_perp(z, x)
    else:
        raise ValueError(f"未知姿态族：{profile}")
    y = np.cross(z, x)
    C = np.stack([x, y, z], axis=2)          # (T,3,3)：列 = 体轴
    if offset:
        Roff = offset_dcm(offset)
        C = np.einsum("ij,tjk->tik", Roff, C)
    return C


# ==================== 太阳 / 月球（Meeus 低精度，生产可换 skyfield） ====================

def _obliquity(T):
    return np.radians(23.43929111 - 0.0130042 * np.asarray(T, dtype=float))


def sun_vector_eci(jd):
    """Meeus《Astronomical Algorithms》第 25 章低精度太阳位置（ECI 单位向量，精度 ~0.01°）。
    jd 可为标量或数组，返回 (T,3)。"""
    jd = np.asarray(jd, dtype=float)
    T = (jd - 2451545.0) / 36525.0
    L0 = np.radians(280.46646 + 36000.76983 * T + 0.0003032 * T * T)
    M = np.radians(357.52911 + 35999.05029 * T - 0.0001537 * T * T)
    C = (1.914602 - 0.004817 * T - 0.000014 * T * T) * np.sin(M) \
        + (0.019993 - 0.000101 * T) * np.sin(2 * M) + 0.000289 * np.sin(3 * M)
    true_long = L0 + np.radians(C)
    Omega = np.radians(125.04 - 1934.136 * T)
    lam = true_long - np.radians(0.00569) - np.radians(0.00478) * np.sin(Omega)
    eps = _obliquity(T) + np.radians(0.00256) * np.cos(Omega)
    return np.stack([np.cos(lam), np.cos(eps) * np.sin(lam),
                     np.sin(eps) * np.sin(lam)], axis=1)


def moon_vector_eci(jd):
    """Meeus 低精度月球位置（ECI 单位向量，精度 ~0.3°，光照约束足够）。"""
    jd = np.asarray(jd, dtype=float)
    T = (jd - 2451545.0) / 36525.0
    Lp = 218.316 + 481267.8813 * T
    D = 297.850 + 445267.1115 * T
    M = 357.529 + 35999.050 * T
    Mp = 134.963 + 477198.8676 * T
    F = 93.272 + 483202.0175 * T
    d2r = math.pi / 180.0
    l = Lp + 6.289 * np.sin(Mp * d2r) + 1.274 * np.sin((2 * D - Mp) * d2r) \
        + 0.658 * np.sin(2 * D * d2r) + 0.214 * np.sin(2 * Mp * d2r) \
        - 0.186 * np.sin(M * d2r) - 0.114 * np.sin(2 * F * d2r)
    b = 5.128 * np.sin(F * d2r) + 0.280 * np.sin((Mp + F) * d2r) \
        + 0.277 * np.sin((Mp - F) * d2r) + 0.173 * np.sin((2 * D - F) * d2r)
    lam = np.radians(l)
    beta = np.radians(b)
    eps = _obliquity(T)
    x = np.cos(beta) * np.cos(lam)
    y = np.cos(eps) * np.cos(beta) * np.sin(lam) - np.sin(eps) * np.sin(beta)
    z = np.sin(eps) * np.cos(beta) * np.sin(lam) + np.cos(eps) * np.sin(beta)
    return np.stack([x, y, z], axis=1)


# ==================== 传感器指向 ====================

def pointing_boresight_ecef(pointing, r, v_ecf, C, target=None, dt_sec=None, jd=None):
    """视轴 b（ECI→体坐标变换后），返回 (T,3)。

    pointing：None=天底；或 {"type": "Fixed"/"Targeted"/"GrazingAlt"/"Spinning", ...}
    target：circle 模式的目标点 ECEF 坐标（(3,)），Targeted 需要。
    dt_sec：(T,) 相对分析起点的秒数，Spinning 需要。
    jd：太阳对齐姿态需要（由调用方传入，此处不用）。
    """
    r = np.asarray(r, dtype=float)
    if not pointing or not pointing.get("type"):
        # ATK 默认：传感器固定于星体，视轴沿体 +Z（VVLH 姿态下即天底；姿态改变则跟随体轴）
        return np.einsum("tij,j->ti", C, np.array([0.0, 0.0, 1.0]))
    typ = pointing["type"]
    if typ == "Fixed":
        method = pointing.get("method", "YPR")
        params = pointing.get("params") or []
        if str(method).lower() == "azel":
            az, el = math.radians(float(params[0])), math.radians(float(params[1]))
            b_body = np.array([math.cos(el) * math.cos(az),
                               math.cos(el) * math.sin(az),
                               -math.sin(el)])
        else:
            R = fixed_rotation(method, params)
            b_body = R @ np.array([0.0, 0.0, 1.0])
        return np.einsum("tij,j->ti", C, b_body)
    if typ == "Targeted":
        if target is None:
            raise ValueError("Targeted 指向需要目标点")
        u = normalize(target - r)                    # 卫星→目标（ECEF）
        u_body = np.einsum("tji,tj->ti", C, u)
        if pointing.get("target_mode") == 1:         # 固定指向：叠加偏置
            method = pointing.get("offset_method", "YPR")
            params = pointing.get("offset_params") or []
            Roff = fixed_rotation(method, params)
            b_body = np.einsum("ij,tj->ti", Roff, u_body)
        else:                                        # 追踪：直接指向目标
            b_body = u_body
        return np.einsum("tij,tj->ti", C, b_body)
    if typ == "GrazingAlt":
        az = math.radians(float(pointing.get("azimuth", 90.0)))
        alt_m = float(pointing.get("altitude", 10000.0))
        a = R_EARTH_KM + alt_m / 1000.0
        rs = np.linalg.norm(r, axis=1)
        beta = np.arcsin(np.clip(a / rs, 0.0, 1.0))   # 视轴与天底夹角
        delta = np.pi / 2.0 - beta                     # 相对水平面下俯角
        b_body = np.stack([np.cos(delta) * math.cos(az),
                           np.cos(delta) * math.sin(az),
                           np.sin(delta)], axis=1)
        return np.einsum("tij,tj->ti", C, b_body)
    if typ == "Spinning":
        az = math.radians(float(pointing.get("azimuth", 0.0)))
        el = math.radians(float(pointing.get("elevation", -90.0)))
        cone = math.radians(float(pointing.get("cone_angle", 30.0)))
        rate = float(pointing.get("rate", 1.0))
        offset0 = math.radians(float(pointing.get("offset", 0.0)))
        scan = pointing.get("scan", "Continuous")
        start = math.radians(float(pointing.get("start_angle", 0.0)))
        stop = math.radians(float(pointing.get("stop_angle", 360.0)))
        s = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az),
                      -math.sin(el)])                 # 旋转轴（体坐标，el=-90=天底）
        s = s / np.linalg.norm(s)
        n0 = proj_perp(np.array([1.0, 0.0, 0.0]), s)  # 0 相位参考（沿迹向，待校准 3.8）
        b0 = s * math.cos(cone) + n0 * math.sin(cone)
        if dt_sec is None:
            raise ValueError("Spinning 指向需要 dt_sec")
        phase = offset0 + rate * dt_sec                # (T,)，φ₀ 锚定分析起点（3.6 节）
        if scan == "Unidirectional":
            psi = np.clip(phase, start, stop)
        elif scan == "Bidirectional":
            L = max(stop - start, 1e-9)
            p = np.mod(phase, 2.0 * L)
            psi = start + np.where(p <= L, p, 2.0 * L - p)
        else:                                          # Continuous
            psi = phase
        qs = np.cos(psi / 2.0)
        qv = s[None, :] * np.sin(psi / 2.0)[:, None]     # (T,3)
        tvec = 2.0 * np.cross(qv, b0[None, :])           # (T,3)
        b_body = b0[None, :] + qs[:, None] * tvec + np.cross(qv, tvec)
        return np.einsum("tij,tj->ti", C, b_body)
    if typ == "External":
        raise NotImplementedError("External 外部指向文件已决定不做（格式无据、主流程无需求）")
    raise ValueError(f"未知指向类型：{typ}")


def sensor_frame_axes(b, v):
    """传感器系：z=视轴 b，x=速度在 ⊥b 投影（沿迹向），y=b×x。返回 (T,3,3)。"""
    x = proj_perp(v, b)
    y = np.cross(b, x)
    return np.stack([x, y, b], axis=2)


# ==================== 视场判定（第 4 章） ====================

def pattern_covered(u_s, sensor_type, sensor_params, eps_ground, min_elev,
                    u=None, v=None, sar_fwd=None, sar_aft=None):
    """视场不等式。u_s=(T,3) 目标方向在传感器系坐标；返回 (T,) bool。
    SAR 额外需要 u（卫星→目标单位方向）与速度 v（前后排除角）。"""
    stype = sensor_type or "SimpleCone"
    z = u_s[:, 2]
    x = u_s[:, 0]
    y = u_s[:, 1]
    if stype == "SimpleCone":
        eta = float(sensor_params[0]) if sensor_params else 45.0
        ok = z >= math.cos(math.radians(eta))
    elif stype == "Rectangular":
        h = float(sensor_params[0]) if sensor_params else 40.0
        vh = float(sensor_params[1]) if len(sensor_params or []) > 1 else 60.0
        ok = (z > 0.0) & (np.abs(np.degrees(np.arctan2(x, z))) <= h) \
            & (np.abs(np.degrees(np.arctan2(y, z))) <= vh)
    elif stype == "Conical":
        inner = float(sensor_params[0]) if sensor_params else 0.0
        outer = float(sensor_params[1]) if len(sensor_params or []) > 1 else 45.0
        cmin = float(sensor_params[2]) if len(sensor_params or []) > 2 else 0.0
        cmax = float(sensor_params[3]) if len(sensor_params or []) > 3 else 360.0
        theta = np.degrees(np.arccos(np.clip(z, -1.0, 1.0)))
        clock = np.degrees(np.arctan2(x, y)) % 360.0
        ok = (theta >= inner) & (theta <= outer) & (clock >= cmin) & (clock <= cmax)
    elif stype == "HalfPower":
        freq_ghz = float(sensor_params[0]) if sensor_params else 14.5
        diam = float(sensor_params[1]) if len(sensor_params or []) > 1 else 1.0
        lam = C_LIGHT / (freq_ghz * 1e9) if freq_ghz > 0 else 0.0
        eta = 70.0 * lam / diam / 2.0 if diam > 0 else 45.0
        ok = z >= math.cos(math.radians(eta))
    elif stype == "SAR":
        emin = float(sensor_params[0]) if sensor_params else 12.0
        emax = float(sensor_params[1]) if len(sensor_params or []) > 1 else 70.0
        fwd = float(sensor_params[2]) if len(sensor_params or []) > 2 else 30.0
        aft = float(sensor_params[3]) if len(sensor_params or []) > 3 else 30.0
        ok = (eps_ground >= emin) & (eps_ground <= emax)
        if u is not None and v is not None:
            vh = normalize(v)
            ang_f = np.degrees(np.arccos(np.clip(np.sum(u * vh, axis=1), -1.0, 1.0)))
            ang_a = np.degrees(np.arccos(np.clip(np.sum(u * (-vh), axis=1), -1.0, 1.0)))
            ok &= (ang_f >= fwd) & (ang_a >= aft)
    else:
        raise ValueError(f"未知传感器类型：{stype}")
    # 物理基线：目标必须在地平线以上（视线不被地球遮挡）。现有引擎经足印地平线截断
    # 天然满足；复杂引擎必须显式加这一条，否则“视线穿过地球背面”也会通过视场不等式。
    ok &= eps_ground >= 0.0
    # 最小仰角（目标点级别约束；SAR 的仰角带在内部已判，这里仍允许叠加 facility 级门槛）
    if float(min_elev or 0.0) > 0.0 and stype != "SAR":
        ok &= eps_ground >= float(min_elev)
    return ok


# ==================== 约束（第 5 章，P2） ====================

def _azimuth_at_target(P, sat):
    """目标点处看卫星的大圆方位角（°），P/sat 为 ECEF 坐标。"""
    up = normalize(P)
    north = normalize(np.array([-up[1], up[0], 0.0]) if abs(up[2]) < 0.999 else
                      np.array([1.0, 0.0, 0.0]))
    east = np.cross(north, up)
    d = sat - P
    az = np.degrees(np.arctan2(np.sum(d * east, axis=-1), np.sum(d * north, axis=-1))) % 360.0
    return az


def elevation_of_ground(P, sat):
    """目标点 P 处观测卫星的仰角（°），P/sat 为 ECEF 坐标（(3,) 或 (T,3)）。"""
    up = normalize(P)
    u = normalize(sat - P)
    return np.degrees(np.arcsin(np.clip(np.sum(u * up, axis=-1), -1.0, 1.0)))


def check_constraints(constraints, P, r, v, b, u, sun, moon, dt_sec):
    """约束逐时刻判定（Include 语义）；ExcludeIntervals 由调用方取反。
    P：目标点 ECEF（(3,)）；r/v/b/u：卫星位置/速度/视轴/卫星→目标单位方向（(T,3)）。
    返回 (T,) bool。"""
    T = len(r)
    ok = np.ones(T, dtype=bool)
    for c in constraints or []:
        name = c.get("name", "")
        params = (c.get("params") or "").strip()
        m = None
        import re
        mm = re.match(r"Min\s+([-\d.eE+]+)", params)
        mx = re.match(r".*Max\s+([-\d.eE+]+)", params)
        vmin = float(mm.group(1)) if mm else None
        vmax = float(mx.group(1)) if mx else None
        exclude = "ExcludeIntervals" in params
        def _band(val):
            okk = np.ones(T, dtype=bool)
            if vmin is not None:
                okk &= val >= vmin
            if vmax is not None:
                okk &= val <= vmax
            return okk
        if name == "AzimuthAngle":
            val = _azimuth_at_target(P, r)
            cond = _band(val)
        elif name == "ElevationAngle":
            cond = _band(elevation_of_ground(P, r))
        elif name == "Range":
            cond = _band(np.linalg.norm(r - P, axis=1))
        elif name in ("AzimuthRate", "ElevationRate", "RangeRate"):
            if name == "RangeRate":
                val = np.linalg.norm(r - P, axis=1)
                rate = np.gradient(val, dt_sec) if len(dt_sec) > 1 else np.zeros(T)
            elif name == "ElevationRate":
                val = elevation_of_ground(P, r)
                rate = np.gradient(val, dt_sec) if len(dt_sec) > 1 else np.zeros(T)
            else:
                val = _azimuth_at_target(P, r)
                rate = np.gradient(val, dt_sec) if len(dt_sec) > 1 else np.zeros(T)
            cond = _band(rate)
        elif name == "AngularRate":
            d = normalize(r - P)
            ang = np.zeros(T)
            if len(dt_sec) > 1:
                rate = np.linalg.norm(np.gradient(d, dt_sec, axis=0), axis=1)
                ang = np.degrees(rate)
            cond = _band(ang)
        elif name == "Altitude":
            cond = _band(np.linalg.norm(P, axis=-1) - R_EARTH_KM
                         if np.ndim(P) > 1 else np.full(T, np.linalg.norm(P) - R_EARTH_KM))
        elif name == "PropagationDelay":
            cond = _band(np.linalg.norm(r - P, axis=1) / 299792.458)
        elif name == "LineOfSight":
            want = (params or "").upper() != "OFF"
            cond = np.full(T, want) if want else np.ones(T, dtype=bool)
        elif name == "fieldofview":
            want = (params or "").upper() != "OFF"
            cond = np.ones(T, dtype=bool)   # 视场已由 pattern 判定，此处不重复收紧
        elif name == "TerrainMask":
            cond = np.ones(T, dtype=bool)   # DEM 后期实现，暂不生效
        elif name == "SunElevationAngle":
            up = normalize(r)
            val = np.degrees(np.arcsin(np.clip(np.sum(sun * up, axis=1), -1.0, 1.0)))
            cond = _band(val)
        elif name == "SunGroundElevAngle":
            up = normalize(P)
            val = np.degrees(np.arcsin(np.clip(np.sum(sun * up, axis=-1), -1.0, 1.0)))
            cond = _band(val)
        elif name == "LunarElevationAngle":
            up = normalize(r)
            val = np.degrees(np.arcsin(np.clip(np.sum(moon * up, axis=1), -1.0, 1.0)))
            cond = _band(val)
        elif name in ("LOSSunExclusion", "LOSLunarExclusion", "BSSunExclusion",
                      "BSLunarExclusion"):
            ref = moon if "Lunar" in name else sun
            vec = b if name.startswith("BS") else normalize(r - P)
            val = np.degrees(np.arccos(np.clip(np.sum(vec * ref, axis=1), -1.0, 1.0)))
            cond = _band(val)
        else:
            continue
        ok &= (cond if not exclude else ~cond)
    return ok


# ==================== 主覆盖判定（复杂引擎） ====================

def _is_simple_nadir(cfg):
    """是否退化为"现有引擎"默认口径：SimpleCone + 天底（无指向/姿态/约束）。
    是则直接委托现有 compute_covered_series，保证默认路径逐位零回归
    （含 5km 目标圆边缘余量 lam_t 与球面/椭球口径完全一致）。"""
    if cfg.get("pointing") or cfg.get("attitude") or cfg.get("constraints"):
        return False
    st = cfg.get("sensor_type") or "SimpleCone"
    return st == "SimpleCone"


def compute_covered_series_complex(sat, t, dt_list, region, cfg):
    """与 compute_covered_series 同签名：返回 (covered, ratio)。
    cfg 需含 sensor_type/sensor_params/pointing/attitude/constraints（可为空）+ 原有字段。"""
    if _is_simple_nadir(cfg):
        import coverage_analysis as cov
        base_cfg = dict(cfg)
        sp = cfg.get("sensor_params")
        if sp:
            base_cfg["fov_half_angle_deg"] = float(sp[0])
        return cov.compute_covered_series(sat, t, dt_list, region, base_cfg)
    jd = np.asarray(t.ut1, dtype=float)
    jdi = np.floor(jd)
    jdf = jd - jdi
    err, r_teme, v_teme = sat.model.sgp4_array(jdi, jdf)
    if np.any(err):
        raise RuntimeError(f"SGP4 传播失败（NORAD {getattr(sat, 'norad_id', '?')}）：错误码 {err}")
    gmst = np.radians(np.asarray(t.gmst, dtype=float) * 15.0)
    r = rot_z_array(gmst, r_teme)           # ECEF 位置
    v_in = rot_z_array(gmst, v_teme)        # 惯性速度在地固系坐标
    v = v_in - cross_omega(r)               # ECEF 速度

    T = len(r)
    dt_sec = np.array([(dt - dt_list[0]).total_seconds() for dt in dt_list], dtype=float)

    # 姿态
    attitude = cfg.get("attitude") or {}
    profile = int(attitude.get("profile", 0))
    sun = None
    if profile == 4:
        sun_eci = sun_vector_eci(jd)
        sun = rot_z_array(gmst, sun_eci)
    C = body_dcm(r, v, v_in, sun=sun, profile=profile,
                 offset=attitude.get("offset"))

    # 目标点
    if region["mode"] == "circle":
        lat = math.radians(float(region["lat"]))
        lon = math.radians(float(region["lon"]))
        P = np.array([R_EARTH_KM * math.cos(lat) * math.cos(lon),
                      R_EARTH_KM * math.cos(lat) * math.sin(lon),
                      R_EARTH_KM * math.sin(lat)])
        u = normalize(P - r)                       # 卫星→目标（ECEF），(T,3)
        b = pointing_boresight_ecef(cfg.get("pointing"), r, v, C,
                                    target=P, dt_sec=dt_sec, jd=jd)
        Ax = sensor_frame_axes(b, v)               # (T,3,3)
        u_s = np.einsum("tij,tj->ti", np.swapaxes(Ax, 1, 2), u)
        eps_ground = elevation_of_ground(P, r)
        covered = pattern_covered(u_s, cfg.get("sensor_type"), cfg.get("sensor_params"),
                                  eps_ground, cfg.get("min_elevation_deg", 0.0),
                                  u=u, v=v)
        # 约束（P2）
        cons = cfg.get("constraints") or []
        if cons:
            sun_c = sun if sun is not None else rot_z_array(gmst, sun_vector_eci(jd))
            moon_c = rot_z_array(gmst, moon_vector_eci(jd))
            covered &= check_constraints(cons, P, r, v, b, u, sun_c, moon_c, dt_sec)
        ratio = covered.astype(float)
        return covered, ratio

    # polygon 模式：网格点逐一过 4.1 判定
    grid_lats, grid_lons = region["lats"], region["lons"]
    npts = len(grid_lats)
    any_cov = np.zeros(T, dtype=bool)
    ratio = np.zeros(T, dtype=float)
    for k in range(npts):
        lat = math.radians(float(grid_lats[k]))
        lon = math.radians(float(grid_lons[k]))
        Pk = np.array([R_EARTH_KM * math.cos(lat) * math.cos(lon),
                       R_EARTH_KM * math.cos(lat) * math.sin(lon),
                       R_EARTH_KM * math.sin(lat)])
        uk = normalize(Pk - r)
        bk = pointing_boresight_ecef(cfg.get("pointing"), r, v, C,
                                     target=Pk, dt_sec=dt_sec, jd=jd)
        Ax = sensor_frame_axes(bk, v)
        u_s = np.einsum("tij,tj->ti", np.swapaxes(Ax, 1, 2), uk)
        eps_g = elevation_of_ground(Pk, r)
        cov_k = pattern_covered(u_s, cfg.get("sensor_type"), cfg.get("sensor_params"),
                                eps_g, cfg.get("min_elevation_deg", 0.0),
                                u=uk, v=v)
        any_cov |= cov_k
        ratio += cov_k.astype(float)
    ratio = ratio / max(npts, 1)
    cons = cfg.get("constraints") or []
    if cons:
        sun_c = sun if sun is not None else rot_z_array(gmst, sun_vector_eci(jd))
        moon_c = rot_z_array(gmst, moon_vector_eci(jd))
        P0 = np.array([R_EARTH_KM * math.cos(math.radians(float(grid_lats[0]))) *
                       math.cos(math.radians(float(grid_lons[0]))),
                       R_EARTH_KM * math.cos(math.radians(float(grid_lats[0]))) *
                       math.sin(math.radians(float(grid_lons[0]))),
                       R_EARTH_KM * math.sin(math.radians(float(grid_lats[0])))])
        b0 = pointing_boresight_ecef(cfg.get("pointing"), r, v, C,
                                     target=P0, dt_sec=dt_sec, jd=jd)
        u0 = normalize(P0 - r)
        any_cov &= check_constraints(cons, P0, r, v, b0, u0, sun_c, moon_c, dt_sec)
    return any_cov, ratio


# ==================== 配置辅助 ====================

def is_complex(cfg):
    """判断是否需要复杂引擎（有传感器类型/参数/指向/姿态/约束任一配置）。"""
    return bool(cfg.get("sensor_type") or cfg.get("sensor_params")
                or cfg.get("pointing") or cfg.get("attitude") or cfg.get("constraints")
                or cfg.get("sat_sensor_overrides"))


def merge_sat_complex_cfg(norad_id, cfg):
    """全局复杂配置 + 逐星覆盖（cfg['sat_sensor_overrides']），返回该星复杂配置 dict。"""
    out = {}
    for k in ("sensor_type", "sensor_params", "pointing", "attitude", "constraints"):
        if cfg.get(k):
            out[k] = cfg[k]
    for o in cfg.get("sat_sensor_overrides") or []:
        if o.get("norad") == norad_id:
            for k in ("sensor_type", "sensor_params", "pointing", "attitude", "constraints"):
                if o.get(k):
                    out[k] = o[k]
    return out
