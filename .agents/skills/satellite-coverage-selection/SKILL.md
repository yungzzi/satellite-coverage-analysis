---
name: satellite-coverage-selection
description: 计算浙江城市的卫星区域覆盖窗口，并执行五项虚拟星座选星任务。适用于用户询问城市覆盖率、覆盖窗口、MRT、ART、K重覆盖、给定卫星预算或最少卫星组合；不用于轨道设计、云量预测或真实任务排程。
---

# 卫星区域覆盖与选星

本 Skill 在当前项目所在机器直接执行 Python 引擎，不需要 FastAPI 或 MCP。

它是引擎的 Agent 适配层，不是把物理引擎和数据全部复制进 Skill 文件夹的独立软件包。项目内
使用时零配置；若安装到全局 Skill 目录或交给另一套本地 Agent，首次使用先读取
[运行环境与移交](references/runtime-and-portability.md)，并运行环境自检。

## 处理请求

1. 在新的 Agent/机器首次执行时，运行 `scripts/run_skill.py --self-check --pretty`。只有返回
   `status=ok` 和 `ready_for_direct_agent_use=true` 才启动计算；已在本任务验证过时无需每次重复。
2. 默认将普通区域分析理解为“覆盖分析并选星”，使用任务①：以网格时间覆盖率为主指标，求达到目标所需的最少卫星。缺少 `temporal_coverage_target_pct` 时先追问，不能臆造阈值。只有用户明确说“只看覆盖”“不选星”时才使用 `coverage_only`；用户给出 MRT、卫星预算或其他明确目标时改用对应任务②～⑤。
3. 确定任务后，只询问该任务的必填参数。需要完整字段时读取 [参数与结果约定](references/input-output-schema.md)，不要把平台旧的空间 `coverage_target_pct` 猜成网格时间覆盖率。任务仍不明确时优先说明推荐的任务①及其含义，再列出其余选星目标，不把“不选星”作为推荐行为。
4. 普通用户默认使用 `siriagent_catalog_swath_v1`：平台 110 星目录按“可见光、分辨率不差于 1 m”筛为 88 颗，分辨率只筛选，逐星幅宽按矩形跨轨范围计算。只有用户明确提出 SAR、其他传感器类别、矩形/锥形视场、侧摆、旋转、姿态偏置或光照/约束等高级要求时，才读取参数约定中的高级 Profile 字段并追问缺少的高级参数。不要替用户臆造传感器数值。
5. 将用户输入组成 JSON，运行 `scripts/run_skill.py --input-file <json文件> --pretty`；也可以通过标准输入传 JSON。
6. 若返回 `needs_input`，按 `missing_fields` 和 `parameter_hints` 继续提问，不启动重计算。
7. 若返回 `completed`，先回答是否可行、是否从严格面积降级、推荐卫星和最优性，再展示指标卡、薄弱区域和最有用的覆盖窗口。高级请求还要说明结果中的 `profile.mode=advanced` 和 `profile.effective_config` 是本次实际使用的配置。

## 不可改变的语义

- 核心输出必须保留卫星覆盖开始、结束和时长，不能退化成布尔值。
- `min_simultaneous` 是同一网格同一时刻至少覆盖的卫星数 K，不是窗口次数。
- 代表点不带半径圆；区域面积使用裁剪网格的真实面积权重。
- 严格目标不可行时只能显式报告并按请求尝试面积降级，不得静默降低门槛。
- `proven_optimal` 才能称最终窗口业务目标已证明最优；`proven_discrete_optimal` 只证明离散时间片或
  MRT 桶模型最优，不能扩大解释为连续窗口全局最优。任务③会比较贪心/ILP 的原始窗口结果后再推荐，
  任务⑤固定为贪心近似。
- 平台目录 Profile 不是完整厂家 Profile：非 SAR 的沿轨 60°是场景假设；3 颗 SAR 缺原生入射角带时必须阻止圆锥降级。请求“全部”若排除了缺参 SAR，不得称为完整 110 星全池最优。
- `optical_rectangular_validation_v1` 等固定验收 Profile 只验证调用链，不得描述成平台卫星的真实载荷性能。
- 高级区域 Profile 支持 `SimpleCone`、`Rectangular`、`Conical`、`HalfPower`、`SAR`，以及 `Fixed`、`GrazingAlt`、`Spinning` 指向；`Targeted` 在区域模式自动跟踪全体保留网格代表点的面积加权形心，不是每个网格点各自独立转轴。
- 逐星配置放在 `satellite_profile_overrides`，按 `norad_id` 覆盖全局 `sensor_profile`；普通请求不要传这两个字段。

## 执行失败

- `invalid_environment`：按 `hints` 修复项目根目录、依赖或 CBC 求解器；不要尝试编造结果。
- `invalid_input`：解释错误并请用户修正，不猜测坐标系、传感器或阈值。
- `execution_error`：报告错误类型和消息；不要伪造计算结果。
- 没有对应城市边界或 TLE 时，明确说明需要补齐数据。
