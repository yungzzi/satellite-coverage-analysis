---
name: satellite-coverage-selection
description: 计算浙江城市的卫星区域覆盖窗口，并执行五项虚拟星座选星任务。适用于用户询问城市覆盖率、覆盖窗口、MRT、ART、K重覆盖、给定卫星预算或最少卫星组合；不用于轨道设计、云量预测或真实任务排程。
---

# 卫星区域覆盖与选星

本 Skill 在当前项目所在机器直接执行 Python 引擎，不需要 FastAPI 或 MCP。

## 处理请求

1. 先判断用户要“只分析覆盖”还是五项选星任务之一。任务不明确时，运行 `scripts/run_skill.py --describe --pretty`，向用户列出六种 operation。
2. 确定任务后，只询问该任务的必填参数。需要完整字段时读取 [参数与结果约定](references/input-output-schema.md)，不要把平台旧的空间 `coverage_target_pct` 猜成网格时间覆盖率。
3. 将用户输入组成 JSON，运行 `scripts/run_skill.py --input-file <json文件> --pretty`；也可以通过标准输入传 JSON。
4. 若返回 `needs_input`，按 `missing_fields` 和 `parameter_hints` 继续提问，不启动重计算。
5. 若返回 `completed`，先回答是否可行、是否从严格面积降级、推荐卫星和最优性，再展示指标卡、薄弱区域和最有用的覆盖窗口。

## 不可改变的语义

- 核心输出必须保留卫星覆盖开始、结束和时长，不能退化成布尔值。
- `min_simultaneous` 是同一网格同一时刻至少覆盖的卫星数 K，不是窗口次数。
- 代表点不带半径圆；区域面积使用裁剪网格的真实面积权重。
- 严格目标不可行时只能显式报告并按请求尝试面积降级，不得静默降低门槛。
- `proven_optimal` 才能称为离散模型已证明最优；任务⑤固定为贪心近似。
- 固定验收 Profile 只验证调用链，不得描述成平台卫星的真实载荷性能。

## 执行失败

- `invalid_input`：解释错误并请用户修正，不猜测坐标系、传感器或阈值。
- `execution_error`：报告错误类型和消息；不要伪造计算结果。
- 没有对应城市边界或 TLE 时，明确说明需要补齐数据。
