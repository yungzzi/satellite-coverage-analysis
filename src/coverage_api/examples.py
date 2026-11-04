"""Swagger 命名示例，测试直接执行同一份数据，不另维护一套示例。"""
from copy import deepcopy

BASE = {
    'operation': 'coverage_only', 'city_adcode': '330100',
    'start_utc': '2026-08-30T00:00:00Z', 'analysis_horizon_h': 8,
    'grid_size_km': 5, 'detection_step_s': 10, 'max_windows': 100,
}
OPTICAL = {
    'sensor_type': 'Rectangular', 'swath_km': 20, 'along_track_half_angle_deg': 60,
    'min_elevation_deg': 0, 'attitude': {'profile': 0},
    'pointing': {'type': 'Fixed', 'method': 'YPR', 'params': ['YPR', 0, 0, 0]}, 'constraints': [],
}


def example(summary, description, **changes):
    return {'summary': summary, 'description': description, 'value': deepcopy(BASE | changes)}


def advanced(summary, description, pointing=None, **changes):
    profile = deepcopy(OPTICAL)
    if pointing is not None:
        profile['pointing'] = pointing
    profile.update(changes)
    return example(summary, description, satellite_ids=[41026], sensor_profile=profile)


REQUEST_EXAMPLES = {
    'coverage': example('01 杭州覆盖分析（不选星）', '历史冻结 TLE，默认 100 星目录、5 km 网格；冷计算需等待。不是今天的预测。'),
    'task1': example('02 任务①：最少卫星达到时间覆盖率', '每格时间覆盖率≥5%，至少90%面积达标。可行性由实际窗口决定。',
        operation='task1', temporal_coverage_target_pct=5, area_target_pct=90, allow_area_fallback=False, method='greedy'),
    'task2': example('03 任务②：最少卫星满足最长等待', '至少90%面积连续不可见时间≤180分钟。',
        operation='task2', mrt_target_minutes=180, area_target_pct=90, allow_area_fallback=False, method='greedy'),
    'task3': example('04 任务③：10星扩大达标面积', '最多10颗，每格时间覆盖率门槛5%，最大化达标面积。',
        operation='task3', n_budget=10, temporal_coverage_target_pct=5, method='greedy'),
    'task4': example('05 任务④：10星缩短大部分区域最长等待', '最多10颗，缩短至少90%面积的最长等待。示例用贪心，可改 ilp。',
        operation='task4', n_budget=10, area_target_pct=90, method='greedy'),
    'task5': example('06 任务⑤：10星缩短全市平均等待', '最多10颗，按真实面积加权；本任务 method 只能 greedy。',
        operation='task5', n_budget=10, method='greedy'),
    'fixed': advanced('07 固定侧偏 +10°（单星）', 'YAOGAN-28；20 km 名义天底幅宽，体轴参考族0，roll方向+10°。配置为演示假设。',
        pointing={'type': 'Fixed', 'method': 'YPR', 'params': ['YPR', 0, 0, 10]}),
    'targeted': advanced('08 跟踪杭州网格形心（单星）', '由后端生成面积加权形心，不允许手动填写 target_lat/target_lon。',
        pointing={'type': 'Targeted'}),
    'spinning': advanced('09 连续旋转扫描（单星）', '绕天底轴，视轴离轴10°，0.1°/s。rate使用rad/s，其他角度为度。不是厂家载荷参数。',
        pointing={'type': 'Spinning', 'azimuth': 0, 'elevation': -90, 'cone_angle': 10,
                  'rate': 0.001745329252, 'offset': 0, 'scan': 'Continuous'}),
    'bidirectional': advanced('10 往复扫描（单星）', '相位在-60°至60°区间往复；速率0.1°/s。零offset表示从start_angle开始。',
        pointing={'type': 'Spinning', 'azimuth': 0, 'elevation': -90, 'cone_angle': 10,
                  'rate': 0.001745329252, 'offset': 0, 'scan': 'Bidirectional', 'start_angle': -60, 'stop_angle': 60}),
    'grazing': advanced('11 掠射指向（单星）', '方位90°、掠射高度10000 m；可能无地面成像窗口，完成不等于有可用影像。',
        pointing={'type': 'GrazingAlt', 'azimuth': 90, 'altitude': 10000}),
    'constraints': advanced('12 增加目标点白天与仰角条件', '要求目标点太阳高度≥10°、目标点看卫星仰角≥10°。不包含云量/DEM预测。',
        constraints=[{'name': 'SunGroundElevAngle', 'params': 'Min 10'}, {'name': 'ElevationAngle', 'params': 'Min 10'}]),
    'per_satellite': example('13 A/B逐星配置（2星示例）', 'A星41026为20 km/0°，B星43081为30 km/+10°。逐星pointing对象整体替换，不做深合并。',
        satellite_ids=[41026, 43081], sensor_profile=OPTICAL,
        satellite_profile_overrides=[{'norad_id': 43081, 'swath_km': 30,
            'pointing': {'type': 'Fixed', 'method': 'YPR', 'params': ['YPR', 0, 0, 10]}}]),
}
