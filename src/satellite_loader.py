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
USER_CATALOG = DATA_DIR / "user_catalog.toml"           # 用户收藏（不入库）

# CelesTrak 免费接口（URL 即接口，无需 token）
CELESTRAK_GP = "https://celestrak.org/NORAD/elements/gp.php"
# 编目元数据接口（名字/所属/发射日期/轨道参数；不含 TLE，TLE 走 gp.php）
CELESTRAK_SATCAT = "https://celestrak.org/satcat/records.php"
USER_AGENT = "satellite-coverage-analysis/0.1 (educational project)"

# 缓存默认过期时间（小时）：搜索结果/编目 24 小时更新一次即可
DEFAULT_MAX_AGE_HOURS = 24


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


def _age_hours(path: Path) -> float:
    """文件已存在多久（小时）。"""
    if not path.exists():
        return float("inf")
    return (time.time() - path.stat().st_mtime) / 3600.0


def download_text(url: str, cache_name: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                  force: bool = False, offline: bool = False) -> Optional[Path]:
    """下载文本文件并缓存；缓存未过期直接复用。返回本地文件路径。

    - cache_name: 缓存文件名（如 satcat.csv / search_YAOGAN.txt）
    - offline   : True 时不联网，只用缓存；缓存缺失则返回 None
    """
    _ensure_dirs()
    cache_path = CACHE_DIR / cache_name
    if cache_path.exists() and not force and _age_hours(cache_path) < max_age_hours:
        print(f"  [缓存命中] {cache_name}（{_age_hours(cache_path):.1f} 小时前下载）")
        return cache_path
    if offline:
        print(f"  [离线] 缓存 {cache_name} 缺失或过期，且离线模式不联网，跳过下载。")
        return None

    print(f"  [下载] {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        print(f"  [失败] HTTP {e.code}: {url}")
        return None
    except Exception as e:
        print(f"  [失败] 网络错误：{e}")
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
                    max_results: int = 300,
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
    path = download_text(url, _safe_cache_name(keyword, "_meta.csv"),
                         max_age_hours, offline=offline)
    if path is None:
        return []
    rows = []
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                row = {k.lower().strip(): (v or "").strip() for k, v in raw.items()}
                rows.append(row)
    except Exception as e:
        print(f"  [失败] 读取搜索缓存失败：{e}")
        return []

    out = []
    for row in rows:
        if country and country.upper() != row.get("owner", "").upper():
            continue
        if launch_year and not row.get("launch_date", "").startswith(launch_year):
            continue
        alt = _row_altitude(row)
        if min_alt_km is not None and (alt is None or alt < min_alt_km):
            continue
        if max_alt_km is not None and (alt is None or alt > max_alt_km):
            continue
        try:
            rid = int(row.get("norad_cat_id", ""))
        except ValueError:
            rid = None
        out.append({
            "name": row.get("object_name", ""),
            "norad_id": rid,
            "country": row.get("owner", ""),
            "launch_date": row.get("launch_date", ""),
            "object_type": row.get("object_type", ""),
            "decayed": False,   # 已按 ONORBIT=1 过滤
            "inclination": _safe_float(row.get("inclination", "")),
            "altitude_km": alt,
            "has_tle": False,   # 编目只有元数据；TLE 通过 gp.php 获取
        })
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


def search_by_name_online(keyword: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                          offline: bool = False) -> List[dict]:
    """在线按名字模糊搜索（CelesTrak gp.php?NAME=...），返回 TLE 记录列表。"""
    import urllib.parse
    url = f"{CELESTRAK_GP}?NAME={urllib.parse.quote(keyword)}&FORMAT=TLE"
    path = download_text(url, _safe_cache_name(keyword, ".txt"), max_age_hours,
                         offline=offline)
    if path is None:
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        return parse_tle_records(f.read())


def search_by_norad_online(norad_id: int, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                           offline: bool = False) -> List[dict]:
    """在线按 NORAD 编号查询单星（CelesTrak gp.php?CATNR=...）。"""
    url = f"{CELESTRAK_GP}?CATNR={norad_id}&FORMAT=TLE"
    path = download_text(url, f"norad_{norad_id}.txt", max_age_hours, offline=offline)
    if path is None:
        return []
    with open(path, "r", encoding="ascii", errors="replace") as f:
        return parse_tle_records(f.read())


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
    if entry_type not in ("name_filter", "norad_list", "file"):
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

def _records_from_file(file_path: str, max_age_hours: float, offline: bool) -> List[dict]:
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
    return _records_by_norad_ids(ids, max_age_hours, offline)


def _records_by_norad_ids(norad_ids: List[int], max_age_hours: float,
                          offline: bool) -> List[dict]:
    """按编号清单找 TLE：每个编号走 gp.php?CATNR=，带本地缓存与小延时。"""
    out = []
    for rid in norad_ids:
        recs = search_by_norad_online(rid, max_age_hours, offline=offline)
        if recs:
            out.append(recs[0])
        else:
            print(f"  [警告] 查不到编号 {rid}")
        time.sleep(0.2)  # 礼貌性小延时，避免高频请求
    return out


def _records_by_name_filter(keyword: str, max_age_hours: float,
                            offline: bool) -> List[dict]:
    """按名字规则找 TLE：走 gp.php?NAME=，并只保留"在轨载荷"。

    用编目元数据（records.php）过滤掉碎片/火箭体/已再入物体，
    保证名字规则里只含真正有意义的卫星。
    """
    recs = search_by_name_online(keyword, max_age_hours, offline=offline)
    meta = search_metadata(keyword, max_results=10000, max_age_hours=max_age_hours,
                           offline=offline)
    payload_ids = {m["norad_id"] for m in meta if m.get("norad_id") is not None}
    if payload_ids:
        recs = [r for r in recs if r.get("norad_id") in payload_ids]
    return recs


def resolve_collection(collection: dict, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                       offline: bool = False) -> List[dict]:
    """把一个收藏夹解析成 TLE 记录列表（按 NORAD 编号去重，保留顺序）。"""
    records: Dict[int, dict] = {}
    for entry in collection["entries"]:
        etype, value = entry["type"], entry["value"]
        if etype == "name_filter":
            recs = _records_by_name_filter(value, max_age_hours, offline)
        elif etype == "norad_list":
            ids = [int(x) for x in value.replace(",", " ").split() if x.strip().isdigit()]
            recs = _records_by_norad_ids(ids, max_age_hours, offline)
        elif etype == "file":
            recs = _records_from_file(value, max_age_hours, offline)
        else:
            print(f"  [警告] 未知条目类型：{etype}，跳过")
            continue
        for r in recs:
            if r.get("norad_id") is not None:
                records[r["norad_id"]] = r
    return list(records.values())


def resolve_collections_to_satellites(collection_names: List[str],
                                      max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
                                      offline: bool = False) -> List[Satellite]:
    """选中多个收藏夹 -> 合并去重 -> 下载 TLE -> 解析成 Satellite 包装对象。"""
    all_collections = load_all_collections()
    records: Dict[int, dict] = {}
    for name in collection_names:
        c = find_collection(all_collections, name)
        if c is None:
            print(f"  [警告] 找不到收藏夹：{name}，跳过")
            continue
        for r in resolve_collection(c, max_age_hours, offline):
            if r.get("norad_id") is not None:
                records[r["norad_id"]] = r
    ts = load.timescale(builtin=True)
    sats = []
    for r in records.values():
        try:
            orbit = EarthSatellite(r["line1"], r["line2"], r.get("name", ""), ts)
            sats.append(Satellite(orbit, r.get("name", ""), r["norad_id"]))
        except Exception as e:
            print(f"  [警告] 解析失败 {r.get('name', r['norad_id'])}: {e}")
    return sats


# ==================== 6. 展示辅助 ====================

def _entry_label(entry: dict) -> str:
    labels = {"name_filter": "名字规则", "norad_list": "编号清单", "file": "导入文件"}
    return f"{labels.get(entry['type'], entry['type'])} = {entry['value']}"


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
        print("  3. 复制内置夹到我的收藏")
        print("  4. 改名 / 删除（用户收藏夹）")
        print("  0. 返回")
        choice = _ask_number("> ", 0, 4)
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
            builtin = load_builtin_collections()
            c = _pick_collection(builtin, "选择要复制到我的收藏的内置夹")
            if c:
                copy_builtin_to_user(c["name"])
        elif choice == 4:
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
    parser.add_argument("--copy-builtin", metavar="名称", help="复制内置夹到用户收藏")
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
    if args.copy_builtin:
        copy_builtin_to_user(args.copy_builtin)
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

    # 没有任何参数时进入交互模式
    if not any([args.search, args.norad, args.catalog, args.create, args.rename,
                args.delete, args.copy_builtin, args.import_file, args.add_entry,
                args.resolve]):
        interactive_main()


if __name__ == "__main__":
    main()
