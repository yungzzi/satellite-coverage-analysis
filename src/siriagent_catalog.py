# -*- coding: utf-8 -*-
"""SIRIAgent 冻结卫星目录的读取、筛选与逐星 Profile 适配。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping


CATALOG_RELATIVE_PATH = "data/platform/siriagent_110_catalog.json"
CATALOG_SENSOR_TYPES = ("可见光", "高光谱", "红外", "SAR", "全部")
CATALOG_PROFILE_SLUG = "siriagent_catalog_swath_v1"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_catalog(root: str | Path) -> dict[str, Any]:
    root_path = Path(root).resolve()
    path = root_path / CATALOG_RELATIVE_PATH
    raw = path.read_bytes()
    catalog = json.loads(raw.decode("utf-8-sig"))
    rows = catalog.get("satellites") or []
    if catalog.get("schema_version") != "siriagent_satellite_catalog_v1":
        raise ValueError("SIRIAgent 卫星目录 schema_version 不受支持。")
    if len(rows) != 110:
        raise ValueError(f"SIRIAgent 冻结目录应有 110 颗，实际 {len(rows)} 颗。")
    ids = [int(row["norad_id"]) for row in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("SIRIAgent 冻结目录含重复 NORAD。")
    tle_path = root_path / str(catalog["tle_snapshot_path"])
    tle_raw = tle_path.read_bytes()
    if _sha256(tle_raw) != catalog.get("tle_snapshot_sha256"):
        raise ValueError("SIRIAgent 110 星 TLE 快照哈希不一致。")
    catalog["_catalog_path"] = str(path)
    catalog["_catalog_sha256"] = _sha256(raw)
    catalog["_tle_path"] = str(tle_path)
    return catalog


def select_entries(
    catalog: Mapping[str, Any],
    *,
    sensor_type: str = "可见光",
    max_resolution_m: float = 10.0,
    satellite_ids: Iterable[int] | None = None,
) -> list[dict[str, Any]]:
    if sensor_type not in CATALOG_SENSOR_TYPES:
        raise ValueError(f"平台候选传感器类型不支持：{sensor_type}")
    wanted = None if satellite_ids is None else {int(value) for value in satellite_ids}
    rows: list[dict[str, Any]] = []
    for row in catalog.get("satellites") or []:
        norad_id = int(row["norad_id"])
        if wanted is not None and norad_id not in wanted:
            continue
        if sensor_type != "全部" and row.get("sensor_class") != sensor_type:
            continue
        if float(row["resolution_m"]) > float(max_resolution_m):
            continue
        rows.append(dict(row))
    return rows


def _user_override_is_complete_sar(value: Mapping[str, Any] | None) -> bool:
    if not value or value.get("sensor_type") != "SAR":
        return False
    params = value.get("sensor_params") or []
    return isinstance(params, (list, tuple)) and len(params) >= 4


def build_catalog_overrides(
    entries: Iterable[Mapping[str, Any]],
    *,
    user_overrides: Iterable[Mapping[str, Any]] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """返回可运行逐星配置和因缺物理参数被阻止的卫星。"""

    user_by_id = {int(item["norad_id"]): dict(item) for item in user_overrides}
    ready: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for entry in entries:
        norad_id = int(entry["norad_id"])
        user = user_by_id.get(norad_id)
        profile = entry.get("profile") or {}
        if not profile.get("engine_ready"):
            if _user_override_is_complete_sar(user):
                ready.append({"norad": norad_id, **{k: v for k, v in user.items() if k != "norad_id"}})
            else:
                blocked.append(
                    {
                        "norad_id": norad_id,
                        "name": entry.get("name"),
                        "sensor_class": entry.get("sensor_class"),
                        "missing_fields": list(profile.get("blocking_missing_fields") or []),
                    }
                )
            continue
        generated = {
            "norad": norad_id,
            "sensor_type": "Rectangular",
            "swath_km": float(entry["swath_km"]),
            "along_track_half_angle_deg": float(profile["along_track_half_angle_deg"]),
            "min_elevation_deg": 0.0,
        }
        if user:
            generated.update({key: value for key, value in user.items() if key != "norad_id"})
        ready.append(generated)
    return ready, blocked


__all__ = [
    "CATALOG_PROFILE_SLUG",
    "CATALOG_RELATIVE_PATH",
    "CATALOG_SENSOR_TYPES",
    "build_catalog_overrides",
    "load_catalog",
    "select_entries",
]
