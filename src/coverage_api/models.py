"""HTTP 类型与文档；业务默认值和条件必填规则仍由原 Schema 决定。"""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from siriagent_skill.schemas import OPERATION_ALIASES, PARAMETER_SPECS
from .examples import REQUEST_EXAMPLES
from .profiles import SensorProfile, SatelliteProfileOverride


class AnalysisInput(BaseModel):
    # 不给可选项重复赋业务默认值。提交时 exclude_unset，让 normalize_request 统一处理。
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False, json_schema_extra={'examples': [
        REQUEST_EXAMPLES['fixed']['value'], REQUEST_EXAMPLES['per_satellite']['value'],
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
    sensor_profile: SensorProfile | None = Field(default=None, description='展开 SensorProfile 查看完整字段。pointing/attitude 为整体替换，显式 null 可清除；不是深合并。')
    satellite_profile_overrides: list[SatelliteProfileOverride] | None = Field(default=None, max_length=500, description='展开 SatelliteProfileOverride；各项包含 norad_id 与需要覆盖的 Profile 字段。')
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

    def engine_payload(self):
        # 原接口忽略顶层 null，但保留嵌套字典里的 null（用于清除继承的指向/姿态）。
        # exclude_none=True 会递归删掉新嵌套模型的显式 null，故这里仅过滤最外层。
        data = self.model_dump(mode='json', exclude_unset=True)
        return {key: value for key, value in data.items() if value is not None}

# 默认值/条件必填/范围只从原 Schema 生成文案，不重新指定一套业务默认值。
for name, field in AnalysisInput.model_fields.items():
    spec = PARAMETER_SPECS.get(name, {})
    parts = [spec.get('description', ''), field.description or '']
    if 'default' in spec:
        parts.append(f"省略时：{spec['default']}。")
    if 'range' in spec:
        parts.append(f"取值范围：{spec['range']}。")
    if isinstance(spec.get('required'), str):
        parts.append(f"条件必填：{spec['required']}。")
    field.description = ' '.join(part for part in parts if part)
    if 'allowed' in spec:
        allowed = list(OPERATION_ALIASES) if name == 'operation' else list(spec['allowed'])
        field.json_schema_extra = {'enum': allowed + ([None] if field.default is None else [])}
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
