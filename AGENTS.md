# AGENTS.md — 当前项目记忆

> 本文件只保留**当前有效**的约束、入口和决策；完整历史已归档到
> `docs/history/AGENTS-历史基线.md`。开始任务时先读本文件，再按下面的文档路由
> 精确读取所需资料，不要默认扫描 `docs/history/`。

## 1. 项目目标

“卫星覆盖性分析”以 TLE + SGP4 + 几何判定计算卫星对目标的覆盖窗口，并在窗口结果上进行
虚拟星座/选星。当前扩展目标是将“区域时间覆盖 + 选星”作为 Skill 接入
研究院 SIRIAgent；首版按 Agent 与引擎同机直接调用 Python 实现。

### 不可退化的输出

- 核心结果必须是**覆盖时刻/窗口**（开始、结束、时长）及其派生统计，不能退化为一个布尔值。
- 选星五任务仍以窗口序列为事实来源；搜索可使用离散时间片，最终指标必须回到窗口级复核。

## 2. 当前技术与产品决策

1. **主覆盖引擎**：纯 Python 物理计算（TLE/SGP4 + 自研几何），不依赖商业软件。
2. **ATK**：不进入未来 Skill、FastAPI、Docker 或 Linux 服务，也不再新增产品功能；目前仅允许在本机作为
   自研复杂传感器的临时对拍参考。自研区域路径验收后，再单独裁剪 ATK 运行代码与依赖。
3. **SIRIAgent 接入（2026-09-01 更新）**：公开部署已确认是 Vercel 上的 Web + Flask API +
   Python Skill runner；调用为 `/api/skill/run` 的 `{slug, script_id, inputs}`，结果走 NDJSON。
   首选方案一是在 Vercel 同运行时部署瘦身引擎包，不需要 MCP、FastAPI、Docker 或数据库；只有
   Preview 证明包体、CBC、内存或时限不合适时，才切方案二（Vercel 转发到研究院 FastAPI 服务），
   不重写业务 facade。访问 `https://siri-iota.vercel.app` 必须使用本机 mihomo 代理
   `http://127.0.0.1:7890`，直连 443 不通。
4. **区域建模**：城市边界在冻结的浙江等面积米制投影中裁剪为 5 km 方格单元；
   面积权重为裁剪面积，代表点只做覆盖判定且不带圆。优先全部面积达标，确实不可行时才显式降为
   至少 90% 面积达标；不得静默改变阈值。
5. **传感器**：分辨率只作候选筛选，不进入几何。光学幅宽走矩形足印（跨轨由幅宽约束、沿轨放松）；
   SAR 用原生入射角带，不能退回简单圆锥。姿态/指向是固定 Profile 参数，不做行为调度优化。
6. **人可见输出**：指标卡、覆盖薄弱区域地图、卫星表、城市过境窗口表和假设说明。逐网格原始数据、
   稀疏窗口和 Excel 仅作调试/验收产物。

## 3. 当前开发状态与下一段

- `src/region_grid.py` 已完成：固定米制格网、裁剪单元、面积权重、内部代表点和调试 GeoJSON 导出。
- `src/validate_region_grid.py` 已覆盖规则格、带洞/凹形单元、离散面、稳定性和杭州 5 km/2.5 km 面积守恒。
- `src/regional_opportunities.py` 已完成第一版“卫星 × 网格 → 稀疏覆盖窗口”底座：每星一次轨道准备、
  网格分块向量化、SimpleCone/Rectangular/SAR 判定和 Targeted 区域一致性保护。
- `src/validate_regional_opportunities.py` 已完成杭州真实边界小规模回归；后续接区域指标/窗口精化时仍**不修改**
  旧的 `coverage_analysis.make_grid_points` 与原单点+半径行为。
- `src/regional_metrics.py` 已完成第一版指标层：从稀疏窗口计算网格时间覆盖率、MRT、ART、达标面积比例、
  面积加权城市汇总和全市/目标面积同时覆盖时间比例；`src/validate_regional_metrics.py` 已通过人工小例子
  与真实杭州串联自检。
- `src/regional_selection.py` 已完成任务②第一版（`K=1`）：MRT 约束下的最少卫星，支持严格 100%/降级
  面积目标、贪心+反向删除、滑动窗口 ILP，以及统一窗口级复核；`src/validate_regional_selection.py`
  已用小规模穷举和真实杭州机会窗口自检。`regional_opportunities.py` 还支持按需 1 秒边界精化，
 任务①（`K=1` 时间覆盖率门槛）也已完成第一版并由同一自检脚本穷举核对；任务③（`K=1` 给定预算
  先最大化达标面积、再最大化面积加权时间覆盖率）已完成贪心+两阶段 ILP 及穷举核对；任务④（`K=1`
  给定预算最小化面积分位 MRT）已完成离散二分、任务②复用和穷举核对；任务⑤（`K=1` 给定预算
  最小化面积加权 ART，含贪心、单星交换、反向删除和面积分位 MRT 输出）已完成；五项任务的 `K>1`
  通用同时覆盖变量与贪心种子逻辑也已接入，并由人工窗口和全量回归核对。
- `src/siriagent_skill/` 已完成同机调用层：六种 operation、机器可读参数说明、缺参追问、
  五项选星分派、指标卡、卫星表、城市过境窗口和薄弱区输出。Codex Skill 位于
  `.agents/skills/satellite-coverage-selection/`；现已增加 `agents/openai.yaml`、跨工作目录项目发现、
  `SATELLITE_COVERAGE_PROJECT_ROOT`/`--project-root` 移交方式和 `--self-check` 环境检查，
  `src/validate_agent_skill_package.py` 验证项目内与全局安装两种启动。`src/validate_siriagent_skill.py`
  已通过综合自检。浙江省及
  11 地市边界已全部冻结：原始 DataV/高德 2021-05 GCJ-02 快照保留在
  `data/geo/raw_datav_gcj02/`，计算文件由固定迭代逆变换归一为 WGS84，并在
  `data/geo/metadata/zhejiang_boundaries.json` 记录源文件、转换文件和几何哈希。
  现已支持普通请求按预设运行；用户明确提出高级需求时，可通过 `sensor_profile` 和
  `satellite_profile_overrides` 将已有复杂传感器、统一指向、姿态和约束参数接入区域路径，结果会回显
  `profile.effective_config`。区域 `Targeted` 统一跟踪所有保留网格代表点的面积加权形心，
  不再逐网格点独立转轴。
- 平台 110 星目录已冻结为 `data/platform/siriagent_110_catalog.json`，NORAD 与固定 TLE 快照
  `data/test_data/siriagent_110_snapshot.tle` 110/110 匹配。默认“可见光且分辨率不差于 1 m”候选池为
  88 颗；`siriagent_catalog_swath_v1` 对 107 颗非 SAR 由逐星幅宽生成矩形跨轨范围，沿轨 60°明确为
  场景假设。3 颗 SAR 因平台未给原生入射角带而阻止圆锥降级。完整审计和 11 城基准见
  `docs/current/SIRIAgent平台110星目录与Profile基线.md`。
- 默认 88 星、8 小时、5 km、10 秒检测的 11 城顺序基准总计约 286 秒；单城市 12～38 秒，耗时与
  网格数近似线性。绍兴 410 网格的完整业务验收也已通过：一次生成 3897 个窗口后复用到 K=1
  五任务和 K=2/K=3 代表性场景，技术失败 0 项；任务⑤ K>1 已改用真实窗口重叠见证种子，任务③
  的 ILP 只标 `proven_discrete_optimal` 并按原始窗口结果择优推荐。报告见
  `docs/current/SIRIAgent平台默认业务验收报告.md`。下一步是取得平台注册样例和真实光学沿轨/SAR Profile。
- `integrations/siriagent/` 已提供平台注册 `skill.md`、统一启动器、瘦身依赖、三组样例、自动生成器
  和部署说明；生成物是忽略版本控制的 `dist/satellite_coverage_selection/`，内含最小引擎与冻结数据。
  `src/validate_siriagent_delivery_package.py` 会从包外验证目录、依赖/CBC、覆盖和任务②选星。下一步由
  平台研发把生成包加入真实仓库做 Vercel Preview，方案一失败才启动 FastAPI 方案二。
- 11 个城市边界已记录 `adcode`、边界版本/哈希、源坐标系和转换方法；每次网格结果继续记录投影、
  格长和原点。以后若换平台边界，必须先比较版本、坐标系和哈希，未知坐标系不可直接与 WGS84
  轨道地面点混用。

## 4. 用户沟通

- 全程中文；用户非航天专业、是 vibe coding 新手。
- 每个模块交付附白话说明（类比 + 必要公式拆解）；关键代码写中文注释。
- 用户说“没听懂”时先解释清楚再继续；提交信息用中文。

## 5. Git 与工作树（硬约束）

- 主工作树：`D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析`，分支 `main`，只作交付基准。
- 开发工作树：`D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析-dev`，集成分支
  `codex/siriagent-dev`。
- 每项功能必须从集成分支新建 `codex/<功能名>`；完成后 `git merge --no-ff` 回集成分支。
- 不在主工作树直接开发。最终合并 `main` 前先保留主树用户自己的未提交修改，再 `--no-ff` 合并并验证；
  之后才能删除开发工作树和已合并分支。
- 不使用 `git reset --hard`、`git checkout --` 等破坏性命令；现有无关改动属于用户。

## 6. 代码入口

```text
src/
├── coverage_analysis.py      旧覆盖分析主路径；稳定，除非明确任务否则不改
├── sensor_geometry.py        自研复杂传感器/姿态/指向/约束几何
├── satellite_selection.py    旧单点窗口选星：贪心 + ILP
├── region_grid.py            新区域 5 km 裁剪格网
├── regional_opportunities.py 新区域逐星逐网格稀疏覆盖窗口
├── regional_metrics.py       新区域指标与城市汇总
├── regional_selection.py     新区域任务①～⑤（支持 K=1/K>1）选星
├── siriagent_skill/           同机 Skill Schema、编排、人类可读输出与 JSON 入口
├── prepare_siriagent_catalog.py 平台卫星字段清洗、NORAD/TLE 冻结
├── siriagent_catalog.py       平台目录筛选与逐星 Profile 适配
├── benchmark_siriagent_catalog.py  平台默认候选池 × 11 市性能基准
├── benchmark_siriagent_business.py 平台 88 星五任务与 K=2/K=3 业务验收
├── benchmark_regional_k.py   真实城市网格 K=1/K=2 性能基准
├── loader_app.py / cli.py    本地 GUI / CLI
├── satellite_loader.py       TLE、目录与缓存
└── validate_*.py             对应的回归或验收脚本
```

`atk_engine.py`、`atk_ui.py`、`atk_parallel/` 是本机历史对拍路径；未来服务不得依赖它们。

## 7. 文档路由

先读 `docs/README.md`；具体任务按需读取：

- SIRIAgent 的数学、实现、部署：`docs/current/`。
- 自研物理模型、验证、缓存：`docs/reference/`。
- 当前阶段问答：`docs/答疑记录.md`；每次涉及概念、方法或验证的用户提问，在该文件追加一条并提交。
- `docs/history/` 仅保存旧答疑、ATK 对拍和早期单点资料；**只有**用户要求追溯历史或当前资料不足时才读取。

## 8. 验证底线

- 新区域格网：运行 `python src/validate_region_grid.py`。
- 浙江边界基线：运行 `python src/validate_zhejiang_boundaries.py`，检查 12 份哈希、坐标回代、
  11 市 5 km 网格和省市边界一致性。
- 修改旧覆盖/选星路径前，先找到对应 `validate_*.py` 并保持既有回归通过。
- 每次涉及区域面积时检查裁剪单元面积和与边界投影面积一致；代表点必须位于自己单元内。
- 服务化阶段不改变上述物理/窗口语义，只增加输入校验、缓存、接口和展示适配。
- 同机 Skill：运行 `python src/validate_siriagent_skill.py`；正式使用和验收按
  `docs/current/SIRIAgent同机Skill使用与验收.md`。
- Agent Skill 包装或启动器变化：运行 `python src/validate_agent_skill_package.py`，核对项目外工作目录、
  全局 Skill 缺引擎报错，以及显式参数/环境变量连接。
- SIRIAgent 平台交付包：运行 `python src/validate_siriagent_delivery_package.py`；真实平台可行性最终以
  Vercel Preview 的包体、Linux CBC、内存、时限、NDJSON 和响应大小验收为准。
