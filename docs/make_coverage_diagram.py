# -*- coding: utf-8 -*-
"""
生成"覆盖判定"示意图（docs/覆盖示意图.png）
============================================
用途：给老师/答辩讲解覆盖的定义——
  视场半角 η（卫星真实传感器指标）-> 覆盖圆（足印）
  目标点 P 的仰角 ε（任务质量门槛，默认 >=10°）
  覆盖判据 = 目标点同时满足 在覆盖圆内 且 仰角达标。

用法：python docs/make_coverage_diagram.py
"""

import os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Arc

for _f in [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"]:
    if os.path.exists(_f):
        font_manager.fontManager.addfont(_f)
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

R = 6378.0        # 地球半径 km（示意用）
H = 600.0         # 卫星高度 km（典型低轨）
ETA = np.radians(30.0)


def foot_edge(sign):
    """视场边缘射线与地表交点（球面近似），sign=+1 上侧 / -1 下侧"""
    Sx = R + H
    t = (R + H) * np.cos(ETA) - np.sqrt((R + H)**2 * np.cos(ETA)**2 - 2 * R * H - H**2)
    return np.array([Sx - t * np.cos(ETA), sign * t * np.sin(ETA)])


P_up = foot_edge(+1)
P_dn = foot_edge(-1)
P = P_dn          # 用下半边缘点画仰角（卫星在头顶一侧，更直观）
S = np.array([R + H, 0.0])
N = np.array([R, 0.0])

lam = np.degrees(np.arctan2(P_up[1], P_up[0]))
v = S - P
elev = np.degrees(np.arcsin(np.clip(np.dot(v, P) / (np.linalg.norm(v) * np.linalg.norm(P)), -1, 1)))
print(f"几何校验：地心角 λ = {lam:.2f}°，覆盖圆边缘仰角 ε = {elev:.1f}°（LEO 600km，视场半角 30°）")

fig, ax = plt.subplots(figsize=(10, 8))
ax.set_aspect("equal")
ax.axis("off")

# ---- 地球（截面） ----
th = np.linspace(0, 2 * np.pi, 500)
ax.plot(R * np.cos(th), R * np.sin(th), color="#95a5a6", lw=2)
ax.fill(R * np.cos(th), R * np.sin(th), color="#d6eaf8", alpha=0.45)
ax.plot(0, 0, "+", color="#7f8c8d")
ax.text(150, -180, "地心 O", fontsize=10, color="#7f8c8d")

# ---- 卫星与星下点 ----
ax.plot(S[0], S[1], "o", color="#c0392b", ms=11, zorder=6)
ax.text(S[0] + 220, S[1] + 280, "卫星 S", fontsize=13, color="#c0392b", fontweight="bold")
ax.plot([S[0], N[0]], [S[1], N[1]], ls="--", color="#95a5a6", lw=1.3)
ax.plot(N[0], N[1], "o", color="#34495e", ms=7, zorder=6)
ax.text(N[0] - 320, N[1] - 320, "星下点 N", fontsize=11, color="#34495e")

# ---- 视场锥与覆盖圆（足印） ----
ax.plot([S[0], P_up[0]], [S[1], P_up[1]], color="#e74c3c", lw=2)
ax.plot([S[0], P_dn[0]], [S[1], P_dn[1]], color="#e74c3c", lw=2)
ax.fill([S[0], P_up[0], P_dn[0]], [S[1], P_up[1], P_dn[1]],
        color="#e74c3c", alpha=0.07)
a0 = np.arctan2(P_dn[1], P_dn[0])
a1 = np.arctan2(P_up[1], P_up[0])
arc = np.linspace(a0, a1, 120)
ax.plot(R * np.cos(arc), R * np.sin(arc), color="#f39c12", lw=6, alpha=0.9,
        solid_capstyle="round")
ax.text(R * np.cos((a0 + a1) / 2) * 1.20, R * np.sin((a0 + a1) / 2) * 1.20,
        "覆盖圆（足印）", fontsize=12, color="#f39c12", ha="center")

# ---- 目标点 P 与仰角 ----
ax.plot(P[0], P[1], "o", color="#f39c12", ms=9, zorder=6)
ax.text(P[0] - 500, P[1] - 330, "目标点 P", fontsize=12, color="#f39c12")
ang = np.arctan2(P[1], P[0])
t1 = np.array([-np.sin(ang), np.cos(ang)])
if np.dot(t1, S - P) < 0:
    t1 = -t1
L = 1000
ax.plot([P[0] - L * t1[0], P[0] + L * t1[0]],
        [P[1] - L * t1[1], P[1] + L * t1[1]], color="#27ae60", lw=1.5, ls=":")
ax.text(P[0] + L * t1[0] * 1.02, P[1] + L * t1[1] * 1.02, "水平面",
        fontsize=10, color="#27ae60")
ax.plot([P[0], S[0]], [P[1], S[1]], color="#8e44ad", lw=2)  # 视线
d_ps = (S - P) / np.linalg.norm(S - P)
a_ps = np.degrees(np.arctan2(d_ps[1], d_ps[0]))
a_t1 = np.degrees(np.arctan2(t1[1], t1[0]))
ax.add_patch(Arc(P, 420, 420, theta1=min(a_ps, a_t1), theta2=max(a_ps, a_t1),
                 color="#27ae60", lw=2))
mid = np.radians((a_ps + a_t1) / 2)
lab = P + 300 * np.array([np.cos(mid), np.sin(mid)])
ax.text(lab[0], lab[1], f"仰角 ε≈{elev:.0f}°", fontsize=12, color="#27ae60")

# ---- 视场半角 η（卫星处） ----
d_nadir = (N - S) / np.linalg.norm(N - S)
d_edge = (P_up - S) / np.linalg.norm(P_up - S)
a_n = np.degrees(np.arctan2(d_nadir[1], d_nadir[0]))
a_e = np.degrees(np.arctan2(d_edge[1], d_edge[0]))
ax.add_patch(Arc(S, 900, 900, theta1=min(a_n, a_e), theta2=max(a_n, a_e),
                 color="#e74c3c", lw=2))
mid = np.radians((a_n + a_e) / 2)
lab = S + 580 * np.array([np.cos(mid), np.sin(mid)])
ax.text(lab[0], lab[1], "视场半角 η=30°", fontsize=12, color="#e74c3c")

# ---- 地心角 λ（地心处） ----
ax.add_patch(Arc((0, 0), 1200, 1200, theta1=0, theta2=np.degrees(ang),
                 color="#f39c12", lw=2))
mid = np.radians(np.degrees(ang) / 2)
lab = 830 * np.array([np.cos(mid), np.sin(mid)])
ax.text(lab[0], lab[1], f"地心角 λ≈{lam:.0f}°", fontsize=12, color="#f39c12")

ax.set_title("覆盖判定示意图（LEO 高度 600 km，视场半角 30°）\n"
             "目标点同时满足：在覆盖圆内（地心角 λ ≤ λ_max）且 仰角 ≥ 10°（最小仰角，任务门槛）",
             fontsize=13)
ax.set_xlim(-R * 1.22, R + H + R * 0.28)
ax.set_ylim(-R * 1.22, R * 1.22)
plt.tight_layout()

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "覆盖示意图.png")
fig.savefig(out, dpi=150)
print("已保存：", out)
