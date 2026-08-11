# -*- coding: utf-8 -*-
"""构建交付版：PyInstaller 打包 → 组装交付文件夹 → 写《使用说明.txt》。

用法：.venv\\Scripts\\python build_delivery.py
输出：D:\\研究生学习资料\\02_课题组工作\\长期项目\\卫星覆盖分析-交付版\\
原工程文件夹不会被改动。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist" / "sat"
TARGET = Path(r"D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖分析-交付版")


USAGE = """卫星覆盖分析工作台 —— 使用说明

一、启动
双击本文件夹里的 sat.exe，在弹出的窗口中选择交互方式：
  - GUI 网页版（强烈推荐，需要电脑有浏览器）：自动打开默认浏览器，
    界面和开发版一致（搜索/收藏夹/星座导入/下载与自检/覆盖分析与选星）。
  - CLI 命令行：进入交互菜单，按数字选功能，不用记命令。

二、命令行快捷用法（在“卫星覆盖分析-交付版”文件夹里打开终端）
  .\\sat search GAOFEN-1          搜索卫星（含变体重查与拼写提示）
  .\\sat add 我的夹 25544 41194    往夹里加星
  .\\sat resolve 我的夹           解析并下载 TLE
  .\\sat cons GPS 我的GPS          星座导入
  .\\sat select 我的夹 --start "2026-08-11 00:00:00" --end "2026-08-11 08:00:00"
  .\\sat run 我的夹               一键流程：覆盖→连续覆盖选星→Excel→虚拟星座
  .\\sat menu                     交互菜单
  .\\sat check                    自检
  .\\sat --help                   全部命令
常用命令列表：gui / menu / search / norad / catalog / create / rm / add / del /
resolve / import / cons / copy / move / locations / loc / sensors / sensor /
update / check / select / run。

三、数据说明
  data/                    内置星座目录、常用地点、地区边界、用户收藏与缓存
  data/tle_cache/          卫星 TLE 缓存（已带上，可离线用一部分）
  首次联网使用会自动下载最新 TLE；CelesTrak 数据约每 2 小时更新一次，
  频繁强制更新会被对方限流（显示 403 时稍后再试，程序会自动用本地缓存）。
  需要更全的国产遥感星数据，可到 SpaceMapper 遥感星座目录
  （spacemapper.cn，需注册/登录，token 属用户自己的账号）下载 TLE 文件，
  然后在 GUI ②/③/⑤页或 .\\sat import 导入。

四、常见问题
  1. 关闭浏览器标签页不会停止程序，重新打开 http://localhost:8501 即可回来；
     彻底停止：点界面左侧“停止并退出程序”按钮，或按 Ctrl+C。
  2. 端口 8501 被占用时程序会报错，关掉占用该端口的程序后重试。
  3. 网络不好时下载可能失败，稍后重试；离线可用已有缓存（命令加 --offline）。
  4. 本文件夹可以整体拷贝到别的电脑/路径使用（数据随文件夹走）。
"""


def main() -> int:
    py = sys.executable
    print("== 1/3 开始 PyInstaller 打包（首次约 3~8 分钟） ==")
    subprocess.run([py, "-m", "PyInstaller", "--clean", "--noconfirm",
                    str(ROOT / "delivery.spec")], check=True, cwd=ROOT)

    print("== 2/3 组装交付文件夹 ==")
    if not DIST.exists():
        print(f"打包产物不存在：{DIST}")
        return 1
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(DIST, TARGET)
    # 数据目录（内置目录/用户数据/TLE 缓存）放在交付文件夹根目录，随文件夹整体拷贝
    shutil.copytree(ROOT / "data", TARGET / "data")

    print("== 3/3 写入使用说明 ==")
    (TARGET / "使用说明.txt").write_text(USAGE, encoding="utf-8")
    size_mb = sum(f.stat().st_size for f in TARGET.rglob("*")
                  if f.is_file()) / 1024 / 1024
    print(f"完成：{TARGET}（{size_mb:.0f} MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
