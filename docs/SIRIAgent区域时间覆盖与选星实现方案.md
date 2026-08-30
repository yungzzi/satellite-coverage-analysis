# SIRIAgent 区域时间覆盖与选星编程实现方案

> 文档状态：实现设计，2026-08-29。
>
> 前置数学口径：`docs/SIRIAgent区域时间覆盖与选星数学依据.md`。
>
> 本文给出模块、数据结构、算法、接口和验证顺序；当前阶段不修改现有覆盖引擎和 GUI。

## 1. 实现目标与非目标

### 1.1 首版必须完成

1. 使用浙江 11 个地市的冻结边界生成裁剪后的 5 km 网格单元和面积权重；
2. 对筛选后的卫星池计算“卫星 × 网格点”的逐点覆盖窗口；
3. 从窗口构造区域时间覆盖率、观测次数、MRT、ART；
4. 支持五类区域选星任务；
5. 严格模式先求全部面积，失败后显式降级到 90% 面积；
6. 输出全池物理上限、选星结果、逐网格统计和逐星窗口；
7. 用独立 FastAPI 服务包装，供 SIRIAgent Flask 后端调用；
8. 保证现有单点、点加半径、GUI 和可选 ATK 路径不被本功能隐式改写。

### 1.2 首版明确不做

- 不优化卫星姿态切换或扫描动作；
- 不做成像任务排程、能量/存储/下传资源调度；
- 不加入云量或真实采购冲突；
- 不重写平台现有“累计空间覆盖达到 P% 的最早时间”skill；
- 不先上数据库；
- 不先把 ATK 作为 Linux 服务的必选依赖；
- 不自动从 90% 继续降到更低面积比例；
- 不擅自执行“只留复杂引擎”的尚未拍板整合。

## 2. 现有代码不能直接复用到底的地方

| 现状 | 对新任务的影响 | 实现要求 |
| --- | --- | --- |
| `coverage_analysis.make_grid_points` 按 0.2°经纬度撒内部点 | 不是 5 km 等距离单元，没有边界面积权重 | 新建米制裁剪格网模块，不直接改旧函数 |
| 简单多边形路径返回 `any(axis=1)` 和未加权点比例 | 只知道城市任一点是否覆盖，丢失逐点时间线 | 新增逐点返回接口，旧汇总接口继续兼容 |
| `analyze_one_satellite` 输出每星一个区域并集窗口 | 无法得到 $\mathcal I_{s,g}$ | 新增卫星—网格稀疏窗口存储 |
| 复杂多边形路径逐点计算后只累计 `any_cov/ratio` | 无法选星；大网格性能受 Python 循环影响 | 分块向量化或批处理，保留逐点结果 |
| 复杂路径的约束在区域末尾只按首点 `P0` 汇总 | 不能代表其他网格点的约束状态 | 所有启用约束必须逐点计算 |
| Targeted 在循环中把每个 `Pk` 分别当目标 | 会把同一时刻多个互斥指向误当成同时可执行 | 城市 Profile 必须生成与网格无关的统一波束轴；逐点 Targeted 只能标“潜在可达” |
| `satellite_selection` 输入是“卫星 × 时间” | 只能处理单目标 | 新建区域选星模块，不把三维语义硬塞进旧函数 |
| `k_min` 表示同时可见数 | 容易被误读成观测次数 | 新接口拆成 `min_simultaneous` 和 `min_observation_count` |
| 当前 MRT 时间片换算使用 `round` | 非整倍数阈值可能有边界歧义 | 新模块用明确的 `floor(M0/Δt)` 和秒级复核 |

这些是新增区域功能的缺口，不等于现有单点功能已经错误。实现应以新模块旁路扩展，先避免对稳定旧路径造成回归。

## 3. 建议目录与职责

```text
src/
├─ region_grid.py               # 城市边界、等面积投影、5 km 裁剪单元
├─ regional_opportunities.py    # 逐星逐网格覆盖状态与窗口
├─ regional_metrics.py          # q、面积达标率、次数、MRT、ART
├─ regional_selection.py        # 五任务贪心/ILP/复核
├─ regional_pipeline.py         # 从输入到结果的唯一编排入口
└─ service/
   ├─ app.py                    # FastAPI 应用
   ├─ schemas.py                # 请求/响应模型
   └─ jobs.py                   # 若平台要求异步任务，再加入任务状态

data/
├─ geo/
│  └─ zhejiang_cities.geojson  # 冻结的 11 地市边界与 adcode
└─ sensor_profiles.toml         # 光学推扫、SAR 等有限场景 Profile

tests/
├─ test_region_grid.py
├─ test_regional_metrics.py
├─ test_regional_selection_small.py
├─ test_regional_profile_semantics.py
└─ test_regional_service.py
```

若项目继续沿用 `src/validate_*.py` 自检风格，可同时提供一个 `validate_regional_selection.py` 作为用户可直接运行的综合验收脚本；核心单元测试仍应拆开。

## 4. 第一层：城市边界与 5 km 面积网格

### 4.1 数据入口

首选从平台研发取得 `bootstrap` 中 11 个城市的完整边界，并冻结为本项目 GeoJSON。每个 Feature 至少包含：

```json
{
  "adcode": "330600",
  "name": "绍兴市",
  "boundary_version": "...",
  "geometry": {"type": "MultiPolygon", "coordinates": []}
}
```

保存时记录文件 SHA-256。运行请求按 `adcode` 匹配，不只按中文名匹配。接收边界时必须向平台研发确认源坐标系；如果是 GCJ-02 等偏移坐标，应先按经确认的转换流程归一到 WGS84/项目标准，不能仅根据数值范围猜测。

### 4.2 新依赖

建议新增 `pyproj`，负责：

- WGS84/平台边界坐标到冻结米制等面积坐标的转换；
- 5 km 格网坐标反投影到经纬度；
- 以平方米计算裁剪单元面积。

不能直接把“5 km”换算成固定经纬度差，因为经度一度对应的距离随纬度变化。

### 4.3 建议数据类

```python
@dataclass(frozen=True)
class RegionGridCell:
    grid_id: str
    representative_lat: float
    representative_lon: float
    area_km2: float

@dataclass(frozen=True)
class RegionGrid:
    adcode: str
    city_name: str
    cells: tuple[RegionGridCell, ...]
    boundary_hash: str
    projected_crs: str
    grid_size_km: float
    grid_origin_xy: tuple[float, float]
```

热路径只使用代表点数组和面积数组；裁剪后的 Shapely 几何主要用于生成、可视化和审计，不必在每次轨道计算中传递。

### 4.4 核心函数

```python
def load_city_boundary(path: Path, adcode: str) -> CityBoundary: ...

def build_clipped_grid(
    boundary: CityBoundary,
    grid_size_km: float = 5.0,
    projected_crs: str | None = None,
    fixed_origin_xy: tuple[float, float] | None = None,
) -> RegionGrid: ...
```

网格首次生成后应缓存为版本化文件。相同边界哈希、投影、格长和原点不得重复生成。

## 5. 第二层：逐星逐网格观测机会

### 5.1 新的返回对象

现有区域函数只返回 `any_cov` 和 `ratio`。新函数应返回逐点状态，或直接转成逐点窗口：

```python
@dataclass(frozen=True)
class AccessWindow:
    sat_id: int
    grid_id: str
    start_utc: datetime
    end_utc: datetime
    duration_s: float

@dataclass
class OpportunityStore:
    satellites: list[SatelliteMeta]
    grid: RegionGrid
    decision_step_s: int
    # 稀疏窗口：key=(sat_index, grid_index)
    windows: dict[tuple[int, int], list[AccessWindow]]
    # 优化用位集合，可按需构建
    coverage_bits: CoverageBitStore | None
```

空窗口不存储，因此“每个网格自己的覆盖窗口没有保留”的问题得到解决。

### 5.2 计算顺序

对每颗卫星：

1. SGP4 一次向量化传播整个时间轴；
2. 一次计算固定 Profile 下的姿态/波束轴；
3. 把网格点按块，例如每块 128～512 点；
4. 对当前块向量化计算视线、传感器坐标、足印判定和逐点约束；
5. 得到形状为 `时间 × 当前网格块` 的布尔状态；
6. 将连续状态压缩为 `AccessWindow`；
7. 释放块级临时矩阵，再处理下一块。

这样不会一次长期保留巨大的浮点三维数组，卫星轨道也不会为每个网格点重复传播。

新的城市路径应直接复用 `sensor_geometry` 的三维视线、传感器坐标系和 `pattern_covered` 判据，为所有 Profile 生成逐点状态；不要先调用已经聚合成 `any_cov` 的旧多边形接口，也不要把旧圆目标重叠公式套到网格代表点。这样只是在新服务旁路建立统一几何内核，不等于删除旧简单引擎。

### 5.3 Profile 统一足印验收

接口必须区分：

```text
area_consistent = true
```

表示同一时刻卫星足印对全部网格统一，可以用于区域选星；以及：

```text
potential_access_only = true
```

表示每个网格被分别指向，只能输出逐点潜在机会，不得声称得到可同时执行的区域方案。

首版 FastAPI skill 只接受 `area_consistent=true` 的 Profile。Targeted 如需进入首版，应固定指向城市中心或另一个统一目标，而不是循环中指向当前网格点。

### 5.4 光学与 SAR

- 光学：将 `swath_km` 映射为矩形跨轨范围，沿轨参数来自场景 Profile；
- SAR：Profile 必须提供近/远入射角或已经验证的原生参数；
- 缺参数：请求校验失败或使用明确命名的假设 Profile，并在结果中回显；
- 禁止把 SAR 静默降级成简单圆锥；
- `resolution_m` 只在候选池筛选阶段使用。

## 6. 第三层：稀疏存储与优化位集合

### 6.1 主存储保留窗口

窗口记录是可解释、可导出和秒级复核的主数据。建议保存扁平表：

```text
sat_index | sat_id | grid_index | grid_id | start_s | end_s
```

并按 `(sat_index, grid_index)` 建立偏移索引。首版可以用内存数组/Parquet 文件；最小 demo 不需要数据库。

### 6.2 位集合只用于加速

将离散覆盖状态按 64 位打包：

```text
coverage_bits[sat, grid, word]
```

若 8 小时、1 分钟一步，共 480 位，只需 8 个 `uint64`。以 110 颗星、350 个网格估算：

$$
110\times350\times8\times8\ \mathrm{bytes}
\approx2.35\ \mathrm{MiB}.
$$

位集合用于按位或、按位与和 `popcount`；不能取代窗口主数据。

### 6.3 缓存键

机会缓存键至少包含：

```text
TLE内容哈希
城市边界哈希
网格规格
开始/结束时刻
检测与决策步长
卫星筛选结果
逐星传感器Profile哈希
姿态/指向/约束哈希
引擎版本
```

缺少其中任一项都可能错误复用旧结果。

## 7. 第四层：区域指标

建议纯函数化实现，使它们可以脱离 GUI、FastAPI 和轨道传播单独测试：

```python
def combine_grid_timeline(store, selected, min_simultaneous=1) -> GridTimelines: ...

def temporal_coverage_by_grid(timelines) -> np.ndarray: ...

def gaps_by_grid(timelines, t0, t1) -> list[list[Gap]]: ...

def mrt_by_grid(gaps) -> np.ndarray: ...

def art_by_grid(gaps) -> np.ndarray: ...

def observation_count_by_grid(
    store,
    selected,
    mode="satellite_pass",
    min_event_s=0,
    merge_gap_s=0,
) -> np.ndarray: ...

def area_pass_ratio(values, areas, predicate) -> float: ...
```

`regional_metrics.py` 不得调用求解器；它只负责对给定卫星集合做确定性评估。

## 8. 第五层：选星算法

### 8.1 共同预处理

1. 删除全时段、全网格都无覆盖的卫星；
2. 在 `min_simultaneous=1` 且任务不统计逐卫星机会次数时，可合并完全相同覆盖签名的候选星，保留等价星清单供结果展示；
3. 在同成本条件下删除被另一颗星完全支配的候选；
4. 计算全候选池上限；
5. 若严格目标物理不可行，跳到 90% 模式；
6. 若全池连 90% 都不可行，停止优化并报告上限。

“相同签名分组/支配”只在任务口径一致、卫星成本相同、`min_simultaneous=1`、不要求逐卫星观测次数且不会影响传感器类别多样性时启用。`min_simultaneous>1` 或 `observation_count_mode=satellite_pass` 时，两颗轨迹相同的卫星仍可能提供两份并发/次数贡献，不能合并。第一版最稳妥的是只默认删除全无覆盖卫星，其余预处理经过测试后再开。

### 8.2 任务①

- 贪心：按面积加权的新增达标时间单元评分；
- ILP：使用 `y[g,k]`、`z[g]` 和时间覆盖率阈值；
- 严格模式先求 $\beta=1$，再求 $\beta=0.9$；
- 秒级窗口复核不通过时，返回离散边界误差并触发修复求解，不能直接把方案标成满足。

### 8.3 任务②

常规 `min_simultaneous=1`：

1. 从位集合构造每个滑窗的候选卫星集合；
2. 用哈希删除完全相同的约束；严格模式可跨网格按候选集合去重，90% 模式因右端变量是各自的 `z[g]`，只在同一网格内去重；
3. 严格模式使用正向集合覆盖贪心；
4. 90% 模式使用全池开始的反向删除；
5. 将贪心方案作为 ILP 初始可行解；
6. ILP 只保留 `x[s]`，90% 模式再加 `z[g]`；
7. 求解完成后用秒级窗口计算真实 MRT。

`min_simultaneous>1` 使用通用 `y[g,k]` 模型，不套用 `x`-only 快速路径。

### 8.4 任务③

使用两次 ILP：

1. 最大化达到 `temporal_coverage_target_pct` 的面积；
2. 固定第一阶段最优面积，再最大化面积—时间平均覆盖率。

贪心版本也按同一字典序语义比较候选，不使用随意加权和。

### 8.5 任务④

对 MRT 上限做离散二分，每次调用任务②的预算可行性模型：

```python
def select_min_area_quantile_mrt(..., beta, n_budget) -> SelectionResult: ...
```

最后对离散最优值附近用秒级窗口复核并报告真实值。

### 8.6 任务⑤

首版：

1. 正向选择使面积加权 ART 降低最多的卫星；
2. 选满预算或再加入卫星不再改善；
3. 做删除、单星交换等局部改进；
4. 明确标注“贪心近似，未证明最优”；
5. 同时报告面积分位 MRT，防止平均值掩盖极差区域。

### 8.7 约束按需生成实验

任务②先实现“全部去重约束一次求解”作为基准。另实现实验开关：

```text
constraint_strategy = full_deduplicated | iterative
```

`iterative` 每轮批量加入当前解违反的全部代表性滑窗，并记录：迭代次数、累计约束数、建模时间、求解时间和最终最优性状态。只有在绍兴、中等城市、浙江最大地市三档基准中稳定获益，才改为默认。

### 8.8 可选观测次数约束

当 `observation_count_mode=satellite_pass` 时，先统计每颗卫星对每个网格的有效窗口数 `pass_count[s,g]`，观测次数可以写成线性和，并叠加严格/90%面积变量。如果一个请求要求同一片面积同时满足覆盖率、次数和 MRT，三类约束必须绑定同一个 `z[g]`，不能各自选择不同的 90% 网格。`union_window` 的次数会随不同卫星窗口合并而变化，首版只做结果报告和贪心评估，不宣称有精确 ILP。

## 9. 第六层：统一流水线

```python
def run_regional_selection_pipeline(
    request: RegionalSelectionRequest,
    progress: Callable | None = None,
) -> RegionalSelectionResult:
    """区域覆盖机会与选星的唯一业务入口。"""
```

建议顺序：

```text
校验请求
  ↓
按分辨率/传感器/TLE筛选卫星
  ↓
加载冻结城市边界与5km网格
  ↓
解析并冻结逐星Profile
  ↓
读取机会缓存或计算逐星逐网格窗口
  ↓
计算全池物理上限
  ↓
严格100%面积求解
  ↓ 不可行
90%面积求解
  ↓
秒级窗口复核
  ↓
输出摘要、清单、逐网格指标、窗口和复现元数据
```

旧的 `analyze_collection_pipeline(...)` 和 `run_selection(...)` 不在第一步被修改；新流水线可复用其底层轨道、窗口合并和选星思想，但不得复用已经丢失网格维度的区域汇总结果。

## 10. FastAPI 最小接口

### 10.1 请求字段建议

```json
{
  "city_adcode": "330600",
  "start_utc": "2026-08-30T00:00:00Z",
  "analysis_horizon_h": 8,
  "objective": "min_satellites_temporal_coverage",
  "temporal_coverage_target_pct": 10,
  "strict_area_target_pct": 100,
  "fallback_area_target_pct": 90,
  "mrt_target_minutes": 180,
  "min_observation_count": null,
  "observation_count_mode": "satellite_pass",
  "n_satellites_budget": null,
  "min_simultaneous": 1,
  "max_resolution_m": 1.0,
  "sensor_types": ["可见光"],
  "profile_slug": "optical_pushbroom_default_v1",
  "grid_size_km": 5.0,
  "decision_step_s": 60,
  "min_event_s": 0
}
```

必须把 `temporal_coverage_target_pct` 和 `area_target_pct` 拆开。平台现有 `coverage_target_pct=100` 是累计空间覆盖语义，不能未经转换直接传给新 skill。

默认分析时长按已确认的 8 小时。单元时间覆盖率阈值尚不能从现有平台默认值推断，首版应要求用户输入或由明确 Profile 提供。

### 10.2 响应字段建议

```json
{
  "status": "completed",
  "request_id": "...",
  "result_scope": "geometric_observation_opportunities",
  "optimality": "proven_optimal",
  "used_area_target_pct": 90,
  "strict_result": {"feasible": false, "reason": "..."},
  "fallback_result": {"feasible": true},
  "selected_satellites": [],
  "all_pool_upper_bound": {},
  "regional_metrics": {},
  "grid_summary": {},
  "assumptions": [],
  "reproducibility": {
    "tle_hash": "...",
    "boundary_hash": "...",
    "profile_hash": "...",
    "engine_version": "..."
  },
  "artifacts": {
    "per_grid_metrics": "...",
    "access_windows": "..."
  }
}
```

聊天响应不宜直接塞入完整三维矩阵。接口返回摘要和关键清单，大表作为 CSV/Parquet/JSON 文件产物保存并给出受控下载地址；最小本机 demo 可以先返回本地结果标识。

### 10.3 同步还是异步

- 本机最小 demo：同步 `POST /v1/regional-selection/run`，先测固定绍兴小样例；
- 正式平台：若计算超过 Flask/Vercel 调用时限，改为提交任务返回 `job_id`，再轮询 `GET /v1/jobs/{id}`；
- 最终采用哪一种，必须由平台研发给出超时、回调和鉴权规范。

FastAPI 只是接口壳，不重写算法。最小 demo 不需要数据库；TLE、Profile、缓存和结果先放计算服务持久目录。

## 11. 错误与不可行状态

响应必须区分：

| 状态 | 含义 |
| --- | --- |
| `invalid_input` | 参数矛盾、缺 Profile、边界不存在等 |
| `no_candidates` | 分辨率/传感器筛选后无卫星 |
| `strict_infeasible` | 全池也不能使全部面积达标，已尝试 90% |
| `fallback_infeasible` | 全池也不能使 90% 面积达标 |
| `feasible_heuristic` | 贪心得到可行方案，但未证明最优 |
| `feasible_time_limit` | ILP 到时限，返回当前可行解和最优性间隙 |
| `proven_optimal` | ILP 已证明离散模型全局最优，且秒级复核通过 |
| `verification_failed` | 离散方案未通过秒级窗口复核，需要修复求解 |

“物理不可行”不能包装成“算法失败”；“求解器超时”也不能包装成“已证明最优”。

## 12. 验证矩阵

### 12.1 网格

- MultiPolygon、岛屿、孔洞、狭长边界；
- 每个代表点位于对应裁剪单元内；
- 所有 $a_g>0$；
- $\sum a_g$ 与城市投影面积一致；
- 相同边界和配置重复生成字节级一致的网格；
- 5 km 主网格对一个城市用 2.5 km 做敏感性对照，只作验证，不启用 10 km 粗筛。

### 12.2 覆盖机会

- 人工构造只含一个代表点的 `RegionGrid`，与旧单点半径为 0 的模式窗口一致；
- 增加卫星不会降低任何网格的覆盖率，也不会增大 MRT；
- 每个离散 1 值都能追溯到一个逐点窗口；
- 简单圆锥、矩形和 SAR 使用已对拍样例；
- 开启约束后逐点结果符合约束，不能只检查第一个网格；
- Targeted 逐点重定向样例必须被标记或拒绝为区域一致 Profile。

### 12.3 指标

- 全程覆盖：$q=100\%$、MRT=ART=0；
- 全程不覆盖：$q=0$、MRT=ART=分析时长；
- 首尾空窗计入；
- 重叠卫星窗口在 `satellite_pass` 和 `union_window` 两种次数口径下结果不同且可解释；
- 面积权重不能退化成简单点数比例。

### 12.4 优化

对少量卫星、少量网格、少量时间片进行全部 $2^n$ 组合枚举，只用于测试：

- 任务①～④ ILP 与穷举最优值一致；
- 任务② `x`-only 与通用 `y` 模型在 $K_{\mathrm{sim}}=1$ 时一致；
- 完全相同约束去重前后最优解集合一致；
- $\beta$ 从 1 降到 0.9 后，最少卫星数不能增加；
- MRT 阈值放宽后，最少卫星数不能增加；
- 反向删除结果始终保持 90% 可行，但不标精确最优；
- 任务⑤只比较质量和稳定性，不断言精确最优。

### 12.5 性能

至少三档：

1. 固定绍兴小样例：少量卫星，便于开发；
2. 绍兴完整 5 km 网格 × 平台 110 星 × 8 小时；
3. 浙江面积最大的地市完整 5 km 网格 × 平台 110 星 × 8 小时。

分别记录：轨道传播、几何、窗口压缩、矩阵/bitset 构造、贪心、ILP、秒级复核、峰值内存。对任务②比较：

```text
通用 y 模型
x-only 全部约束
x-only 去重约束
x-only 按需约束
```

依据实测而不是感觉决定默认策略。

## 13. 分阶段开发顺序

### 阶段 A：冻结语义和测试数据

- 获取/固化 11 城市边界；
- 定义 Profile 和缺失参数处理；
- 确认观测次数默认采用 `satellite_pass`；
- 固定绍兴 8 小时基准请求和期望字段。

### 阶段 B：5 km 网格模块

- 新增 `pyproj`；
- 实现裁剪单元、面积权重、代表点和缓存；
- 完成面积与可复现性测试。

### 阶段 C：逐点机会模块

- 从简单统一姿态 Profile 开始；
- 产出逐星逐网格窗口；
- 再接矩形光学和 SAR；
- 修正复杂路径的逐点约束和统一足印语义。

### 阶段 D：区域指标

- 先实现不选星的全池评估；
- 输出 q、次数、MRT、ART、面积达标率和面积—时间平均；
- 与人工小例子核对。

### 阶段 E：区域选星

- 任务② `K=1` 的集合覆盖模型优先，因为最贴近当前讨论；
- 加严格贪心、90% 反向删除、ILP；
- 再依次完成任务①、③、④、⑤；
- 小规模穷举验证精确任务。

### 阶段 F：FastAPI 最小 demo

- 一个城市；
- 少量可见光卫星；
- 固定 Profile；
- 同步接口；
- 无数据库、无内网穿透；
- 验证“JSON 参数 → 逐点窗口 → 严格/90% 选星 → JSON 摘要”。

### 阶段 G：平台联调与部署

- 取得 skill 注册样例、`script_id` 位置和调用协议；
- 根据平台时限决定同步/异步；
- Docker 化并在 Linux 做冒烟测试；
- 配置鉴权、HTTPS、日志、持久缓存和结果保留策略；
- 最后做聊天到报告的端到端验收。

## 14. 开工前仍需明确的三项数据/接口语义

数学和算法主线已经闭合，但以下字段不能靠程序猜：

1. **单元时间覆盖率默认阈值**：平台现有 `coverage_target_pct=100` 是空间累计目标，不能直接当新 skill 的 $q_0$。首版可把它设为必填，或由任务 Profile 明确给出。
2. **观测次数口径**：建议默认逐卫星过境机会 `satellite_pass`；若老师/平台要的是合并后的连续可观测段，应改用 `union_window`。
3. **平台城市边界坐标系**：必须确认边界是 WGS84、CGCS2000、GCJ-02 还是其他坐标系，并冻结转换方法和边界哈希。

这三项不阻碍先搭建模块和人工测试数据，但在正式生成平台城市网格或公开 skill 输入协议前必须确认并写入接口说明。
