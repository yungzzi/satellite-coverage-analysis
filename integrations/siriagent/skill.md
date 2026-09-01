---
name: 卫星区域覆盖与选星
slug: satellite_coverage_selection
description: >-
  当用户需要分析浙江城市未来一段时间的卫星覆盖窗口、覆盖率、最长等待、平均等待、薄弱区域，
  或执行五项虚拟星座选星任务时调用。支持 K 重同时覆盖、面积严格/降级口径和高级传感器 Profile；
  不用于云量预测、轨道设计或真实任务排程。
goal: >-
  基于冻结城市边界、TLE、卫星目录和传感器 Profile，输出可复现的区域覆盖窗口、城市指标、
  薄弱区域及选星方案；严格目标不可行时明确报告物理上限，不静默降低门槛。
executable: true
open_manually: true
inputs:
  - name: operation
    type: string
    required: true
    enum:
      - coverage_only
      - task_1_min_satellites_temporal
      - task_2_min_satellites_mrt
      - task_3_max_qualified_area
      - task_4_min_area_quantile_mrt
      - task_5_min_area_weighted_art
    description: 只做覆盖，或五项选星任务之一。
  - name: city_adcode
    type: string
    required: true
    description: 浙江省 11 个地级市 adcode。
  - name: start_utc
    type: string
    required: false
    description: ISO 8601 UTC 开始时刻；不填时由服务端采用当前 UTC。
  - name: analysis_horizon_h
    type: number
    required: false
    default: 8
    description: 分析时长，单位小时。
  - name: catalog_sensor_type
    type: string
    required: false
    default: 可见光
    enum: [可见光, 红外, 高光谱, SAR, 全部]
  - name: max_resolution_m
    type: number
    required: false
    default: 1.0
    description: 允许的最大分辨率；只用于候选池筛选，不进入几何。
  - name: min_simultaneous
    type: integer
    required: false
    default: 1
    description: 同一网格同一时刻至少同时覆盖的卫星数 K。
  - name: temporal_coverage_target_pct
    type: number
    required: false
    description: 任务①/③的单网格时间覆盖率门槛。
  - name: mrt_target_minutes
    type: number
    required: false
    description: 任务②中一个地点最长允许连续多久看不到卫星，单位分钟。
  - name: n_budget
    type: integer
    required: false
    description: 任务③/④/⑤允许选择的卫星数量。
  - name: area_target_pct
    type: number
    required: false
    default: 100
    description: 要求达标的城市面积比例。
  - name: allow_area_fallback
    type: boolean
    required: false
    default: true
  - name: fallback_area_target_pct
    type: number
    required: false
    default: 90
  - name: sensor_profile
    type: object
    required: false
    description: 仅高级请求传入的全局传感器、指向、姿态和约束配置。
  - name: satellite_profile_overrides
    type: array
    required: false
    description: 按 NORAD 覆盖全局 Profile 的逐星配置。
outputs:
  - name: status
    type: string
  - name: summary
    type: string
  - name: summary_cards
    type: object
  - name: satellite_table
    type: array
  - name: city_pass_window_table
    type: array
  - name: weak_areas
    type: array
  - name: selection
    type: object
  - name: assumptions
    type: array
  - name: reproducibility
    type: object
scripts:
  - id: run_satellite_coverage_selection
    path: scripts/run_satellite_coverage_selection.py
    language: python
    entrypoint: run
    description: 执行区域覆盖与五项选星，并返回结构化结果和 HTML 预览。
hitl_questions:
  - id: operation
    label: 请选择选星任务
    type: select
    control: buttons
    required: true
    options:
      - value: task_1_min_satellites_temporal
        label: 任务① 最少卫星达到覆盖率目标
        description: 指定每个网格的时间覆盖率和达标面积比例，满足要求时尽量少选卫星。
      - value: task_2_min_satellites_mrt
        label: 任务② 最少卫星满足最长等待要求
        description: 指定一个地点最长允许连续多久看不到卫星，满足要求时尽量少选卫星。
      - value: task_3_max_qualified_area
        label: 任务③ 给定数量扩大达标区域
        description: 给定卫星数量，先让尽可能多的城市面积达到覆盖率目标，再提高全市平均覆盖率。
      - value: task_4_min_area_quantile_mrt
        label: 任务④ 缩短大部分区域最长等待
        description: 给定卫星数量，让至少目标面积比例（如 90%）的最坏等待上限尽可能短。
      - value: task_5_min_area_weighted_art
        label: 任务⑤ 缩短全市平均等待
        description: 给定卫星数量，按网格真实面积加权，尽量缩短全市平均重访间隔并报告薄弱区。
  - id: city_adcode
    label: 请选择浙江省目标城市
    type: city_picker
    options_source: cities
    required: true
  - id: analysis_horizon_h
    label: 请输入分析时长（小时）
    type: number
    control: slider
    min: 1
    max: 72
    step: 1
    default: 8
    required: true
  - id: catalog_sensor_type
    label: 请选择候选传感器类型
    type: select
    control: buttons
    default: 可见光
    required: true
    options:
      - value: 可见光
        label: 可见光（推荐）
        recommended: true
      - value: 红外
        label: 红外
      - value: 高光谱
        label: 高光谱
      - value: SAR
        label: SAR（需原生入射角）
  - id: max_resolution_m
    label: 请输入最大分辨率（米）
    type: number
    control: slider
    min: 0.5
    max: 100
    step: 0.5
    default: 1.0
    required: true
  - id: min_simultaneous
    label: 同一网格至少同时覆盖几颗卫星（K）
    type: number
    control: slider
    min: 1
    max: 3
    step: 1
    default: 1
    required: true
metadata:
  version: 0.1.0
  executable: true
  open_manually: true
  deployment_preference: vercel_same_runtime_first
---

## 应调用的情况

- 用户要查看浙江省地级市未来一段时间的卫星覆盖窗口、覆盖率、最长等待、平均等待或薄弱区域。
- 用户要求按覆盖率、最大空窗或卫星预算组成虚拟星座。
- 用户明确要求 K=2/K=3 同时覆盖，或提供复杂传感器、指向、姿态和约束参数。

## 不应调用的情况

- 云量和成像质量预测、真实卫星任务排程、姿态机动优化或轨道设计。
- 浙江 11 个地级市之外、且没有提供已验收边界的区域。
- 只有普通遥感科普意图，没有覆盖或选星任务。

## 执行口径

1. 普通请求默认包含选星；用户未指定优化目标时，只展示任务①～⑤并让用户选择，不默认其中任何一项。只有用户明确要求不选星时才执行 `coverage_only`。默认分析 8 小时、5 km 裁剪网格，候选池为平台可见光且分辨率不差于 1 m 的卫星。
2. 分辨率只筛选候选卫星，不参与覆盖几何；代表点不带半径圆，面积按裁剪网格真实面积加权。
3. 输出必须保留覆盖窗口，不能退化为布尔结果。
4. 严格面积目标不可行时先报告物理上限；只有用户允许时才尝试 90% 等面积降级。
5. 高级传感器参数必须由用户或已验收 Profile 提供，不得臆造。SAR 缺原生入射角带时必须阻止圆锥降级。
6. 结果是几何观测机会，不包含云量、真实排程、存储、下传和姿态行为优化。

## 参数追问

平台当前 HITL 卡片先收集共同参数。任务①～⑤缺少专用阈值或预算时，执行入口返回
`needs_input` 和 `missing_fields`；平台适配层应继续追问，而不是替用户猜值。高级需求同理。

## 结果展示

优先展示：是否可行、严格/降级口径、推荐卫星、最优性状态、城市指标卡、覆盖窗口、薄弱区域、
假设和可复现哈希。只有 `proven_optimal` 才能称连续窗口业务目标已证明最优。
