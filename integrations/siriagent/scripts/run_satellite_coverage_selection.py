# -*- coding: utf-8 -*-
"""SIRIAgent 平台启动器，同时提供本地 JSON 命令行验收入口。"""

from __future__ import annotations

import argparse
import contextlib
import html
import importlib
import io
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = ("numpy", "pandas", "matplotlib", "shapely", "pyproj", "skyfield", "sgp4", "pulp")


def _engine_root(explicit: str | Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_root = os.environ.get("SATELLITE_COVERAGE_ENGINE_ROOT")
    if env_root:
        candidates.append(Path(env_root))
    candidates.append(PACKAGE_ROOT / "engine")
    candidates.extend(parent for parent in PACKAGE_ROOT.parents)
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if (resolved / "src" / "siriagent_skill" / "runner.py").is_file():
            return resolved
    raise FileNotFoundError(
        "未找到 engine/src/siriagent_skill/runner.py；请使用完整交付包，"
        "或设置 SATELLITE_COVERAGE_ENGINE_ROOT。"
    )


def _notify(progress: Callable[..., Any] | None, percent: int, message: str) -> None:
    if not callable(progress):
        return
    try:
        progress(percent, message)
    except TypeError:
        progress({"percent": percent, "message": message})


def self_check(engine_root: str | Path | None = None) -> dict[str, Any]:
    try:
        root = _engine_root(engine_root)
    except FileNotFoundError as exc:
        return {"status": "failed", "ready": False, "message": str(exc)}
    missing_files = [
        relative
        for relative in (
            "src/siriagent_skill/runner.py",
            "src/regional_selection.py",
            "data/geo/330100.json",
            "data/test_data/jilin1_all.tle",
            "data/test_data/siriagent_110_snapshot.tle",
        )
        if not (root / relative).is_file()
    ]
    dependency_versions: dict[str, str] = {}
    dependency_errors: dict[str, str] = {}
    for name in REQUIRED_MODULES:
        try:
            module = importlib.import_module(name)
            dependency_versions[name] = str(getattr(module, "__version__", "installed"))
        except Exception as exc:  # pragma: no cover - 由缺依赖环境触发
            dependency_errors[name] = f"{type(exc).__name__}: {exc}"
    cbc_available = False
    if "pulp" not in dependency_errors:
        import pulp

        try:
            cbc_available = bool(pulp.PULP_CBC_CMD(msg=False).available())
        except Exception:
            cbc_available = False
    ready = not missing_files and not dependency_errors and cbc_available
    return {
        "status": "ok" if ready else "failed",
        "ready": ready,
        "engine_root": str(root),
        "missing_files": missing_files,
        "dependencies": dependency_versions,
        "dependency_errors": dependency_errors,
        "cbc_available": cbc_available,
        "message": (
            "交付包结构、Python 依赖和 ILP/CBC 求解器均可用。"
            if ready
            else "自检未通过；请根据 missing_files、dependency_errors 和 cbc_available 修复。"
        ),
    }


def _summary(result: Mapping[str, Any]) -> str:
    status = result.get("status")
    if status != "completed":
        return str(result.get("message") or f"执行状态：{status}")
    city = result.get("city") or {}
    cards = result.get("summary_cards") or {}
    operation = result.get("operation", "coverage_only")
    base = (
        f"{city.get('name', city.get('adcode', '目标区域'))}分析完成："
        f"候选/选中卫星 {cards.get('satellite_count', 0)} 颗，"
        f"城市面积加权时间覆盖率 {cards.get('city_mean_temporal_coverage_pct', 0)}%，"
        f"网格最长等待的面积加权平均值 {cards.get('area_weighted_mrt_minutes', 0)} 分钟。"
    )
    if operation != "coverage_only":
        feasible = "达到严格或允许的降级目标" if result.get("feasible") else "未达到目标"
        base += f" 选星结果{feasible}。"
    return base


def _table(headers: list[tuple[str, str]], rows: list[Mapping[str, Any]], limit: int = 12) -> str:
    if not rows:
        return '<p class="muted">无可显示记录。</p>'
    head = "".join(f"<th>{html.escape(label)}</th>" for _, label in headers)
    body = []
    for row in rows[:limit]:
        cells = "".join(
            f"<td>{html.escape(str(row.get(key, '—') if row.get(key) is not None else '—'))}</td>"
            for key, _ in headers
        )
        body.append(f"<tr>{cells}</tr>")
    return f"<div class=table-wrap><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _quality_map_svg(result: Mapping[str, Any]) -> str:
    """把紧凑 GeoJSON 渲染成无外部依赖的预览图。

    平台前端可直接使用 ``coverage_map.geojson`` 做交互式地图；这里的 SVG 只是
    view_html 的降级预览，避免 skill 单独打开时还依赖 Leaflet/网络地图底图。
    """

    coverage_map = result.get("coverage_map") or {}
    geojson = coverage_map.get("geojson") or {}
    features = geojson.get("features") or []
    if not features:
        return '<p class="muted">暂无可绘制的网格质量地图。</p>'

    def rings(geometry: Mapping[str, Any]) -> list[list[tuple[float, float]]]:
        kind = geometry.get("type")
        coordinates = geometry.get("coordinates") or []
        if kind == "Polygon":
            return [
                [(float(point[0]), float(point[1])) for point in ring]
                for ring in coordinates
                if len(ring) >= 3
            ]
        if kind == "MultiPolygon":
            return [
                [(float(point[0]), float(point[1])) for point in ring]
                for polygon in coordinates
                for ring in polygon
                if len(ring) >= 3
            ]
        return []

    all_points = [
        point
        for feature in features
        for ring in rings(feature.get("geometry") or {})
        for point in ring
    ]
    if not all_points:
        return '<p class="muted">网格几何为空，无法绘制地图。</p>'
    min_lon = min(point[0] for point in all_points)
    max_lon = max(point[0] for point in all_points)
    min_lat = min(point[1] for point in all_points)
    max_lat = max(point[1] for point in all_points)
    lon_span = max(max_lon - min_lon, 1e-9)
    lat_span = max(max_lat - min_lat, 1e-9)
    width, height, pad = 800.0, 430.0, 12.0

    def project(point: tuple[float, float]) -> tuple[float, float]:
        x = pad + (point[0] - min_lon) / lon_span * (width - 2 * pad)
        y = pad + (max_lat - point[1]) / lat_span * (height - 2 * pad)
        return x, y

    def color(value: float) -> str:
        # 预览采用连续的红-黄-绿三段色阶；不做空间平滑，避免制造不存在的覆盖。
        bounded = max(0.0, min(100.0, value)) / 100.0
        if bounded < 0.5:
            ratio = bounded * 2.0
            return f"rgb(239,{int(80 + 130 * ratio)},{int(70 - 20 * ratio)})"
        ratio = (bounded - 0.5) * 2.0
        return f"rgb({int(239 - 170 * ratio)},{int(210 + 25 * ratio)},{int(50 + 75 * ratio)})"

    paths: list[str] = []
    for feature in features:
        props = feature.get("properties") or {}
        value = float(props.get("temporal_coverage_pct") or 0.0)
        subpaths: list[str] = []
        for ring in rings(feature.get("geometry") or {}):
            points = [project(point) for point in ring]
            subpaths.append("M " + " L ".join(f"{x:.2f},{y:.2f}" for x, y in points) + " Z")
        if subpaths:
            paths.append(
                f'<path d="{" ".join(subpaths)}" fill="{color(value)}" '
                'stroke="#ffffff" stroke-width="0.35" fill-rule="evenodd">'
                f'<title>{html.escape(str(props.get("grid_id", "网格")))}：时间覆盖率 {value:.2f}%</title></path>'
            )
    city_name = html.escape(str(geojson.get("properties", {}).get("city_name") or "目标城市"))
    return (
        f'<div class="quality-map"><div class="map-caption">{city_name} 覆盖质量地图（默认：时间覆盖率）</div>'
        f'<svg viewBox="0 0 {int(width)} {int(height)}" role="img" aria-label="{city_name} 覆盖质量地图">'
        f'{"".join(paths)}</svg>'
        '<div class="legend"><span><i style="background:#ef5046"></i>低</span>'
        '<span><i style="background:#efd232"></i>中</span>'
        '<span><i style="background:#45b96b"></i>高</span></div>'
        '<small>颜色表示分析时段内累计时间覆盖率；平台前端可切换最长等待、平均等待和达标状态。</small></div>'
    )


def _view_html(result: Mapping[str, Any]) -> str:
    title = html.escape(_summary(result))
    if result.get("status") != "completed":
        missing = result.get("missing_fields") or []
        extra = f"<p>还需要：{html.escape(', '.join(map(str, missing)))}</p>" if missing else ""
        return f"<section class=sat-result><h2>卫星覆盖与选星</h2><p>{title}</p>{extra}</section>"
    cards = result.get("summary_cards") or {}
    card_specs = [
        ("卫星数", cards.get("satellite_count")),
        ("平均时间覆盖率", f"{cards.get('city_mean_temporal_coverage_pct', 0)}%"),
        ("最长等待（面积加权）", f"{cards.get('area_weighted_mrt_minutes', 0)} 分钟"),
        ("平均等待（面积加权）", f"{cards.get('area_weighted_art_minutes', 0)} 分钟"),
    ]
    card_html = "".join(
        f"<div class=card><span>{html.escape(label)}</span><strong>{html.escape(str(value))}</strong></div>"
        for label, value in card_specs
    )
    satellites = _table(
        [("satellite", "卫星"), ("norad_id", "NORAD"), ("city_pass_count", "城市窗口数"),
         ("cumulative_city_intersection_minutes", "累计相交/分钟"), ("first_window_utc", "首个窗口 UTC")],
        list(result.get("satellite_table") or []),
    )
    windows = _table(
        [("satellite", "卫星"), ("start_utc", "开始 UTC"), ("end_utc", "结束 UTC"),
         ("duration_minutes", "时长/分钟"), ("maximum_covered_area_pct", "峰值覆盖面积")],
        list(result.get("city_pass_window_table") or []),
    )
    weak = _table(
        [("grid_id", "薄弱单元"), ("representative_lat", "纬度"), ("representative_lon", "经度"),
         ("temporal_coverage_pct", "时间覆盖率/%"), ("mrt_minutes", "最长等待/分钟")],
        list(result.get("weak_areas") or []),
    )
    quality_map = _quality_map_svg(result)
    assumptions = "".join(f"<li>{html.escape(str(item))}</li>" for item in result.get("assumptions") or [])
    return f"""<!doctype html><meta charset=utf-8><style>
    .sat-result{{font-family:system-ui,sans-serif;color:#172033;line-height:1.5}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
    .card{{border:1px solid #dde4ee;border-radius:10px;padding:12px;background:#f8fafc}}
    .card span{{display:block;color:#657086;font-size:13px}} .card strong{{font-size:20px}}
    h2{{margin:18px 0 8px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
    th,td{{border-bottom:1px solid #e5e9f0;padding:7px;text-align:left;white-space:nowrap}}
    .table-wrap{{overflow:auto}} .muted,small{{color:#657086}}
    .quality-map{{border:1px solid #dde4ee;border-radius:10px;padding:10px;background:#fff}}
    .quality-map svg{{display:block;width:100%;height:auto;max-height:430px;background:#f5f7fa;border-radius:7px}}
    .map-caption{{font-weight:600;margin:2px 0 8px}} .legend{{display:flex;gap:16px;margin:8px 0 3px;font-size:12px;color:#657086}}
    .legend i{{display:inline-block;width:12px;height:12px;border-radius:3px;vertical-align:-2px;margin-right:4px}}
    </style><section class=sat-result><h2>卫星区域覆盖与选星结果</h2><p>{title}</p>
    <div class=cards>{card_html}</div><h2>覆盖质量地图</h2>{quality_map}<h2>卫星摘要</h2>{satellites}<h2>城市覆盖窗口</h2>{windows}
    <h2>覆盖薄弱区域</h2>{weak}<h2>口径与假设</h2><ul>{assumptions}</ul></section>"""


def run(
    inputs: Mapping[str, Any] | None,
    progress: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """平台约定入口：接收 inputs，返回 ``result`` 与可直接展示的 HTML。"""

    root = _engine_root(kwargs.get("engine_root"))
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    _notify(progress, 5, "正在校验输入与加载冻结数据")
    from siriagent_skill.runner import run_skill

    captured = io.StringIO()
    _notify(progress, 15, "正在计算城市网格覆盖机会")
    with contextlib.redirect_stdout(captured):
        result = run_skill(dict(inputs or {}), root=root)
    diagnostic = captured.getvalue().strip()
    if diagnostic:
        print(diagnostic, file=sys.stderr)
    result = dict(result)
    result.setdefault("summary", _summary(result))
    _notify(progress, 100, "分析完成")
    return {"result": result, "view_html": _view_html(result)}


def _read_inputs(args: argparse.Namespace) -> dict[str, Any]:
    if args.describe:
        return {"action": "describe", "describe_operation": args.describe_operation}
    if args.input_json:
        return json.loads(args.input_json)
    if args.input_file:
        return json.loads(args.input_file.read_text(encoding="utf-8-sig"))
    if not sys.stdin.isatty():
        raw = sys.stdin.read().strip()
        if raw:
            return json.loads(raw)
    return {"action": "describe", "describe_operation": args.describe_operation}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="SIRIAgent 卫星覆盖与选星启动器")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--describe", action="store_true")
    parser.add_argument("--describe-operation")
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--input-json")
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            output = self_check(args.engine_root)
        else:
            output = run(_read_inputs(args), engine_root=args.engine_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        output = {"result": {"status": "invalid_input", "message": str(exc)}, "view_html": ""}
    print(json.dumps(output, ensure_ascii=False, indent=2 if args.pretty else None, allow_nan=False))
    if args.self_check:
        return 0 if output.get("ready") else 1
    return 0 if output.get("result", {}).get("status") not in {"invalid_input", "execution_error"} else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["run", "self_check"]

