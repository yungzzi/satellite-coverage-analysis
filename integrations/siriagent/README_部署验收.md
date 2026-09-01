# SIRIAgent 卫星覆盖与选星交付包

这不是完整工程的副本，而是给 SIRIAgent 研发接入的最小自包含目录。它包括平台注册说明、
一个统一 Python 入口、运行所需的引擎源码、浙江边界、冻结 TLE/目录和验收样例；不包括
Streamlit 界面、ATK、Excel 导出、下载缓存和开发测试。

## 1. 目录与调用契约

生成后的目录如下：

```text
satellite_coverage_selection/
├── skill.md
├── scripts/run_satellite_coverage_selection.py
├── requirements-skill.txt
├── examples/
├── engine/src/
├── engine/data/
├── VERSION.json
└── README_部署验收.md
```

平台登记值：

- `slug`: `satellite_coverage_selection`
- `script_id`: `run_satellite_coverage_selection`
- 脚本：`scripts/run_satellite_coverage_selection.py`
- 入口：`run(inputs, progress=None)`

入口返回：

```json
{
  "result": {"status": "completed", "summary": "..."},
  "view_html": "<section>...</section>"
}
```

这是按 2026-09-01 对公开 SIRIAgent 的实际接口整理的适配层：前端向
`POST /api/skill/run` 发送 `{slug, script_id, inputs}`，服务端以 NDJSON 推送进度和结果。
如果平台仓库里的通用 runner 对 Python 返回值另有一层包装，研发只需在这个启动器最外层
调整一次，`engine/src/siriagent_skill/runner.py` 的业务结果契约不需要改。

## 2. 在本工程中生成交付包

在开发工作树根目录执行：

```powershell
& 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析\.venv\Scripts\python.exe' `
  integrations\siriagent\build_package.py
```

产物位于 `dist/satellite_coverage_selection/`。`dist/` 不入 Git；每次都由清单重新生成，
这样正式源码只有一份。生成器只会替换带有同包名 `VERSION.json` 的旧产物，避免误删其他目录。

## 3. 用户本地怎么验收

最快是一条总验收：

```powershell
& 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析\.venv\Scripts\python.exe' `
  src\validate_siriagent_delivery_package.py
```

它会在临时目录重新生成包，并从包外启动它，依次验证：目录不缺文件、依赖和 CBC 可用、
覆盖分析与五项选星均已暴露、覆盖分析可完成、任务②选星可完成、返回值包含结构化
`result` 和 `view_html`。

也可以逐项看结果：

```powershell
$py = 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析\.venv\Scripts\python.exe'
$pkg = 'D:\研究生学习资料\02_课题组工作\长期项目\卫星覆盖性分析-dev\dist\satellite_coverage_selection'

& $py "$pkg\scripts\run_satellite_coverage_selection.py" --self-check --pretty
& $py "$pkg\scripts\run_satellite_coverage_selection.py" --input-file "$pkg\examples\coverage_quick.json" --pretty
& $py "$pkg\scripts\run_satellite_coverage_selection.py" --input-file "$pkg\examples\task2_quick.json" --pretty
```

验收标准：

- 自检：`ready` 为 `true`，`cbc_available` 为 `true`；
- 业务样例：`result.status` 为 `completed`；
- 覆盖样例：有 `summary_cards`、`city_pass_window_table`、`weak_areas`；
- 选星样例：`selection` 非空，并保留 `feasible`、`optimality`；
- 两者均返回非空 `view_html`，可被平台直接展示。

`examples/platform_default_coverage.json` 是 5 km 网格、10 秒检测步长和平台 110 星快照的
较重冒烟测试，用来观察真实默认口径的耗时，不建议每次开发都运行。

## 4. 平台研发怎么接入方案一（Vercel 同运行时）

1. 把整个生成目录放到平台仓库的 Skill 根目录；不要只复制 `skill.md` 和脚本。
2. 按现有技能注册器加载 `skill.md`，登记上述 `slug` 与 `script_id`。
3. 把 `requirements-skill.txt` 合并进平台部署根目录的 Python 依赖文件。Vercel 不会因为它
   藏在 Skill 子目录中就自动安装依赖。
4. 确保部署产物包含 `engine/data/`。运行期间这些冻结文件只读，不要求数据库。
5. 为执行 `/api/skill/run` 的 Python Function 开启 Fluid Compute，并把最大时长配置到
   当前账号允许值；同时检查部署包体积、内存和 4.5 MB 响应上限。
6. 先发 Vercel Preview，不直接改生产；依次验证意图识别、参数追问、进度流、覆盖、五项选星、
   HTML 展示和错误返回。

当前启动器输出默认不携带逐网格完整 GeoJSON 和调试窗口，因此返回体较小。薄弱区域只返回
前若干代表点；若平台要做地图，可按坐标画点，或在后续专用地图接口中按需取 GeoJSON。

## 5. 哪些验证本地做不了

本地总验收能够证明“包是独立的、接口能调、业务能算、ILP 能跑”，但不能证明平台账号的
Vercel 限额一定容纳它。只有平台研发能完成最后一级 Preview 验收，重点看：

- Python Function 的解压后体积是否超限；
- 5 km/10 秒和较大候选池的峰值内存、冷启动、总耗时；
- PuLP 自带 CBC 二进制在 Vercel Linux 中是否有执行权限；
- 平台通用 runner 是否原样接受 `{result, view_html}`；
- 五项任务在 Function 时限内是否都能返回。

如果 Preview 因体积、CBC、内存或时限失败，才切到方案二：Vercel 继续负责页面、意图和轻量
转发，研究院 Linux 服务器运行 FastAPI 计算服务。业务引擎和这份输入/输出契约可继续复用，
不需要重写算法。

## 6. 业务口径提醒

- 默认分析 8 小时；分辨率只筛卫星，不进入几何。
- 普通请求默认包含选星；未指定目标时展示任务①～⑤供用户选择，不默认其中任何一项。只有明确
  要求不选星时才用 `coverage_only`。
- 区域采用裁剪后的 5 km 网格单元和真实面积权重，代表点本身不带圆。
- 输出保留每颗卫星的城市覆盖窗口，并给出城市平均指标与薄弱区域。
- SAR 必须使用原生入射角带；缺参数时阻止错误的圆锥替代。
- 复杂传感器、指向、姿态与约束通常使用预设；高级用户可以传 `sensor_profile` 和逐星覆盖。
- 结果是几何观测机会，不包含云量、真实任务调度、姿态行为优化、存储与下传约束。
