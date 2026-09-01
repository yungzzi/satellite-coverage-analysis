# 输入与输出约定

需要向用户展示完整参数、解释某项任务，或平台要构造 `inputs` 时读取本文件。运行
`scripts/run_skill.py --describe --pretty` 得到的 JSON 是机器可读的唯一参数真值；本文件只作人工速查。

## operation

省略 `operation` 时默认进入 `task_1_min_satellites_temporal`，即覆盖分析后按时间覆盖率目标选出
尽量少的卫星；此时仍必须提供 `temporal_coverage_target_pct`。只有用户明确要求不选星时才传
`coverage_only`。

| operation | 含义 | 必填任务参数 |
| --- | --- | --- |
| `coverage_only` | 使用全部候选卫星评估区域覆盖 | 无 |
| `task_1_min_satellites_temporal` | 时间覆盖率达标的最少卫星 | `temporal_coverage_target_pct` |
| `task_2_min_satellites_mrt` | MRT 达标的最少卫星 | `mrt_target_minutes` |
| `task_3_max_qualified_area` | 给定 N 最大化达标面积 | `n_budget`、`temporal_coverage_target_pct` |
| `task_4_min_area_quantile_mrt` | 给定 N 最小化面积分位 MRT | `n_budget` |
| `task_5_min_area_weighted_art` | 给定 N 最小化面积加权 ART | `n_budget` |

所有计算都要求 `city_adcode`。当前默认：8 小时、5 km 网格、10 秒检测、60 秒决策、
`K=1`；首版允许 `K=1/2/3`。任务①②默认先要求 100% 面积，失败后显式尝试 90%。

## 普通预设与高级 Profile

普通请求可省略 `profile_slug`，默认使用 `siriagent_catalog_swath_v1` 和配套的
`siriagent_110_snapshot`。平台目录默认按 `catalog_sensor_type="可见光"`、
`max_resolution_m=1.0` 筛出 88 颗候选；分辨率只筛选，非 SAR 的逐星幅宽生成矩形跨轨范围。

若要修改目录筛选，可传：

```json
{
  "catalog_sensor_type": "可见光",
  "max_resolution_m": 1.0
}
```

3 颗 SAR 只有幅宽、没有原生入射角带，默认不会退回圆锥；SAR 请求缺参会返回 `invalid_input`。
只有用户明确提出“使用
SAR 入射角带、矩形/锥形视场、侧摆/旋转姿态、指向或约束”等高级要求时，才传下面两个字段：

```json
{
  "profile_slug": "siriagent_catalog_swath_v1",
  "sensor_profile": {
    "sensor_type": "SAR",
    "sensor_params": [12, 70, 30, 30],
    "min_elevation_deg": 10,
    "pointing": {"type": "Fixed", "method": "azel", "params": [90, -90]},
    "attitude": {"profile": 0},
    "constraints": []
  },
  "satellite_profile_overrides": [
    {"norad_id": 40961, "sensor_type": "Rectangular", "sensor_params": [45, 45]}
  ]
}
```

`sensor_profile` 是全局覆盖配置；`satellite_profile_overrides` 按 NORAD 覆盖其中字段。
支持的传感器类型为 `SimpleCone`、`Rectangular`、`Conical`、`HalfPower`、`SAR`；支持的区域
统一指向为 `Fixed`、`GrazingAlt`、`Spinning`；区域模式还支持 `Targeted`，但它自动把目标设为
所有保留网格代表点的面积加权形心，表示整座区域共用一个跟踪目标，不是每个网格点各自转轴。
姿态 `profile` 使用项目已有的 0～4 定义；
约束对象的具体字段沿用 `src/sensor_geometry.py` 的既有实现。

形心口径不是直接平均经纬度。对第 `g` 个网格代表点在冻结等面积投影中的坐标
`(x_g, y_g)` 和裁剪面积 `a_g`，先计算：

```text
x_c = Σ(a_g · x_g) / Σ(a_g),    y_c = Σ(a_g · y_g) / Σ(a_g)
```

再把 `(x_c, y_c)` 反投影到 WGS84，作为 Targeted 的唯一地面跟踪目标。这样与区域面积统计
使用同一套网格和权重，避免经纬度单位不等距造成偏差。

高级参数必须有明确物理来源，Skill 不会把缺少的载荷数值自动猜成“真实卫星参数”。结果中的
`profile.mode`、`profile.effective_config` 和 `profile.satellite_profile_overrides` 用来回显本次
实际计算配置；`mode=preset` 表示完全使用预设，`mode=advanced` 表示至少有一个高级字段生效。
平台目录模式还会在 `input_summary.catalog_mapping`、`profile.catalog_mapping` 和
`reproducibility.catalog_sha256` 回显筛选条件、被阻止卫星和目录版本。

## 最小请求示例

可直接运行的快速样例见同目录 `example-coverage-quick.json` 和
`example-task2-quick.json`。下面是 K=2 的简化参数片段：

```json
{
  "operation": "task_2_min_satellites_mrt",
  "city_adcode": "330100",
  "start_utc": "2026-08-08T00:00:00Z",
  "analysis_horizon_h": 8,
  "mrt_target_minutes": 120,
  "min_simultaneous": 2,
  "method": "both"
}
```

## 状态

| status | Agent 行为 |
| --- | --- |
| `ok` | `describe` 成功 |
| `needs_input` | 按 `missing_fields` 继续询问 |
| `invalid_input` | 解释具体字段错误并修正 |
| `completed` | 展示真实计算结果 |
| `execution_error` | 报告执行错误，不能编造结果 |

`completed` 中重点读取：`feasible`、`optimality`、`summary_cards`、`selection`、
`satellite_table`、`city_pass_window_table`、`weak_areas`、`assumptions` 和 `warnings`。
普通聊天保持 `debug=false`；只有调试/验收才打开逐网格 GeoJSON 和窗口明细。

选星结果中的 `optimality` 还必须区分：

| optimality | 含义 |
| --- | --- |
| `proven_optimal` | 最终窗口业务目标已证明最优，且原始窗口复核通过 |
| `proven_discrete_optimal` | 只证明决策时间片或 MRT 桶模型最优；窗口指标已回算，但不声称连续目标全局最优 |
| `feasible_heuristic` | 找到并复核了可行近似解，未证明全局最优 |
| `feasible_time_limit` | 达到求解时限，保留当前可行解 |
| `verification_failed` | 离散候选没有通过原始窗口复核，不能作为可行推荐 |

任务③在 `method="both"` 时会按原始窗口的“达标面积优先、城市平均时间覆盖率次优先”比较贪心
与 ILP，推荐窗口级得分更好的方案。不要只因某个 ILP 子结果带 `proven_discrete_optimal` 就覆盖
最终推荐。
