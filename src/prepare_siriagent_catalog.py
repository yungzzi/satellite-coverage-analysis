# -*- coding: utf-8 -*-
"""冻结 SIRIAgent 110 星目录，并按 NORAD 从本地 active.tle 生成 TLE 快照。

平台 ``/api/bootstrap`` 还包含城市边界和前端配置，甚至可能包含公开地图 Key；本脚本只提取
卫星目录允许字段，不保存整份响应。平台 ``orbit_period_min`` 仅作为原始声称值保留，计算周期
一律从 TLE 第 2 行平均运动重新推导。

默认通过用户指定的 mihomo 代理访问平台：

    python src/prepare_siriagent_catalog.py --active-tle data/tle_cache/active.tle

也可以使用已经下载到临时目录的 bootstrap，完成完全离线重建：

    python src/prepare_siriagent_catalog.py --bootstrap-json <bootstrap.json> \
        --active-tle data/tle_cache/active.tle
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_URL = "https://siri-iota.vercel.app/api/bootstrap"
DEFAULT_PROXY = "http://127.0.0.1:7890"
CATALOG_PATH = ROOT / "data" / "platform" / "siriagent_110_catalog.json"
TLE_SNAPSHOT_PATH = ROOT / "data" / "test_data" / "siriagent_110_snapshot.tle"
SCHEMA_VERSION = "siriagent_satellite_catalog_v1"
PROFILE_POLICY_VERSION = "catalog_swath_rectangular_v1"
ALONG_TRACK_HALF_ANGLE_DEG = 60.0

PLATFORM_FIELDS = (
    "satellite_id",
    "name",
    "series",
    "sensor_type",
    "resolution_m",
    "swath_km",
    "mean_motion",
    "orbit_period_min",
    "tle_available",
    "tle_fetched_at_utc",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_bytes(value: Any, *, indent: int | None = None) -> bytes:
    separators = None if indent is not None else (",", ":")
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            indent=indent,
            separators=separators,
        )
        + "\n"
    ).encode("utf-8")


def _download_bootstrap(proxy: str) -> bytes:
    handlers: list[Any] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        BOOTSTRAP_URL,
        headers={"User-Agent": "satellite-coverage-analysis/1.0"},
    )
    with opener.open(request, timeout=60) as response:
        return response.read()


def _platform_satellites(document: dict[str, Any]) -> list[dict[str, Any]]:
    rows = ((document.get("sat_ephemeris") or {}).get("satellites") or [])
    if not isinstance(rows, list):
        raise ValueError("bootstrap.sat_ephemeris.satellites 不是数组。")
    sanitized: list[dict[str, Any]] = []
    seen: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("平台卫星目录含非对象条目。")
        try:
            norad_id = int(str(raw.get("satellite_id", "")).strip())
        except ValueError as exc:
            raise ValueError(f"平台卫星编号无效：{raw.get('satellite_id')}") from exc
        if norad_id in seen:
            raise ValueError(f"平台卫星编号重复：{norad_id}")
        seen.add(norad_id)
        item = {key: raw.get(key) for key in PLATFORM_FIELDS}
        item["satellite_id"] = norad_id
        sanitized.append(item)
    if len(sanitized) != 110:
        raise ValueError(f"平台目录预期 110 颗，实际 {len(sanitized)} 颗；拒绝静默更新基线。")
    return sanitized


def _parse_tle_records(path: Path) -> dict[int, tuple[str, str, str]]:
    lines = [line.strip() for line in path.read_text(encoding="ascii", errors="strict").splitlines() if line.strip()]
    records: dict[int, tuple[str, str, str]] = {}
    index = 0
    while index + 2 < len(lines):
        name, line1, line2 = lines[index : index + 3]
        if line1.startswith("1 ") and line2.startswith("2 "):
            norad_id = int(line1[2:7])
            records[norad_id] = (name, line1, line2)
            index += 3
        else:
            index += 1
    if not records:
        raise ValueError(f"TLE 文件没有可解析记录：{path}")
    return records


def _tle_epoch_iso(line1: str) -> str:
    year_2 = int(line1[18:20])
    year = 1900 + year_2 if year_2 >= 57 else 2000 + year_2
    day_of_year = float(line1[20:32])
    moment = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year - 1.0)
    return moment.isoformat().replace("+00:00", "Z")


def _tle_mean_motion(line2: str) -> float:
    value = float(line2[52:63])
    if value <= 0.0:
        raise ValueError("TLE 平均运动必须大于 0。")
    return value


def _profile_metadata(row: dict[str, Any]) -> dict[str, Any]:
    sensor_class = str(row.get("sensor_type") or "").strip()
    swath_km = float(row["swath_km"])
    if sensor_class == "SAR":
        return {
            "engine_ready": False,
            "production_verified": False,
            "model": "SAR",
            "blocking_missing_fields": [
                "incidence_angle_min_deg",
                "incidence_angle_max_deg",
                "forward_exclusion_deg",
                "aft_exclusion_deg",
            ],
            "note": "平台只有幅宽，不能唯一反推出 SAR 原生入射角带；禁止退回圆锥。",
        }
    return {
        "engine_ready": True,
        "production_verified": False,
        "model": "Rectangular",
        "swath_km": swath_km,
        "cross_track_source": "platform_catalog_swath_km",
        "along_track_half_angle_deg": ALONG_TRACK_HALF_ANGLE_DEG,
        "along_track_source": "scene_assumption_not_platform_catalog",
        "note": "跨轨宽度来自平台目录；沿轨放松角是场景假设，不是载荷实测值。",
    }


def prepare(
    *,
    bootstrap_bytes: bytes,
    active_tle_path: Path,
    snapshot_label: str,
) -> dict[str, Any]:
    document = json.loads(bootstrap_bytes.decode("utf-8-sig"))
    platform_rows = _platform_satellites(document)
    tle_by_id = _parse_tle_records(active_tle_path)
    platform_payload_sha256 = _sha256(_canonical_bytes(platform_rows))

    missing_tle = [row["satellite_id"] for row in platform_rows if row["satellite_id"] not in tle_by_id]
    if missing_tle:
        raise ValueError(f"active.tle 缺少平台卫星：{missing_tle}")

    tle_lines: list[str] = []
    output_rows: list[dict[str, Any]] = []
    platform_period_mismatch_count = 0
    epochs: list[str] = []
    for row in platform_rows:
        norad_id = int(row["satellite_id"])
        _, line1, line2 = tle_by_id[norad_id]
        mean_motion = _tle_mean_motion(line2)
        tle_period_min = 1440.0 / mean_motion
        if not 80.0 <= tle_period_min <= 180.0:
            raise ValueError(
                f"NORAD {norad_id} 的 TLE 周期 {tle_period_min:.3f} min 超出当前低轨目录验收范围。"
            )
        epoch = _tle_epoch_iso(line1)
        epochs.append(epoch)
        listed_period = row.get("orbit_period_min")
        if listed_period is not None and abs(float(listed_period) - tle_period_min) > 5.0:
            platform_period_mismatch_count += 1
        tle_lines.extend([str(row["name"]), line1, line2])
        output_rows.append(
            {
                "norad_id": norad_id,
                "name": str(row["name"]),
                "series": str(row["series"]),
                "sensor_class": str(row["sensor_type"]),
                "resolution_m": float(row["resolution_m"]),
                "swath_km": float(row["swath_km"]),
                "platform_tle_available": bool(row["tle_available"]),
                "platform_tle_fetched_at_utc": row.get("tle_fetched_at_utc"),
                "platform_mean_motion_claim": row.get("mean_motion"),
                "platform_orbit_period_min_claim": listed_period,
                "tle_epoch_utc": epoch,
                "tle_mean_motion_rev_per_day": mean_motion,
                "tle_orbit_period_min": round(tle_period_min, 6),
                "profile": _profile_metadata(row),
            }
        )

    tle_bytes = ("\n".join(tle_lines) + "\n").encode("ascii")
    TLE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    TLE_SNAPSHOT_PATH.write_bytes(tle_bytes)
    CATALOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    sensor_counts: dict[str, int] = {}
    for row in output_rows:
        key = row["sensor_class"]
        sensor_counts[key] = sensor_counts.get(key, 0) + 1
    catalog = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_label": snapshot_label,
        "source_url": BOOTSTRAP_URL,
        "platform_satellite_payload_sha256": platform_payload_sha256,
        "tle_snapshot_path": str(TLE_SNAPSHOT_PATH.relative_to(ROOT)).replace("\\", "/"),
        "tle_snapshot_sha256": _sha256(tle_bytes),
        "tle_epoch_range_utc": {"min": min(epochs), "max": max(epochs)},
        "profile_policy_version": PROFILE_POLICY_VERSION,
        "quality_summary": {
            "satellite_count": len(output_rows),
            "tle_matched_count": len(output_rows),
            "sensor_class_counts": sensor_counts,
            "platform_period_present_count": sum(
                row["platform_orbit_period_min_claim"] is not None for row in output_rows
            ),
            "platform_period_mismatch_gt_5_min_count": platform_period_mismatch_count,
            "catalog_derived_rectangular_count": sum(
                row["profile"]["engine_ready"] for row in output_rows
            ),
            "sar_missing_native_profile_count": sum(
                not row["profile"]["engine_ready"] for row in output_rows
            ),
            "visible_resolution_le_1m_count": sum(
                row["sensor_class"] == "可见光" and row["resolution_m"] <= 1.0
                for row in output_rows
            ),
        },
        "satellites": output_rows,
    }
    CATALOG_PATH.write_bytes(_canonical_bytes(catalog, indent=2))
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser(description="冻结 SIRIAgent 110 星目录与匹配 TLE")
    parser.add_argument("--bootstrap-json", help="已下载 bootstrap JSON；省略时按代理在线获取")
    parser.add_argument("--active-tle", default="data/tle_cache/active.tle")
    parser.add_argument("--proxy", default=DEFAULT_PROXY)
    parser.add_argument("--snapshot-label", default="2026-08-31")
    args = parser.parse_args()

    if args.bootstrap_json:
        bootstrap_bytes = Path(args.bootstrap_json).read_bytes()
    else:
        bootstrap_bytes = _download_bootstrap(args.proxy)
    active_path = Path(args.active_tle)
    if not active_path.is_absolute():
        active_path = ROOT / active_path
    catalog = prepare(
        bootstrap_bytes=bootstrap_bytes,
        active_tle_path=active_path,
        snapshot_label=args.snapshot_label,
    )
    quality = catalog["quality_summary"]
    print(
        f"已冻结 {quality['satellite_count']} 颗平台卫星；TLE 匹配 "
        f"{quality['tle_matched_count']}/110；非 SAR 可运行 Profile "
        f"{quality['catalog_derived_rectangular_count']}，SAR 待参数 "
        f"{quality['sar_missing_native_profile_count']}。"
    )
    print(f"目录：{CATALOG_PATH}")
    print(f"TLE：{TLE_SNAPSHOT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
