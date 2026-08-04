# 卫星覆盖性分析（Satellite Coverage Analysis）

用纯 Python 完成“哪些卫星在什么时间能覆盖某个地区”的分析，替代 ATK 仿真软件，无商业软件依赖。

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
| 视场半角 | 传感器最大能“歪着头”看的角度（默认 30°） |
| 仰角 | 站在目标点抬头看卫星，视线与水平面的夹角（默认要求 ≥ 10°） |
| 网格代表点 | 在地区内按固定间距撒的采样点，用来代表“地区被覆盖” |

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
3. 运行：`.venv\Scripts\python src\coverage_analysis.py --config config.toml`

优先级：**命令行参数 > 配置文件 > 代码默认值**。

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
