# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（交付版）：onedir + 数据目录 + 网页脚本。

构建：python build_delivery.py
"""

from PyInstaller.utils.hooks import collect_all

datas = [
    ("src/loader_app.py", "."),    # Streamlit 网页脚本（GUI 运行时用）
]
binaries = []
hiddenimports = [
    "query_coverage",              # loader_app.py 脚本层依赖
    "validate_loader",             # GUI ④页“运行自检”依赖
]

# 无现成 hook 的包：整包收集（含静态文件 / CBC 求解器二进制）
for _pkg in ("streamlit", "altair", "pulp"):
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

a = Analysis(
    ["src/cli.py"],
    pathex=["src"],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=[
        "validate_selection",
        "validate_sensor_params",
        "validate_china_remote",
        "validate_n2yo_crosscheck",
        "validate_crosscheck",
        "validate_multisat",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sat",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,      # 保留控制台：显示提示横幅 / CLI 输出
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="sat",
)
