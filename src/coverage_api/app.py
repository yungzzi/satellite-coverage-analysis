"""HTTP 入口：uvicorn coverage_api.app:app --app-dir src --workers 1。"""
from contextlib import asynccontextmanager
from dataclasses import asdict
from functools import lru_cache
import hmac
import json
import math
import sqlite3
from typing import Annotated
from uuid import UUID

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from siriagent_skill.schemas import (
    SkillInputError, SkillNeedsInput, describe_capabilities, normalize_request, TLE_SOURCES, CITY_NAME_TO_CODE,
)
from .config import Settings
from .models import AnalysisInput, AnalysisResult, JobView, APIError
from .middleware import BodyLimitMiddleware
from .service import JobService
from .store import QueueFull
from .documentation import API_GUIDE
from .examples import REQUEST_EXAMPLES


def fail(status_code, code, message, **extra):
    raise HTTPException(status_code=status_code, detail={'code': code, 'message': message, **extra})


@lru_cache(maxsize=32)
def count_grid_cells(boundary_path: str, grid_size_km: float, modified_ns: int):
    # 只缓存数量，不在 API 进程保留大块几何或窗口数据；mtime 变化会使缓存失效。
    from region_grid import build_clipped_grid, load_city_boundary
    return len(build_clipped_grid(load_city_boundary(boundary_path), grid_size_m=grid_size_km * 1000).cells)


def canonical_payload(body: AnalysisInput, settings: Settings):
    """固定 start_utc，避免在队列等候后变成另一个分析时段。"""
    payload = body.engine_payload()
    try:
        json.dumps(payload, allow_nan=False)
        request = normalize_request(payload)
    except SkillNeedsInput as exc:
        fail(422, 'missing_fields', str(exc), **exc.details)
    except (SkillInputError, ValueError) as exc:
        fail(422, 'invalid_input', str(exc))
    root = settings.project_root
    if request.city_adcode not in set(CITY_NAME_TO_CODE.values()):
        fail(422, 'unknown_city', '没有该城市的冻结边界。')
    if not (root / 'data/geo' / f'{request.city_adcode}.json').is_file():
        fail(503, 'data_unavailable', '城市边界文件不可用。')
    if not (root / TLE_SOURCES[request.tle_source]).is_file():
        fail(503, 'data_unavailable', '指定轨道数据不可用。')
    if request.grid_size_km < settings.min_grid_km or request.analysis_horizon_h > settings.max_horizon_h or request.detection_step_s < settings.min_detection_step_s:
        fail(422, 'resource_limit', '请求超过本服务资源限制，请参考 capabilities 中的 service_limits。')
    boundary_path = root / 'data/geo' / f'{request.city_adcode}.json'
    cell_count = count_grid_cells(str(boundary_path), request.grid_size_km, boundary_path.stat().st_mtime_ns)
    slots = math.ceil(request.analysis_horizon_h * 3600 / request.decision_step_s)
    if cell_count > settings.max_grid_cells or cell_count * slots > settings.max_grid_time_cells:
        fail(422, 'resource_limit', '网格数或网格×时间片数量超过服务上限，请增大网格或决策步长。')
    # 原 Schema 的 _pct 未统一拒绝 NaN，HTTP 边界补充全量有限值检查。
    canonical = asdict(request)
    canonical.pop('tle_path')
    canonical['start_utc'] = request.start_utc.isoformat()
    try:
        json.dumps(canonical, allow_nan=False)
    except ValueError:
        fail(422, 'invalid_input', '不允许非有限数值。')
    return canonical


def create_app(settings: Settings | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        service = JobService(settings)
        service.start()
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title='卫星覆盖与选星计算 API', version='1.0.0', lifespan=lifespan,
                  responses={422: {'model': APIError, 'description': '参数类型、条件必填或服务资源限制校验失败'}},
                  description=API_GUIDE,
                  swagger_ui_parameters={'defaultModelsExpandDepth': 1, 'defaultModelExpandDepth': 2, 'displayRequestDuration': True})
    app.state.settings = settings
    app.add_middleware(BodyLimitMiddleware)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # 不回显整个输入；其中可能有 NaN 或很大的高级参数对象。
        fields = [{'field': '.'.join(map(str, item['loc'])), 'message': item['msg'], 'type': item['type']}
                  for item in exc.errors()]
        return JSONResponse({'detail': {'code': 'validation_error', 'message': '请求参数不合法。', 'fields': fields}}, status_code=422)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins),
                           allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'X-API-Key'])
    api_key = APIKeyHeader(name='X-API-Key', auto_error=False)

    def authorize(key: Annotated[str | None, Depends(api_key)]):
        if settings.api_token and (not key or not hmac.compare_digest(key.encode(), settings.api_token.encode())):
            fail(401, 'unauthorized', 'API Key 无效。')

    def view(row):
        return {k: row[k] for k in ['job_id', 'status', 'operation', 'created_at', 'started_at', 'finished_at', 'error']} | {
            'status_url': f"/api/v1/jobs/{row['job_id']}",
            'result_url': f"/api/v1/jobs/{row['job_id']}/result",
        }

    def get_job(job_id):
        row = app.state.service.store.get(str(job_id))
        if row is None:
            fail(404, 'not_found', '没有这个任务。')
        return row

    @app.get('/healthz', tags=['health'])
    def health():
        return {'status': 'ok'}

    @app.get('/readyz', tags=['health'])
    def readiness():
        import pulp
        files = [settings.project_root / x for x in TLE_SOURCES.values()]
        files += [settings.project_root / 'data/platform/siriagent_110_catalog.json',
                  settings.project_root / 'data/geo/metadata/zhejiang_boundaries.json']
        if not app.state.service.ready() or not all(x.is_file() for x in files) or not pulp.PULP_CBC_CMD().available():
            fail(503, 'not_ready', '计算进程、CBC 或内置数据尚未就绪。')
        try:
            with app.state.service.store.connection() as db:
                db.execute('SELECT job_id FROM jobs LIMIT 1')
        except sqlite3.Error:
            fail(503, 'not_ready', '任务数据库不可用。')
        return {'status': 'ready'}

    @app.get('/api/v1/capabilities', dependencies=[Depends(authorize)], tags=['analysis'])
    def capabilities(operation: str | None = Query(default=None)):
        try:
            described = describe_capabilities(settings.project_root, operation=operation)
        except SkillInputError as exc:
            fail(422, 'invalid_input', str(exc))
        # 不把旧 Skill 的执行方式和任意本地路径接口公开到 HTTP。
        described['parameters'].pop('tle_path', None)
        return {k: described[k] for k in ['tasks', 'parameters', 'available_cities', 'profiles', 'notes']} | {
            'execution_mode': 'http_jobs', 'tle_sources': list(TLE_SOURCES),
            'service_limits': {'max_pending': settings.max_pending, 'job_timeout_s': settings.job_timeout_s,
                               'min_grid_km': settings.min_grid_km, 'max_horizon_h': settings.max_horizon_h,
                               'min_detection_step_s': settings.min_detection_step_s,
                               'max_grid_cells': settings.max_grid_cells,
                               'max_grid_time_cells': settings.max_grid_time_cells, 'max_body_bytes': 65536},
        }

    @app.post('/api/v1/jobs', status_code=202, response_model=JobView, dependencies=[Depends(authorize)], tags=['analysis'],
              summary='提交覆盖/选星任务：在 Examples 下拉菜单选择完整配置',
              description='先选择命名示例，再 Try it out。展开下方 Schemas 查看高级字段说明。提交返回202和job_id，不是直接返回计算结果。')
    def submit(body: Annotated[AnalysisInput, Body(openapi_examples=REQUEST_EXAMPLES)], response: Response):
        payload = canonical_payload(body, settings)
        if not app.state.service.ready():
            fail(503, 'not_ready', '计算服务尚未就绪。')
        try:
            row = app.state.service.store.submit(payload, settings.max_pending)
        except QueueFull:
            raise HTTPException(429, detail={'code': 'queue_full', 'message': '队列已满，请稍后重试。'}, headers={'Retry-After': '5'})
        response.headers['Location'] = f"/api/v1/jobs/{row['job_id']}"
        return view(row)

    @app.get('/api/v1/jobs/{job_id}', response_model=JobView, dependencies=[Depends(authorize)], tags=['analysis'])
    def status(job_id: UUID):
        return view(get_job(job_id))

    @app.get('/api/v1/jobs/{job_id}/result', response_model=AnalysisResult, dependencies=[Depends(authorize)], tags=['analysis'])
    def result(job_id: UUID):
        row = get_job(job_id)
        if row['status'] != 'completed':
            fail(409, 'result_not_available', '任务未完成或失败，请先查询任务状态。', status=row['status'], error=row['error'])
        result_file = app.state.service.store.result_path(str(job_id))
        if not result_file.is_file():
            fail(500, 'result_missing', '结果文件丢失，请检查持久化存储。')
        # 结果可能含地图，直接传文件避免每次轮询都反复解析大 JSON。
        return FileResponse(result_file, media_type='application/json', headers={'Cache-Control': 'no-store'})

    return app


app = create_app()
