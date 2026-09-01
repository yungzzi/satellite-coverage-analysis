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
        f"面积加权 MRT {cards.get('area_weighted_mrt_minutes', 0)} 分钟。"
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
        ("面积加权 MRT", f"{cards.get('area_weighted_mrt_minutes', 0)} 分钟"),
        ("面积加权 ART", f"{cards.get('area_weighted_art_minutes', 0)} 分钟"),
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
         ("temporal_coverage_pct", "时间覆盖率/%"), ("mrt_minutes", "MRT/分钟")],
        list(result.get("weak_areas") or []),
    )
    assumptions = "".join(f"<li>{html.escape(str(item))}</li>" for item in result.get("assumptions") or [])
    return f"""<!doctype html><meta charset=utf-8><style>
    .sat-result{{font-family:system-ui,sans-serif;color:#172033;line-height:1.5}}
    .cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}}
    .card{{border:1px solid #dde4ee;border-radius:10px;padding:12px;background:#f8fafc}}
    .card span{{display:block;color:#657086;font-size:13px}} .card strong{{font-size:20px}}
    h2{{margin:18px 0 8px}} table{{border-collapse:collapse;width:100%;font-size:13px}}
    th,td{{border-bottom:1px solid #e5e9f0;padding:7px;text-align:left;white-space:nowrap}}
    .table-wrap{{overflow:auto}} .muted,small{{color:#657086}}
    </style><section class=sat-result><h2>卫星区域覆盖与选星结果</h2><p>{title}</p>
    <div class=cards>{card_html}</div><h2>卫星摘要</h2>{satellites}<h2>城市覆盖窗口</h2>{windows}
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

