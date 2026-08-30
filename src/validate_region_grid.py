# -*- coding: utf-8 -*-
"""区域裁剪格网自检。

运行：
    .venv\\Scripts\\python.exe src\\validate_region_grid.py

这不是卫星覆盖验证；它只验证“5 km 网格、裁剪面积、内部代表点”这一层。这样以后
接入轨道计算时，如果结果不对，能先排除边界/网格本身的问题。
"""

from __future__ import annotations

import math
from pathlib import Path

from pyproj import CRS, Transformer
from shapely.geometry import MultiPolygon, Point, Polygon, box
from shapely.ops import transform

from region_grid import (
    CityBoundary,
    WGS84_CRS,
    ZHEJIANG_EQUAL_AREA_CRS,
    _hash_geometry,
    build_clipped_grid,
    load_city_boundary,
)


ROOT = Path(__file__).resolve().parents[1]
PROJECTED = CRS.from_user_input(ZHEJIANG_EQUAL_AREA_CRS)
TO_WGS84 = Transformer.from_crs(PROJECTED, CRS.from_user_input(WGS84_CRS), always_xy=True)
TO_PROJECTED = Transformer.from_crs(CRS.from_user_input(WGS84_CRS), PROJECTED, always_xy=True)


def boundary_from_projected(geometry, *, adcode: str = "test", name: str = "测试区域") -> CityBoundary:
    """把便于人工构造的米制测试形状反投影为输入边界。"""

    geometry_wgs84 = transform(TO_WGS84.transform, geometry)
    return CityBoundary(
        adcode=adcode,
        city_name=name,
        geometry_source=geometry_wgs84,
        boundary_hash=_hash_geometry(geometry_wgs84),
        source_crs=WGS84_CRS,
    )


def assert_area_conserved(grid, tolerance_m2: float = 1e-3) -> None:
    assert abs(grid.area_error_m2) <= tolerance_m2, (
        f"面积不守恒：单元和={grid.total_area_m2}, 边界={grid.boundary_area_m2}, "
        f"误差={grid.area_error_m2}"
    )


def assert_representatives_inside(grid) -> None:
    for cell in grid.cells:
        projected_point = Point(cell.representative_x_m, cell.representative_y_m)
        projected_unit = transform(TO_PROJECTED.transform, cell.geometry_wgs84)
        assert projected_unit.covers(projected_point), f"代表点跑出单元：{cell.grid_id}"


def test_full_cells_and_repeatability() -> None:
    """10 km × 10 km 矩形应正好成为四个 5 km 整格，且两次结果完全一致。"""

    boundary = boundary_from_projected(box(10_000, 10_000, 20_000, 20_000))
    first = build_clipped_grid(boundary, grid_size_m=5_000)
    second = build_clipped_grid(boundary, grid_size_m=5_000)

    assert len(first.cells) == 4
    assert all(math.isclose(cell.area_m2, 25_000_000, abs_tol=10) for cell in first.cells)
    assert all(cell.representative_method == "cell_center" for cell in first.cells)
    assert [cell.grid_id for cell in first.cells] == [cell.grid_id for cell in second.cells]
    assert [(cell.representative_x_m, cell.representative_y_m) for cell in first.cells] == [
        (cell.representative_x_m, cell.representative_y_m) for cell in second.cells
    ]
    assert_area_conserved(first)
    assert_representatives_inside(first)


def test_concave_or_hollow_boundary_uses_interior_point() -> None:
    """格心与面积质心均落在洞里时，保留完整裁剪单元并切换到内部代表点。"""

    outer = box(30_000, 30_000, 40_000, 40_000)
    hole = box(33_000, 33_000, 37_000, 37_000)
    donut = Polygon(outer.exterior.coords, [hole.exterior.coords])
    grid = build_clipped_grid(boundary_from_projected(donut), grid_size_m=10_000)

    assert len(grid.cells) == 1
    cell = grid.cells[0]
    assert math.isclose(cell.area_m2, 84_000_000, abs_tol=10)
    assert cell.representative_method == "interior_point"
    assert_area_conserved(grid)
    assert_representatives_inside(grid)


def test_disconnected_parts_in_one_square_are_not_discarded() -> None:
    """同一个原始方格内的两块离散陆地，应保留为两个有独立代表点的单元。"""

    left = box(50_500, 50_500, 52_000, 52_000)
    right = box(57_000, 57_000, 59_500, 59_500)
    islands = MultiPolygon([left, right])
    grid = build_clipped_grid(boundary_from_projected(islands), grid_size_m=10_000)

    assert len(grid.cells) == 2
    assert len({cell.grid_id for cell in grid.cells}) == 2
    assert all(cell.grid_col == 5 and cell.grid_row == 5 for cell in grid.cells)
    assert_area_conserved(grid)
    assert_representatives_inside(grid)


def test_real_hangzhou_boundary_and_scale_sensitivity() -> None:
    """真实杭州边界应面积守恒；5 km 与 2.5 km 仅改变近似分辨率，不能丢面积。"""

    boundary = load_city_boundary(ROOT / "data" / "geo" / "330100.json", adcode="330100")
    assert boundary.city_name == "杭州市"
    grid_5km = build_clipped_grid(boundary, grid_size_m=5_000)
    grid_2_5km = build_clipped_grid(boundary, grid_size_m=2_500)

    assert len(grid_5km.cells) > 0
    assert len(grid_2_5km.cells) > len(grid_5km.cells)
    assert_area_conserved(grid_5km, tolerance_m2=1e-2)
    assert_area_conserved(grid_2_5km, tolerance_m2=1e-2)
    assert_representatives_inside(grid_5km)
    assert_representatives_inside(grid_2_5km)


def main() -> int:
    tests = [
        test_full_cells_and_repeatability,
        test_concave_or_hollow_boundary_uses_interior_point,
        test_disconnected_parts_in_one_square_are_not_discarded,
        test_real_hangzhou_boundary_and_scale_sensitivity,
    ]
    for test in tests:
        test()
        print(f"[通过] {test.__name__}")
    print(f"区域网格自检通过：{len(tests)}/{len(tests)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
