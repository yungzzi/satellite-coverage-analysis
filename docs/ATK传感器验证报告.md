# ATK 复杂传感器覆盖窗口验证报告（2026-08-13）

> 目的：回答"复杂传感器算出来的东西，还是不是我们熟悉的覆盖窗口？有没有新的输出方式/新指标？
> 约束会不会带来新的输出方式和指标？"
> 方法：三层验证（输出形态 / 退化一致 / 物理合理）+ 第一层口径对拍（仰角约束 vs 自研 min_elevation）。
> 复现脚本：`experiment/atk/validate_sensor_windows.py`（主）+ `validate_sensor_worker.py`（单场景 worker）。

## 1. 环境与样本

- ATK 4.0.1-rc.5（20260629 构建），**无头 Connect 模式**（`ATK.exe --headless --new-scenario --port 6655`），
  ImportTLEFile 批量导入（纯两行格式），传感器用 `New + Define`，资产逐条 `Cov ... Asset ... Assign` 登记，
  一次 `CovMulti Access Compute` + 一次 `Cov_RM "Coverage"` 取报告（GBK 解码）。
- 样本：中国遥感全量 303 颗中按 45°×8h 覆盖时长取前 24 颗 LEO（YAOGAN-30/35/36/39/50 等），
  目标点杭州 (30.25, 120.17)，时段 2026-08-12 00:00:00–08:00:00 UTC。
- 自研引擎：`coverage_analysis.py`（10s 粗筛 + 1s 精化），目标口径 = 5km 圆（ATK 为点地面站，
  这是 1~28s 边界偏差的来源）。

## 2. 第一层：口径对拍（SimpleCone + 仰角约束 vs 自研）

| 场景 | ATK 窗口 | 自研窗口 | 匹配 | 最大偏差 | 平均偏差 |
| --- | --- | --- | --- | --- | --- |
| SimpleCone 45（无约束）vs 自研 fov45/min_elev0 | 69 | 70 | 69 | 4.0s | 1.3s |
| SimpleCone 45 + 地面站约束 Min 10 vs 自研 fov45/min_elev10 | 69 | 70 | 69 | 4.0s | 1.3s |
| SimpleCone 45 + 地面站约束 Min 30 vs 自研 fov45/min_elev30 | 69 | 70 | 69 | 4.0s | 1.3s |
| **SimpleCone 80 + 地面站约束 Min 30 vs 自研 fov80/min_elev30（决定性）** | **80** | **77** | **76** | **28.0s** | **2.5s** |

结论：
1. **地面站 `SetConstraint ElevationAngle Min X` 与自研 `min_elevation_deg = X` 是同一口径**。
   决定性证据：80° 大视场下约束把 ATK 窗口从 102 砍到 80（自研从 84 砍到 77），两者 76/77 匹配。
2. **约束必须加在地面站（被覆盖方）上**。加在传感器上时，ATK 的 ElevationAngle 是"目标在传感器
   本地系里的俯仰角"（向下看为负角），`Min 10` 会把全部窗口滤光（实测 0 窗口）——这是两种完全
   不同的物理量，切勿混用。
3. 45° 视场下仰角掩膜几乎不改变窗口（70 不变），因为锥边本身比 10°/30° 掩膜更紧；
   80° 视场才让掩膜生效，这也是选 80° 做决定性测试的原因。

## 3. 第二层：复杂传感器三重验证（场景 A，24 星 × 8h，无约束）

| 变体 | 配置 | 窗口数 | 总覆盖(s) | 最长窗口(s) | 判读 |
| --- | --- | --- | --- | --- | --- |
| S45 | SimpleCone 45 | 69 | 9281.3 | 299.9 | 基准（= 自研 fov45/min0） |
| SC0 | Conical 0 45 0 360 | 69 | 9281.3 | 299.9 | **退化一致：与 S45 逐窗口相同，边界差 0.01s** |
| SC20 | Conical 20 45 0 360 | 88 | 8382.7 | 299.9 | 环形足印：窗口更多更短，100% 落在 S45 内，物理合理 |
| RECT | Rectangular 45 45 | 74 | 13959.5 | 535.8 | 方形足印面积更大，覆盖总时长更长，物理合理 |
| SPIN | SimpleCone 20 + Spinning 0 -90 30 Continuous 6 0 | 20~130（跨次运行不稳定） | ~1300 | ~64~175 | 扫描式指向产生大量短窗口，98% 落在 S45 内；相位未锚定历元，跨实例不完全复现 |
| SAR12 | SAR 12 70 30 30 550000 | 0 | 0 | 0 | **Connect 无头模式计算不出覆盖（见 §5）** |
| SAR90 | SAR 0 90 0 0 550000 | 0 | 0 | 0 | 同上（宽口径也 0） |
| HP30 | HalfPower 0.5 0.35（约 60° 波束） | 0 | 0 | 0 | 同上 |
| HP14 | HalfPower 14.5 1（官方默认，0.72° 波束） | 0 | 0 | 0 | 同上 |

三项检查：
- **输出形态**：覆盖时段报告 = 序号 / 开始 / 结束 / 时长 / 覆盖对象 五列，320+ 窗口 0 行格式异常；
  传感器类型、指向、约束都不改变报告形态。
- **退化一致**：Conical(0,45,0,360) ≡ SimpleCone45（69/69，边界差 0.01s）。
- **物理合理**：Conical 内锥 20° 产生环形足印（窗口更多更短）；Rectangular 面积更大（覆盖更长）；
  Spinning 产生大量短窗口且都在固定锥窗口内。

## 4. 新输出方式 / 新指标（约束会不会带来）

- `Cov_RM` 有 **5 种报告样式**：Coverage（覆盖时段，我们用的）/ Figure Of Merit（品质参数）/
  Satisfaction（有效覆盖）/ Daily Coverage（每日覆盖）/ Coverage Gaps（未覆盖）。
- **16 种品质因子**（FOM）：覆盖时间、访问时长、访问间隔、重访时间、响应时间、多重覆盖、
  访问次数、覆盖间隔次数、精度衰减因子、可见性约束等——都是**独立、可选**的报告，
  不改变 Coverage 窗口报告的形态。实测成功 3 例（地面站口径）：
  - `CoverageTime Compute Total` → 1819.0s
  - `NAsset Compute Maximum` → 1.0（最多同时 1 颗覆盖）
  - `NumberOfAccesses Compute Total` → 9
- **约束**：加约束后输出仍是同一份覆盖时段报告，只是窗口变少/变短，没有新增列或新形态。

## 5. 已知限制与遗留（重要）

1. **SAR / HalfPower：`Define` 命令路线算不出覆盖，但场景 XML 路线完全可用（见 §7.1）**。
   根因是 4.0.1-rc.5 的 Connect `Define` 命令对这两种模式静默失效（SensorQuery 显示参数已写入、
   但 Access/Cov 均"无分析数据"），不是模型本身不行——把传感器写成场景 XML
   （SensorType 5 = SAR、4 = HalfPower）后 Load，SAR 出 6 个窗口、HalfPower 出 5 个窗口，物理合理。
2. **Targeted 指向：Connect 命令全部 NACK**（Tracking/Fixed 各变体，含 Receive/Transmit 模式、
   带不带 `*/`、带不带引号），GUI 中有该功能；命令库文档与实际实现存在缺口，列为遗留。
3. **覆盖定义（区域网格）：计算命令能跑，但窗口报告接口未开放**。`New CoverageDefinition + Cov Grid`
   （LatLonRegion + PointGranularity 0.1°）+ `Cov ... Asset ... Assign` + `Access Compute` 均 ACK，
   但 `Cov_RM */CoverageDefinition/... Access Compute "Coverage"` 与 FOM 报告都 NACK。
   点目标（地面站）路径完全可用；区域目标建议 GUI 验证或走"网格点转多地面站"近似。
4. **Spinning 相位不稳定**：同一命令跨 ATK 实例窗口数 20~130 不等（旋转初相未锚定历元），
   需要显式相位控制（Offset 参数/固定起转时刻）才能复现；不影响"输出形态仍是窗口"的结论。
5. **工程性踩坑记录**（已写进脚本注释与实验记录 §10）：
   `CovMulti/Assets` 路径不能带引号；逐条 Assign 比批量 Assets 可靠；ATK 是启动器+子进程结构，
   收尾必须按端口杀进程树；ATKConnect 连接被杀会带崩同进程 Python Winsock，多场景必须
   worker 子进程隔离。

## 6. 结论

- 复杂传感器（Conical/Rectangular/Spinning）与约束（地面站仰角）**输出的仍然是标准覆盖窗口**，
  可以直接喂给现有选星模块；退化一致与物理合理性均通过。
- "新指标"确实存在（FOM/每日/间隔等 5 种报告样式），但都是**独立可选**的，不影响 Coverage 窗口。
- **SAR/HalfPower 可用，但必须走场景 XML 路线**（SensorType 5/4 + Load），`Define` 命令路线
  在这版本对这两种模式静默失效；**Targeted 指向在 Connect 仍不可用**（所有语法变体 NACK，GUI 有）。
  若老师要求必须支持 Targeted，需向官方确认命令缺口或升级版本。


## 7. 追加验证（2026-08-13 下午：SAR/HalfPower 突破 + 性能/端口/Spinning/覆盖定义/地面站）

### 7.1 SAR / HalfPower 突破：场景 XML 路线（关键发现）

- 方法：Connect 建场景（导入 TLE + 建 SimpleCone 传感器 + 地面站）→ `Save / * "xxx.atk"`
  → 手改 XML 的 `<SensorType>` 与 `<Pattern>` 块 → 新实例 `Load / Scenario "xxx.atk"` → 计算覆盖。
- **SensorType 索引实测**：0=SimpleCone、3=Conical、4=HalfPower、5=SAR（文档列表顺序与实现有偏移，
  之前按文档猜 4=SAR 是错的）。SAR 块字段：`<MinElevAngle>/<MaxElevAngle>/<FwdExclAngle>/<AftExclAngle>/<Altitude>/<TrackParentAlt>`。
- **结果（1 颗 LEO，2026-08-12 8h，杭州点）**：
  - SAR(12°–70°, 前后排除 30°, 550km)：**6 个窗口**；其中 01:06–01:21 那次过境被拆成
    455s+395s 两段（MaxElev 70° 排除近天顶 → 条带中间有孔洞，物理完全合理）；
  - HalfPower(0.5GHz/0.35m ≈ 60° 波束)：**5 个窗口**，时长 759–805s，与 SimpleCone60 量级一致；
  - 对照 SimpleCone45：5 个窗口（XML 路线与 Define 路线一致）。
- **结论**：SAR/HalfPower 模型本身没问题，是 `Define` 命令在 4.0.1-rc.5 对这两种模式静默失效
  （又一个"像 TLE 导入"的通道问题）。批量工具要支持它们，需把传感器配置改为
  "Save→改 XML→Load" 路线（当前 worker 用 Define，暂不支持，列入下一步）。

### 7.2 性能：传感器变体数量线性影响耗时

- 实测（24 星 × 8h × 杭州点，worker 全流程墙钟）：1 变体 6.1s（该次 ATK 随机堆损坏崩溃，参考值）；
  9 变体 15.3s、320 窗口。约每颗星每多一个变体 +0.4s（New+Define+Assign 命令往返）。
- 几百颗真实遥感规模多几个变体影响很小；几千颗时传感器创建命令数仍是主要瓶颈（已知）。

### 7.3 端口：已改动态分配，避免与别的软件冲突

- 原 `runner.py` 是 `6655 + i` 硬编码递增；现改为 `pick_free_ports()`（bind 探测当前空闲端口），
  `validate_sensor_windows.py` 同样按场景动态取端口。若 6655 被占用会自动往后找空闲端口。

### 7.4 Spinning 相位：实例内确定，跨实例落在"离散相位状态"（完整控制变量实验）

同一 XML（SimpleCone20 + Spinning 0 -90 30 Continuous 6 45，ThetaZero=45）反复实验：

| 实验 | 结果 |
| --- | --- |
| 同一实例连续算两次 | 逐窗口完全一致（无随机） |
| 同一实例 Load 两次 | 完全一致 |
| 分析时段 00–08h → 02–10h 平移 | 共同时段窗口毫秒级吻合（相位锚定场景内固定参考） |
| ThetaZero=0/45/90/180 | 窗口模式都不同（参数生效，只做相对偏移） |
| 两实例并行同时启动（差 2s） | 7 vs 8 窗口（与墙钟无关） |
| 连接后严格校验场景身份（卫星/传感器/指向 Offset=45）+ 4 个干净实例 | 3/3/7/8 窗口，仍不一致 |
| XML 显式 AttitudeDefine（VVLH/ECF速度/ECI速度/Fixed） | 类型改变窗口模式；**显式 VVLH 的 4 个干净实例仍落在两个离散状态**（实例1&3=9 窗口毫秒级一致，实例2&4=3 窗口毫秒级一致） |
| 去掉 Load 后的 SetAnalysisTimePeriod（微控制） | 仍落在 8/7/3/3 不同状态 |

最终结论（所有可控参数逐一排除，2026-08-14 上午追加）：
- 从 AtAttitude.dll 提取到**真实有效的姿态类型名**：`AttitudeVVLH`/`AttitudeVVLHCBI`/`AttitudeVVLHCBF`/
  `AttitudeEcfAligndVel`/`AttitudeEciAligndVel`/`AttitudeFixedInCBF` 等（此前猜的 AttitudeSatelliteVVLH
  无效会被静默忽略）。改用正确的 `<AttitudeDefine Type="AttitudeVVLH"/>` 后 GetAttitude 确认
  "Nadir alignment with ECI velocity constraint"（姿态已显式确定）。
- 但**显式 VVLH + ThetaZero=45 + 星历步长 1s + SetAnimation CurrentTime 锚定时钟**的 4 个干净实例，
  窗口仍落在不同状态（如 3/11/7/3、3/3/8/8、3/3/3/7）——姿态、星历采样、时钟、初始偏差角、
  实例卫生全部排除，**相位差异来自 ATK 内部未暴露的实例级初始化**（疑似旋转相位基准值未初始化）。
- 实用缓解：**同一实例内（含同实例重新 Load）相位保持不变、结果毫秒级自洽**——生产上应使用
  单个长驻实例跑完整分析（不要为每个场景/分片重启实例），保证一次分析内部自洽；跨实例
  复现需 GUI 测试（GUI 初始化路径可能不同）或官方反馈。

### 7.5 覆盖定义（区域网格）报告：Connect 无接口（GUI 可用）——含品质因子深挖

- 用户提示 GUI 教程里覆盖定义必须添加品质因子才有分析结果。核实官方《11-区域覆盖案例》：
  操作流程确实是"创建覆盖定义（网格）→ 创建品质因子（选择覆盖定义1）→ 类型=覆盖时间/计算=每日最大
  → 区域覆盖工具计算 → 覆盖时段报告 + 品质因子报告"。
- Connect 实验（四轮，逐项排除）：
  a) 自建 CD + `New / */CoverageDefinition/CD1/FigureOfMerit FOM1`（子对象）+ `Cov FOMDefine` 配置
     + `Cov Interval` + `Cov Asset Assign` + `Cov Access Compute` → 创建/配置/计算都 ACK，
     但 `Cov_RM CD ...` 全部样式 NACK；`QuickReport_RM GetList` 为空；
  b) 直接 Load 官方示例 `11-区域覆盖案例/区域覆盖案例.atk`（含品质因子1，FomName=2/Compute=19/Enable=1）
     → 中文对象名导致 Connect 命令 NACK（Rename 也 NACK，中文名是 Connect 命令解析的坑）；
  c) 把官方示例 XML 的中文名改成 ASCII（覆盖定义1→CD1、品质因子1→FOM1、卫星2→SAT2）后 Load
     → 全部对象正常加载，Assign/Compute ACK，但 `Cov_RM`（Coverage/Coverage Gaps/Satisfaction/
     Daily Coverage/FOMDefine 全部样式）、`CovMulti Objects CD`、`CovMulti_RM` **全部 NACK**；
  d) 二次开发案例目录确认无"区域覆盖"案例（只有地面站覆盖案例）；综合案例《灾害监测星座》
     的区域覆盖步骤全为 GUI 操作（右键→区域覆盖→计算→报告模板）。
- 结论：**品质因子是 GUI 区域覆盖出结果流程的一部分（确认），但 Connect 命令库确实没有开放
  覆盖定义的窗口/报告接口**——不是品质因子缺失的问题。品质因子对象本身可创建配置（命令见上），
  但报告命令只对地面站等对象有效。
- 替代：区域网格转点阵地面站（每个网格点一个 Facility，走 `Cov_RM */Facility/...` 报告路径），
  或多地面站近似目标圆；区域目标如需精确网格报告，只能 GUI 或等官方开放接口。

### 7.6 地面站：只能是点，没有半径

- Facility（地面站）就是点目标（Geodetic 经纬高），**没有半径概念**。自研的"5km 圆目标"与 ATK
  点地面站的差异正是对拍 1~28s 边界偏差的来源。
- 要表达"点+半径"：a) 覆盖定义小网格（GUI 可用，Connect 无报告接口）；b) 多地面站点阵近似圆
  （Connect 可用，报告走 facility 路径）；c) 保留自研引擎做圆目标、ATK 做复杂传感器验证。

### 7.7 地面站报告接口出处（用户询问）

- 文档位置：`ATK安装目录/Help/html/二次开发教程/2-二次开发CONNECT模式/6-Connect对象命令库/工具/覆盖性命令/Cov_RM Access.html`
  —— 在"工具→覆盖性命令"分类下（不在"地面站"或"覆盖定义"分类下，所以容易找不到）。
- 语法：`Cov_RM <ObjectPath> Access Compute "<ReportStyle>" [{TimeIntervals} | UseObjectTimes]`，
  示例：`Cov_RM */Satellite/Satellite1 Access Compute "Coverage" "14 Mar 2024 00:00:00.000" "15 Mar 2024 00:00:00.000"`。
