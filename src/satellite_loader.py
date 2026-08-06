# -*- coding: utf-8 -*-
"""
satellite_loader.py —— 卫星数据加载器（阶段 1，独立模块）

职责（对应用户确认过的设计）：
1. 搜索：从 CelesTrak 免费接口（无需 token）按 名字 / NORAD 编号 / 常用指标 搜索卫星。
   本地有一份"编目缓存"（satcat.csv），可离线按 国家 / 发射年份 / 高度 过滤。
2. 收藏夹：内置目录（只读，随软件发布）+ 用户收藏（单文件 data/user_catalog.toml）。
   支持 新建 / 改名 / 删除（交互时提示一次）/ 移动条目；夹内条目三种类型：
     name_filter —— 名字规则（"活的"，更新时自动纳入新星）
     norad_list  —— 手动挑选的星 / 编号清单（"固定快照"，新星不自动进来）
     file        —— 导入的 TLE / 编号文件（程序复制到 data/user_files/ 统一管理）
3. 下载与缓存：下载的文件存 data/tle_cache/，超时才重新下载，不重复请求。
4. 解析：把 TLE 解析成"包装类" Satellite（内部装 skyfield 的 EarthSatellite，
   对外提供 name / norad_id / sensor 字段，并把 .at() 等调用透传给轨道对象）。
5. 交互菜单：命令行交互；以后做图形界面只需替换这一层，核心函数不动。

阶段约定：本模块不修改 coverage_analysis.py，可被 validate_loader.py 独立测试。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    tomllib = None

from skyfield.api import EarthSatellite, load


# ==================== 0. 路径与常量 ====================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "tle_cache"          # 下载缓存（不入库）
USER_FILES_DIR = DATA_DIR / "user_files"    # 用户导入文件的副本（程序管理，不入库）
BUILTIN_CATALOG = DATA_DIR / "satellite_catalog.toml"   # 内置目录（入库）
CONSTELLATIONS_CATALOG = DATA_DIR / "constellations.toml"  # 星座目录（入库，④页用）
USER_CATALOG = DATA_DIR / "user_catalog.toml"           # 用户收藏（不入库）

# CelesTrak 免费接口（URL 即接口，无需 token）
CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php"
# 编目元数据接口（名字/所属/发射日期/轨道参数；不含 TLE，TLE 走 gp.php）
CELESTRAK_SATCAT = "https://celestrak.org/satcat/records.php"
# 全集现役 TLE（一次下载覆盖大多数单星查询，避免逐个请求）
CELESTRAK_ACTIVE = f"{CELESTRAK_GP}?GROUP=active&FORMAT=TLE"
USER_AGENT = "satellite-coverage-analysis/0.1 (educational project)"

# 缓存默认过期时间（小时）：搜索结果/编目 24 小时更新一次即可
DEFAULT_MAX_AGE_HOURS = 24

_ACTIVE_FAILED = False   # 本次运行中全集缓存下载失败过则不再重复尝试

CACHE_README = """\
data/tle_cache/ —— 卫星数据缓存目录（程序自动维护；整个目录可随时删除，会自动重新下载）

文件说明：
- active.tle        全集现役 TLE：下载时刻"所有现役卫星"的快照（不是只存查过的星）；
                    网络差时可能下载失败而缺失，程序会自动退回逐个单星下载
- meta/*.csv        搜索结果"卫星档案表"（CelesTrak records.php，CSV 表格）
- name/*.tle        按名字搜索的 TLE（gp.php?NAME=）
- group/*.tle       按星座整组下载的 TLE（gp.php?GROUP=starlink 等，④页星座导入用）
- norad/*.tle       单颗星 TLE（gp.php?CATNR=）
- resolved/*.tle    成品：每个收藏夹解析后的合并 TLE（人看这个文件夹即可）

缓存默认 24 小时过期后自动更新；GUI ③页有"缓存管理"可查看/清空。
"""


# ==================== 1. 包装类 Satellite ====================

class Satellite:
    """包装类：内部装 skyfield 的 EarthSatellite（发动机），外面贴铭牌。

    - orbit   : EarthSatellite 轨道对象（负责 SGP4 计算）
    - name    : 卫星名
    - norad_id: NORAD 编目编号（TLE 第一行的 3~7 位）
    - sensor  : 以后放该星专属传感器参数；现在 None = 用全局默认
    - 透传    : 找不到的属性（如 .at()）自动转发给 orbit，覆盖算法无感知
    """

    def __init__(self, orbit: EarthSatellite, name: str, norad_id: int,
                 sensor: Optional[dict] = None):
        self.orbit = orbit
        self.name = name
        self.norad_id = norad_id
        self.sensor = sensor

    def __getattr__(self, item):
        # 只有普通查找失败才进来：把 .at/.model 等全部转给轨道对象
        return getattr(self.orbit, item)

    def __repr__(self):
        return f"Satellite({self.name!r}, NORAD {self.norad_id})"


def _extract_norad_id(line1: str) -> Optional[int]:
    """从 TLE 第 1 行提取 NORAD 编号（第 3~7 列，0 起 2:7）。"""
    try:
        return int(line1[2:7].strip())
    except (ValueError, IndexError):
        return None


def parse_tle_text(text: str, ts=None) -> List[Satellite]:
    """把 3LE 文本（名字+第1行+第2行）解析成 Satellite 包装对象列表。"""
    if ts is None:
        ts = load.timescale(builtin=True)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    sats: List[Satellite] = []
    i = 0
    while i + 2 < len(lines):
        name = lines[i]
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            norad = _extract_norad_id(lines[i + 1])
            try:
                orbit = EarthSatellite(lines[i + 1], lines[i + 2], name, ts)
                sats.append(Satellite(orbit, name, norad))
            except Exception as e:
                print(f"  跳过解析失败的卫星 {name}: {e}")
            i += 3
        else:
            i += 1
    return sats


def parse_tle_records(text: str) -> List[dict]:
    """把 3LE 文本解析成字典记录列表（不创建轨道对象，搜索时更快）。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    records: List[dict] = []
    i = 0
    while i + 2 < len(lines):
        name = lines[i]
        if lines[i + 1].startswith("1 ") and lines[i + 2].startswith("2 "):
            records.append({
                "name": name,
                "norad_id": _extract_norad_id(lines[i + 1]),
                "line1": lines[i + 1],
                "line2": lines[i + 2],
            })
            i += 3
        else:
            i += 1
    return records


# ==================== 2. 下载与缓存 ====================

def _ensure_dirs():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    USER_FILES_DIR.mkdir(parents=True, exist_ok=True)
    _clean_legacy_cache()
    _write_cache_readme()


def _write_cache_readme():
    """在缓存目录放一个说明文件，方便在资源管理器里直接看懂。"""
    try:
        (CACHE_DIR / "README.txt").write_text(CACHE_README, encoding="utf-8")
    except OSError:
        pass


def _clean_legacy_cache():
    """清理早期版本留在缓存根目录的散文件（新版本只允许 active.tle 在根目录）。"""
    if not CACHE_DIR.exists():
        return
    for p in CACHE_DIR.iterdir():
        if p.is_file() and p.name not in ("active.tle", "README.txt"):
            try:
                p.unlink()
            except OSError:
                pass
    # 早期版本 name/norad 用 .txt 后缀，统一为 .tle 后清理旧文件
    for sub in ("name", "norad"):
        d = CACHE_DIR / sub
        if d.exists():
            for p in d.glob("*.txt"):
                try:
                    p.unlink()
                except OSError:
                    pass


def _age_hours(path: Path) -> float:
    """文件已存在多久（小时）。"""
    if not path.exists():
        return float("inf")
    return (time.time() - path.stat().st_mtime) / 3600.0


def download_text(url: str, cache_name: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                  force: bool = False, offline: bool = False,
                  retries: int = 3) -> Optional[Path]:
    """下载文本文件并缓存；缓存未过期直接复用。返回本地文件路径。

    - cache_name: 缓存文件名（如 satcat.csv / search_YAOGAN.txt）
    - offline   : True 时不联网，只用缓存；缓存缺失则返回 None
    """
    _ensure_dirs()
    cache_path = CACHE_DIR / cache_name
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if cache_path.exists() and not force and _age_hours(cache_path) < max_age_hours:
        print(f"  [缓存命中] {cache_name}（{_age_hours(cache_path):.1f} 小时前下载）")
        return cache_path
    if offline:
        print(f"  [离线] 缓存 {cache_name} 缺失或过期，且离线模式不联网，跳过下载。")
        return None

    data = None
    last_err = None
    for attempt in range(1, retries + 1):   # 网络抖动自动重试
        print(f"  [下载] {url}（第 {attempt}/{retries} 次尝试）")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            break
        except urllib.error.HTTPError as e:
            print(f"  [失败] HTTP {e.code}: {url}")
            return None
        except Exception as e:
            last_err = e
            print(f"  [重试] 网络错误：{e}")
            if attempt < retries:
                time.sleep(2 * attempt)
    if data is None:
        print(f"  [失败] 重试 {retries} 次仍失败：{last_err}")
        return None

    # 先写临时文件再改名，避免半截文件污染缓存
    fd, tmp_name = tempfile.mkstemp(dir=str(CACHE_DIR), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, cache_path)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)
    print(f"  [完成] 已缓存到 {cache_path}")
    return cache_path


# ==================== 3. 编目搜索（CelesTrak satcat） ====================


def _safe_cache_name(keyword: str, suffix: str) -> str:
    """把用户输入的关键字转成安全的缓存文件名。"""
    safe = "".join(ch for ch in keyword if ch.isalnum() or ch in "-_").strip() or "search"
    return f"{safe[:40]}{suffix}"


def search_metadata(keyword: str,
                    max_results: int = 10000,
                    country: Optional[str] = None,
                    launch_year: Optional[str] = None,
                    min_alt_km: Optional[float] = None,
                    max_alt_km: Optional[float] = None,
                    max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                    offline: bool = False) -> List[dict]:
    """按名字关键字搜索编目元数据（服务器端查询，结果按关键字缓存）。

    只返回"在轨载荷"（PAYLOADS=1&ONORBIT=1），过滤掉碎片/火箭体/已再入物体；
    元数据包含 所属/发射日期/轨道参数，可用于展示和二次筛选。
    注意：编目不直接含 TLE；真正解析时走 gp.php。
    """
    import urllib.parse
    url = (f"{CELESTRAK_SATCAT}?NAME={urllib.parse.quote(keyword)}"
           f"&FORMAT=CSV&MAX={int(max_results)}&PAYLOADS=1&ONORBIT=1")
    path = download_text(url, f"meta/{_safe_cache_name(keyword, '_meta.csv')}",
                         max_age_hours, offline=offline)
    if path is None:
        return []

    out = []
    for row in _parse_meta_csv(path):
        if country and country.upper() != row.get("owner", "").upper():
            continue
        if launch_year and not row.get("launch_date", "").startswith(launch_year):
            continue
        alt = _row_altitude(row)
        if min_alt_km is not None and (alt is None or alt < min_alt_km):
            continue
        if max_alt_km is not None and (alt is None or alt > max_alt_km):
            continue
        out.append(_row_to_meta(row))
    return out


def _row_altitude(row: dict) -> Optional[float]:
    """记录的平均轨道高度（km）；编目里可能缺字段，取不到返回 None。"""
    try:
        apo = float(row.get("apogee", ""))
        peri = float(row.get("perigee", ""))
        return (apo + peri) / 2.0
    except ValueError:
        return None


def _safe_float(v: str) -> Optional[float]:
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _parse_meta_csv(path: Path) -> List[dict]:
    """解析 CelesTrak satcat 返回的 CSV（键名统一小写）。"""
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                row = {k.lower().strip(): (v or "").strip() for k, v in raw.items()}
                rows.append(row)
    except Exception as e:
        print(f"  [失败] 读取缓存失败：{e}")
        return []
    return rows


def _row_to_meta(row: dict) -> dict:
    """把 satcat 的一行转成统一的元数据字典（搜索/补全共用）。"""
    try:
        rid = int(row.get("norad_cat_id", ""))
    except ValueError:
        rid = None
    return {
        "name": row.get("object_name", ""),
        "norad_id": rid,
        "object_id": row.get("object_id", ""),   # 国际编号，如 1998-067A
        "country": row.get("owner", ""),
        "launch_date": row.get("launch_date", ""),
        "object_type": row.get("object_type", ""),
        "decayed": False,   # 已按 ONORBIT=1 过滤
        "inclination": _safe_float(row.get("inclination", "")),
        "altitude_km": _row_altitude(row),
        "has_tle": False,   # 编目只有元数据；TLE 通过 gp.php 获取
    }


def search_metadata_by_norad(norad_id: int,
                             max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                             offline: bool = False) -> List[dict]:
    """按 NORAD 编号查单星元数据（records.php?CATNR=，缓存到 meta/catnr_*.csv）。"""
    url = f"{CELESTRAK_SATCAT}?CATNR={norad_id}&FORMAT=CSV&PAYLOADS=1&ONORBIT=1"
    path = download_text(url, f"meta/catnr_{norad_id}.csv",
                         max_age_hours, offline=offline)
    if path is None:
        return []
    return [_row_to_meta(row) for row in _parse_meta_csv(path)]


def search_metadata_by_intdes(intdes: str,
                              max_results: int = 500,
                              max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                              offline: bool = False) -> List[dict]:
    """按国际编号（发射编号，如 1998-067A）查该次发射的在轨载荷元数据。

    records.php?INTDES=... 支持一次发射的多个物体；PAYLOADS=1&ONORBIT=1
    过滤掉碎片/火箭体/已再入物体，和名字搜索口径一致。
    """
    import urllib.parse
    url = (f"{CELESTRAK_SATCAT}?INTDES={urllib.parse.quote(intdes)}&FORMAT=CSV"
           f"&MAX={int(max_results)}&PAYLOADS=1&ONORBIT=1")
    path = download_text(url, f"meta/{_safe_cache_name(intdes, '_intdes.csv')}",
                         max_age_hours, offline=offline)
    if path is None:
        return []
    return [_row_to_meta(row) for row in _parse_meta_csv(path)]


def search_by_name_online(keyword: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                          offline: bool = False) -> List[dict]:
    """在线按名字模糊搜索（CelesTrak gp.php?NAME=...），返回 TLE 记录列表。"""
    import urllib.parse
    url = f"{CELESTRAK_GP}?NAME={urllib.parse.quote(keyword)}&FORMAT=TLE"
    path = download_text(url, f"name/{_safe_cache_name(keyword, '.tle')}", max_age_hours,
                         offline=offline)
    if path is None:
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        return parse_tle_records(f.read())


def search_by_norad_online(norad_id: int, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                           offline: bool = False) -> List[dict]:
    """在线按 NORAD 编号查询单星（CelesTrak gp.php?CATNR=...）。"""
    url = f"{CELESTRAK_GP}?CATNR={norad_id}&FORMAT=TLE"
    path = download_text(url, f"norad/{norad_id}.tle", max_age_hours, offline=offline)
    if path is None:
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        return parse_tle_records(f.read())


def suggest_similar_names(keyword: str, n: int = 3, cutoff: float = 0.6,
                          max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                          offline: bool = False) -> List[str]:
    """拼写纠错提示：在全集 TLE 名字表里找编辑距离最近的名字（标准库 difflib）。

    仅用于"搜不到"时的提示（"你是不是想找 X？"），不自动替换搜索结果。
    名字表来自 active.tle（全集现役 TLE 缓存），约 1.6 万条，毫秒级匹配。
    先把带编号的全名（如 STARLINK-1234）归并成"家族名"（STARLINK）再匹配，
    否则 difflib 拿短关键字匹配长编号名会找不到（Q79 实测教训）。
    """
    import difflib
    import re
    _, active_recs = load_active_tle(max_age_hours, offline=offline)
    fams = set()
    for r in active_recs:
        # 去掉第一个数字之后的部分，再清掉两端分隔符：STARLINK-1234 -> STARLINK
        fam = re.sub(r"[0-9].*$", "", r.get("name", "")).strip(" -_")
        if len(fam) >= 3:
            fams.add(fam)
    names = sorted(fams)
    if not names:
        return []
    return difflib.get_close_matches(keyword, names, n=n, cutoff=cutoff)


def ensure_active_tle(max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                      force: bool = False, offline: bool = False) -> Optional[Path]:
    """确保本地有"全集现役 TLE"缓存（一次下载，单星/名字查询优先查它）。

    全集文件较大、网络差时容易失败，只试 1 次；失败会自动退回逐个单星下载。
    """
    global _ACTIVE_FAILED
    if _ACTIVE_FAILED and not force:
        return None
    path = download_text(CELESTRAK_ACTIVE, "active.tle", max_age_hours,
                         force, offline, retries=1)
    if path is None and not offline:
        _ACTIVE_FAILED = True
    return path


def load_active_tle(max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                    offline: bool = False):
    """读取全集缓存，返回 (by_id: {NORAD: 记录}, records: [记录, ...])。"""
    path = ensure_active_tle(max_age_hours, offline=offline)
    if path is None:
        return {}, []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        records = parse_tle_records(f.read())
    by_id = {}
    for r in records:
        if r.get("norad_id") is not None:
            by_id[r["norad_id"]] = r
    return by_id, records


# ==================== 4. 收藏夹（内置 + 用户） ====================

def load_builtin_collections() -> List[dict]:
    """读取内置目录（只读）。格式见 data/satellite_catalog.toml。"""
    if tomllib is None or not BUILTIN_CATALOG.exists():
        return []
    with open(BUILTIN_CATALOG, "rb") as f:
        data = tomllib.load(f)
    collections = []
    for c in data.get("collections", []):
        entries = _dedupe_entries(c.get("entries", []))
        collections.append({
            "name": c.get("name", "未命名"),
            "description": c.get("description", ""),
            "builtin": True,
            "entries": entries,
        })
    return collections


def load_user_collections() -> List[dict]:
    """读取用户收藏；文件不存在时返回空列表（第一次运行会创建）。"""
    if tomllib is None or not USER_CATALOG.exists():
        return []
    try:
        with open(USER_CATALOG, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        print(f"  [警告] 用户收藏文件损坏，按空收藏处理：{e}")
        return []
    collections = []
    for c in data.get("collections", []):
        entries = _dedupe_entries(c.get("entries", []))
        collections.append({
            "name": c.get("name", "未命名"),
            "description": c.get("description", ""),
            "builtin": False,
            "entries": entries,
        })
    return collections


def _dedupe_entries(entries: list) -> list:
    """去掉完全重复的条目（同类型 + 同值只保留一条）。"""
    seen = set()
    out = []
    for e in entries:
        key = (e.get("type", ""), e.get("value", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append({"type": e["type"], "value": e["value"]})
    return out


def _dump_collections(collections: List[dict]) -> str:
    """把收藏夹列表写成 TOML 文本（结构简单，手写即可，无需第三方库）。"""
    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'

    lines = []
    for c in collections:
        lines.append("[[collections]]")
        lines.append(f"name = {q(c['name'])}")
        if c.get("description"):
            lines.append(f"description = {q(c['description'])}")
        lines.append("entries = [")
        for e in c["entries"]:
            lines.append(f"  {{ type = {q(e['type'])}, value = {q(e['value'])} }},")
        lines.append("]")
        lines.append("")
    return "\n".join(lines)


def save_user_collections(collections: List[dict]):
    """保存用户收藏到 data/user_catalog.toml（先写临时文件再替换）。"""
    _ensure_dirs()
    text = ("# 用户收藏夹（程序自动维护，无需手动编辑）\n"
            "# 条目类型：name_filter=名字规则 / norad_list=编号清单 / file=导入文件\n\n"
            + _dump_collections(collections))
    fd, tmp_name = tempfile.mkstemp(dir=str(USER_CATALOG.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp_name, USER_CATALOG)
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def load_all_collections() -> List[dict]:
    """内置 + 用户 合并（内置在前，标 builtin=True）。"""
    return load_builtin_collections() + load_user_collections()


def load_constellations() -> List[dict]:
    """读取星座目录（data/constellations.toml，入库只读），④页"星座导入"用。"""
    if tomllib is None or not CONSTELLATIONS_CATALOG.exists():
        return []
    with open(CONSTELLATIONS_CATALOG, "rb") as f:
        data = tomllib.load(f)
    return list(data.get("constellations", []))


def find_collection(collections: List[dict], name: str) -> Optional[dict]:
    for c in collections:
        if c["name"] == name:
            return c
    return None


def create_collection(name: str, description: str = "") -> bool:
    """新建用户收藏夹；同名返回 False。"""
    user = load_user_collections()
    if find_collection(user, name) or find_collection(load_builtin_collections(), name):
        print(f"  [失败] 已存在同名收藏夹：{name}")
        return False
    user.append({"name": name, "description": description, "builtin": False, "entries": []})
    save_user_collections(user)
    print(f"  [完成] 已新建收藏夹：{name}")
    return True


def rename_collection(old_name: str, new_name: str) -> bool:
    """改用户收藏夹名；内置夹只读不可改。"""
    user = load_user_collections()
    c = find_collection(user, old_name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{old_name}")
        return False
    if find_collection(user, new_name) or find_collection(load_builtin_collections(), new_name):
        print(f"  [失败] 已存在同名收藏夹：{new_name}")
        return False
    c["name"] = new_name
    save_user_collections(user)
    print(f"  [完成] 已改名：{old_name} -> {new_name}")
    return True


def delete_collection(name: str) -> bool:
    """删除用户收藏夹（交互菜单会先提示一次）。"""
    user = load_user_collections()
    c = find_collection(user, name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{name}")
        return False
    user.remove(c)
    save_user_collections(user)
    print(f"  [完成] 已删除收藏夹：{name}")
    return True


def copy_builtin_to_user(name: str, new_name: Optional[str] = None) -> bool:
    """把内置夹复制到用户收藏（可改名），方便用户自由修改。"""
    builtin = load_builtin_collections()
    c = find_collection(builtin, name)
    if c is None:
        print(f"  [失败] 未找到内置收藏夹：{name}")
        return False
    target = new_name or (name + "（副本）")
    if find_collection(load_user_collections(), target):
        print(f"  [失败] 已存在同名用户收藏夹：{target}")
        return False
    user = load_user_collections()
    user.append({"name": target, "description": c.get("description", ""),
                 "builtin": False,
                 "entries": [dict(e) for e in c["entries"]]})
    save_user_collections(user)
    print(f"  [完成] 已复制内置夹「{name}」到用户收藏「{target}」")
    return True


def add_entry(collection_name: str, entry_type: str, value: str) -> bool:
    """向用户收藏夹加一条目（name_filter / norad_list / file）。"""
    user = load_user_collections()
    c = find_collection(user, collection_name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{collection_name}")
        return False
    if entry_type not in ("name_filter", "norad_list", "file", "celestrak_group"):
        print(f"  [失败] 未知条目类型：{entry_type}")
        return False
    for e in c["entries"]:
        if e["type"] == entry_type and e["value"] == value:
            print(f"  [跳过] 「{collection_name}」已存在相同条目：{entry_type} = {value}")
            return False
    c["entries"].append({"type": entry_type, "value": value})
    save_user_collections(user)
    print(f"  [完成] 已加入「{collection_name}」：{entry_type} = {value}")
    return True


def add_norad_to_collection(collection_name: str, norad_ids: List[int]) -> bool:
    """向收藏夹追加 NORAD 编号：已有编号清单则合并去重，否则新建一条清单。"""
    user = load_user_collections()
    c = find_collection(user, collection_name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{collection_name}")
        return False
    new_ids = {str(i) for i in norad_ids if str(i).isdigit()}
    if not new_ids:
        return False
    target = None
    for e in c["entries"]:
        if e["type"] == "norad_list":
            existing = {x for x in e["value"].replace(",", " ").split() if x.strip().isdigit()}
            merged = sorted(existing | new_ids, key=lambda x: int(x))
            e["value"] = " ".join(merged)
            target = e
            break
    if target is None:
        c["entries"].append({"type": "norad_list",
                             "value": " ".join(sorted(new_ids, key=lambda x: int(x)))})
    save_user_collections(user)
    print(f"  [完成] 已向「{collection_name}」添加编号：{' '.join(sorted(new_ids, key=lambda x: int(x)))}")
    return True


def remove_norad_from_collection(collection_name: str, norad_id: int) -> bool:
    """从收藏夹的编号清单中删除单个编号（清单删空则整条移除）。"""
    user = load_user_collections()
    c = find_collection(user, collection_name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{collection_name}")
        return False
    rid = str(norad_id)
    removed = False
    for e in list(c["entries"]):
        if e["type"] == "norad_list":
            ids = [x for x in e["value"].replace(",", " ").split() if x.strip().isdigit()]
            if rid in ids:
                ids.remove(rid)
                removed = True
            if ids:
                e["value"] = " ".join(sorted(ids, key=lambda x: int(x)))
            else:
                c["entries"].remove(e)
    if removed:
        save_user_collections(user)
        print(f"  [完成] 已从「{collection_name}」移除编号 {rid}")
    else:
        print(f"  [跳过] 编号 {rid} 不在「{collection_name}」中")
    return removed


def remove_entry(collection_name: str, index: int) -> bool:
    """按序号删除用户收藏夹里的条目（0 起）。"""
    user = load_user_collections()
    c = find_collection(user, collection_name)
    if c is None:
        print(f"  [失败] 未找到用户收藏夹：{collection_name}")
        return False
    if not 0 <= index < len(c["entries"]):
        print(f"  [失败] 序号越界：{index}")
        return False
    e = c["entries"].pop(index)
    save_user_collections(user)
    print(f"  [完成] 已移除「{collection_name}」第 {index + 1} 条：{e['type']} = {e['value']}")
    return True


def move_entry(from_name: str, index: int, to_name: str) -> bool:
    """把条目从一个用户收藏夹移动到另一个（支持跨夹整理）。"""
    user = load_user_collections()
    src = find_collection(user, from_name)
    dst = find_collection(user, to_name)
    if src is None or dst is None:
        print(f"  [失败] 收藏夹不存在：{from_name} / {to_name}")
        return False
    if not 0 <= index < len(src["entries"]):
        print(f"  [失败] 序号越界：{index}")
        return False
    e = src["entries"].pop(index)
    dst["entries"].append(e)
    save_user_collections(user)
    print(f"  [完成] 已把「{from_name}」的条目移到「{to_name}」")
    return True


def import_file_to_collection(file_path: str, collection_name: str,
                              new_collection: bool = False) -> bool:
    """导入本地 TLE / NORAD 编号文件：复制到 data/user_files/ 并加入收藏夹。"""
    src = Path(file_path)
    if not src.exists():
        print(f"  [失败] 文件不存在：{src}")
        return False
    _ensure_dirs()
    dst = USER_FILES_DIR / src.name
    if dst.exists():
        dst = USER_FILES_DIR / f"{src.stem}_{int(time.time())}{src.suffix}"
    try:
        import shutil
        shutil.copy2(src, dst)
    except Exception as e:
        print(f"  [失败] 复制文件失败：{e}")
        return False
    print(f"  [完成] 已复制文件到：{dst}")

    if new_collection:
        if not create_collection(collection_name):
            return False
    return add_entry(collection_name, "file", str(dst))


# ==================== 5. 把收藏夹解析成卫星列表 ====================

def _records_from_file(file_path: str, max_age_hours: float, offline: bool,
                       progress=None) -> List[dict]:
    """读本地文件：TLE 文本 或 NORAD 编号清单。"""
    path = Path(file_path)
    if not path.exists():
        print(f"  [警告] 导入文件不存在，跳过：{path}")
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        text = f.read()
    records = parse_tle_records(text)
    if records:
        return records
    # 不是 TLE，按 NORAD 编号清单处理（一行一个，或逗号/空格分隔）
    ids = []
    for tok in text.replace(",", " ").split():
        if tok.strip().isdigit():
            ids.append(int(tok))
    return _records_by_norad_ids(ids, max_age_hours, offline, progress=progress)


def _records_by_norad_ids(norad_ids: List[int], max_age_hours: float,
                          offline: bool, progress=None) -> List[dict]:
    """按编号清单找 TLE：优先查"全集现役 TLE"缓存（一次下载覆盖大多数），
    查不到的个别编号才逐个走 gp.php?CATNR=（带缓存与小延时）。"""
    by_id, _ = load_active_tle(max_age_hours, offline=offline)
    out = []
    total = len(norad_ids)
    for i, rid in enumerate(norad_ids, 1):
        r = by_id.get(rid)
        if r is not None:
            out.append(r)
        else:
            recs = search_by_norad_online(rid, max_age_hours, offline=offline)
            if recs:
                out.append(recs[0])
            else:
                print(f"  [警告] 查不到编号 {rid}")
            time.sleep(0.2)  # 礼貌性小延时，避免高频请求
        if progress:
            progress(i, total, f"获取编号 {rid}（{i}/{total}）")
    return out


def _is_debris_name(name: str) -> bool:
    """根据名字判断是否为碎片/火箭体（黑名单过滤用，大小写不敏感）。"""
    n = name.upper()
    return "DEB" in n or "R/B" in n


def _records_by_name_filter(keyword: str, max_age_hours: float,
                            offline: bool) -> List[dict]:
    """按名字规则找 TLE：优先查"全集现役 TLE"缓存（按名字子串匹配），
    黑名单排除名字含碎片/火箭体标记的物体，保证只含载荷；
    缓存里没有匹配时退回 gp.php?NAME=。

    注意：早期版本用"编目白名单"（records.php 元数据）过滤，但该接口有约一万条
    硬上限，STARLINK 会漏 769 颗；改为黑名单后全集内载荷全量保留
    （实测 YAOGAN/GAOFEN 两种口径结果一致，无副作用）。
    """
    _, active_recs = load_active_tle(max_age_hours, offline=offline)
    recs = [r for r in active_recs if keyword.lower() in r.get("name", "").lower()
            and not _is_debris_name(r.get("name", ""))]
    if recs:
        return recs
    online = search_by_name_online(keyword, max_age_hours, offline=offline)
    return [r for r in online if not _is_debris_name(r.get("name", ""))]


def _records_by_group(group: str, max_age_hours: float,
                      offline: bool) -> List[dict]:
    """按 CelesTrak 星座分组（GROUP=starlink / beidou 等）下载整组 TLE。

    分组是官方维护的"一个星座一整包"，不受 records.php 约万条上限影响；
    同样做黑名单过滤（个别分组可能混入火箭体）。
    """
    import urllib.parse
    url = f"{CELESTRAK_GP}?GROUP={urllib.parse.quote(group)}&FORMAT=TLE"
    path = download_text(url, f"group/{_safe_cache_name(group, '.tle')}",
                         max_age_hours, offline=offline)
    if path is None:
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        recs = parse_tle_records(f.read())
    return [r for r in recs if not _is_debris_name(r.get("name", ""))]


def _entry_units(entry: dict) -> int:
    """一个条目要处理的"单元"数（编号清单按编号数算，其他按 1 算）。"""
    if entry.get("type") == "norad_list":
        ids = [x for x in entry.get("value", "").replace(",", " ").split()
               if x.strip().isdigit()]
        return max(1, len(ids))
    return 1


def collection_units(collection: dict) -> int:
    """一个收藏夹的解析单元总数（用于进度条）。"""
    return sum(_entry_units(e) for e in collection.get("entries", []))


def resolve_collection(collection: dict, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                       offline: bool = False, progress=None) -> List[dict]:
    """把一个收藏夹解析成 TLE 记录列表（按 NORAD 编号去重，保留顺序）。

    progress 是可选的进度回调：progress(done, total, 说明文字)。
    """
    records: Dict[int, dict] = {}
    total = collection_units(collection)
    done = 0
    for entry in collection["entries"]:
        etype, value = entry["type"], entry["value"]
        units = _entry_units(entry)
        if etype == "name_filter":
            recs = _records_by_name_filter(value, max_age_hours, offline)
            done += units
            if progress:
                progress(done, total, f"处理条目：{_entry_label(entry)}")
        elif etype == "celestrak_group":
            recs = _records_by_group(value, max_age_hours, offline)
            done += units
            if progress:
                progress(done, total, f"处理条目：{_entry_label(entry)}")
        elif etype == "norad_list":
            ids = [int(x) for x in value.replace(",", " ").split() if x.strip().isdigit()]
            def sub(d, t, m):
                if progress:
                    progress(done + d, total, m)
            recs = _records_by_norad_ids(ids, max_age_hours, offline, progress=sub)
            done += units
        elif etype == "file":
            def sub2(d, t, m):
                if progress:
                    progress(done + d, total, m)
            recs = _records_from_file(value, max_age_hours, offline, progress=sub2)
            done += units
        else:
            print(f"  [警告] 未知条目类型：{etype}，跳过")
            continue
        for r in recs:
            if r.get("norad_id") is not None:
                records[r["norad_id"]] = r
    return list(records.values())


def resolve_collections_to_records(collection_names: List[str],
                                   max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                                   offline: bool = False, progress=None) -> List[dict]:
    """选中多个收藏夹 -> 合并去重 -> 下载 TLE -> TLE 记录列表。"""
    all_collections = load_all_collections()
    cols = []
    for name in collection_names:
        c = find_collection(all_collections, name)
        if c is None:
            print(f"  [警告] 找不到收藏夹：{name}，跳过")
            continue
        cols.append(c)
    total = sum(collection_units(c) for c in cols)
    records: Dict[int, dict] = {}
    done = 0
    for c in cols:
        def sub(d, t, m):
            if progress:
                progress(done + d, total, m)
        for r in resolve_collection(c, max_age_hours, offline, progress=sub):
            if r.get("norad_id") is not None:
                records[r["norad_id"]] = r
        done += collection_units(c)
    return list(records.values())


def resolve_collection_with_meta(collection: dict,
                                 max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                                 offline: bool = False,
                                 progress=None) -> List[dict]:
    """解析收藏夹并补全元数据（所属/发射日期/高度/倾角），供 GUI 完整展示。

    元数据来源：名字规则直接用已缓存的搜索档案表；
    编号清单里的星逐个用 records.php?CATNR= 补（缓存到 meta/catnr_*.csv）。
    """
    recs = resolve_collection(collection, max_age_hours, offline, progress=progress)
    meta_map = {}
    for e in collection.get("entries", []):
        if e.get("type") == "name_filter":
            for m in search_metadata(e["value"], max_results=10000,
                                     max_age_hours=max_age_hours, offline=offline):
                if m.get("norad_id") is not None:
                    meta_map[m["norad_id"]] = m
    missing = [r["norad_id"] for r in recs if r["norad_id"] not in meta_map]
    for rid in missing:
        for m in search_metadata_by_norad(rid, max_age_hours, offline=offline):
            if m.get("norad_id") is not None:
                meta_map[m["norad_id"]] = m
        time.sleep(0.1)
    out = []
    for r in recs:
        m = meta_map.get(r["norad_id"], {})
        out.append({
            "name": r.get("name") or m.get("name", ""),
            "norad_id": r["norad_id"],
            "country": m.get("country", ""),
            "launch_date": m.get("launch_date", ""),
            "object_type": m.get("object_type", ""),
            "altitude_km": m.get("altitude_km"),
            "inclination": m.get("inclination"),
            "line1": r.get("line1"),
            "line2": r.get("line2"),
        })
    return out


def records_to_satellites(records: List[dict]) -> List[Satellite]:
    """把 TLE 记录列表解析成 Satellite 包装对象列表。"""
    ts = load.timescale(builtin=True)
    sats = []
    for r in records:
        try:
            orbit = EarthSatellite(r["line1"], r["line2"], r.get("name", ""), ts)
            sats.append(Satellite(orbit, r.get("name", ""), r["norad_id"]))
        except Exception as e:
            print(f"  [警告] 解析失败 {r.get('name', r['norad_id'])}: {e}")
    return sats


def resolve_collections_to_satellites(collection_names: List[str],
                                      max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                                      offline: bool = False) -> List[Satellite]:
    """选中多个收藏夹 -> 合并去重 -> 下载 TLE -> Satellite 包装对象。"""
    return records_to_satellites(resolve_collections_to_records(
        collection_names, max_age_hours, offline))


def records_to_tle_text(records: List[dict]) -> str:
    """把 TLE 记录拼成标准 3LE 文本（名字+两行，每 3 行一颗）。"""
    lines = []
    for r in records:
        lines.append(r.get("name", ""))
        lines.append(r.get("line1", ""))
        lines.append(r.get("line2", ""))
    return "\n".join(lines).rstrip("\n") + "\n"


def save_resolved_tle(collection_name: str, records: List[dict]) -> Path:
    """把解析结果合并写成一个文件（每个收藏夹一个 .tle，便于查看/复用）。"""
    _ensure_dirs()
    out_dir = CACHE_DIR / "resolved"
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(ch for ch in collection_name if ch.isalnum() or ch in "-_").strip() \
        or "collection"
    path = out_dir / f"{safe[:40]}.tle"
    path.write_text(records_to_tle_text(records), encoding="ascii", errors="replace")
    print(f"  [完成] 已保存合并 TLE（{len(records)} 颗）：{path}")
    return path


# ==================== 6. 展示辅助 ====================

def _entry_label(entry: dict) -> str:
    labels = {"name_filter": "名字规则", "norad_list": "编号清单",
              "file": "导入文件", "celestrak_group": "星座分组"}
    return f"{labels.get(entry['type'], entry['type'])} = {entry['value']}"


def longest_common_prefix(names: List[str]) -> str:
    """找一批卫星名的公共前缀（用于"手动清单 -> 自动规则"的归纳建议）。

    例如 ["YAOGAN-2", "YAOGAN-35 01C", "YAOGAN-41"] -> "YAOGAN"。
    大小写不敏感比较，返回时去掉末尾的 - _ . 等分隔符。
    """
    if not names:
        return ""
    prefix = names[0].strip()
    for n in names[1:]:
        n = n.strip()
        i = 0
        while i < len(prefix) and i < len(n) and prefix[i].upper() == n[i].upper():
            i += 1
        prefix = prefix[:i]
        if not prefix:
            return ""
    return prefix.rstrip(" -_.")


def _object_type_label(code: str) -> str:
    """CelesTrak 编目的短代码 -> 中文（便于阅读）。"""
    return {"PAY": "载荷", "R/B": "火箭体", "DEB": "碎片", "UNK": "未知"}.get(code, code)


def print_collections(collections: List[dict]):
    if not collections:
        print("  （暂无收藏夹）")
        return
    for i, c in enumerate(collections, 1):
        tag = "（内置，只读）" if c.get("builtin") else ""
        desc = f" —— {c['description']}" if c.get("description") else ""
        print(f"  {i}. {c['name']}{tag}{desc}（{len(c['entries'])} 条）")


def print_search_results(rows: List[dict], start: int = 0):
    for i, r in enumerate(rows, start + 1):
        alt = f"{r['altitude_km']:.0f} km" if r.get("altitude_km") is not None else "?"
        inc = f"{r['inclination']:.1f}°" if r.get("inclination") is not None else "?"
        country = r.get("country") or "?"
        launch = (r.get("launch_date") or "?")[:10]
        decay = "（已再入）" if r.get("decayed") else ""
        print(f"  {i}. {r['name']}  NORAD:{r['norad_id']}  {country}  {launch}  "
              f"高度≈{alt} 倾角≈{inc}  {_object_type_label(r.get('object_type', ''))}{decay}")


def _ask_number(prompt: str, lo: int, hi: int, default: Optional[int] = None) -> Optional[int]:
    while True:
        s = input(prompt).strip()
        if not s and default is not None:
            return default
        try:
            n = int(s)
            if lo <= n <= hi:
                return n
        except ValueError:
            pass
        print(f"  请输入 {lo}~{hi} 之间的数字。")


def _ask_text(prompt: str, allow_empty: bool = False) -> Optional[str]:
    s = input(prompt).strip()
    if not s and not allow_empty:
        print("  输入不能为空。")
        return None
    return s


def _pick_collection(collections: List[dict], title: str) -> Optional[dict]:
    """让用户从列表里挑一个收藏夹。"""
    print(f"\n{title}")
    print_collections(collections)
    if not collections:
        return None
    n = _ask_number(f"  请选择（1~{len(collections)}，0=返回）> ", 0, len(collections))
    if n is None or n == 0:
        return None
    return collections[n - 1]


def _select_or_create_collection() -> Optional[str]:
    """搜索/导入后，选择要存入的用户收藏夹（或新建）。"""
    user = load_user_collections()
    print("\n选择要存入的用户收藏夹：")
    print_collections(user)
    print("  0. 返回")
    print("  n. 新建收藏夹（输入 n 加名称，如：n 我的遥感）")
    while True:
        s = input("> ").strip()
        if s == "0":
            return None
        if s.lower().startswith("n "):
            name = s[2:].strip()
            if name and create_collection(name):
                return name
            continue
        if s.isdigit():
            n = int(s)
            if 1 <= n <= len(user):
                return user[n - 1]["name"]
        print("  输入无效，重试。")


# ==================== 7. 交互菜单 ====================

def _menu_search():
    print("\n===== 搜索卫星 =====")
    keyword = _ask_text("  输入名字关键字（如 YAOGAN / GF-1，留空返回）> ", allow_empty=True)
    if not keyword:
        return
    print("  正在搜索编目（首次会自动下载并缓存）……")
    rows = search_metadata(keyword=keyword, max_results=300, offline=False)
    if not rows:
        print(f"  [结果] 没有找到名字含「{keyword}」的卫星。")
        return
    print(f"  [结果] 共 {len(rows)} 条（显示前 {min(20, len(rows))} 条）：")
    show = rows[:20]
    print_search_results(show)

    print("\n  如何保存？（a=存成名字规则[自动纳入新星] / 输入编号如 1,3-5 / 0=取消）")
    s = input("> ").strip().lower()
    if s == "0":
        return
    collection = _select_or_create_collection()
    if collection is None:
        return
    if s == "a":
        add_entry(collection, "name_filter", keyword)
    else:
        # 解析 "1,3-5,8" 这类选择，转成 NORAD 编号清单（固定快照）
        idx = set()
        for part in s.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-", 1)
                if a.isdigit() and b.isdigit():
                    idx.update(range(max(1, int(a)), min(len(rows), int(b)) + 1))
            elif part.isdigit():
                n = int(part)
                if 1 <= n <= len(rows):
                    idx.add(n)
        ids = [str(rows[i - 1]["norad_id"]) for i in sorted(idx)
               if rows[i - 1].get("norad_id") is not None]
        if not ids:
            print("  [失败] 没有选中任何有效的卫星编号。")
            return
        add_entry(collection, "norad_list", " ".join(ids))


def _menu_manage():
    while True:
        all_c = load_all_collections()
        print("\n===== 收藏夹管理 =====")
        print_collections(all_c)
        print("  1. 新建收藏夹")
        print("  2. 打开收藏夹（查看/加/删/移动条目）")
        print("  3. 改名 / 删除（用户收藏夹）")
        print("  0. 返回")
        choice = _ask_number("> ", 0, 3)
        if choice is None or choice == 0:
            return
        if choice == 1:
            name = _ask_text("  新收藏夹名称（如：中国遥感）> ")
            if name:
                create_collection(name)
        elif choice == 2:
            c = _pick_collection(all_c, "选择要打开的收藏夹")
            if c is None:
                continue
            _menu_open_collection(c)
        elif choice == 3:
            user = load_user_collections()
            c = _pick_collection(user, "选择用户收藏夹（改名/删除）")
            if c is None:
                continue
            print(f"  当前：{c['name']}")
            print("  1. 改名")
            print("  2. 删除（会提示确认）")
            print("  0. 返回")
            op = _ask_number("> ", 0, 2)
            if op == 1:
                new = _ask_text("  新名称 > ")
                if new:
                    rename_collection(c["name"], new)
            elif op == 2:
                confirm = input(f"  确认删除收藏夹「{c['name']}」？此操作不可撤销（y/N）> ").strip().lower()
                if confirm == "y":
                    delete_collection(c["name"])


def _menu_open_collection(c: dict):
    while True:
        print(f"\n===== 收藏夹：{c['name']} =====")
        if c.get("description"):
            print(f"  备注：{c['description']}")
        if not c["entries"]:
            print("  （空收藏夹）")
        for i, e in enumerate(c["entries"], 1):
            print(f"  {i}. {_entry_label(e)}")
        print("  1. 添加条目（名字规则 / 编号清单 / 文件）")
        print("  2. 移除条目")
        print("  3. 移动条目到另一个收藏夹")
        print("  4. 解析本夹（下载 TLE 并统计数量）")
        print("  0. 返回")
        choice = _ask_number("> ", 0, 4)
        if choice is None or choice == 0:
            return
        if choice == 1:
            if c.get("builtin"):
                print("  内置夹只读，请先复制到我的收藏再修改。")
                continue
            print("  条目类型：1=名字规则  2=编号清单  3=导入文件")
            t = _ask_number("> ", 1, 3)
            if t == 1:
                v = _ask_text("  名字关键字（如 YAOGAN）> ")
                if v:
                    add_entry(c["name"], "name_filter", v)
            elif t == 2:
                v = _ask_text("  编号清单（空格或逗号分隔，如 25544 28699）> ")
                if v:
                    add_entry(c["name"], "norad_list", v)
            else:
                p = _ask_text("  文件路径（TLE 或编号清单 .txt）> ")
                if p:
                    import_file_to_collection(p, c["name"])
        elif choice == 2:
            if c.get("builtin"):
                print("  内置夹只读，请先复制到我的收藏再修改。")
                continue
            n = _ask_number(f"  输入要移除的条目序号（1~{len(c['entries'])}）> ",
                            1, max(1, len(c["entries"])))
            if n:
                remove_entry(c["name"], n - 1)
        elif choice == 3:
            if c.get("builtin"):
                print("  内置夹只读，无法移动其中的条目。")
                continue
            n = _ask_number(f"  输入要移动的条目序号（1~{len(c['entries'])}）> ",
                            1, max(1, len(c["entries"])))
            if not n:
                continue
            user = [x for x in load_user_collections() if x["name"] != c["name"]]
            dst = _pick_collection(user, "移动到哪个收藏夹")
            if dst:
                move_entry(c["name"], n - 1, dst["name"])
        elif choice == 4:
            recs = resolve_collection(c, offline=False)
            print(f"  [结果] 解析到 {len(recs)} 颗卫星（按 NORAD 编号去重）。")


def _menu_resolve():
    all_c = load_all_collections()
    c = _pick_collection(all_c, "选择要解析（下载 TLE）的收藏夹")
    if c is None:
        return
    recs = resolve_collection(c, offline=False)
    print(f"  [结果] 「{c['name']}」解析到 {len(recs)} 颗卫星。")
    if recs:
        print("  前 10 颗：")
        for r in recs[:10]:
            print(f"    {r['name']}  NORAD:{r['norad_id']}")


def _menu_import():
    print("\n===== 导入本地文件 =====")
    p = _ask_text("  文件路径（TLE 或 NORAD 编号清单）> ")
    if not p:
        return
    collection = _select_or_create_collection()
    if collection is None:
        return
    import_file_to_collection(p, collection)


def interactive_main():
    """命令行交互主菜单（以后 GUI 只需替换这一层）。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 终端中文不乱码
    except Exception:
        pass
    print("=" * 56)
    print("卫星数据加载器（阶段 1）—— 搜索 / 收藏夹 / 下载")
    print("=" * 56)
    while True:
        print("\n主菜单：")
        print("  1. 搜索卫星并入收藏")
        print("  2. 查看 / 管理收藏夹")
        print("  3. 解析收藏夹（下载 TLE）")
        print("  4. 导入本地文件（TLE / 编号清单）")
        print("  0. 退出")
        choice = _ask_number("> ", 0, 4)
        if choice is None:
            continue
        if choice == 0:
            print("再见。")
            return
        if choice == 1:
            _menu_search()
        elif choice == 2:
            _menu_manage()
        elif choice == 3:
            _menu_resolve()
        elif choice == 4:
            _menu_import()


# ==================== 8. 命令行入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="卫星数据加载器：搜索 / 收藏夹 / 下载缓存（阶段 1，独立模块）")
    parser.add_argument("--search", metavar="关键字", help="按名字关键字搜索并打印结果")
    parser.add_argument("--norad", type=int, help="按 NORAD 编号查询单星")
    parser.add_argument("--catalog", action="store_true", help="列出所有收藏夹（内置+用户）")
    parser.add_argument("--create", metavar="名称", help="新建用户收藏夹")
    parser.add_argument("--rename", nargs=2, metavar=("旧名", "新名"), help="改用户收藏夹名")
    parser.add_argument("--delete", metavar="名称", help="删除用户收藏夹")
    parser.add_argument("--import-file", metavar="路径", help="导入 TLE/编号文件（配合 --collection）")
    parser.add_argument("--collection", metavar="名称", help="配合 --import-file 指定目标收藏夹")
    parser.add_argument("--add-entry", metavar="类型", choices=["name_filter", "norad_list"],
                        help="往 --collection 加条目（配合 --value）")
    parser.add_argument("--value", metavar="值", help="配合 --add-entry 的条目值")
    parser.add_argument("--resolve", metavar="名称", help="解析收藏夹并打印卫星数量")
    parser.add_argument("--offline", action="store_true", help="离线模式：只用缓存，不联网")
    parser.add_argument("--force-update", action="store_true", help="强制重新下载编目")
    args = parser.parse_args()

    if args.offline and not (args.resolve or args.catalog):
        print("提示：--offline 需配合 --resolve/--catalog 使用。")

    if args.search:
        print(f"在线搜索：{args.search}")
        recs = search_by_name_online(args.search, offline=args.offline)
        print(f"找到 {len(recs)} 颗：")
        for r in recs[:20]:
            print(f"  {r['name']}  NORAD:{r['norad_id']}")
    if args.norad:
        recs = search_by_norad_online(args.norad, offline=args.offline)
        print(f"NORAD {args.norad}：找到 {len(recs)} 条")
        for r in recs[:5]:
            print(f"  {r['name']}  NORAD:{r['norad_id']}")
    if args.catalog:
        print("收藏夹列表：")
        print_collections(load_all_collections())
    if args.create:
        create_collection(args.create)
    if args.rename:
        rename_collection(args.rename[0], args.rename[1])
    if args.delete:
        delete_collection(args.delete)
    if args.import_file:
        col = args.collection or "导入文件"
        import_file_to_collection(args.import_file, col, new_collection=not args.collection)
    if args.add_entry:
        if not args.collection or not args.value:
            print("--add-entry 需要配合 --collection 和 --value。")
        else:
            add_entry(args.collection, args.add_entry, args.value)
    if args.resolve:
        all_c = load_all_collections()
        c = find_collection(all_c, args.resolve)
        if c is None:
            print(f"找不到收藏夹：{args.resolve}")
        else:
            recs = resolve_collection(c, offline=args.offline)
            print(f"「{args.resolve}」解析到 {len(recs)} 颗卫星。")
            for r in recs[:10]:
                print(f"  {r['name']}  NORAD:{r['norad_id']}")
            if recs:
                save_resolved_tle(args.resolve, recs)

    # 没有任何参数时进入交互模式
    if not any([args.search, args.norad, args.catalog, args.create, args.rename,
                args.delete, args.import_file, args.add_entry,
                args.resolve]):
        interactive_main()


if __name__ == "__main__":
    main()
