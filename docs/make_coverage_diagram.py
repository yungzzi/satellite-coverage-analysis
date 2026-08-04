# -*- coding: utf-8 -*-
"""
生成"覆盖判定"示意图（docs/覆盖示意图.png）——本地放大视图版
=============================================================
用途：给老师/答辩讲解覆盖的定义：
  ① 卫星视场半角 η（传感器真实指标）决定"覆盖圆（足印）"；
  ② 目标点 P 的仰角 ε 是任务质量门槛（默认 ≥10°）；
  ③ 判定 = P 在覆盖圆内 且 ε ≥ 最小仰角。

画法：主图用"本地放大视图"（把地面拉平，角度看得清），
右下角加一个"小地球"插图说明星下点/覆盖圆/地心角 λ。

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

# ================= 主图：本地放大视图（地面拉平） =================
fig = plt.figure(figsize=(11.5, 8))
ax = fig.add_axes([0.04, 0.14, 0.92, 0.82])
ax.set_xlim(-8.5, 8.5)
ax.set_ylim(-1.3, 7.6)
ax.set_aspect("equal")
ax.axis("off")

S = np.array([0.0, 5.0])       # 卫星
N = np.array([0.0, 0.0])       # 星下点
ETA = 30.0                     # 视场半角（度）
PX = 1.8                       # 目标点 P 的水平位置
P = np.array([PX, 0.0])

# 视场锥与足印
half = 5.0 * np.tan(np.radians(ETA))          # 足印半宽 = 2.887
E_R = np.array([half, 0.0])
E_L = np.array([-half, 0.0])

# 地面
ax.plot([-8.0, 8.0], [0.0, 0.0], color="#8d6e63", lw=4, solid_capstyle="round")
ax.text(8.15, -0.18, "地面（地球表面局部）", fontsize=12, color="#8d6e63", va="center")

# 卫星与星下点
ax.plot(*S, "o", ms=17, color="#c0392b", zorder=6)
ax.annotate("卫星 S", xy=S, xytext=(0.8, 6.4), fontsize=15, color="#c0392b",
            fontweight="bold", arrowprops=dict(arrowstyle="-", color="#c0392b", lw=1.4))
ax.plot([S[0], N[0]], [S[1], N[1]], ls="--", color="#95a5a6", lw=1.5)
ax.plot(*N, "o", ms=9, color="#34495e", zorder=6)
ax.annotate("星下点 N", xy=N, xytext=(-3.4, 0.35), fontsize=13, color="#34495e",
            arrowprops=dict(arrowstyle="-", color="#34495e", lw=1.2))

# 视场锥（半透明）
ax.plot([S[0], E_R[0]], [S[1], E_R[1]], color="#e74c3c", lw=2.5)
ax.plot([S[0], E_L[0]], [S[1], E_L[1]], color="#e74c3c", lw=2.5)
ax.fill([S[0], E_R[0], E_L[0]], [S[1], E_R[1], E_L[1]], color="#e74c3c", alpha=0.05)

# 覆盖圆（足印）线段
ax.plot([E_L[0], E_R[0]], [0.02, 0.02], color="#f39c12", lw=10, alpha=0.9,
        solid_capstyle="round")
ax.plot(E_R[0], 0.0, "o", color="#f39c12", ms=8)
ax.plot(E_L[0], 0.0, "o", color="#f39c12", ms=8)
ax.text(0.0, 1.35, "覆盖圆（足印）", fontsize=13, color="#f39c12", ha="center")

# 目标点 P、视线、仰角
ax.plot(*P, "o", ms=12, color="#f39c12", zorder=6)
ax.annotate("目标点 P", xy=P, xytext=(0.2, -0.62), fontsize=13, color="#f39c12",
            arrowprops=dict(arrowstyle="-", color="#f39c12", lw=1.2))
ax.plot([P[0], S[0]], [P[1], S[1]], color="#8e44ad", lw=2.4)   # 视线

elev = np.degrees(np.arctan2(S[1] - P[1], S[0] - P[0]))
ax.add_patch(Arc(P, 2.8, 2.8, theta1=0.0, theta2=elev, color="#27ae60", lw=2.5))
mid = np.radians(elev / 2)
lab = P + 2.0 * np.array([np.cos(mid), np.sin(mid)])
ax.text(lab[0], lab[1], f"仰角 ε≈{elev:.0f}°", fontsize=13, color="#27ae60")

# 最小仰角 10°（门槛线）
ray = P + 4.6 * np.array([np.cos(np.radians(10)), np.sin(np.radians(10))])
ax.plot([P[0], ray[0]], [P[1], ray[1]], color="#27ae60", lw=2, ls="--")
ax.text(ray[0] + 0.25, ray[1] + 0.25, "最小仰角 10°（门槛）", fontsize=12,
        color="#27ae60")

# 视场半角 η
ax.add_patch(Arc(S, 2.2, 2.2, theta1=270.0, theta2=300.0, color="#e74c3c", lw=2.5))
mid = np.radians(285.0)
lab = S + 1.7 * np.array([np.cos(mid), np.sin(mid)])
ax.annotate("视场半角 η=30°", xy=lab, xytext=(2.6, 3.7), fontsize=13,
            color="#e74c3c", arrowprops=dict(arrowstyle="-", color="#e74c3c", lw=1.2))

# 判定公式
ax.text(0.0, -1.05,
        "判定：目标点 P 在覆盖圆内（λ ≤ λ_max）且 仰角 ε ≥ 最小仰角（10°）→ 有效覆盖",
        ha="center", fontsize=13,
        bbox=dict(boxstyle="round,pad=0.45", facecolor="#fdf6e3", edgecolor="#95a5a6"))

# ================= 右上角：小地球插图（地心角 λ / 星下点 / 足印） =================
iax = fig.add_axes([0.72, 0.60, 0.26, 0.26])
iax.set_xlim(-1.5, 1.5)
iax.set_ylim(-1.5, 1.6)
iax.set_aspect("equal")
iax.axis("off")

th = np.linspace(0, 2 * np.pi, 300)
iax.plot(np.cos(th), np.sin(th), color="#95a5a6", lw=1.2)
iax.fill(np.cos(th), np.sin(th), color="#ecf0f1", alpha=0.8)
iax.plot(0, 0, "+", color="#7f8c8d", ms=6)
iax.text(0.08, -0.08, "O", fontsize=8, color="#7f8c8d")
iax.plot(0, 1.0, "o", ms=5, color="#34495e")
iax.text(0.10, 1.02, "N", fontsize=8, color="#34495e")
iax.plot(0, 1.28, "o", ms=6, color="#c0392b")
iax.text(0.10, 1.32, "S", fontsize=8, color="#c0392b")
# 足印弧（±22°）
arcp = np.linspace(np.radians(68), np.radians(112), 100)
iax.plot(np.cos(arcp), np.sin(arcp), color="#f39c12", lw=4, solid_capstyle="round")
# 地心角 λ 弧
iax.add_patch(Arc((0, 0), 0.62, 0.62, theta1=68.0, theta2=90.0, color="#2980b9", lw=2))
iax.text(0.06, 0.46, "λ", fontsize=9, color="#2980b9")
iax.text(0.0, -1.30, "小地球：星下点 N、覆盖圆（足印）、地心角 λ",
         ha="center", fontsize=9, color="#7f8c8d")

fig.text(0.04, 0.965, "覆盖判定示意图（本地放大视图，非等比例）", fontsize=16,
         fontweight="bold", color="#2c3e50")

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "覆盖示意图.png")
fig.savefig(out, dpi=150)
print("已保存：", out)
