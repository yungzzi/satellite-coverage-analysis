"""HTTP 边界、队列、恢复，以及真实引擎与旧入口一致性测试。"""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

from fastapi.testclient import TestClient
import pytest
from coverage_api.app import create_app, canonical_payload
from coverage_api.config import Settings
from coverage_api.models import AnalysisInput
from coverage_api.service import InstanceLock, JobService, kill_tree
from coverage_api.store import JobStore, QueueFull
from siriagent_skill.runner import run_skill

ROOT = Path(__file__).resolve().parents[1]
QUICK = json.loads((ROOT / 'integrations/siriagent/examples/coverage_quick.json').read_text(encoding='utf-8'))


class QuietService:
    """仅在 HTTP 边界单元测试中使用，便于确定性检查排队状态。"""
    def __init__(self, settings):
        self.store = JobStore(settings.state_dir)
    def start(self):
        pass
    def close(self):
        pass
    def ready(self):
        return True


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('coverage_api.app.JobService', QuietService)
    with TestClient(create_app(Settings(project_root=ROOT, state_dir=tmp_path, max_pending=1))) as client:
        yield client


def test_health_and_openapi(client):
    assert client.get('/healthz').json() == {'status': 'ok'}
    assert client.get('/readyz').status_code == 200
    meta = client.get('/api/v1/capabilities').json()
    assert len(meta['tasks']) == 6
    assert 'tle_path' not in meta['parameters']
    assert meta['parameters']['max_resolution_m']['default'] == 10
    schema = client.get('/openapi.json').json()['components']['schemas']['AnalysisInput']
    assert schema['additionalProperties'] is False
    assert 'sensor_profile' in schema['properties']
    assert schema['properties']['grid_size_km']['description']
    error_schema = client.get('/openapi.json').json()['paths']['/api/v1/jobs']['post']['responses']['422']['content']['application/json']['schema']
    assert error_schema['$ref'].endswith('/APIError')


@pytest.mark.parametrize('payload', [
    {}, {'operation': 'task1', 'city_adcode': '330100'},
    {'operation': 'task2', 'city_adcode': '330100'},
    {'operation': 'task3', 'city_adcode': '330100'},
    {'operation': 'task4', 'city_adcode': '330100'},
    {'operation': 'task5', 'city_adcode': '330100'},
    {**QUICK, 'operation': 'oops'}, {**QUICK, 'city_adcode': '../test'},
    {**QUICK, 'city_adcode': '../platform/siriagent_110_catalog'},
    {**QUICK, 'tle_path': 'secret'}, {**QUICK, 'grid_size_km': 0.01},
    {**QUICK, 'analysis_horizon_h': 25}, {**QUICK, 'detection_step_s': 1},
    {**QUICK, 'max_resolution_m': -1}, {**QUICK, 'min_simultaneous': 4},
    {**QUICK, 'detection_step_s': 10.5},
    {**QUICK, 'sensor_profile': {'sensor_type': 'wrong'}},
    {**QUICK, 'sensor_profile': {'pointing': {'type': 'Targeted', 'target_lat': 30}}},
])
def test_validation(client, payload):
    assert client.post('/api/v1/jobs', json=payload).status_code == 422


def test_queue_status_and_missing_result(client):
    response = client.post('/api/v1/jobs', json=QUICK)
    assert response.status_code == 202
    row = response.json()
    assert response.headers['location'] == row['status_url']
    assert row['status'] == 'queued'
    assert 'payload' not in row
    assert client.get(row['status_url']).status_code == 200
    assert client.get(row['result_url']).status_code == 409
    full = client.post('/api/v1/jobs', json=QUICK)
    assert full.status_code == 429 and full.headers['retry-after'] == '5'
    assert client.get(f'/api/v1/jobs/{uuid.uuid4()}').status_code == 404
    assert client.get('/api/v1/jobs/not-a-uuid/result').status_code == 422


def test_auth_cors(tmp_path, monkeypatch):
    monkeypatch.setattr('coverage_api.app.JobService', QuietService)
    settings = Settings(project_root=ROOT, state_dir=tmp_path, api_token='test-key', cors_origins=('http://localhost:5173',))
    with TestClient(create_app(settings)) as client:
        assert client.get('/healthz').status_code == 200
        assert client.get('/api/v1/capabilities').status_code == 401
        assert client.get('/api/v1/capabilities', headers={'X-API-Key': 'test-key'}).status_code == 200
        cors = client.options('/api/v1/jobs', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST'})
        assert cors.headers['access-control-allow-origin'] == 'http://localhost:5173'
        denied = client.options('/api/v1/jobs', headers={'Origin': 'http://untrusted', 'Access-Control-Request-Method': 'POST'})
        assert 'access-control-allow-origin' not in denied.headers


def test_start_time_is_frozen(tmp_path):
    from datetime import datetime
    settings = Settings(project_root=ROOT, state_dir=tmp_path)
    payload = canonical_payload(AnalysisInput(operation='coverage_only', city_adcode='330100'), settings)
    fixed = datetime.fromisoformat(payload['start_utc'])
    assert fixed.second == 0 and fixed.microsecond == 0
    converted = canonical_payload(AnalysisInput(**{**QUICK, 'start_utc': '2026-08-08T08:00:00+08:00'}), settings)
    assert converted['start_utc'] == '2026-08-08T00:00:00+00:00'


@pytest.mark.parametrize('limit', ['max_grid_cells', 'max_grid_time_cells'])
def test_matrix_resource_limit(tmp_path, monkeypatch, limit):
    monkeypatch.setattr('coverage_api.app.JobService', QuietService)
    settings = replace(Settings(project_root=ROOT, state_dir=tmp_path), **{limit: 1})
    with TestClient(create_app(settings)) as client:
        response = client.post('/api/v1/jobs', json=QUICK)
        assert response.status_code == 422
        assert response.json()['detail']['code'] == 'resource_limit'


def test_database_readiness_failure(client, monkeypatch):
    import sqlite3
    def broken_connection():
        raise sqlite3.OperationalError('test failure')
    monkeypatch.setattr(client.app.state.service.store, 'connection', broken_connection)
    assert client.get('/readyz').status_code == 503
    assert client.get('/healthz').status_code == 200


def test_atomic_admission_recovery_and_lock(tmp_path):
    store = JobStore(tmp_path)
    def submit(_):
        try:
            return store.submit(QUICK, 2)
        except QueueFull:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = [r for r in pool.map(submit, range(20)) if r]
    assert len(rows) == 2
    claimed = store.claim()
    assert claimed
    store.interrupt_running()
    assert store.get(claimed['job_id'])['error']['code'] == 'interrupted'
    assert store.claim()  # 未领取的排队任务没有丢失。
    with pytest.raises(ValueError):
        store.result_path('../../secret')
    lock = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path)
    finally:
        lock.close()


def wait_job(client, url, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = client.get(url).json()
        if row['status'] in ['completed', 'failed']:
            return row
        assert client.get('/healthz').status_code == 200
        time.sleep(0.1)
    pytest.fail('任务未在测试时限内结束')


def clean_result(result):
    result.pop('request_id', None)
    result.pop('timings_s', None)
    result['input_summary']['tle_path'] = Path(result['input_summary']['tle_path']).name
    # GeoJSON 的原生 Python 坐标为 tuple，HTTP JSON 中自然变成 list。
    return json.loads(json.dumps(result, allow_nan=False))


def test_body_size_and_finite_numbers(client):
    assert client.post('/api/v1/jobs', content=b' ' * 65537, headers={'Content-Type': 'application/json'}).status_code == 413
    bad = json.dumps({**QUICK, 'temporal_coverage_target_pct': float('nan')})
    assert client.post('/api/v1/jobs', content=bad, headers={'Content-Type': 'application/json'}).status_code == 422


def test_real_engine_all_tasks_advanced_and_restart(tmp_path):
    settings = Settings(project_root=ROOT, state_dir=tmp_path)
    samples = [QUICK]
    for operation in ['task1', 'task2', 'task3', 'task4', 'task5']:
        samples.append({**QUICK, 'operation': operation, 'method': 'greedy', 'n_budget': 2,
                        'temporal_coverage_target_pct': 1, 'mrt_target_minutes': 240})
    samples.append({**QUICK, 'satellite_ids': [40961], 'sensor_profile': {
        'sensor_type': 'Rectangular', 'sensor_params': [45, 45], 'attitude': {'profile': 0},
        'pointing': {'type': 'Targeted'}}, 'satellite_profile_overrides': [
            {'norad_id': 40961, 'swath_km': 30}]})
    samples.append({**QUICK, 'operation': 'task2', 'method': 'ilp', 'selection_time_limit_s': 2,
                    'mrt_target_minutes': 240})
    with TestClient(create_app(settings)) as client:
        assert client.get('/readyz').status_code == 200
        for sample in samples:
            expected = run_skill(sample, root=ROOT)
            assert expected['status'] == 'completed', expected
            response = client.post('/api/v1/jobs', json=sample)
            assert response.status_code == 202, response.text
            job = response.json()
            finished = wait_job(client, job['status_url'])
            assert finished['status'] == 'completed', finished
            result = client.get(job['result_url']).json()
            assert clean_result(result) == clean_result(expected)
        result_url = job['result_url']
    # 相同持久化目录重启后，已完成结果仍可查询。
    with TestClient(create_app(settings)) as client:
        assert client.get(result_url).status_code == 200


def test_worker_timeout_and_recovery(tmp_path):
    # 时限短于引擎冷导入，确定性触发监督器清理与状态落库。
    settings = Settings(project_root=ROOT, state_dir=tmp_path, job_timeout_s=0.001)
    with TestClient(create_app(settings)) as client:
        job = client.post('/api/v1/jobs', json=QUICK).json()
        finished = wait_job(client, job['status_url'], timeout=30)
        assert finished['status'] == 'failed'
        assert finished['error']['code'] == 'timeout'
        assert client.get(job['result_url']).status_code == 409
        assert client.get('/healthz').status_code == 200


def test_queued_restart_and_worker_crash(tmp_path):
    settings = Settings(project_root=ROOT, state_dir=tmp_path)
    queued = JobStore(tmp_path).submit(QUICK, 8)
    with TestClient(create_app(settings)) as client:
        row = wait_job(client, f"/api/v1/jobs/{queued['job_id']}")
        assert row['status'] == 'completed'
        service = client.app.state.service
        old_pid = service.process.pid
        service.process.kill()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if service.ready() and service.process.pid != old_pid:
                break
            time.sleep(0.1)
        assert service.ready() and service.process.pid != old_pid
        next_job = client.post('/api/v1/jobs', json=QUICK).json()
        assert wait_job(client, next_job['status_url'])['status'] == 'completed'


def test_owned_process_tree_cleanup():
    import os
    import psutil
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print(p.pid,flush=True); time.sleep(60)"
    process = subprocess.Popen([sys.executable, '-c', code], stdout=subprocess.PIPE, text=True,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        child_pid = int(process.stdout.readline().strip())
        child = psutil.Process(child_pid)
        kill_tree(process.pid)
        assert process.wait(timeout=5) != 0
        assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
    finally:
        if process.poll() is None:
            kill_tree(process.pid)
        process.stdout.close()
