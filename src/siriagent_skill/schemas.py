# -*- coding: utf-8 -*-
"""Skill 的统一输入 Schema、默认值和缺参提示。

这里刻意不依赖 FastAPI、MCP 或 Pydantic：同机技能执行器只要能启动 Python，
就可以读取同一份机器可读参数说明并完成校验。以后增加 HTTP/MCP 包装时，也应
继续复用本文件，不能再维护第二套参数口径。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OPERATION_ALIASES = {
    "coverage": "coverage_only",
    "coverage_only": "coverage_only",
    "task1": "task_1_min_satellites_temporal",
    "task_1": "task_1_min_satellites_temporal",
    "task_1_min_satellites_temporal": "task_1_min_satellites_temporal",
    "task2": "task_2_min_satellites_mrt",
    "task_2": "task_2_min_satellites_mrt",
    "task_2_min_satellites_mrt": "task_2_min_satellites_mrt",
    "task3": "task_3_max_qualified_area",
    "task_3": "task_3_max_qualified_area",
    "task_3_max_qualified_area": "task_3_max_qualified_area",
    "task4": "task_4_min_area_quantile_mrt",
    "task_4": "task_4_min_area_quantile_mrt",
    "task_4_min_area_quantile_mrt": "task_4_min_area_quantile_mrt",
    "task5": "task_5_min_area_weighted_art",
    "task_5": "task_5_min_area_weighted_art",
    "task_5_min_area_weighted_art": "task_5_min_area_weighted_art",
}

TASK_SPECS: dict[str, dict[str, Any]] = {
    "coverage_only": {
        "label": "区域覆盖分析（不选星）",
        "description": "评估全部候选卫星形成的城市覆盖窗口和区域指标。",
        "required": ["city_adcode"],
        "task_parameters": [],
    },
    "task_1_min_satellites_temporal": {
        "label": "任务①：满足时间覆盖率的最少卫星",
        "description": "要求目标面积中的每个单元达到时间覆盖率门槛，尽量少选卫星。",
        "required": ["city_adcode", "temporal_coverage_target_pct"],
        "task_parameters": [
            "temporal_coverage_target_pct",
            "area_target_pct",
            "allow_area_fallback",
            "fallback_area_target_pct",
        ],
    },
    "task_2_min_satellites_mrt": {
        "label": "任务②：满足 MRT 的最少卫星",
        "description": "要求目标面积中的每个单元最大空窗不超过门槛，尽量少选卫星。",
        "required": ["city_adcode", "mrt_target_minutes"],
        "task_parameters": [
            "mrt_target_minutes",
            "area_target_pct",
            "allow_area_fallback",
            "fallback_area_target_pct",
        ],
    },
    "task_3_max_qualified_area": {
        "label": "任务③：给定 N 最大化达标面积",
        "description": "在卫星预算内先最大化时间覆盖率达标面积，再最大化面积加权时间覆盖率。",
        "required": ["city_adcode", "n_budget", "temporal_coverage_target_pct"],
        "task_parameters": ["n_budget", "exact_budget", "temporal_coverage_target_pct"],
    },
    "task_4_min_area_quantile_mrt": {
        "label": "任务④：给定 N 最小化面积分位 MRT",
        "description": "在卫星预算内最小化指定面积比例对应的 MRT 分位值。",
        "required": ["city_adcode", "n_budget"],
        "task_parameters": ["n_budget", "area_target_pct"],
    },
    "task_5_min_area_weighted_art": {
        "label": "任务⑤：给定 N 最小化面积加权 ART",
        "description": "在卫星预算内降低全市面积加权平均空窗，并报告薄弱区域和面积分位 MRT。",
        "required": ["city_adcode", "n_budget"],
        "task_parameters": [
            "n_budget",
            "exact_budget",
            "mrt_area_quantile_pct",
            "max_local_rounds",
        ],
    },
}

TLE_SOURCES = {
    "jilin1_test": "data/test_data/jilin1_all.tle",
    "china_remote_sensing_test": "data/test_data/china_remote_sensing_all.tle",
}

# 当前只是可重复验收用的固定 Profile。正式接平台目录时必须按逐星幅宽/传感器表生成
# Rectangular/SAR 参数，不能把这个演示 Profile 当作真实载荷参数。
PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "optical_rectangular_validation_v1": {
        "label": "光学矩形验收 Profile（固定角度，仅用于接入验收）",
        "config": {
            "sensor_type": "Rectangular",
            "sensor_params": [45.0, 45.0],
            "min_elevation_deg": 0.0,
        },
        "assumption": (
            "使用固定 45°×45° Rectangular 验收 Profile；它用于验证 Skill 调用链，"
            "不代表平台 110 颗卫星的真实逐星载荷。"
        ),
    },
    "simple_cone_benchmark_v1": {
        "label": "历史基准圆锥 Profile（仅用于回归对照）",
        "config": {"fov_half_angle_deg": 30.0, "min_elevation_deg": 0.0},
        "assumption": (
            "使用历史 30° SimpleCone 基准，只用于与现有性能脚本对照；"
            "不得替代正式光学矩形或 SAR 原生模型。"
        ),
    },
}

PARAMETER_SPECS: dict[str, dict[str, Any]] = {
    "operation": {
        "type": "string",
        "required": True,
        "description": "coverage_only 或五项选星任务之一。",
        "allowed": list(TASK_SPECS),
    },
    "city_adcode": {
        "type": "string",
        "required": True,
        "description": "目标城市行政区划代码；首版只能使用项目中已冻结边界的地区。",
    },
    "start_utc": {
        "type": "ISO-8601 UTC string",
        "required": False,
        "default": "当前 UTC 整分钟",
        "description": "分析开始时刻；无时区时按 UTC 解释。",
    },
    "analysis_horizon_h": {
        "type": "number",
        "required": False,
        "default": 8.0,
        "range": "0 < value <= 168",
        "description": "分析时长，单位小时。",
    },
    "tle_source": {
        "type": "string",
        "required": False,
        "default": "jilin1_test",
        "allowed": list(TLE_SOURCES),
        "description": "内置可重复验收 TLE 数据源；也可改传 tle_path。",
    },
    "tle_path": {
        "type": "string",
        "required": False,
        "description": "项目目录内的 3LE/TLE 文件路径；传入后覆盖 tle_source。",
    },
    "satellite_ids": {
        "type": "integer[]",
        "required": False,
        "default": "TLE 文件中的全部卫星",
        "description": "可选 NORAD 编号白名单。",
    },
    "profile_slug": {
        "type": "string",
        "required": False,
        "default": "optical_rectangular_validation_v1",
        "allowed": list(PROFILE_SPECS),
        "description": "固定传感器/姿态场景 Profile。",
    },
    "grid_size_km": {
        "type": "number",
        "required": False,
        "default": 5.0,
        "range": "0 < value <= 50",
        "description": "裁剪格网边长；正式口径默认 5 km。",
    },
    "detection_step_s": {
        "type": "integer",
        "required": False,
        "default": 10,
        "range": "1..3600",
        "description": "覆盖机会检测步长，单位秒。",
    },
    "decision_step_s": {
        "type": "integer",
        "required": False,
        "default": 60,
        "range": "1..3600",
        "description": "选星决策时间片，单位秒。",
    },
    "min_simultaneous": {
        "type": "integer",
        "required": False,
        "default": 1,
        "allowed": [1, 2, 3],
        "description": "每个网格同一时刻至少同时覆盖的卫星数 K。",
    },
    "method": {
        "type": "string",
        "required": False,
        "default": "both（任务④默认 ilp，任务⑤固定 greedy）",
        "allowed": ["greedy", "ilp", "both"],
        "description": "选星方法；both 同时运行贪心和 ILP 并推荐结果。",
    },
    "selection_time_limit_s": {
        "type": "integer",
        "required": False,
        "default": 30,
        "range": "1..3600",
        "description": "单次 ILP 求解时间上限。",
    },
    "temporal_coverage_target_pct": {
        "type": "number",
        "required": "任务①、③必填",
        "range": "0..100",
        "description": "每个网格的时间覆盖率目标，不是城市空间覆盖率。",
    },
    "mrt_target_minutes": {
        "type": "number",
        "required": "任务②必填",
        "range": "value >= 0",
        "description": "最大允许重访空窗 MRT，单位分钟。",
    },
    "area_target_pct": {
        "type": "number",
        "required": False,
        "default": "任务①②为100，任务④为90",
        "range": "0..100",
        "description": "要求达标或参与面积分位统计的城市面积比例。",
    },
    "allow_area_fallback": {
        "type": "boolean",
        "required": False,
        "default": True,
        "description": "任务①②严格面积不可行时，是否显式尝试降级面积。",
    },
    "fallback_area_target_pct": {
        "type": "number",
        "required": False,
        "default": 90.0,
        "range": "0..100",
        "description": "任务①②的显式降级面积比例。",
    },
    "n_budget": {
        "type": "integer",
        "required": "任务③④⑤必填",
        "range": "value >= 0",
        "description": "最多或恰好选择的卫星数量 N。",
    },
    "exact_budget": {
        "type": "boolean",
        "required": False,
        "default": False,
        "description": "false 表示最多 N 颗，true 表示恰好 N 颗。",
    },
    "mrt_area_quantile_pct": {
        "type": "number",
        "required": False,
        "default": 90.0,
        "range": "0..100",
        "description": "任务⑤附带报告的面积分位 MRT 比例。",
    },
    "max_local_rounds": {
        "type": "integer",
        "required": False,
        "default": 3,
        "range": "0..20",
        "description": "任务⑤单星交换局部改进轮数。",
    },
    "debug": {
        "type": "boolean",
        "required": False,
        "default": False,
        "description": "是否在结果中附带逐网格地图 GeoJSON；普通对话应保持 false。",
    },
    "max_windows": {
        "type": "integer",
        "required": False,
        "default": 10,
        "range": "1..100",
        "description": "聊天结果最多返回多少个城市过境窗口。",
    },
    "max_weak_areas": {
        "type": "integer",
        "required": False,
        "default": 10,
        "range": "1..100",
        "description": "最多返回多少个覆盖薄弱网格摘要。",
    },
}


class SkillInputError(ValueError):
    """输入不合法；``details`` 可直接返回平台。"""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.details = dict(details or {})


class SkillNeedsInput(SkillInputError):
    """请求方向已明确，但还缺少用户必须给出的字段。"""

    def __init__(self, missing_fields: list[str], operation: str | None = None):
        self.missing_fields = missing_fields
        self.operation = operation
        hints = {name: PARAMETER_SPECS[name] for name in missing_fields if name in PARAMETER_SPECS}
        super().__init__(
            "还需要补充参数：" + "、".join(missing_fields),
            details={"missing_fields": missing_fields, "parameter_hints": hints},
        )


@dataclass(frozen=True)
class SkillRequest:
    operation: str
    city_adcode: str
    start_utc: datetime
    analysis_horizon_h: float
    tle_source: str
    tle_path: str | None
    satellite_ids: tuple[int, ...] | None
    profile_slug: str
    grid_size_km: float
    detection_step_s: int
    decision_step_s: int
    min_simultaneous: int
    method: str
    selection_time_limit_s: int
    temporal_coverage_target_pct: float | None
    mrt_target_minutes: float | None
    area_target_pct: float
    allow_area_fallback: bool
    fallback_area_target_pct: float
    n_budget: int | None
    exact_budget: bool
    mrt_area_quantile_pct: float
    max_local_rounds: int
    debug: bool
    max_windows: int
    max_weak_areas: int

    @property
    def end_utc(self) -> datetime:
        return self.start_utc + timedelta(hours=self.analysis_horizon_h)


def _available_cities(root: Path) -> list[dict[str, str]]:
    def repair(value: object) -> str:
        text = str(value)
        try:
            return text.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            return text

    cities: list[dict[str, str]] = []
    for path in sorted((root / "data" / "geo").glob("*.json")):
        name = path.stem
        try:
            document = json.loads(path.read_text(encoding="utf-8-sig"))
            if document.get("type") == "FeatureCollection":
                features = document.get("features") or []
                props = (features[0].get("properties") or {}) if features else {}
            else:
                props = document.get("properties") or {}
            name = repair(props.get("name") or props.get("city_name") or name)
        except Exception:
            pass
        cities.append({"adcode": path.stem, "name": name})
    return cities


def describe_capabilities(
    root: str | Path | None = None,
    *,
    operation: str | None = None,
) -> dict[str, Any]:
    """返回人和程序都能读取的任务/参数说明。"""

    project_root = Path(root).resolve() if root else PROJECT_ROOT
    normalized_operation: str | None = None
    if operation:
        normalized_operation = OPERATION_ALIASES.get(str(operation).strip().lower())
        if normalized_operation is None:
            raise SkillInputError(
                f"未知 operation：{operation}",
                details={"allowed_operations": list(TASK_SPECS)},
            )
    task_items = []
    for key, spec in TASK_SPECS.items():
        if normalized_operation and key != normalized_operation:
            continue
        task_items.append({"operation": key, **spec})
    common_names = [
        "city_adcode", "start_utc", "analysis_horizon_h", "tle_source", "tle_path",
        "satellite_ids", "profile_slug", "grid_size_km", "detection_step_s",
        "decision_step_s", "min_simultaneous", "method", "selection_time_limit_s",
        "debug", "max_windows", "max_weak_areas",
    ]
    parameter_names = list(common_names)
    if normalized_operation:
        parameter_names.extend(TASK_SPECS[normalized_operation]["task_parameters"])
    else:
        parameter_names.extend(
            name for spec in TASK_SPECS.values() for name in spec["task_parameters"]
        )
    parameter_names = list(dict.fromkeys(parameter_names))
    parameters = {name: dict(PARAMETER_SPECS[name]) for name in parameter_names}
    if normalized_operation == "task_4_min_area_quantile_mrt":
        parameters["method"]["default"] = "ilp"
    elif normalized_operation == "task_5_min_area_weighted_art":
        parameters["method"]["default"] = "greedy"
        parameters["method"]["allowed"] = ["greedy"]
    return {
        "status": "ok",
        "skill": "satellite-coverage-selection",
        "execution_mode": "local_python_direct",
        "mcp_required": False,
        "fastapi_required": False,
        "tasks": task_items,
        "parameters": parameters,
        "available_cities": _available_cities(project_root),
        "tle_sources": TLE_SOURCES,
        "profiles": {
            key: {"label": value["label"], "assumption": value["assumption"]}
            for key, value in PROFILE_SPECS.items()
        },
        "notes": [
            "默认 8 小时，K 默认 1；K 首版支持 1～3。",
            "任务①③的时间覆盖率目标与城市空间覆盖率不是同一个参数。",
            "正式载荷接入前必须用平台逐星参数替换验收 Profile。",
        ],
    }


def _parse_datetime(value: Any) -> datetime:
    if value in (None, ""):
        now = datetime.now(timezone.utc)
        return now.replace(second=0, microsecond=0)
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError as exc:
            raise SkillInputError("start_utc 必须是 ISO-8601 时间，例如 2026-08-31T00:00:00Z。") from exc
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    raise SkillInputError(f"{name} 必须是 true 或 false。")


def _as_float(payload: Mapping[str, Any], name: str, default: float) -> float:
    try:
        return float(payload.get(name, default))
    except (TypeError, ValueError) as exc:
        raise SkillInputError(f"{name} 必须是数字。") from exc


def _as_int(payload: Mapping[str, Any], name: str, default: int) -> int:
    value = payload.get(name, default)
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise SkillInputError(f"{name} 必须是整数。") from exc
    if isinstance(value, float) and not value.is_integer():
        raise SkillInputError(f"{name} 必须是整数。")
    return number


def _pct(value: float, name: str) -> float:
    if not 0.0 <= value <= 100.0:
        raise SkillInputError(f"{name} 必须在 0～100 之间。")
    return value


def _satellite_ids(value: Any) -> tuple[int, ...] | None:
    if value in (None, "", []):
        return None
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, (list, tuple, set)):
        raise SkillInputError("satellite_ids 必须是 NORAD 编号数组或逗号分隔字符串。")
    try:
        result = tuple(dict.fromkeys(int(item) for item in items))
    except (TypeError, ValueError) as exc:
        raise SkillInputError("satellite_ids 中只能包含整数 NORAD 编号。") from exc
    return result or None


def normalize_request(payload: Mapping[str, Any]) -> SkillRequest:
    """把用户/平台 JSON 归一化为稳定的 SkillRequest。"""

    if not isinstance(payload, Mapping):
        raise SkillInputError("输入必须是 JSON 对象。")
    raw_operation = payload.get("operation", payload.get("task_type"))
    if raw_operation in (None, ""):
        raise SkillNeedsInput(["operation"])
    operation = OPERATION_ALIASES.get(str(raw_operation).strip().lower())
    if operation is None:
        raise SkillInputError(
            f"未知 operation：{raw_operation}",
            details={"allowed_operations": list(TASK_SPECS)},
        )

    city_value = payload.get("city_adcode", payload.get("city"))
    missing: list[str] = []
    if city_value in (None, ""):
        missing.append("city_adcode")
    for name in TASK_SPECS[operation]["required"]:
        if name != "city_adcode" and payload.get(name) in (None, ""):
            missing.append(name)
    if missing:
        raise SkillNeedsInput(missing, operation)

    city_text = str(city_value).strip()
    city_name_to_code = {"杭州": "330100", "杭州市": "330100", "浙江": "330000", "浙江省": "330000"}
    city_adcode = city_name_to_code.get(city_text, city_text)

    horizon = _as_float(payload, "analysis_horizon_h", 8.0)
    if not 0.0 < horizon <= 168.0:
        raise SkillInputError("analysis_horizon_h 必须大于 0 且不超过 168 小时。")
    grid_size_km = _as_float(payload, "grid_size_km", 5.0)
    if not 0.0 < grid_size_km <= 50.0:
        raise SkillInputError("grid_size_km 必须大于 0 且不超过 50 km。")
    detection_step_s = _as_int(payload, "detection_step_s", 10)
    decision_step_s = _as_int(payload, "decision_step_s", 60)
    if not 1 <= detection_step_s <= 3600 or not 1 <= decision_step_s <= 3600:
        raise SkillInputError("detection_step_s 和 decision_step_s 必须在 1～3600 秒之间。")
    min_simultaneous = _as_int(payload, "min_simultaneous", 1)
    if min_simultaneous not in {1, 2, 3}:
        raise SkillInputError("min_simultaneous 首版只支持 1、2、3。")
    if operation == "task_4_min_area_quantile_mrt":
        method_default = "ilp"
    elif operation == "task_5_min_area_weighted_art":
        method_default = "greedy"
    else:
        method_default = "both"
    method = str(payload.get("method", method_default)).strip().lower()
    if method not in {"greedy", "ilp", "both"}:
        raise SkillInputError("method 必须是 greedy、ilp 或 both。")
    if operation == "task_5_min_area_weighted_art" and method != "greedy":
        raise SkillInputError("任务⑤当前只实现 greedy，method 必须是 greedy。")
    time_limit = _as_int(payload, "selection_time_limit_s", 30)
    if not 1 <= time_limit <= 3600:
        raise SkillInputError("selection_time_limit_s 必须在 1～3600 秒之间。")

    temporal_target = None
    if payload.get("temporal_coverage_target_pct") not in (None, ""):
        temporal_target = _pct(
            _as_float(payload, "temporal_coverage_target_pct", 0.0),
            "temporal_coverage_target_pct",
        )
    mrt_minutes = None
    if payload.get("mrt_target_minutes") not in (None, ""):
        mrt_minutes = _as_float(payload, "mrt_target_minutes", 0.0)
        if mrt_minutes < 0.0:
            raise SkillInputError("mrt_target_minutes 不能为负数。")
    area_default = 90.0 if operation == "task_4_min_area_quantile_mrt" else 100.0
    area_target = _pct(_as_float(payload, "area_target_pct", area_default), "area_target_pct")
    fallback_area = _pct(
        _as_float(payload, "fallback_area_target_pct", 90.0),
        "fallback_area_target_pct",
    )
    allow_fallback = _as_bool(payload.get("allow_area_fallback", True), "allow_area_fallback")
    if (
        allow_fallback
        and operation in {"task_1_min_satellites_temporal", "task_2_min_satellites_mrt"}
        and fallback_area > area_target
    ):
        raise SkillInputError("fallback_area_target_pct 不能高于严格 area_target_pct。")

    n_budget = None
    if payload.get("n_budget") not in (None, ""):
        n_budget = _as_int(payload, "n_budget", 0)
        if n_budget < 0:
            raise SkillInputError("n_budget 不能为负数。")
    quantile = _pct(
        _as_float(payload, "mrt_area_quantile_pct", 90.0),
        "mrt_area_quantile_pct",
    )
    max_local_rounds = _as_int(payload, "max_local_rounds", 3)
    if not 0 <= max_local_rounds <= 20:
        raise SkillInputError("max_local_rounds 必须在 0～20 之间。")
    max_windows = _as_int(payload, "max_windows", 10)
    max_weak_areas = _as_int(payload, "max_weak_areas", 10)
    if not 1 <= max_windows <= 100 or not 1 <= max_weak_areas <= 100:
        raise SkillInputError("max_windows 和 max_weak_areas 必须在 1～100 之间。")

    tle_source = str(payload.get("tle_source", "jilin1_test")).strip()
    tle_path = payload.get("tle_path")
    if tle_path in (None, "") and tle_source not in TLE_SOURCES:
        raise SkillInputError(
            f"未知 tle_source：{tle_source}",
            details={"allowed_tle_sources": list(TLE_SOURCES)},
        )
    profile_slug = str(payload.get("profile_slug", "optical_rectangular_validation_v1")).strip()
    if profile_slug not in PROFILE_SPECS:
        raise SkillInputError(
            f"未知 profile_slug：{profile_slug}",
            details={"allowed_profiles": list(PROFILE_SPECS)},
        )

    return SkillRequest(
        operation=operation,
        city_adcode=city_adcode,
        start_utc=_parse_datetime(payload.get("start_utc")),
        analysis_horizon_h=horizon,
        tle_source=tle_source,
        tle_path=None if tle_path in (None, "") else str(tle_path),
        satellite_ids=_satellite_ids(payload.get("satellite_ids")),
        profile_slug=profile_slug,
        grid_size_km=grid_size_km,
        detection_step_s=detection_step_s,
        decision_step_s=decision_step_s,
        min_simultaneous=min_simultaneous,
        method=method,
        selection_time_limit_s=time_limit,
        temporal_coverage_target_pct=temporal_target,
        mrt_target_minutes=mrt_minutes,
        area_target_pct=area_target,
        allow_area_fallback=allow_fallback,
        fallback_area_target_pct=fallback_area,
        n_budget=n_budget,
        exact_budget=_as_bool(payload.get("exact_budget", False), "exact_budget"),
        mrt_area_quantile_pct=quantile,
        max_local_rounds=max_local_rounds,
        debug=_as_bool(payload.get("debug", False), "debug"),
        max_windows=max_windows,
        max_weak_areas=max_weak_areas,
    )


__all__ = [
    "PARAMETER_SPECS",
    "PROFILE_SPECS",
    "PROJECT_ROOT",
    "SkillInputError",
    "SkillNeedsInput",
    "SkillRequest",
    "TASK_SPECS",
    "TLE_SOURCES",
    "describe_capabilities",
    "normalize_request",
]
