# SIRIAgent 同机 Skill 使用与验收

> 版本：2026-08-31 平台目录可调用版。
>
> 当前一句话结论：**Agent 与引擎同机时，可直接运行 Python Skill，不需要 FastAPI、MCP、Docker 或数据库。**

## 1. 这一版已经能做什么

- `coverage_only`：只分析区域覆盖，不选星；
- 任务①：满足网格时间覆盖率的最少卫星；
- 任务②：满足 MRT（最大空窗）的最少卫星；
- 任务③：给定 `N` 颗卫星，先最大化达标面积，再最大化面积加权时间覆盖率；
- 任务④：给定 `N` 颗卫星，最小化指定面积分位的 MRT；
- 任务⑤：给定 `N` 颗卫星，最小化面积加权 ART（平均空窗）。

六种 operation 共用同一份 Schema，支持 `K=1、2、3`。任务①/②默认先求 100% 面积，不可行时才显式尝试 90%，不会暗中降低标准。

结果保留人真正关心的信息：指标卡、推荐卫星、城市过境窗口、薄弱区域、严格/降级状态、最优性声明和可复现哈希。

## 2. 验收前的环境

在开发工作树中执行：

```powershell
Set-Location 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析-dev'
$python = 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析\.venv\Scripts\python.exe'
```

这里只是让开发工作树共用主工作树的虚拟环境。最终合并回主工作树后，在项目根目录直接用 `.\.venv\Scripts\python.exe` 即可。

平台 110 星目录已与固定 TLE 110/110 映射。普通请求默认使用平台目录 Profile，按平台默认条件
“可见光且分辨率不差于 1 m”得到 88 颗候选；分辨率只筛选，非 SAR 的幅宽进入矩形几何。

## 3. 七步验收

### 第一步：看 Skill 自己说明参数

```powershell
& $python .agents\skills\satellite-coverage-selection\scripts\run_skill.py --describe --pretty
```

通过标准：

- `status` 是 `ok`；
- 显示 1 个覆盖任务 + 5 个选星任务；
- `execution_mode` 是 `local_python_direct`；
- `mcp_required` 和 `fastapi_required` 都是 `false`；
- 默认时长是 8 小时，K 支持 1～3；
- 默认 `profile_slug=siriagent_catalog_swath_v1`、`tle_source=siriagent_110_snapshot`。

### 第二步：验收“缺参会提示”

```powershell
& $python .agents\skills\satellite-coverage-selection\scripts\run_skill.py --input-json '{"operation":"task2"}' --pretty
```

通过标准：返回 `needs_input`，且 `missing_fields` 只指出 `city_adcode` 和 `mrt_target_minutes`；没有启动重计算。

### 第三步：跑一个快速真实覆盖样例

```powershell
& $python .agents\skills\satellite-coverage-selection\scripts\run_skill.py `
  --input-file .agents\skills\satellite-coverage-selection\references\example-coverage-quick.json `
  --pretty
```

这个样例使用真实杭州边界和固定的 58 颗吉林一号 TLE，但把网格放宽到 30 km，用来快速验证调用链。

通过标准：

- `status=completed`；
- `city.adcode=330100`，并有网格数和城市面积；
- 有 `summary_cards`、`satellite_table`、`city_pass_window_table`、`weak_areas`；
- 窗口保留开始、结束和持续时间；
- `reproducibility` 包含 TLE、边界和 Profile 哈希。

### 第四步：跑一个快速选星样例

```powershell
& $python .agents\skills\satellite-coverage-selection\scripts\run_skill.py `
  --input-file .agents\skills\satellite-coverage-selection\references\example-task2-quick.json `
  --pretty
```

通过标准：

- `status=completed`，有 `selection.strict_result` 和 `selection.recommended_result`；
- 不可行时也是合法科学结果，但必须正确显示 `feasible`、是否尝试 90% 和使用的面积目标；
- 若返回推荐组合，指标卡和窗口只评估该组合，`metrics_satellite_scope=selected_satellites`。

### 第五步：验收平台 110 星目录链路

```powershell
& $python .agents\skills\satellite-coverage-selection\scripts\run_skill.py `
  --input-json '{"operation":"coverage_only","city":"宁波","start_utc":"2026-08-30T00:00:00Z","analysis_horizon_h":1,"grid_size_km":30,"detection_step_s":60}' `
  --pretty
```

通过标准：候选数为 88，`profile.catalog_mapping.generated_override_count=88`；卫星表回显
`sensor_class`、`resolution_m` 和 `swath_km`；`reproducibility` 包含目录和 TLE 哈希。这个快速样例
只跑 1 小时/30 km，正式默认仍是 8 小时/5 km。

### 第六步：验收高级 Profile（仅在需要时）

普通用户不需要这一步；默认预设已经覆盖日常请求。研发或高级用户可以在 JSON 中加入
`sensor_profile`，例如：

```json
{
  "operation": "coverage_only",
  "city_adcode": "330100",
  "analysis_horizon_h": 1,
  "grid_size_km": 30,
  "sensor_profile": {
    "sensor_type": "Rectangular",
    "sensor_params": [45, 45],
    "min_elevation_deg": 0,
    "attitude": {"profile": 0}
  }
}
```

通过标准：返回 `completed`，`input_summary.advanced_mode=true`，且 `profile.mode=advanced`、
`profile.effective_config` 与请求的传感器/姿态配置一致。逐星配置使用
`satellite_profile_overrides=[{"norad_id": ..., ...}]`；未知 NORAD 会被拒绝。区域 `Targeted`
会自动跟踪所有保留网格代表点的面积加权形心，不能理解成每个网格点同时各自转轴；结果中的
`profile.targeting` 会回显形心经纬度和投影坐标。

### 第七步：跑自动回归

```powershell
& $python src\validate_zhejiang_boundaries.py
& $python src\validate_region_grid.py
& $python src\validate_siriagent_catalog.py
& $python src\validate_siriagent_skill.py
& $python src\validate_regional_selection.py
```

通过标准：浙江边界 `4/4`、区域格网 `4/4`、平台目录 `5/5`、Skill `3/3`、区域选星 `7/7`。
平台目录自检会核对 110/110 TLE、默认 88 颗候选、矩形幅宽、SAR 缺参保护和宁波快速端到端；
Skill 自检会验证六种调用语义、K=3 入参、五项选星分派，以及真实杭州边界+真实 TLE 的同链路执行。

## 4. 怎样让 Agent 用

### Codex

从本项目根目录开一个新 Codex 任务，让它重新扫描项目 Skill，然后可以直接说：

```text
请用 $satellite-coverage-selection 分析杭州市未来 8 小时的区域覆盖窗口。
```

或：

```text
请用 $satellite-coverage-selection 执行任务②：杭州、MRT 180 分钟、K=2，先严格 100% 面积，不可行再试 90%。
```

Skill 会按任务只追问必需参数。项目内 Skill 位于 `.agents/skills/`；它不等于 MCP 服务。

### SIRIAgent

还不能只靠本仓库自动出现在 SIRIAgent 网页里。平台研发需要：

1. 用平台的注册样例登记 slug、意图、参数和 `script_id`；
2. 把 `script_id` 指向 `scripts/run_skill.py` 或直接 import `run_skill(inputs)`；
3. 确保 runner 的 Python 环境装有项目依赖，并能读取边界/TLE 文件；
4. 把 JSON 中的指标卡、薄弱区和窗口表接到前端展示。

如果他们证实 skill runner 与引擎不同机，我们再在现有 `run_skill` 外包 FastAPI，算法与 JSON 口径不需重写。

### Coze 等其他 Agent

算法可以复用，但是否能直接“安装这个 Skill 文件夹”取决于平台自己的 Skill/插件规范。对不能直接执行本机 Python 的云端 Agent，通常需要 HTTP API 或平台自定义工具适配；MCP 是可选的标准工具协议，不是本引擎运行的必需条件。

## 5. 当前验收边界（不能误当正式产品）

- 浙江省及 11 地市边界已全部冻结；原始 DataV/高德 2021-05 GCJ-02 快照和转换后的 WGS84
  计算文件都有版本与哈希。平台若要求使用另一套边界，仍需先比较后重新验收；
- `siriagent_110_snapshot` 已完整映射平台 110 星目录，但它是 2026-08-31 的可复现快照；正式运行仍需
  规定 TLE 更新频率、版本留存和结果绑定哈希；
- 默认 88 星候选来自平台“可见光且分辨率不差于 1 m”筛选。107 颗非 SAR 可由平台幅宽派生矩形
  跨轨范围，但沿轨 60°仍是明确场景假设，不是厂家验证参数；
- 3 颗 SAR 缺少原生入射角带，当前会阻止圆锥降级；请求“全部”而不补参数时不能声称是完整
  110 星全池最优；
- `optical_rectangular_validation_v1` 与 `simple_cone_benchmark_v1` 是验收/回归 Profile，不是真实逐星光学幅宽或 SAR 入射角；
- 高级 `sensor_profile`/`satellite_profile_overrides` 已能把已有复杂传感器、姿态和约束引擎接到区域 Skill，
  但参数仍必须由研发提供真实载荷 Profile，不能把验收数值当作平台 110 颗星的生产配置；
- 当前输出是 JSON 数据，覆盖薄弱区前端地图还需 SIRIAgent 平台展示层对接；
- 当前是同步、单次调用，尚未做跨请求缓存和任务队列。

因此，这一版可验收的是：**平台 110 星目录/TLE 已接入，默认 88 星非 SAR 链路可计算；Skill 能正确
说明参数、追问缺参、执行五项选星并返回结构化结果。**不应在补齐真实沿轨/SAR Profile、完成平台
注册和前端联调前，对外声称已是 SIRIAgent 生产系统。详细数据见
[SIRIAgent平台110星目录与Profile基线.md](SIRIAgent平台110星目录与Profile基线.md)。
