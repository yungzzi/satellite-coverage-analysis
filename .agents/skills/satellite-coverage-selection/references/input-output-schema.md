# 输入与输出约定

需要向用户展示完整参数、解释某项任务，或平台要构造 `inputs` 时读取本文件。运行
`scripts/run_skill.py --describe --pretty` 得到的 JSON 是机器可读的唯一参数真值；本文件只作人工速查。

## operation

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
