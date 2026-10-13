# 卫星覆盖性分析（Satellite Coverage Analysis）

用纯 Python 完成“哪些卫星在什么时间能覆盖某个地区”的分析，替代 ATK 仿真软件，无商业软件依赖。

## 项目位置（2026-10-13）

本仓库位于 `D:\研究生学习资料\02_课题组工作\卫星覆盖与选星\05_代码\satellite-coverage-analysis`。
外层“卫星覆盖与选星”是项目资料工作区，不是第二个 Git 仓库；汇报、对外交付、外部资料与独立
ATK 实验放在外层对应目录。代码配套的 `docs/`、运行数据 `data/` 与 Skill 留在本仓库。
可打开外层 `卫星覆盖与选星.code-workspace` 浏览整个项目，或只打开本仓库进行开发。

## 这是什么（白话）
- **输入**：目标区域（两种：① 点+半径，如杭州某点 5 km 圆；② 省/市多边形 GeoJSON）、
  卫星星座轨道数据（TLE）、时间段
- **输出**：每颗卫星的覆盖时间窗口 + 卫星汇总 + 整体时段状态表 + 空窗表 +
  文字摘要 + 可用卫星数曲线 + 甘特图；另有查询工具可按时间段查、导出 Excel

## 核心概念（白话术语表）
| 名词 | 白话解释 |
|---|---|
| TLE | 卫星轨道的公开标准数据格式（两行数字），描述卫星某时刻的轨道状态。CelesTrak、spacemapper 都能下载 |
| SGP4 | 根据 TLE 预测卫星未来位置的标准算法（已由 skyfield 库实现，不用自己写） |
| 星下点 | 卫星正下方、垂直到地面的那个点 |
| 覆盖圆（足印） | 卫星传感器视场圆锥打到地面形成的圆形区域；半径由轨道高度和视场角决定 |
| 视场半角 | 视线张角（天底圆锥半角，假设值 10°）；普通成像星约 1°~10°，宽幅任务约 30° |
| 仰角 | 站在目标点抬头看卫星，视线与水平面的夹角（默认要求 ≥ 10°） |
| 网格代表点 | 在地区内按固定间距撒的采样点，用来代表“地区被覆盖” |

覆盖窗口的检测采用两段式：**10 秒粗筛**找大致覆盖段，**1 秒边界精化**把起止定到秒级
（横跨全时段的静止轨道窗口直接跳过不精化）；选星按 1 分钟一段统计覆盖，
最终覆盖率/空窗/连续判定用覆盖窗口**精确回算到秒**（分钟内的短空窗不会被吞）。
⑤页选星区可选"**按 10 秒精细做选星决策（更精确，仅用贪心算法）**"：把选星决策段
从 1 分钟加密到 10 秒，更贴合"连续覆盖/尽量没有短空窗"的需求；10 秒精细决策下
整数规划计算量过大，自动改用贪心。
CLI 用 `--decision-step-seconds 10`。

## 逐星传感器参数（可选）
全局默认（视场半角 10°、最小仰角 10°）对所有卫星生效；如需给某颗星单独设参数，
编辑 `data/sensor_params.toml`（首次运行自动生成模板，不入库）或在 GUI ⑤页
“传感器参数表”折叠区直接增删改：
- 只按 **NORAD 编号**精确匹配；未设置的星一律用全局默认。
- 每行可填 视场半角 / 最小仰角 / 幅宽（km）；填了幅宽会按该时刻轨道高度换算成
  等效视场半角，仍按覆盖圆锥计算；**幅宽默认不生效**（`swath_enabled = false`，
  只记录不参与计算）。勾选⑤页右上角“幅宽（逐星）生效”（或改文件开关）后，
  只有本表填了幅宽的星按它自己的轨道高度换算；**未填幅宽的星一律按默认视场半角计算**。
- 高轨/静止卫星（周期 ≥ 700 分钟）没有条带成像概念：即使给它们填了幅宽，
  分析时也会自动回退视场半角并给出提示。
- 优先级：逐星行（NORAD 匹配，行内幅宽或角度）> 全局视场半角；最小仰角逐星优先、否则全局。
- 重复编号以文件里靠后的为准。CLI 可用 `--sensor-params 表路径` 指定其他表。
- ⑤页还有"从本次覆盖分析的卫星来源导入（去重）"：把上面勾选的收藏夹里的卫星
  按 NORAD 去重列出，支持搜索（名称/NORAD）、按来源筛选、"只看未加入参数表的"、
  多行选择/全选，选好后可统一填一套参数加入表格。

## 目录结构
```
卫星覆盖性分析/
├── data/
│   ├── raw/     # 原始 TLE（不入库，用下载脚本重新获取）
│   └── geo/     # 地区边界 GeoJSON
├── src/
│   └── coverage_analysis.py   # 主程序（中文注释，按步骤划分）
├── output/      # 结果 CSV 与甘特图
├── requirements.txt
├── README.md
└── AGENTS.md    # 项目背景记忆（Codex 自动读取）
```

## 运行方法（MVP）
1. 安装依赖：`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
2. 下载数据（TLE 和地区边界）：见 `AGENTS.md` 数据来源；首次由 Codex 代跑
3. 运行：`.venv\Scripts\python src\coverage_analysis.py`
4. 结果在 `output/` 目录
5. 查询工具：`.venv\Scripts\python src\query_coverage.py`（交互菜单）
   或参数模式：`.venv\Scripts\python src\query_coverage.py --start "2026-08-03 12:00" --end "14:00" --export xlsx`

**偷懒方式**：项目根目录有 `run.bat`——输入 `run` 或双击即可（自动用 `config.toml`），
想临时加参数就 `run --radius 10`。

### 目标区域两种模式
```powershell
# polygon 模式（默认）：GeoJSON 多边形 + 网格点
.venv\Scripts\python src\coverage_analysis.py
# circle 模式（点+半径，默认半径 5 km，导师拍板口径）
.venv\Scripts\python src\coverage_analysis.py --mode circle --lat 30.25 --lon 120.15 --radius 5
# 可选：--geojson 换多边形、--start/--end 换分析时段（UTC）
```
circle 模式覆盖判据：卫星足印圆与目标圆**有交集**且圆心处仰角 ≥ 最小仰角；
`max_covered_ratio` 列输出"目标圆被覆盖面积占比"。

### 配置文件（可选，TOML）
不想每次敲命令行参数，或想保存常用参数组合时，用配置文件：
1. 复制模板：`copy config.example.toml config.toml`（`config.toml` 不入库）；
2. 按需修改（带中文注释）；
3. 运行：直接运行（或 VSCode 点"运行 Python 文件"按钮）即可——**存在 config.toml 会自动使用**，
   也可以显式 `.venv\Scripts\python src\coverage_analysis.py --config config.toml`

优先级：**命令行参数 > config.toml（自动加载）> 代码默认值**。

## CLI 数据管理（与 GUI ①~④页对齐）
数据加载器 CLI（`src/satellite_loader.py`）覆盖 GUI ①②③④页的主要操作：

```powershell
# 搜索（含变体重查与拼写提示，可加 --country/--launch-year/--min-alt/--max-alt 筛选）
.venv\Scripts\python src\satellite_loader.py --search "GAOFEN-1"
.venv\Scripts\python src\satellite_loader.py --norad 25544
# 收藏夹
.venv\Scripts\python src\satellite_loader.py --create 我的夹 --add-entry norad_list --collection 我的夹 --value "25544 41194"
.venv\Scripts\python src\satellite_loader.py --remove-norad 我的夹 41194   # 删单颗星
.venv\Scripts\python src\satellite_loader.py --copy 我的夹 备份夹
.venv\Scripts\python src\satellite_loader.py --move-entry 我的夹 1 备份夹
# 星座导入：--constellation 预览；加 --collection 存为编号快照（星座收藏夹）
.venv\Scripts\python src\satellite_loader.py --constellation GPS --collection 我的GPS
# 常用地点 / 逐星传感器参数
.venv\Scripts\python src\satellite_loader.py --save-location 杭州 30.25 120.15 5 --list-locations
.venv\Scripts\python src\satellite_loader.py --sensor-set 25544 --fov 15 --swath 50 --sensor-list
# 下载 / 自检
.venv\Scripts\python src\satellite_loader.py --force-update 我的夹
.venv\Scripts\python src\satellite_loader.py --selfcheck
```

选星 CLI（`src/satellite_selection.py`）也已对齐 GUI ⑤页：
`--req-mode`（continuous/revisit/coverage_pct/any）、`--objective`、`--decision-step-seconds 10`、
逐星传感器表（自动读 `data/sensor_params.toml`，可用 `--sensor-params` 指定其他表）、
`--xlsx` 导出选星 Excel、`--save-as` 保存为虚拟星座收藏夹。

## 统一入口 sat 与交付版打包
`src/cli.py` 是统一入口：`python src\cli.py <命令>`，无参数时弹出交互方式选择框。
常用命令：`gui` / `menu` / `search` / `add 夹 编号…` / `resolve 夹` / `cons 星座 [夹]` /
`select 夹 --start … --end …` / `run 夹`（一键：覆盖→连续覆盖选星→Excel→虚拟星座）。

交付版（exe + 数据文件夹，不含源码/文档）由 `build_delivery.py` 构建：
```powershell
.venv\Scripts\python build_delivery.py
```
当前工作区布局下，输出到外层 `04_对外交付/卫星覆盖分析-交付版/`（原工程不动）；
若仓库独立放置，则输出到仓库旁的 `04_对外交付/卫星覆盖分析-交付版/`。
Skill 包仍默认生成到仓库 `dist/satellite_coverage_selection/`，正式移交时再保留一份日期归档。

## 输出文件说明（output/）
| 文件 | 内容 | 给谁看 |
| --- | --- | --- |
| coverage_windows.csv | 窗口明细（卫星视角：每颗星的每次覆盖） | 程序/脚本 |
| coverage_summary.csv | 卫星汇总（每颗星一行，含覆盖率=总覆盖时长÷时段长度） | 程序/脚本 |
| coverage_state.csv | 整体时段状态表（时间视角：某段时间覆盖如何；卫星名单按覆盖总时长排序） | 程序/脚本 |
| coverage_gaps.csv | 空窗表（一颗星都没有的时段） | 程序/脚本 |
| analysis_meta.json | 本次分析的元信息（时段/参数/星数） | 程序/脚本 |
| report_summary.txt | 文字摘要（人话总结） | 人 |
| availability_curve.png | 可用卫星数曲线（主图） | 人 |
| gantt.png | 甘特图（默认生成，CONFIG save_gantt=False 可关） | 人 |
| coverage_*.xlsx | 查询工具导出的 Excel（多 sheet，含说明页） | 人 |

CSV 是"数据底座"，Excel 是"展示层"：程序读 CSV，人看 Excel。

## 当前进度
- [x] 工程初始化（git、目录、README、AGENTS.md）
- [x] MVP：遥感星座 164 颗 + 浙江 + 1 天 → 覆盖窗口 CSV + 汇总 + 图
- [x] 与公开过境预报交叉验证（5 颗星 4 种轨道，133 次过境全部对上）
- [x] 输出升级：状态表 / 空窗表 / 文字摘要 / 可用卫星数曲线 / 查询工具 / Excel 导出
- [x] 两段式边界精化（1 分钟粗筛 + 窗口边界 10 秒精化）
- [ ] 扩展：任意地区、几千颗星批量分析
- [ ] 事后回算验证（新 TLE 发布后回算对比）
- [ ] 神经网络方向（后续阶段）
- [ ] 神经网络方向（后续阶段）

## ATK：本机历史对拍参考

主线和未来服务均使用自研 Python 引擎。ATK 不会进入 FastAPI、Docker 或 Linux 部署；当前仅在
Windows 本机保留，必要时用于与自研复杂传感器结果对拍。历史实验、安装与对拍结论见
[docs/history/ATK/](docs/history/ATK/)，不要把其中的旧 GUI/CLI 选项当作当前服务设计。
