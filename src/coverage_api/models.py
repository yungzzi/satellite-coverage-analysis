"""HTTP 类型与文档；业务默认值和条件必填规则仍由原 Schema 决定。"""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from siriagent_skill.schemas import PARAMETER_SPECS


class AnalysisInput(BaseModel):
    # 不给可选项重复赋业务默认值。提交时 exclude_unset，让 normalize_request 统一处理。
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, json_schema_extra={'examples': [
        {'operation': 'coverage_only', 'city_adcode': '330100', 'start_utc': '2026-08-30T00:00:00Z'},
        {'operation': 'task2', 'city_adcode': '330100', 'start_utc': '2026-08-30T00:00:00Z', 'mrt_target_minutes': 180},
    ]})
    operation: str
    city_adcode: str
    start_utc: datetime | None = None
    analysis_horizon_h: float | None = None
    tle_source: str | None = None
    satellite_ids: list[int] | None = Field(default=None, max_length=500)
    catalog_sensor_type: str | None = None
    max_resolution_m: float | None = None
    profile_slug: str | None = None
    sensor_profile: dict[str, Any] | None = None
    satellite_profile_overrides: list[dict[str, Any]] | None = Field(default=None, max_length=500)
    grid_size_km: float | None = None
    detection_step_s: int | None = Field(default=None, strict=True)
    decision_step_s: int | None = Field(default=None, strict=True)
    min_simultaneous: int | None = Field(default=None, strict=True)
    method: str | None = None
    selection_time_limit_s: int | None = Field(default=None, strict=True)
    temporal_coverage_target_pct: float | None = None
    mrt_target_minutes: float | None = None
    area_target_pct: float | None = None
    allow_area_fallback: bool | None = Field(default=None, strict=True)
    fallback_area_target_pct: float | None = None
    n_budget: int | None = Field(default=None, strict=True)
    exact_budget: bool | None = Field(default=None, strict=True)
    mrt_area_quantile_pct: float | None = None
    max_local_rounds: int | None = Field(default=None, strict=True)
    debug: bool | None = Field(default=None, strict=True)
    max_windows: int | None = Field(default=None, strict=True)
    max_weak_areas: int | None = Field(default=None, strict=True)

# OpenAPI 字段说明直接取原 Schema；高级对象的解释见 capabilities，不复制另一份业务规则。
for name, field in AnalysisInput.model_fields.items():
    field.description = PARAMETER_SPECS.get(name, {}).get('description', '')
AnalysisInput.model_rebuild(force=True)


class JobView(BaseModel):
    job_id: str
    status: Literal['queued', 'running', 'completed', 'failed']
    operation: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    error: dict[str, str] | None
    status_url: str
    result_url: str


class APIErrorDetail(BaseModel):
    model_config = ConfigDict(extra='allow')
    code: str
    message: str


class APIError(BaseModel):
    detail: APIErrorDetail


class AnalysisResult(BaseModel):
    """正式结果保留业务层字段。复杂求解细节允许向后兼容扩展。"""
    model_config = ConfigDict(extra='allow')
    status: Literal['completed']
    request_id: str
    operation: str
    feasible: bool
    optimality: str
    summary_cards: dict[str, Any]
    satellite_table: list[dict[str, Any]]
    city_pass_window_table: list[dict[str, Any]]
    weak_areas: list[dict[str, Any]]
    coverage_map: dict[str, Any]
    assumptions: list[str]
    warnings: list[str]
