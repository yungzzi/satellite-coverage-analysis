# -*- coding: utf-8 -*-
"""下载并冻结 DataV 浙江边界，再显式归一化为 WGS84。

DataV.GeoAtlas 的 ``areas_v3`` 数据来自高德开放平台，官方说明其地图数据使用
GCJ-02。卫星轨道地面点使用 WGS84，因此原始文件不能直接进入覆盖计算。本脚本：

1. 把 DataV 返回的原始字节保存到 ``data/geo/raw_datav_gcj02``；
2. 使用固定的迭代逆变换生成 ``data/geo/<adcode>.json`` WGS84 计算文件；
3. 写出原始/转换后哈希清单，保证以后可以追溯和复现。

运行：
    python src/prepare_zhejiang_boundaries.py --refresh
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import urllib.request
from pathlib import Path
from typing import Any

from shapely import get_parts, make_valid
from shapely.geometry import MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[1]
GEO_DIR = ROOT / "data" / "geo"
RAW_DIR = GEO_DIR / "raw_datav_gcj02"
METADATA_DIR = GEO_DIR / "metadata"
MANIFEST_PATH = METADATA_DIR / "zhejiang_boundaries.json"

DATAV_URL = "https://geo.datav.aliyun.com/areas_v3/bound/{adcode}.json"
DATAV_DOC_URL = "https://datav.aliyun.com/portal/school/atlas/area_selector"
DATAV_CRS_DOC_URL = "https://help.aliyun.com/zh/datav/datav-7-0/user-guide/map-data-format-1"
DATASET_VERSION = "DataV areas_v3 / AMap 2021-05"
TRANSFORM_VERSION = "gcj02_to_wgs84_iterative_v1"
BOUNDARY_VERSION = "datav-areas_v3-amap-2021-05-wgs84-v1"
TOPOLOGY_REPAIR = "shapely_make_valid_polygonal_v1"

REGIONS: dict[str, str] = {
    "330000": "浙江省",
    "330100": "杭州市",
    "330200": "宁波市",
    "330300": "温州市",
    "330400": "嘉兴市",
    "330500": "湖州市",
    "330600": "绍兴市",
    "330700": "金华市",
    "330800": "衢州市",
    "330900": "舟山市",
    "331000": "台州市",
    "331100": "丽水市",
}

# 通用 GCJ-02 正向公式常数。逆变换通过“正向投影误差迭代归零”完成，避免只做
# 一次近似减法；输出会再做 WGS84→GCJ-02 回代验收。
_PI = math.pi
_A = 6378245.0
_EE = 0.00669342162296594323


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _geometry_hash(geometry: dict[str, Any]) -> str:
    encoded = json.dumps(
        geometry, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return _sha256(encoded)


def _outside_china(lon: float, lat: float) -> bool:
    return not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271)


def _transform_lat(x: float, y: float) -> float:
    value = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y
    value += 0.2 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    value += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    value += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return value


def _transform_lon(x: float, y: float) -> float:
    value = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y
    value += 0.1 * math.sqrt(abs(x))
    value += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    value += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    value += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x / 30.0 * _PI)) * 2.0 / 3.0
    return value


def wgs84_to_gcj02(lon: float, lat: float) -> tuple[float, float]:
    """标准正向计算，用于迭代逆解和转换后回代验收。"""

    if _outside_china(lon, lat):
        return float(lon), float(lat)
    dlat = _transform_lat(lon - 105.0, lat - 35.0)
    dlon = _transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * _PI
    magic = math.sin(radlat)
    magic = 1.0 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((_A * (1.0 - _EE)) / (magic * sqrt_magic) * _PI)
    dlon = (dlon * 180.0) / (_A / sqrt_magic * math.cos(radlat) * _PI)
    return lon + dlon, lat + dlat


def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """固定点迭代逆解；回代误差目标小于 1e-10 度。"""

    if _outside_china(lon, lat):
        return float(lon), float(lat)
    guess_lon = float(lon)
    guess_lat = float(lat)
    for _ in range(12):
        projected_lon, projected_lat = wgs84_to_gcj02(guess_lon, guess_lat)
        error_lon = projected_lon - lon
        error_lat = projected_lat - lat
        guess_lon -= error_lon
        guess_lat -= error_lat
        if max(abs(error_lon), abs(error_lat)) < 1e-10:
            break
    return guess_lon, guess_lat


def _convert_position(position: list[Any]) -> list[Any]:
    lon, lat = gcj02_to_wgs84(float(position[0]), float(position[1]))
    return [lon, lat, *position[2:]]


def _convert_coordinates(value: Any) -> Any:
    if not isinstance(value, list) or not value:
        return value
    if isinstance(value[0], (int, float)):
        return _convert_position(value)
    return [_convert_coordinates(item) for item in value]


def _validated_polygonal(geometry: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """只在原始拓扑无效时提取 make_valid 的面部分，并记录发生过修复。"""

    value = shape(geometry)
    repaired = False
    if not value.is_valid:
        value = make_valid(value)
        repaired = True
    polygonal_parts = []
    for part in get_parts(value):
        if isinstance(part, Polygon):
            polygonal_parts.append(part)
        elif isinstance(part, MultiPolygon):
            polygonal_parts.extend(part.geoms)
    if not polygonal_parts:
        raise ValueError("边界拓扑修复后没有任何面要素。")
    value = unary_union(polygonal_parts)
    if value.is_empty or not value.is_valid or not isinstance(value, (Polygon, MultiPolygon)):
        raise ValueError("边界拓扑修复后仍不是有效 Polygon/MultiPolygon。")
    return mapping(value), repaired


def _convert_document(document: dict[str, Any], adcode: str, raw_sha256: str) -> dict[str, Any]:
    if document.get("type") != "FeatureCollection" or len(document.get("features") or []) != 1:
        raise ValueError(f"{adcode} 原始文件必须是只含一个 Feature 的 FeatureCollection。")
    feature = document["features"][0]
    properties = dict(feature.get("properties") or {})
    if str(properties.get("adcode")) != adcode:
        raise ValueError(f"{adcode} 原始文件 adcode 不一致：{properties.get('adcode')}")
    geometry = dict(feature.get("geometry") or {})
    if geometry.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ValueError(f"{adcode} 几何类型不支持：{geometry.get('type')}")
    converted_geometry_raw = {
        **geometry,
        "coordinates": _convert_coordinates(geometry.get("coordinates")),
    }
    converted_geometry, topology_repaired = _validated_polygonal(converted_geometry_raw)

    for key in ("center", "centroid"):
        if isinstance(properties.get(key), list) and len(properties[key]) >= 2:
            properties[key] = _convert_position(properties[key])
    properties.update(
        {
            "adcode": int(adcode),
            "name": REGIONS[adcode],
            "boundary_version": BOUNDARY_VERSION,
            "boundary_source": "Alibaba DataV.GeoAtlas / AMap",
            "boundary_source_url": DATAV_URL.format(adcode=adcode),
            "source_dataset_version": DATASET_VERSION,
            "source_dataset_crs": "GCJ-02",
            "geometry_crs": "EPSG:4326",
            "coordinate_transform": TRANSFORM_VERSION,
            "topology_repair": TOPOLOGY_REPAIR if topology_repaired else "none",
            "source_sha256": raw_sha256,
        }
    )
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": properties,
                "geometry": converted_geometry,
            }
        ],
    }


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "satellite-coverage-analysis/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def prepare(*, refresh: bool = False) -> dict[str, Any]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_DIR.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for adcode, expected_name in REGIONS.items():
        url = DATAV_URL.format(adcode=adcode)
        raw_path = RAW_DIR / f"{adcode}.json"
        if refresh or not raw_path.is_file():
            raw_bytes = _download(url)
            raw_path.write_bytes(raw_bytes)
        else:
            raw_bytes = raw_path.read_bytes()
        raw_sha256 = _sha256(raw_bytes)
        document = json.loads(raw_bytes.decode("utf-8-sig"))
        normalized = _convert_document(document, adcode, raw_sha256)
        normalized_bytes = (
            json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        normalized_path = GEO_DIR / f"{adcode}.json"
        normalized_path.write_bytes(normalized_bytes)
        feature = normalized["features"][0]
        entries.append(
            {
                "adcode": adcode,
                "name": expected_name,
                "source_url": url,
                "raw_path": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
                "raw_sha256": raw_sha256,
                "normalized_path": str(normalized_path.relative_to(ROOT)).replace("\\", "/"),
                "normalized_sha256": _sha256(normalized_bytes),
                "geometry_sha256": _geometry_hash(feature["geometry"]),
            }
        )

    manifest = {
        "dataset": DATASET_VERSION,
        "source_crs": "GCJ-02",
        "geometry_crs": "EPSG:4326",
        "coordinate_transform": TRANSFORM_VERSION,
        "boundary_version": BOUNDARY_VERSION,
        "source_documentation": DATAV_DOC_URL,
        "coordinate_system_documentation": DATAV_CRS_DOC_URL,
        "regions": entries,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    ).encode("utf-8")
    MANIFEST_PATH.write_bytes(manifest_bytes)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="准备浙江省及 11 地市 WGS84 边界")
    parser.add_argument("--refresh", action="store_true", help="忽略本地原始快照并重新下载")
    args = parser.parse_args()
    manifest = prepare(refresh=args.refresh)
    print(
        f"已准备 {len(manifest['regions'])} 个边界（浙江省 + 11 地市）；"
        f"计算文件为 {manifest['geometry_crs']}，原始文件为 {manifest['source_crs']}。"
    )
    print(f"清单：{MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
