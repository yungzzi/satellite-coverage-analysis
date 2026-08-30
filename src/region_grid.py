# -*- coding: utf-8 -*-
"""区域覆盖的米制裁剪格网。

本模块只解决一件事：把一个已确认为 WGS84 的城市边界，稳定地变成带面积权重的
5 km 裁剪网格。它不传播卫星轨道，也不修改旧的 ``make_grid_points`` 路径。

白话说明
--------
把城市轮廓放到一张以“米”为单位的纸上，先画全浙江共用、位置固定的正方形格子，
再用城市边界把格子裁开。每一小块仍保留自己的真实面积；另外在每一小块里放一个
保证落在内部的代表点，供后续覆盖几何判定使用。代表点不是圆，也不会把边界小块丢掉。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from pyproj import CRS, Transformer
from shapely.geometry import (
    GeometryCollection,
    MultiPolygon,
    Point,
    Polygon,
    box,
    mapping,
    shape,
)
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform
from shapely import set_precision


WGS84_CRS = "EPSG:4326"
"""轨道/传感器几何使用的经纬度坐标语义。"""

# 以浙江附近为中心的 Lambert 方位等面积投影。面积权重只在这个米制平面中计算。
ZHEJIANG_EQUAL_AREA_CRS = (
    "+proj=laea +lat_0=29.5 +lon_0=120.0 +datum=WGS84 +units=m +no_defs +type=crs"
)

DEFAULT_GRID_SIZE_M = 5_000.0
# 固定为该投影的坐标原点；不能按每个城市的最小外包框临时改变。
DEFAULT_GRID_ORIGIN_XY = (0.0, 0.0)
# 投影往返的毫米级浮点噪声不应生成一条“幽灵边界格”。这不是空间分析分辨率；
# 只是把计算坐标统一到厘米精度，远小于 5 km 网格与公开行政边界本身的精度。
GEOMETRY_PRECISION_M = 0.01


@dataclass(frozen=True)
class CityBoundary:
    """一个已经明确源坐标系的城市边界。"""

    adcode: str
    city_name: str
    geometry_source: BaseGeometry = field(repr=False, compare=False)
    boundary_hash: str
    source_crs: str = WGS84_CRS
    boundary_version: str | None = None
    source_path: str | None = None


@dataclass(frozen=True)
class RegionGridCell:
    """一个裁剪后的连通网格单元。

    ``area_m2`` 是统计权重；``representative_*`` 只用于后续卫星覆盖判定。
    当一个原始方格被城市的离散部分切成多块时，每一块都会单独保留一个 Cell。
    """

    grid_id: str
    grid_col: int
    grid_row: int
    component_index: int
    area_m2: float
    representative_lon: float
    representative_lat: float
    representative_x_m: float
    representative_y_m: float
    representative_method: str
    geometry_wgs84: BaseGeometry = field(repr=False, compare=False)

    @property
    def area_km2(self) -> float:
        """这个裁剪单元的面积，单位 km²。"""

        return self.area_m2 / 1_000_000.0


@dataclass(frozen=True)
class RegionGrid:
    """一次可复现的区域网格结果。"""

    adcode: str
    city_name: str
    cells: tuple[RegionGridCell, ...]
    boundary_hash: str
    source_crs: str
    projected_crs: str
    grid_size_m: float
    grid_origin_xy: tuple[float, float]
    geometry_precision_m: float
    boundary_area_m2: float
    boundary_version: str | None = None

    @property
    def total_area_m2(self) -> float:
        """所有裁剪单元的面积和，应该与 ``boundary_area_m2`` 一致。"""

        return sum(cell.area_m2 for cell in self.cells)

    @property
    def total_area_km2(self) -> float:
        return self.total_area_m2 / 1_000_000.0

    @property
    def area_error_m2(self) -> float:
        """裁剪单元总面积减去城市投影面积的数值误差。"""

        return self.total_area_m2 - self.boundary_area_m2

    def to_feature_collection(self) -> dict[str, Any]:
        """导出审计/调试用 GeoJSON；正常 skill 不直接给普通用户看这张逐格表。"""

        return {
            "type": "FeatureCollection",
            "properties": {
                "adcode": self.adcode,
                "city_name": self.city_name,
                "boundary_hash": self.boundary_hash,
                "boundary_version": self.boundary_version,
                "source_crs": self.source_crs,
                "projected_crs": self.projected_crs,
                "grid_size_m": self.grid_size_m,
                "grid_origin_xy": list(self.grid_origin_xy),
                "geometry_precision_m": self.geometry_precision_m,
                "boundary_area_m2": self.boundary_area_m2,
            },
            "features": [
                {
                    "type": "Feature",
                    "properties": {
                        "grid_id": cell.grid_id,
                        "grid_col": cell.grid_col,
                        "grid_row": cell.grid_row,
                        "component_index": cell.component_index,
                        "area_m2": cell.area_m2,
                        "area_km2": cell.area_km2,
                        "representative_lon": cell.representative_lon,
                        "representative_lat": cell.representative_lat,
                        "representative_method": cell.representative_method,
                    },
                    "geometry": mapping(cell.geometry_wgs84),
                }
                for cell in self.cells
            ],
        }


def _hash_geometry(geometry: BaseGeometry) -> str:
    """按规范化 GeoJSON 计算边界哈希，避免文件空格和属性变化影响网格版本。"""

    encoded = json.dumps(
        mapping(geometry), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _as_polygonal(geometry: BaseGeometry, *, label: str) -> BaseGeometry:
    """拒绝无面积或无效边界，避免程序悄悄篡改行政边界。"""

    if geometry.is_empty:
        raise ValueError(f"{label}为空，无法生成网格。")
    if not geometry.is_valid:
        raise ValueError(f"{label}存在自相交等无效几何，请先修复边界后再生成网格。")
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise ValueError(f"{label}必须是 Polygon 或 MultiPolygon，实际为 {geometry.geom_type}。")
    if geometry.area <= 0:
        raise ValueError(f"{label}没有正面积，无法生成网格。")
    return geometry


def _select_feature(document: dict[str, Any], adcode: str | None) -> dict[str, Any]:
    """从 GeoJSON 中选择一个 Feature；多城市文件必须按 adcode 指定。"""

    kind = document.get("type")
    if kind == "Feature":
        return document
    if kind == "FeatureCollection":
        features = document.get("features", [])
        if not features:
            raise ValueError("GeoJSON FeatureCollection 没有任何 Feature。")
        if adcode is None:
            if len(features) != 1:
                raise ValueError("GeoJSON 包含多个地区，请显式提供 adcode。")
            return features[0]
        target = str(adcode)
        for feature in features:
            properties = feature.get("properties") or {}
            if str(properties.get("adcode")) == target:
                return feature
        raise ValueError(f"GeoJSON 中找不到 adcode={target} 的边界。")
    if kind in {"Polygon", "MultiPolygon"}:
        if adcode is None:
            raise ValueError("裸 Geometry 没有 adcode，请在调用时显式提供。")
        return {"type": "Feature", "properties": {"adcode": str(adcode)}, "geometry": document}
    raise ValueError(f"不支持的 GeoJSON 类型：{kind!r}。")


def _repair_display_text(value: object) -> str:
    """修正旧 DataV GeoJSON 中偶见的 UTF-8 二次转码名称。

    只作用于给人看的名称字段，绝不改坐标、几何或边界哈希。例如旧文件中
    ``æ­å·å¸`` 会恢复为 ``杭州市``；本来正常的中文/英文会原样返回。
    """

    text = str(value)
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text
    return repaired


def load_city_boundary(
    path: str | Path,
    adcode: str | None = None,
    *,
    source_crs: str = WGS84_CRS,
) -> CityBoundary:
    """读取一个城市边界。

    参数 ``source_crs`` 是显式声明，不会猜测坐标系。当前卫星几何默认使用 WGS84；
    如果平台提供 GCJ-02 等坐标，必须先获得确认的转换流程，再传入已归一化的边界。
    """

    path = Path(path)
    with path.open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)

    feature = _select_feature(document, adcode)
    properties = feature.get("properties") or {}
    chosen_adcode = str(properties.get("adcode", adcode or ""))
    if not chosen_adcode:
        raise ValueError("边界缺少 adcode，请在 GeoJSON 属性或函数参数中提供。")
    city_name = _repair_display_text(
        properties.get("name") or properties.get("city_name") or chosen_adcode
    )
    geometry = _as_polygonal(shape(feature["geometry"]), label=f"{city_name}边界")

    # 这里只校验 CRS 可被 pyproj 识别；不做任何“猜坐标系”的隐式转换。
    CRS.from_user_input(source_crs)
    return CityBoundary(
        adcode=chosen_adcode,
        city_name=city_name,
        geometry_source=geometry,
        boundary_hash=_hash_geometry(geometry),
        source_crs=source_crs,
        boundary_version=properties.get("boundary_version"),
        source_path=str(path),
    )


def _iter_polygon_parts(geometry: BaseGeometry) -> Iterator[Polygon]:
    """把 Polygon / MultiPolygon / GeometryCollection 展开成有面积的 Polygon。"""

    if geometry.is_empty:
        return
    if isinstance(geometry, Polygon):
        if geometry.area > 0:
            yield geometry
        return
    if isinstance(geometry, (MultiPolygon, GeometryCollection)):
        for child in geometry.geoms:
            yield from _iter_polygon_parts(child)
        return
    # 线和点是边界相交时的零面积结果，不构成网格单元。


def _stable_parts(geometry: BaseGeometry) -> list[Polygon]:
    """按几何位置排序，保证同一边界重复生成时 component_index 稳定。"""

    return sorted(
        _iter_polygon_parts(geometry),
        key=lambda part: (*part.bounds, part.wkb),
    )


def _choose_representative_point(unit: Polygon, square: Polygon) -> tuple[Point, str]:
    """为一个裁剪单元选择内部代表点，并记录为什么选它。

    顺序刻意保留方格中心优先：完整格和大多数边界格仍与规则网格的空间含义一致；
    只有中心落在单元外时才改用面积质心或 Shapely 保证内部的代表点。
    """

    center = square.centroid
    if unit.covers(center):
        return center, "cell_center"

    centroid = unit.centroid
    if unit.covers(centroid):
        return centroid, "area_centroid"

    # 对凹形边界，面积质心可能落在边界外；representative_point 保证在内部。
    return unit.representative_point(), "interior_point"


def _snap_near_grid_line(value: float, *, tolerance: float = 1e-9) -> float:
    """消除投影往返造成的格线级浮点抖动。

    例如理论坐标正好为 ``2 × 5000 m``，反投影再投影后可能成为
    ``1.9999999999998 × 5000 m``。不吸附会凭空生成一条纳米级边界格；
    这里只吸附到距离整数格线小于 ``tolerance`` 的情况，不会合并真实小单元。
    """

    nearest = round(value)
    return float(nearest) if abs(value - nearest) <= tolerance else value


def build_clipped_grid(
    boundary: CityBoundary,
    *,
    grid_size_m: float = DEFAULT_GRID_SIZE_M,
    projected_crs: str = ZHEJIANG_EQUAL_AREA_CRS,
    fixed_origin_xy: tuple[float, float] = DEFAULT_GRID_ORIGIN_XY,
) -> RegionGrid:
    """生成固定米制格网与裁剪单元。

    ``H_g = Q_g ∩ Ω`` 的面积在等面积投影下计算。方格中心不在裁剪单元时，单元
    本身仍完整保留，只替换覆盖判定用的代表点；绝不按“中心在不在内部”删除边界格。
    """

    if grid_size_m <= 0:
        raise ValueError("grid_size_m 必须大于 0。")
    if len(fixed_origin_xy) != 2:
        raise ValueError("fixed_origin_xy 必须是 (x, y) 二元组。")

    target_crs = CRS.from_user_input(projected_crs)
    source_crs = CRS.from_user_input(boundary.source_crs)
    forward = Transformer.from_crs(source_crs, target_crs, always_xy=True)
    # 后续轨道几何统一使用 WGS84；不能反投影回一个可能是 GCJ-02 等的源坐标系。
    to_wgs84 = Transformer.from_crs(target_crs, CRS.from_user_input(WGS84_CRS), always_xy=True)

    projected_boundary = transform(forward.transform, boundary.geometry_source)
    projected_boundary = _as_polygonal(projected_boundary, label=f"{boundary.city_name}投影边界")
    projected_boundary = set_precision(projected_boundary, GEOMETRY_PRECISION_M)
    projected_boundary = _as_polygonal(projected_boundary, label=f"{boundary.city_name}厘米精度投影边界")
    minx, miny, maxx, maxy = projected_boundary.bounds
    origin_x, origin_y = fixed_origin_xy

    # 左闭右开索引范围：若外包框正好压在格线上，也不会额外产生零面积单元。
    col_start = math.floor(_snap_near_grid_line((minx - origin_x) / grid_size_m))
    col_stop = math.ceil(_snap_near_grid_line((maxx - origin_x) / grid_size_m))
    row_start = math.floor(_snap_near_grid_line((miny - origin_y) / grid_size_m))
    row_stop = math.ceil(_snap_near_grid_line((maxy - origin_y) / grid_size_m))

    cells: list[RegionGridCell] = []
    for grid_col in range(col_start, col_stop):
        x0 = origin_x + grid_col * grid_size_m
        x1 = x0 + grid_size_m
        for grid_row in range(row_start, row_stop):
            y0 = origin_y + grid_row * grid_size_m
            y1 = y0 + grid_size_m
            square = box(x0, y0, x1, y1)
            clipped = square.intersection(projected_boundary)
            for component_index, unit in enumerate(_stable_parts(clipped)):
                representative, method = _choose_representative_point(unit, square)
                lon, lat = to_wgs84.transform(representative.x, representative.y)
                geometry_wgs84 = transform(to_wgs84.transform, unit)
                cells.append(
                    RegionGridCell(
                        grid_id=f"{boundary.adcode}:{grid_col}:{grid_row}:{component_index}",
                        grid_col=grid_col,
                        grid_row=grid_row,
                        component_index=component_index,
                        area_m2=float(unit.area),
                        representative_lon=float(lon),
                        representative_lat=float(lat),
                        representative_x_m=float(representative.x),
                        representative_y_m=float(representative.y),
                        representative_method=method,
                        geometry_wgs84=geometry_wgs84,
                    )
                )

    if not cells:
        raise ValueError(f"{boundary.city_name}边界没有生成任何正面积网格单元。")

    cells.sort(key=lambda cell: (cell.grid_col, cell.grid_row, cell.component_index))
    return RegionGrid(
        adcode=boundary.adcode,
        city_name=boundary.city_name,
        cells=tuple(cells),
        boundary_hash=boundary.boundary_hash,
        source_crs=boundary.source_crs,
        projected_crs=target_crs.to_string(),
        grid_size_m=float(grid_size_m),
        grid_origin_xy=(float(origin_x), float(origin_y)),
        geometry_precision_m=GEOMETRY_PRECISION_M,
        boundary_area_m2=float(projected_boundary.area),
        boundary_version=boundary.boundary_version,
    )


def save_grid_geojson(grid: RegionGrid, path: str | Path) -> Path:
    """把裁剪单元保存为 GeoJSON，供调试、边界审计和后续弱覆盖地图使用。"""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(grid.to_feature_collection(), handle, ensure_ascii=False, indent=2)
    return path


def _main() -> int:
    parser = argparse.ArgumentParser(description="把城市边界生成固定米制裁剪格网")
    parser.add_argument("geojson", help="已确认坐标系的城市 GeoJSON 文件")
    parser.add_argument("--adcode", help="GeoJSON 含多个城市时要选择的 adcode")
    parser.add_argument("--grid-km", type=float, default=5.0, help="格网边长（km），默认 5")
    parser.add_argument("--output", help="可选：输出调试用裁剪格网 GeoJSON")
    args = parser.parse_args()

    boundary = load_city_boundary(args.geojson, args.adcode)
    grid = build_clipped_grid(boundary, grid_size_m=args.grid_km * 1_000)
    print(f"地区：{grid.city_name}（{grid.adcode}）")
    print(f"网格：{len(grid.cells)} 个裁剪单元，边长 {grid.grid_size_m / 1_000:g} km")
    # Windows 的 GBK 终端不能稳定显示上标 ²，命令行输出用普通 km2/m2。
    print(f"面积：{grid.total_area_km2:.3f} km2；面积误差 {grid.area_error_m2:.6f} m2")
    print(f"边界哈希：{grid.boundary_hash}")
    if args.output:
        output = save_grid_geojson(grid, args.output)
        print(f"已导出调试格网：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
