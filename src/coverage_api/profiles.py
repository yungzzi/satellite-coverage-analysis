"""可展开的高级配置模型。只校验输入，不改写原引擎参数格式或运动规律。"""
import math
import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr, model_validator

Number = StrictInt | StrictFloat
Parameter = StrictStr | Number


class ProfileModel(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


def numbers(values):
    try:
        converted = [float(x) for x in values]
    except (ValueError, TypeError):
        raise ValueError('角度/四元数参数必须是数字') from None
    if not all(math.isfinite(x) for x in converted):
        raise ValueError('参数必须是有限数字')
    return converted


def rotation_params(method, params):
    method = (method or 'YPR').lower()
    lengths = {'ypr': 4, 'euler': 4, 'quaternion': 4, 'matrix': 6, 'azel': 2}
    if method not in lengths:
        raise ValueError('method 必须是 YPR、Euler、Quaternion、Matrix 或 azel（大小写兼容）')
    if params is None or len(params) != lengths[method]:
        raise ValueError(f'{method} 的 params 必须含 {lengths[method]} 项；YPR 示例 ["YPR",0,0,10]')
    if method in {'ypr', 'euler'}:
        pattern = r'[YPR]{3}' if method == 'ypr' else r'[123]{3}'
        if not re.fullmatch(pattern, str(params[0]).upper()):
            raise ValueError('YPR 顺序使用 Y/P/R 三字符，Euler 顺序使用 1/2/3 三字符')
        numbers(params[1:])
    else:
        numbers(params)


class PointingConfig(ProfileModel):
    """传感器指向规律。字段默认 null 仅表示未给出；实际默认值见各字段说明。"""
    type: Literal['Fixed', 'Targeted', 'Spinning', 'GrazingAlt'] | None = Field(
        default=None, description='指向模式。Fixed 固定侧偏；Targeted 跟踪区域形心；Spinning 扫描；GrazingAlt 掠射。未指定 type 时沿体轴 +Z。')
    method: str | None = Field(default=None, description='仅 Fixed：角度表示方法，省略按 YPR。支持 YPR/Euler/Quaternion/Matrix/azel，大小写兼容。Matrix 六元素口径待校准，优先使用 YPR。',
                               json_schema_extra={'examples': ['YPR', 'azel']})
    params: list[Parameter] | None = Field(default=None, max_length=6, description=(
        'Fixed 必填。YPR：[顺序,yaw,pitch,roll]，如 ["YPR",0,0,10]（°）；Euler：[顺序,角1,角2,角3]，如 ["123",0,0,10]（°）；'
        'Quaternion：[qx,qy,qz,qs]，调用者需提供单位四元数；azel：[azimuth,elevation]（°），[0,-90] 为体 +Z；'
        'Matrix：[d11,d21,d22,d31,d32,d33]，六元素重建后正交化，待校准。'))
    azimuth: Number | None = Field(default=None, description='Spinning/GrazingAlt：体坐标系方位角，单位 °。省略时 Spinning=0，GrazingAlt=90。')
    elevation: Number | None = Field(default=None, description='仅 Spinning：旋转轴仰角（°），引擎默认 -90，指体 +Z；profile=0 时即天底。')
    cone_angle: Number | None = Field(default=None, ge=0, le=180, description='仅 Spinning：视轴与扫描轴夹角（°），引擎默认 30；不是传感器视场半角。')
    rate: Number | None = Field(default=None, description='仅 Spinning：角速度 rad/s（不是 °/s）。引擎默认 1 rad/s；0.001745329252≈0.1°/s。可为负，表示反向；0 表示停转。')
    offset: Number | None = Field(default=None, description='仅 Spinning：分析起点的扫描相位偏置（°），默认 0。往复模式为扫程相位，不等同于实际初始方位。')
    scan: Literal['Continuous', 'Unidirectional', 'Bidirectional'] | None = Field(default=None, description='仅 Spinning：Continuous 连续转动（默认）；Unidirectional 到边界后停留；Bidirectional 在起止角间往复。')
    start_angle: Number | None = Field(default=None, description='Spinning 有界扫描起始角（°），默认 0。与 stop_angle 配合；Continuous 模式不使用此范围。')
    stop_angle: Number | None = Field(default=None, description='Spinning 有界扫描结束角（°），默认 360，须大于 start_angle。')
    altitude: Number | None = Field(default=None, description='仅 GrazingAlt：视线相对球形地球的掠射高度，单位 m（不是 km），默认 10000；不是卫星轨道高度。')
    target_mode: Literal[0, 1] | None = Field(default=None, description='仅 Targeted：0/未提供=直接跟踪自动形心；1=对形心方向额外施加固定旋转偏置。禁止手动传 target/target_lat/target_lon。')
    offset_method: str | None = Field(default=None, description='Targeted 且 target_mode=1 时的偏置表示法，默认 YPR。')
    offset_params: list[Parameter] | None = Field(default=None, max_length=6, description='Targeted 且 target_mode=1 时必填；顺序与 Fixed.params 相同。')

    @model_validator(mode='after')
    def validate_mode(self):
        if self.type == 'Fixed':
            rotation_params(self.method, self.params)
        elif self.type == 'Targeted' and self.target_mode == 1:
            rotation_params(self.offset_method, self.offset_params)
        elif self.type == 'Spinning' and self.scan in {'Unidirectional', 'Bidirectional'}:
            start = self.start_angle if self.start_angle is not None else 0
            stop = self.stop_angle if self.stop_angle is not None else 360
            if stop <= start:
                raise ValueError('有界扫描 stop_angle 必须大于 start_angle')
        return self


class AttitudeOffset(ProfileModel):
    """参考体轴偏置，可用角度对象或三分量四元数。"""
    type: str | None = Field(default=None, description='Quaternion（默认）/YPR/Euler，大小写兼容。')
    seq: str | None = Field(default=None, description='YPR 旋转顺序默认 YPR；Euler 默认 123。Quaternion 不使用。')
    params: list[Number] | None = Field(default=None, min_length=3, max_length=3, description='YPR=[yaw,pitch,roll]（°）；Euler=[角1,角2,角3]（°）；Quaternion=[qx,qy,qz]（无量纲），标量由引擎补出。')
    offset: list[Number] | None = Field(default=None, min_length=3, max_length=3, description='params 的历史兼容别名，优先使用 params。')

    @model_validator(mode='after')
    def validate_offset(self):
        kind = (self.type or 'Quaternion').lower()
        if kind not in {'quaternion', 'ypr', 'euler'}:
            raise ValueError('姿态偏置 type 必须是 Quaternion、YPR 或 Euler')
        if kind in {'ypr', 'euler'}:
            seq = self.seq or ('YPR' if kind == 'ypr' else '123')
            pattern = r'[YPR]{3}' if kind == 'ypr' else r'[123]{3}'
            if not re.fullmatch(pattern, seq.upper()):
                raise ValueError('姿态偏置旋转顺序不合法')
        if self.model_fields_set and self.params is None and self.offset is None:
            raise ValueError('姿态偏置对象需要提供三个 params 数值')
        return self


class AttitudeConfig(ProfileModel):
    profile: int | None = Field(default=None, ge=0, le=4, description=(
        '参考体轴族，省略时引擎默认 0。0=天底+地固速度水平投影；1=天底+惯性速度水平投影；'
        '2=惯性速度定向+天底约束；3=地固速度定向+天底约束；4=太阳对齐+天底约束。不是姿态优化开关。'))
    offset: list[Number] | AttitudeOffset | None = Field(default=None, description=(
        '体轴姿态偏置。数组必须为 [qx,qy,qz] 三项，隐含非负标量；建议平方和≤1，超过时现有引擎会归一化。'
        '也可传展开的 YPR/Euler/Quaternion 对象。与 pointing 偏置叠加，不要无意重复加角度。'))

    @model_validator(mode='after')
    def validate_vector(self):
        if isinstance(self.offset, list) and len(self.offset) != 3:
            raise ValueError('attitude.offset 数组应为三项 [qx,qy,qz]，不是四项四元数')
        return self


ConstraintName = Literal[
    'AzimuthAngle', 'ElevationAngle', 'Range', 'AzimuthRate', 'ElevationRate', 'RangeRate',
    'AngularRate', 'Altitude', 'PropagationDelay', 'LineOfSight', 'fieldofview', 'TerrainMask',
    'SunElevationAngle', 'SunGroundElevAngle', 'LunarElevationAngle', 'LOSSunExclusion',
    'LOSLunarExclusion', 'BSSunExclusion', 'BSLunarExclusion',
]


class ObservationConstraint(ProfileModel):
    name: ConstraintName = Field(description=(
        '约束名，区分大小写。角度/避让角单位 °；角速度 °/s；Range/Altitude 为 km；RangeRate 为 km/s；'
        'PropagationDelay 为 s。SunGroundElevAngle 才是目标点太阳高度；SunElevationAngle 为卫星处。'
        'Altitude 是目标点相对球面的高度。TerrainMask 为占位，未使用 DEM；LineOfSight/fieldofview 不取消几何基线。'))
    params: str = Field(default='', description='范围字符串，如 "Min 10 Max 80" 或 "Max 2000"；可追加 ExcludeIntervals 取反。最小值在前、最大值在后。LineOfSight/fieldofview 可用 ON/OFF，但地球遮挡和视场底线仍存在。', examples=['Min 10 Max 80', 'Min 10', 'Max 2000', 'Min 0 Max 5 ExcludeIntervals'])

    @model_validator(mode='after')
    def validate_range(self):
        text = self.params.strip()
        if '\n' in text or '\r' in text:
            raise ValueError('约束 params 必须是单行字符串')
        if self.name in {'LineOfSight', 'fieldofview', 'TerrainMask'} and text.upper() in {'', 'ON', 'OFF'}:
            return self
        number = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
        pattern = rf'(?:Min\s+({number}))?\s*(?:Max\s+({number}))?\s*(?:ExcludeIntervals)?'
        matched = re.fullmatch(pattern, text)
        if not matched:
            raise ValueError('约束 params 使用 "Min 数值 Max 数值 [ExcludeIntervals]" 格式')
        limits = [float(v) if v is not None else None for v in matched.groups()]
        if any(v is not None and not math.isfinite(v) for v in limits):
            raise ValueError('约束边界必须是有限数字')
        if None not in limits and limits[0] > limits[1]:
            raise ValueError('约束 Min 不能大于 Max')
        return self


class SensorProfile(ProfileModel):
    """只覆盖明确给出的 Profile 字段；pointing/attitude/constraints 对象是整体替换。"""
    sensor_type: Literal['SimpleCone', 'Rectangular', 'Conical', 'HalfPower', 'SAR'] | None = Field(default=None, description='几何模型。未给出时继承 profile_slug 基础配置。注意：默认目录预设下传入全局 sensor_profile 后，不再自动派生逐星幅宽；统一高级配置应明确几何参数，保留目录几何只改指向应使用逐星覆盖。')
    sensor_params: list[Number] | None = Field(default=None, max_length=4, description=(
        '数值数组的顺序随 sensor_type 变化：SimpleCone=[视场半角°]；Rectangular=[跨轨半角°,沿轨半角°]；'
        'Conical=[内半角°,外半角°,起始clock角°,结束clock角°]；HalfPower=[频率GHz,口径m]；'
        'SAR=[目标点最小仰角°,最大仰角°,前向排除角°,后向排除角°]。SAR 不是直接输入入射角；i 与仰角 ε 满足 i=90°−ε。'
        '矩形存在 swath_km 时，跨轨数组值不生效，使用幅宽换算。缺省继承/采用引擎默认，不表示真实载荷参数。'))
    swath_km: float | None = Field(default=None, gt=0, description='矩形名义天底跨轨全宽，km（不是半宽）。逐时刻按轨道高度换算半角；侧偏后地面宽度不保证仍等于此值。优先于矩形 sensor_params[0]。')
    along_track_half_angle_deg: float | None = Field(default=None, gt=0, lt=90, description='矩形沿轨半角（°），优先于 sensor_params[1]。目录预设中的 60°是场景假设。')
    min_elevation_deg: float | None = Field(default=None, ge=-90, le=90, description='目标点看卫星的最小仰角（°）。SAR 应使用 sensor_params 仰角带或 ElevationAngle 约束；其几何分支不叠加本字段门槛。')
    pointing: PointingConfig | None = Field(default=None, description='展开查看 Fixed/Targeted/Spinning/GrazingAlt 的字段、顺序及单位。此对象整体替换继承值；显式 null 表示清除指向配置。')
    attitude: AttitudeConfig | None = Field(default=None, description='参考体轴及额外姿态偏置。与传感器相对体轴的 pointing 共同决定视轴，不执行真实姿态机动排程。对象整体替换。')
    constraints: list[ObservationConstraint] | None = Field(default=None, description='附加观测条件，默认不增加额外条件；[] 清除继承约束。未知名字拒绝，不把 TerrainMask 宣称为已实现地形遮挡。')


class SatelliteProfileOverride(SensorProfile):
    norad_id: int = Field(description='被覆盖卫星的 NORAD 编号，必须在本次候选池中；不会因为填写此字段自动将卫星加入候选池。', validation_alias=AliasChoices('norad_id', 'norad'))
