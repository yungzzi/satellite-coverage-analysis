# -*- coding: utf-8 -*-
r"""浙江省及 11 地市边界、坐标转换和 5 km 网格综合自检。

运行：
    ..\卫星覆盖性分析\.venv\Scripts\python.exe src\validate_zhejiang_boundaries.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterator

from pyproj import Transformer
from shapely.geometry import Point
from shapely.ops import transform, unary_union


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from prepare_zhejiang_boundaries import (  # noqa: E402
    BOUNDARY_VERSION,
    MANIFEST_PATH,
    REGIONS,
    TRANSFORM_VERSION,
    gcj02_to_wgs84,
    wgs84_to_gcj02,
)
from region_grid import (  # noqa: E402
    ZHEJIANG_EQUAL_AREA_CRS,
    build_clipped_grid,
    load_city_boundary,
)
import regional_opportunities as ro  # noqa: E402


CITY_CODES = tuple(code for code in REGIONS if code != "330000")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iter_positions(value: Any) -> Iterator[tuple[float, float]]:
    if not isinstance(value, list) or not value:
        return
    if isinstance(value[0], (int, float)):
        yield float(value[0]), float(value[1])
        return
    for item in value:
        yield from _iter_positions(item)


def _feature(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    features = document.get("features") or []
    if document.get("type") != "FeatureCollection" or len(features) != 1:
        raise AssertionError(f"{path} 不是单 Feature 的 FeatureCollection。")
    return features[0]


def test_manifest_hashes_and_roundtrip() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["source_crs"] == "GCJ-02"
    assert manifest["geometry_crs"] == "EPSG:4326"
    assert manifest["coordinate_transform"] == TRANSFORM_VERSION
    assert manifest["boundary_version"] == BOUNDARY_VERSION
    assert len(manifest["regions"]) == len(REGIONS) == 12

    max_roundtrip_error_deg = 0.0
    repaired_codes: list[str] = []
    for entry in manifest["regions"]:
        adcode = str(entry["adcode"])
        assert adcode in REGIONS
        raw_path = ROOT / entry["raw_path"]
        normalized_path = ROOT / entry["normalized_path"]
        assert _sha256(raw_path) == entry["raw_sha256"]
        assert _sha256(normalized_path) == entry["normalized_sha256"]

        raw_feature = _feature(raw_path)
        normalized_feature = _feature(normalized_path)
        properties = normalized_feature["properties"]
        assert properties["name"] == REGIONS[adcode]
        assert properties["source_dataset_crs"] == "GCJ-02"
        assert properties["geometry_crs"] == "EPSG:4326"
        assert properties["coordinate_transform"] == TRANSFORM_VERSION
        assert properties["boundary_version"] == BOUNDARY_VERSION
        assert properties["source_sha256"] == entry["raw_sha256"]
        if properties["topology_repair"] != "none":
            repaired_codes.append(adcode)

        for gcj_lon, gcj_lat in _iter_positions(raw_feature["geometry"]["coordinates"]):
            wgs_lon, wgs_lat = gcj02_to_wgs84(gcj_lon, gcj_lat)
            back_lon, back_lat = wgs84_to_gcj02(wgs_lon, wgs_lat)
            max_roundtrip_error_deg = max(
                max_roundtrip_error_deg,
                abs(back_lon - gcj_lon),
                abs(back_lat - gcj_lat),
            )

        boundary = load_city_boundary(normalized_path, adcode=adcode)
        assert boundary.boundary_hash == entry["geometry_sha256"]
        assert boundary.source_crs == "EPSG:4326"
        assert boundary.boundary_version == BOUNDARY_VERSION

    assert max_roundtrip_error_deg < 1e-9
    print(
        "[通过] 12 个原始/转换文件哈希一致；GCJ-02 回代最大误差 "
        f"{max_roundtrip_error_deg:.3e}°；拓扑修复 {len(repaired_codes)} 个："
        f"{','.join(repaired_codes) if repaired_codes else '无'}"
    )


def test_declared_crs_guard() -> None:
    boundary_path = ROOT / "data" / "geo" / "330200.json"
    try:
        load_city_boundary(boundary_path, "330200", source_crs="EPSG:3857")
    except ValueError as exc:
        assert "geometry_crs" in str(exc)
    else:
        raise AssertionError("声明为 WGS84 的边界不应接受 EPSG:3857 作为源坐标系。")
    print("[通过] 边界声明坐标系与调用参数不一致时会立即拒绝。")


def test_all_city_grids() -> None:
    rows: list[tuple[str, str, int, float, float, float]] = []
    for adcode in CITY_CODES:
        boundary = load_city_boundary(ROOT / "data" / "geo" / f"{adcode}.json", adcode)
        grid = build_clipped_grid(boundary, grid_size_m=5_000.0)
        assert grid.cells
        assert abs(grid.area_error_m2) < 0.01
        for cell in grid.cells:
            assert cell.area_m2 > 0.0
            assert cell.geometry_wgs84.covers(
                Point(cell.representative_lon, cell.representative_lat)
            )
        target = ro._targeted_grid_centroid(grid)
        assert target["definition"] == "area_weighted_representative_point_centroid"
        assert abs(target["weight_area_m2"] - grid.total_area_m2) < 0.01
        rows.append(
            (
                adcode,
                grid.city_name,
                len(grid.cells),
                grid.total_area_km2,
                target["latitude_deg"],
                target["longitude_deg"],
            )
        )

    assert len(rows) == 11
    print("[通过] 浙江 11 地市均生成有效 5 km 裁剪网格：")
    for adcode, name, count, area, lat, lon in rows:
        print(
            f"  {adcode} {name}: {count} 格，{area:.3f} km2，"
            f"Targeted 形心 ({lat:.5f}, {lon:.5f})"
        )


def test_city_partition_matches_province() -> None:
    to_equal_area = Transformer.from_crs(
        "EPSG:4326", ZHEJIANG_EQUAL_AREA_CRS, always_xy=True
    )
    province = load_city_boundary(ROOT / "data" / "geo" / "330000.json", "330000")
    province_projected = transform(to_equal_area.transform, province.geometry_source)
    city_projected = [
        transform(
            to_equal_area.transform,
            load_city_boundary(ROOT / "data" / "geo" / f"{code}.json", code).geometry_source,
        )
        for code in CITY_CODES
    ]
    city_union = unary_union(city_projected)
    sum_city_area = sum(item.area for item in city_projected)
    symmetric_difference_pct = (
        province_projected.symmetric_difference(city_union).area
        / province_projected.area
        * 100.0
    )
    overlap_pct = (sum_city_area - city_union.area) / province_projected.area * 100.0
    assert symmetric_difference_pct < 0.5
    assert overlap_pct < 0.01
    print(
        "[通过] 11 市并集与浙江省边界基本一致："
        f"对称差 {symmetric_difference_pct:.4f}%，城市间重叠 {overlap_pct:.4f}%"
    )


def main() -> int:
    tests = [
        test_manifest_hashes_and_roundtrip,
        test_declared_crs_guard,
        test_all_city_grids,
        test_city_partition_matches_province,
    ]
    for test in tests:
        test()
    print(f"浙江边界与网格自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
