"""Swagger 文档完整性、旧请求兼容和高级配置校验。"""
from dataclasses import asdict
import json
from pathlib import Path
import time

from fastapi.testclient import TestClient
import pytest
from coverage_api.app import create_app, canonical_payload
from coverage_api.config import Settings
from coverage_api.examples import REQUEST_EXAMPLES
from coverage_api.models import AnalysisInput
from coverage_api.store import JobStore
from siriagent_skill.schemas import normalize_request

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def docs_client(tmp_path, monkeypatch):
    class QuietService:
        def __init__(self, settings):
            self.store = JobStore(settings.state_dir)
        def start(self):
            pass
        def close(self):
            pass
        def ready(self):
            return True
    monkeypatch.setattr('coverage_api.app.JobService', QuietService)
    with TestClient(create_app(Settings(project_root=ROOT, state_dir=tmp_path, max_pending=30))) as client:
        yield client


def test_openapi_has_nested_models_and_named_examples(docs_client):
    schema = docs_client.get('/openapi.json').json()
    models = schema['components']['schemas']
    for name in ['SensorProfile', 'SatelliteProfileOverride', 'PointingConfig', 'AttitudeConfig', 'AttitudeOffset', 'ObservationConstraint']:
        assert name in models
        assert models[name]['additionalProperties'] is False
        assert all(field.get('description') for field in models[name]['properties'].values())
    assert 'rad/s' in models['PointingConfig']['properties']['rate']['description']
    assert '不是直接输入入射角' in models['SensorProfile']['properties']['sensor_params']['description']
    assert 'DEM' in models['ObservationConstraint']['properties']['name']['description']
    assert '整体替换' in models['SensorProfile']['properties']['pointing']['description']
    assert '省略时：8.0' in models['AnalysisInput']['properties']['analysis_horizon_h']['description']
    examples = schema['paths']['/api/v1/jobs']['post']['requestBody']['content']['application/json']['examples']
    assert set(examples) == set(REQUEST_EXAMPLES)
    assert len(examples) == 13
    assert '条件' in schema['info']['description']
    assert 'task5' in schema['info']['description']


@pytest.mark.parametrize('name', list(REQUEST_EXAMPLES))
def test_named_examples_are_valid_requests(docs_client, name):
    example = REQUEST_EXAMPLES[name]
    assert example['summary'] and example['description']
    response = docs_client.post('/api/v1/jobs', json=example['value'])
    assert response.status_code == 202, response.text


@pytest.mark.parametrize('profile', [
    {'pointing': {'type': 'Fixed', 'params': [0, 0, 10]}},
    {'pointing': {'type': 'Fixed', 'method': 'YPR', 'params': ['bad', 0, 0, 0]}},
    {'pointing': {'type': 'Fixed', 'method': 'YPR', 'params': ['YPR', 0, 'bad', 0]}},
    {'pointing': {'type': 'Fixed', 'method': 'Unknown', 'params': [0, 0]}},
    {'pointing': {'type': 'Spinning', 'rate': '0.1 degrees/s'}},
    {'pointing': {'type': 'Spinning', 'scan': 'back-and-forth'}},
    {'pointing': {'type': 'Spinning', 'scan': 'Bidirectional', 'start_angle': 60, 'stop_angle': -60}},
    {'pointing': {'type': 'Targeted', 'target_lat': 30}},
    {'pointing': {'type': 'Targeted', 'target_mode': 1}},
    {'attitude': {'offset': [0, 0, 0, 1]}},
    {'attitude': {'profile': 5}},
    {'attitude': {'offset': {'type': 'YPR', 'seq': 'BAD', 'params': [0, 0, 0]}}},
    {'constraints': [{'name': 'NotAConstraint', 'params': 'Min 1'}]},
    {'constraints': [{'name': 'Range', 'params': 'Min 100 Max 10'}]},
    {'constraints': [{'name': 'Range', 'params': 'Min 10\nMax 100'}]},
    {'constraints': [{'name': 'Range', 'params': 'Max infinity'}]},
    {'swath_km': 20, 'pointng': {'type': 'Targeted'}},
])
def test_bad_advanced_configuration_is_rejected_before_queue(docs_client, profile):
    response = docs_client.post('/api/v1/jobs', json={**REQUEST_EXAMPLES['fixed']['value'], 'sensor_profile': profile})
    assert response.status_code == 422, response.text
    assert response.json()['detail']['fields']


@pytest.mark.parametrize('case', ['baseline', 'swath', 'swing'])
def test_old_ppt_request_preserves_exact_normalized_payload(case, tmp_path):
    original = json.loads((ROOT / f'integrations/http/examples/hangzhou-{case}.json').read_text(encoding='utf-8-sig'))
    actual = canonical_payload(AnalysisInput.model_validate(original), Settings(project_root=ROOT, state_dir=tmp_path))
    expected = asdict(normalize_request(original))
    # 比较序列化文本，连整数/浮点表示都保持，避免无意改变 profile_sha256。
    assert json.dumps(asdict(normalize_request(actual)), sort_keys=True, default=str) == json.dumps(expected, sort_keys=True, default=str)


@pytest.mark.parametrize('profile', [
    {}, {'pointing': None, 'attitude': None}, {'pointing': {}},
    {'pointing': {'type': 'Spinning'}},
    {'attitude': {'offset': [0, 0, 0]}},
    {'attitude': {'offset': {'type': 'YPR', 'seq': 'YPR', 'params': [0, 0, 5]}}},
    {'constraints': []},
])
def test_partial_defaults_and_explicit_nested_null_are_preserved(profile):
    raw = {**REQUEST_EXAMPLES['fixed']['value'], 'sensor_profile': profile}
    assert AnalysisInput.model_validate(raw).engine_payload()['sensor_profile'] == profile


def test_legacy_norad_alias_and_rotation_formats():
    base = REQUEST_EXAMPLES['coverage']['value']
    raw = {**base, 'satellite_profile_overrides': [{'norad': 41026, 'swath_km': 20}]}
    assert AnalysisInput.model_validate(raw).engine_payload()['satellite_profile_overrides'][0]['norad_id'] == 41026
    for method, params in [('ypr', ['YPR', 0, 0, 10]), ('Euler', ['123', 0, 0, 10]),
                           ('Quaternion', [0, 0, 0, 1]), ('Matrix', [1, 0, 1, 0, 0, 1]), ('azel', [90, -90])]:
        profile = {'pointing': {'type': 'Fixed', 'method': method, 'params': params}}
        assert AnalysisInput.model_validate({**base, 'sensor_profile': profile}).engine_payload()['sensor_profile'] == profile


def test_advanced_named_examples_execute(tmp_path):
    """按命名示例原样执行完整8小时/5km参数，不只做模型校验。"""
    settings = Settings(project_root=ROOT, state_dir=tmp_path)
    with TestClient(create_app(settings)) as client:
        for name in ['fixed', 'targeted', 'spinning', 'bidirectional', 'grazing', 'constraints', 'per_satellite']:
            response = client.post('/api/v1/jobs', json=REQUEST_EXAMPLES[name]['value'])
            assert response.status_code == 202, response.text
            job = response.json()
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                status = client.get(job['status_url']).json()
                if status['status'] in ['completed', 'failed']:
                    break
                time.sleep(0.1)
            assert status['status'] == 'completed', (name, status)
            result = client.get(job['result_url']).json()
            assert result['profile']['mode'] == 'advanced'
            assert len(result['coverage_map']['geojson']['features']) == 786
            if name == 'targeted':
                assert result['profile']['targeting']['definition'] == 'area_weighted_representative_point_centroid'
