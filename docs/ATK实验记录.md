# ATK 二次开发实验记录（codex/atk-poc）

> 本文件记录 ATK 二次开发 PoC 的问题、进展与结论，供后续决策与交接使用。
> 最后更新：2026-08-12

## 1. 分支与工作树说明

- 实验分支：`codex/atk-poc`，独立工作树：`卫星覆盖性分析-atk-experiment`
- 与主工程的关系：main 分支不受影响；本工作树是实验的"主战场"。
- 开发规范（建议）：
  - 本工作树内按功能建子分支（如 `codex/atk-cpp-helper`），完成后 `--no-ff` 合并回 `codex/atk-poc`；
  - 实验结论成熟后，在**主工作树**（卫星覆盖性分析）里把 `codex/atk-poc`（或功能分支）合并进 main；
  - 合并是"分支"操作，与在哪个工作树无关，但习惯在目标分支所在的工作树里执行。
- 参考归档：`reference/atk-doc/`（官方文档离线版，已 gitignore）、`experiment/atk/`（测试脚本）。

## 2. 背景与验收标准

老师要求复杂传感器建模（姿态改变后感知范围变大；传感器类型/指向扩展），评估 ATK 二次开发是否可行。
四条验收标准：
1. 链路跑通：Python 3.13 内建场景→导入 TLE→传感器→点目标覆盖→拿到窗口
2. 正确性对拍：与自研引擎/N2YO 同一星同一时段，窗口起止偏差秒级
3. 性能三档：100 / 1000 / 10766 颗分别计时（1000 颗跑几十分钟则否决）
4. 许可确认：导师负责（用户拍板跳过）

## 3. 环境事实

- ATK 4.0.1-rc.5（20260629 构建），安装于 `D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5`
- 机器有 VS2022 Community（cl.exe 可用）
- Python 3.13 可导入两个模块：
  - Component 模式：`ATKComponentPythonModule.py` + `_ATKComponentPythonModule.pyd`（339 个类）
  - Connect 模式：`ATKConnectModule.py` + `_ATKConnectModule.pyd`（atkOpen/atkConnect/atkClose）
- Connect 服务端口：**6655**（不是 8080）；需要 ATK 界面打开并点"集成→客户端"后才监听

## 4. Component 模式实测（嵌入式库，无界面）

- 单对访问正确：KUANFU 02B 2 窗口 03:43:00.055~03:43:26.911，与自研引擎偏差 <0.1s
- **多对访问串数据 bug**：同一场景内所有 `IAtkAccess` 都返回第一个对象对的结果（ClearAccess/RemoveAccess/调整顺序均无效，已确认是封装层共享数据问题）
- Python 模块**缺少**官方多对象覆盖类：`IAtkObjectCoverage` / `ICvAssetListCollection` / `ICvAssetListElement`（这三个只在 C++ 头文件里有；Java 封装也没有）
- 每场景一星的替代方案：第二个场景创建挂起，不可用
- 结论：**Python Component 模式不可用于多星覆盖**

## 5. Connect 模式实测（网络客户端 = 官方 Python SDK）

### 5.1 关键操作要点（踩过的坑）

- 建场景前若已有场景：`Unload / *` 会弹 GUI 确认框，卡住后续 `New`；干净会话直接 `New` 即可
- 建星用 `New / Satellite <名>` + `SetState ... TLE "l1" "l2"`（`ImportTLEFile` 会把星起成 `tle-xxxx` 乱名，不可用）
- 计算覆盖前必须 `Animate * Reset`
- 报告查询从**地面站侧**：`Cov_RM */Facility/<名> Access Compute "Coverage" ...`
- 报告是 **GBK 编码**，SWIG 返回带 surrogate 字符，需 `encode("utf-8","surrogateescape").decode("gbk")` 修复
- 传感器：`New / */Satellite/<名>/Sensor Sensor1` + `Define ... SimpleCone <角度>`
- 批量计算：`CovMulti / Objects */Facility/<名>` + `CovMulti / Assets <全部传感器，单命令>` + `CovMulti / Access Compute ...`

### 5.2 正确性（已通过）

- 45°×24h：ATK 51 窗口 vs 自研引擎 51 窗口，全匹配，平均起止偏差 ~1s（最大 7s，出现在 3.5s/11s 短窗口，为"目标圆重叠 vs 点判定"边界差异）
- 200 颗×8h（10°）：ATK 22 窗口 vs 自研引擎 23 窗口，22/22 匹配（自研多一个 9 秒短窗口）
- GEO 卫星（GAOFEN-4、GAOFEN-13 01）全天 28800s 覆盖，物理正确
- 合计 **73/73 窗口匹配**，偏差 ~1s

### 5.3 性能（CovMulti 批量）

- 200 颗×8h 批量计算：**0.59s**（线性）
- 推算：1000 颗 ≈ 3s，10766 颗 ≈ 30s（与自研引擎 27.6s 相当）
- 建星+传感器配置：~0.1s/颗（100 颗时）→ ~0.35s/颗（800+ 颗时，随场景变大变慢）；1000 颗约 5 分钟
- 逐星逐个 `Cov Access Compute`（非批量）是 O(N²)：373 颗 177s，不可用于大规模

### 5.4 规模化障碍

- `CovMulti / Assets` 单命令：200 条（8.4KB）成功；303 条（12.5KB）静默失效（0 窗口）；1000 条（42KB）压崩 ATK
- 分批 `Assets` 调用不可靠（第二批后结果错乱/为空）
- 场景状态易损坏：同一 200 颗集合在另一场景重建后 CovMulti 计算 0 窗口
- 大命令/高压操作会崩溃 ATK，需手动重启
- 每会话都要手动开 ATK + 客户端窗口（部署摩擦）

## 6. 结论与路线

| 路线 | 说明 | 评估 |
|---|---|---|
| A. C++ 辅助程序 | 用 C++ 调 Component 完整 API（IAtkObjectCoverage），编译 exe 由 Python 调用 | **推荐**：无头、多对象正确；工作量 1~2 天；唯一未知是数据目录与数据提取机制 |
| B. 回退自研 | 继续自研精细传感器建模 | 可行但建模+验证麻烦；不过 ATK 73/73 对拍已给自研引擎做了交叉验证 |
| C. 接受 ≤300 颗 | 用 Connect 模式 + CovMulti | 若实际需求就是几百颗可考虑，但工程坑多 |

## 7. 遗留问题 / 下一步（C++ 路线）

- [ ] C++ 无头数据目录配置（官方文档：把 AstroData 1.2GB 复制到 `IntegratingWithATK/component/bin` 旁，或找配置机制）
- [ ] C++ `IAtkObjectCoverage` 的数据提取机制确认（头文件里 ICvAssetListElement 只有状态，窗口数据可能走报告 API）
- [ ] 1000 颗资产注册方案（若 C++ 同样受限，需要另找路径）
- [ ] 构建脚本（VS2022 cl.exe / VSCode 终端即可，无需开 VS IDE）
## 8. 重要补充：Component C++ API 的传感器与区域能力上限（2026-08-12 调研）

### 8.1 传感器（Component C++ 头文件实测）

- 模式枚举 `ESnPattern` 只有两项：`eSnSimpleConic`（圆锥）、`eSnRectangular`（矩形）
- 指向枚举 `ESnPointing` 只有一项：`eSnPtFixed`（固定，支持欧拉角/四元数）
- **不存在**：Conical（复杂圆锥）、HalfPower（半功率）、SAR；Spinning（旋转）、Targeted（目标跟踪）、GrazingAlt（入射高度）、External（外部文件）
- 卫星姿态 `EVeProfile` 只有标准廓线：Nadir 对齐（ECF/ECI）、速度对齐、太阳对齐——无任意旋转/指向目标
- 复杂类型与动态指向**只在 Connect 命令库（Define/Point 命令）和 GUI 里**存在（AtConnector.dll 含 conical/halfpower/sar/spinning/targeted/grazingalt/external 解析字符串）
- **结论：Component C++ 不满足老师"复杂传感器/姿态改变感知范围"的要求**

### 8.2 区域（Component C++）

- 点目标：Facility（地面站，AssignGeodetic）
- 区域：CoverageDefinition 网格，边界类型 Global / LatLonRegion（经纬度矩形）/ Lat / LatLine / LonLine；分辨率 Area / Distance / LatLon
- **无任意多边形**（AreaTarget 不在 Component 头文件里，仅 Connect/GUI 有）
- 与我们的对比：点+半径（5km 圆+足印重叠）→ ATK 用点地面站或小矩形近似；GeoJSON 任意多边形 → ATK 只能外接矩形网格
- 结论：区域能力"部分类似"（都有点目标和网格），但缺少任意多边形与目标圆语义

### 8.3 对路线的影响（重要）

- A 路线（C++ 辅助程序）：**只能用简单圆锥/矩形 + 固定指向**，满足不了老师复杂传感器建模的核心诉求
- 全部传感器类型只有 Connect 模式（有 GUI 依赖 + 规模化问题）或自研才有
- 待拍板：与老师确认所需复杂模型清单后，在"Connect 模式（接受 GUI+规模限制）"与"自研扩展"之间选择

### 8.4 多层验证（2026-08-12 追加，排除"版本/封装遗漏"疑问）

为排除"是不是没找对/版本旧"的疑问，做了五层交叉验证，结论一致：
1. 全 ATK 安装仅 355 个 .h（都在 IntegratingWithATK/component/include），全量搜 Conical/SAR/HalfPower/Spinning/Targeted/GrazingAlt/External 零命中
2. `ICEnum.h`：ESnPattern 仅 {SimpleConic, Rectangular}；ESnPointing 仅 {Fixed}
3. `IAtkObject.dll` 导出符号：只有 ISnSimpleConicPattern / ISnRectangularPattern / ISnPtFixed（内部类也仅 AsCSensorSimpleCone / AsCSensorRectangular 两个）
4. Python（339 类）与 Java（517 类）封装同样只有这两种模式
5. 官方文档（覆盖到 v4.1）：Component 敏感器类只提供 SetPatternSimpleConic / SetPatternRectangular / SetPointingFixedEuler / SetPointingFixedQuat

复杂类型（Conical/HalfPower/SAR 与 Spinning/Targeted/GrazingAlt/External）只存在于 Connect 命令层（AtConnector.dll 解析）与 GUI。
结论：不是版本问题，是 Component SDK 的公开 API 缺口（4.1 文档同样）；未来版本可能补齐（ATK 4.0 已到 29 模块、迭代很快），但不能依赖。

## 9. 无头 Connect 大规模实测：TLE→覆盖 全链路验证 + 规模上限绕开（2026-08-12）

### 9.1 官方无头参数发现（重大）

`ATK.exe --help` 官方用法（4.0.1-rc.5，`ATKConsole.exe --help` 同）：

```
Usage: ATK [options] ... scenario
  -h, --help / -v, --version
  -p, --port <int>          connect 端口（默认 0）
      --new-scenario        启动时新建场景
      --hide-startup / --hide-welcome / --no-crash-report
      --no-connect          禁用 connect
      --no-ui / --headless  无界面模式（headless 等价 no-ui）
      --language / --theme / --experimental
      --uiauto-file <文件> / --uiauto-action run|exit|none   官方 UI 自动化机制
      scenario              打开场景文件
```

实测：`ATK.exe --headless --new-scenario --port 6655` 启动后 **6655 直接监听，无需 GUI、无需"集成→客户端"**，Python Connect SDK 可直连。这消除了 Connect 路线最大的部署摩擦。另有 `ATKConsole.exe -c "<脚本>"` 或 `.atks` 脚本文件入口（未验证）。

### 9.2 TLE→覆盖 端到端命令序列（无头 Connect，全部成功）

1. `New / Scenario` → `SetAnalysisTimePeriod * "t0" "t1"` → `Animate * Reset`
2. 每颗星：`New / Satellite <名>` → `SetState <路径> TLE "行1" "行2" TimePeriod "t0" "t1"`
3. 每颗星：`New / */Satellite/<名>/Sensor S45` → `Define <路径> SimpleCone 45`
4. `New / Facility Hangzhou` → `SetPosition <路径> Geodetic 30.25 120.17 0.0`
5. `Animate * Start` → `Animate * Reset`（星历惰性生成，必须先跑仿真再计算）
6. 逐条：`Cov */Facility/Hangzhou Asset <传感器路径> Assign`
7. 单次：`Cov */Facility/Hangzhou Access Compute "t0" "t1"`
8. 单次取报告：`Cov_RM */Facility/Hangzhou Access Compute "Coverage" "t0" "t1"`（GBK 解码）

**"无分析数据"排查真相**：早先 NORAD 40961 覆盖为空并非 bug——自研引擎确认该星在 2026-08-12 00:00–08:00 对杭州 45° 圆锥**物理上 0 窗口**（24h 仅 21:37 一次过境）。换真正过境的 4 颗吉林星（GAOFEN 3E / 3G / 2F、KUANFU 02B 2）后窗口与自研引擎偏差 <1s，与 GUI 模式对拍结论一致。

### 9.3 大规模喂数据实测（无头，45° 圆锥 × 杭州 × 8h）

| 规模 | 建星+传感器 | 逐条 Assign | 单次 Compute+报告 | 窗口（覆盖星数） | 报告 |
|---|---|---|---|---|---|
| 300（遥感） | 14.8s | 4.5s | 1.25s | 181（129） | — |
| 311（遥感全量） | ~17s | ~5s | ~1s | 190（133） | — |
| 1000（active.tle 前 1000） | 55.3s | 60.1s | 26.2s | 543（489） | 62KB |
| 2000（active.tle 前 2000） | 132.2s | 524.3s | 106.3s | 1038（954） | 120KB |

**无崩溃、无静默失效**。瓶颈是 Assign 随场景规模近似线性变慢（2000 条约 8.7 分钟）。

### 9.4 关键机制：Assign vs Assets（崩溃根因与绕开）

- 覆盖模型：**Objects**（被覆盖方，如地面站）← **Assets**（覆盖源，如卫星上的传感器）。
- **Assign**：把单个资产登记到对象，追加语义：`Cov <ObjectPath> Asset <AssetPath> Assign`，单命令几十字节。
- **CovMulti / Assets**：批量登记，要求"全部传感器路径拼成一条命令"。ATK 命令解析有未文档化长度上限：200 条 8.4KB 成功、303 条 12.5KB 静默失效（0 窗口）、1000 条 42KB 压崩进程；分批调用互相覆盖/错乱（之前记录 5.4 的根源）。
- **绕开方案**：放弃 CovMulti / Assets，改逐条短命令 Assign（N 条），全部登记后**只发一次** `Cov Access Compute` + 一次 `Cov_RM`。2000 条 Assign 实测成功。报告是**累加**的（Assign 只增不减），所以无需"分批计算后叠加"——全量一次算即可，分批反而会重算前面资产、报告重复。

### 9.5 正确性验证（311 颗遥感全量 × 杭州 × 8h × 45°）

ATK 无头 vs 自研引擎：覆盖星数 **133/133 一致**；窗口 **190 vs 192**；仅 6 颗星 ±1 个短窗口差异（秒级边界 / 目标圆 vs 点判定，同 GUI 对拍结论）。逐颗对比数据在 `%TEMP%\atk_311.json` / `own_311.json`。

### 9.6 与自研引擎性能对比

| 场景 | 自研引擎 | ATK 无头 Connect |
|---|---|---|
| 303/311 颗 × 8h | 覆盖分析约 1~2s（10s 粗筛+1s 精化） | 建星+Assign 约 22s + Compute 1.25s |
| 1000 颗 × 8h | 秒级 | 约 2.4 分钟（建星 55s + Assign 60s） |
| 2000 颗 × 8h | 秒级 | 约 12.7 分钟（Assign 8.7 分钟） |
| 10766 颗 × 1 天 | 27.6s | 未测（推算 Assign 40+ 分钟，不现实） |

结论：ATK **纯计算**速度与自研同量级（300 颗 1.25s vs 自研 1~2s），但"建星 + Assign"的命令往返开销使**总流程慢 1–2 个数量级**；自研在大规模/快速迭代上完胜。ATK 的价值在于**复杂传感器建模**（SAR/Conical/Spinning 等仅命令层与 GUI 有）和交叉验证。

### 9.7 遗留事项

- `Point Targeted` 语法未调通（NACK；`Facility/Hangzhou` 与 `*/Facility/Hangzhou` 两种路径写法均试过）
- 复杂传感器（Conical/SAR/Spinning）尚未与自研做窗口对拍（几何语义待验证）
- `ATKConsole` .atks 脚本路径未验证（可能绕开逐条命令往返、进一步提升性能）
- 4000+ 颗未测；Assign 线性变慢是主要瓶颈，可"按星座拆多场景并行 + 合并输出"缓解
- 进程清理注意：kill 后必须确认 ATK 进程退出 + 6655 释放，否则下次测试连到残留实例产生 NACK 假象（本次踩坑两次）
### 9.8 建星/登记加速实测（2026-08-12 追加）

**登记为什么慢（根因分解）**
- 单条命令往返固定开销约 11.6ms（GetATKVersion 实测），逐条命令 N 条就是 N×11.6ms 起步。
- `Cov ... Asset ... Assign` 单条在 300 颗场景约 15ms、1000 颗约 60ms、2000 颗约 262ms——**随场景总规模变慢**（ATK 端对象树查找开销，Assign 条数与场景规模共同放大）。
- `CovMulti / Access Compute` 每批有固定初始化开销：2000 颗场景每批约 40s（与批内资产数关系不大），8 批 = 330s。
- 结论：命令往返 + ATK 端查找/初始化是两大瓶颈，Python 侧无法优化，只能减少命令条数/批次。

**已验证的加速手段**
1. **NewMulti 批量建星**：`NewMulti / */Satellite 100 SAT1 SAT2 ...`，300 颗 1.6s（逐条 15s，快约 8 倍）；2000 颗 15.3s（逐条 132s）。
2. **CovMulti 分批 Assets + 每批 Compute**：`CovMulti / Objects */Facility/X` 一次，然后每批 `CovMulti / Assets <≤250 条>` + `CovMulti / Access Compute`。**Assets 是"覆盖"语义（每次替换集合）**，但 `Cov_RM` 报告缓存跨批累计——每批后 Compute，最后一份报告 = 全集窗口（300 颗 3 批后报告 181 窗口/129 资产，与全量一致）。绕开单命令长度上限（>300 条失效/1000 条崩）。
3. 已排除：通配符 Assign（NACK）、ImportTLEFile（无头 NACK，含无中文路径）、多行/分号命令（静默失败）、atkExecuteScript（SWIG std::string& 无法从 Python 构造）、ATKConsole（沙箱用户读不到数据目录注册表；真实用户桌面下可能可用，未验证）。
4. 卫星本体可直接作资产（`CovMulti / Assets */Satellite/SATx`，无传感器也能出窗口）——但**口径未明**（窗口显著长于 45° 圆锥，疑似默认传感器/全向），替代传感器前必须先做口径对拍。

**加速后性能（2000 颗 × 8h × 45° 圆锥，杭州）**
| 阶段 | 原始逐条 | 加速后 |
|---|---|---|
| 建星 | 132s（逐条 New） | 15.3s（NewMulti） |
| SetState + 传感器（6000 条） | 132s | 132s（无批量入口） |
| 资产登记 + 计算 | Assign 524s + Compute 106s | 分批 8 批 330s |
| 报告 | 106s（含 compute） | 77.6s |
| **总计** | **762s ≈ 12.7 分钟** | **555s ≈ 9.3 分钟** |

300 颗（真实遥感规模 311 颗）：**16.5s 全流程**（NewMulti 1.6s + SetState/传感器 10.8s + 分批计算 4.1s），窗口 181/129 与全量一致。

**剩余瓶颈与可选优化**
- SetState 2000 条 + 传感器 4000 条共 6000 条逐条命令（132s）：无官方批量入口，是当前最大可优化项（除非接受卫星本体资产跳过传感器，但需先定口径）。
- CovMulti Compute 每批固定开销（2000 颗场景约 40s/批）：ATK 端行为，无法绕过；可缩时间窗、或按星座拆多 ATK 实例并行最后合并。
- Cov_RM 报告生成（120KB 约 78s）：ATK 端报告引擎开销；可试 Access 报告类型是否更轻。
- 结论：几百颗真实遥感规模已足够快（~17s）；2000 颗约 9 分钟可接受但不适合快速迭代；若坚持几千颗，推荐多实例并行 + 合并输出。
### 9.9 批量设 TLE 的最终探索：atkExecuteScript 打通但大规模不可用（2026-08-12 追加）

**目标**：找到"批量设 TLE/建传感器"的入口，减少 2000 颗时 6000 条逐条命令（约 132s）的开销。

**已排除**：ImportTLEFile（无头 NACK，含无中文路径）、多行/分号命令（静默失败）、通配符 Assign（NACK）、ATKConsole（沙箱用户读不到注册表数据目录；真实用户桌面可能可用，未验证）、NewMulti 建传感器（无跨对象批量）。

**突破：ctypes 直调 ATKConnectorDll.dll 的 atkExecuteScript**
- 官方 Python SDK 是 ATKConnectorDll.dll 的 SWIG 包装，SWIG 把 `atkExecuteScript(std::string&, std::string&)` 的参数堵死（Python 侧无法构造 std::string）。用 ctypes 按 MSVC x64 std::string 布局（SSO 16B+size+cap；长字符串 ptr+size+cap）手工构造，成功调用 `?atkExecuteScript@@YAHAEAV?$basic_string@...@@0@Z`。
- 输入是**连接器表达式**（ATK 脚本子集）：逐行 `atkOpen('127.0.0.1', 6655)` / `atkConnect(conID, 'New', '/ Scenario X')` / `atkClose(conID)`；**字符串必须单引号**（双引号会拆词报错）；**不支持 for 循环**（报 Unexpected ATK connector expression），只能展开；返回值在 out 参数里（GBK，需 surrogateescape 解码）。
- 300 颗完整流程（NewMulti + 300 SetState + 600 传感器 + 覆盖 3 批 + 报告）展开 920 行/132KB：**单次调用 18.9s，报告 21KB 正常返回**（与 Python 逐条 16.5s 相当——底层仍是逐条 TCP，Python 层开销占比小）。
- **2000 颗展开 6047 行/880KB：30 分钟未完成（超时）**——脚本引擎解释超大输入开销爆炸，大规模不可用。

**结论（最终性能口径）**
| 方案 | 300 颗 | 2000 颗 |
|---|---|---|
| Python 逐条（SWIG） | 16.5s | 555s ≈ 9.3 分钟 |
| atkExecuteScript（ctypes 直调） | 18.9s | >30 分钟（不可用） |

批量设 TLE **没有找到比逐条更快的通道**：ATK Connect 协议本质是逐条 TCP 往返（单条约 11.6ms），Python/SWIG 层仅占小头；2000 颗的 6000 条命令（132s）已是下限附近。真正的大头是 ATK 端 CovMulti Compute 分批固定开销（330s）和报告生成（78s），只能靠"多 ATK 实例并行 + 合并输出"或缩时间窗缓解。**几百颗真实遥感规模（约 17s）已足够快，可替代自研；几千颗需并行化。**
### 9.10 重要修正：ImportTLEFile 完全可用（纯两行格式 + 正斜杠路径，无需 Rename）（2026-08-13）

**此前误判**：9.9 节曾记录 ImportTLEFile 无头 NACK 不可用。经用户质疑复查，根因有二：
1. **路径必须用正斜杠**（官方示例 `C:/Users/...`）；反斜杠路径直接 NACK。
2. **TLE 文件必须纯两行格式（无名字行）**：三行格式（带名字行）导入后卫星星历无效（Position 全零、覆盖空）；纯两行格式导入星历正确（Position 与 SetState TLE 一致）。

**完整可用流程（实测 100 颗 4.2s / 2000 颗 8.9 分钟）**
1. TLE 文件整理为纯两行格式（去掉名字行）
2. `ImportTLEFile * "C:/.../xxx.tle"`（一条命令导入全部，2000 颗约 15s）
3. 导入后卫星名是乱名 `tle-<NORAD><密级>`（如 `tle-40961U`），**可带引号直接引用**：
   - 建传感器：`New / "*/Satellite/tle-40961U/Sensor" S45`（`/` 在引号外！之前引号位置错误导致 NACK）
   - 定义口径：`Define "*/Satellite/tle-40961U/Sensor/S45" SimpleCone 45`
   - 登记资产：`Cov */Facility/Hangzhou Asset "*/Satellite/tle-40961U/Sensor/S45" Assign`
4. 无需 Rename（Rename 2000 条实测 O(N²) 不可行：300 颗 172ms/条，2000 颗 30+ 分钟超时）
5. CovMulti 分批 Assets + 每批 Compute（绕开单命令长度上限），报告缓存累计 = 全集

**2000 颗实测（active.tle 前 2000，45° 圆锥 × 杭州 × 8h）**
| 阶段 | 时间 |
|---|---|
| ImportTLEFile | 15.2s |
| 传感器 4000 条（New+Define） | 118.4s |
| CovMulti 分批（8 批） | 323.1s |
| 报告 | 79.7s |
| **总计** | **536s ≈ 8.9 分钟**（窗口 1038，与逐条 SetState 方案一致） |

**对比**：ImportTLEFile 方案（536s）与 NewMulti+SetState 方案（555s）基本持平，略快；正确性一致（1038 窗口）。批量设 TLE 的结论修正为：**ImportTLEFile 是官方推荐入口，文件驱动更简洁；真正的性能大头仍是传感器逐条创建（4000 条 ~2 分钟）与 ATK 端分批计算（~5 分钟）**。
### 9.11 与自研引擎性能对比 + 概念澄清（2026-08-13）

**性能对比（45° 圆锥 × 杭州 × 8h，2026-08-12 00:00-08:00 UTC）**

| 规模 | 自研引擎（10s粗筛+1s精化） | ATK 无头 ImportTLEFile 方案 |
|---|---|---|
| 303 颗中国遥感全量 | 25.5s / 189 窗口 | **12.8s** / 187 窗口（差 2 短窗口） |
| 2000 颗（active.tle 前 2000） | **178.6s** / 867 窗口 | 536s / 1038 窗口 |

结论：**303 颗真实遥感规模 ATK 比自研快约 2 倍**（ATK 内核计算快、无 Python 逐颗开销）；**2000 颗自研快约 3 倍**（ATK 瓶颈：传感器 4000 条 118s + 分批 CovMulti 每批随场景规模的开销 323s + 报告 80s）。2000 颗窗口数差异（1038 vs 867）较大，需后续核对（点 vs 5km 圆目标 + GEO 判定差异，先记录）。

**概念澄清（基于官方文档）**
- **为什么纯两行格式**：官方《从TLE文件》文档明确"支持外部 TLE（**两行根数**）星历文件"；`SetState TLE` 命令参数即 `"<TLECard1>" "<TLECard2>"`（每行必须 69 字符）。TLE 标准本身就是两行，名字行是 CelesTrak 附加显示名，ATK 导入器按两行解析，带名字行会错位 → 星历无效。
- **怎么确定哪颗是哪颗**：导入后乱名 `tle-<SSC><密级>`（如 `tle-40961U` = NORAD/SSC 40961，U=公开）；AllInstanceNames 顺序 = 文件顺序，可建"文件顺序→NORAD→乱名"映射；覆盖报告"覆盖对象"列 = `tle-40961U-S45`，解析前缀即得 NORAD，反查原 TLE 名。
- **SetState 是什么**：Connect 命令库"设置对象状态"命令。`SetState <路径> TLE "行1" "行2"` = 逐颗设置两行 TLE（ImportTLEFile 的单颗版）；另有 SGP4/Cartesian/FromFile 等变体。
- **工作教训**：操作 ATK 前先查官方文档（正斜杠路径、两行格式、引号引用都是文档/示例可查的）。

**更快方案评估（2000 颗）**
- 多实例并行：4 个 headless ATK 各 500 颗 → 每实例场景小、批数少（2 批）、固定开销低，墙钟预估 ~3 分钟，合并输出即可（总 CPU 不变）。
- 跳过传感器（卫星本体作资产）：省 118s，但口径变为默认（非 45° 圆锥），仅"粗覆盖"可用，复杂传感器场景不适用。
- 报告类型/时间窗：缩 8h→4h 计算近减半；报告 Access 类型未测。
- 结论：真实遥感规模（≤303 颗）ATK 已优于自研；几千颗需并行化或接受 ~9 分钟。
### 9.12 三行格式真相 + 4000 颗性能达标（4 实例并行 144s）（2026-08-13）

**为什么 GUI 三行格式能导入且自动匹配名字，Connect ImportTLEFile 不行**
- GUI"从 TLE 文件"（《04-从TLE文件.md》）走的是**标准对象数据库匹配**：解析出 NORAD（SSC）后查 `AstroData/Database/Satellite/atkSatDbAll.sd`，用数据库里的正式名字建星。
- Connect 的 `ImportTLEFile` 命令**不做数据库匹配**：按 TLE 卡流解析，遇到三行格式（名字行）会把名字行当数据，产生 87 个对象（58 颗正常+错位）、星历无效（矩阵实测：LF/CRLF 三行均失败，两行均成功）。
- **解决办法（名字映射在 Python 侧做）**：导入前记录 TLE 文件顺序 → NORAD → 正式名字；导入后 `AllInstanceNames` 顺序 = 文件顺序，乱名 `tle-<NORAD><U>` 含 NORAD；覆盖报告"覆盖对象"列（乱名）→ 反查 NORAD → 替换为正式名字。效果等同"自动匹配名字"，且不依赖 ATK 数据库。

**4000 颗性能（45° × 杭州 × 8h，2026-08-12）**
| 方案 | 耗时 | 窗口 |
|---|---|---|
| 自研引擎 | 339.8s | 1788 |
| ATK 单实例（预估） | ~25-30 分钟 | — |
| **ATK 4 实例并行（每实例 1000 颗）** | **144.3s（2.4 分钟）** | 1962 |

- 用户要求"最多比自研慢 50%"（预算 509.7s）：**实际 144.3s，比自研快 2.4 倍，达标并大幅超出**。
- 4 实例细节：机器 12 逻辑核；每实例 Import 11s + 传感器 2000 条 24s + CovMulti 分批 60-67s + 报告 28-31s；端口 6655-6658 隔离，并行无冲突。
- 窗口差异 1962 vs 1788（ATK 多 174）：主要来自非遥感星（GEO/高轨/星链）的点目标 vs 5km 圆判定差异（303 遥感仅差 2 个窗口，遥感集合一致性良好）；需后续核对。
- 多实例并行是 4000 颗的唯一可行路径（单实例受 CovMulti 每批固定开销+传感器逐条命令限制）。

**4000 颗数据来源**：active.tle 全集前 4000 颗（含星链等，若只要遥感需另配数据源）。
### 9.13 atk_parallel 并行工具完成并验收（2026-08-13，分支 codex/atk-parallel）

**工具**：`atk_parallel/`（runner 调度器 + worker 单片 + tle_utils 三行/两行读取与分片 + report_parser 报告解析）。
- 用法：`python -m atk_parallel.runner --tle <三行或两行TLE> --t0 ... --t1 ... --lat --lon --fov --out-dir`
- 自动：三行转两行（保留名字映射）、动态分片（默认每片 1000）、实例上限 min(核数, 可用内存/1.2GB, 12)、失败重试（≤2 次）、超时看门狗、卫星数校验、缺失片清单、合并输出 merged_windows.csv（NORAD + 正式名 + 窗口）。
- 关键修复：ATK ImportTLEFile **不认中文路径**（分片 TLE 写系统 TEMP）；worker 并行启动而非串行。

**验收（45° × 杭州 × 8h，2026-08-12）**
| 规模 | 分片 | 墙钟 | 窗口 | 备注 |
|---|---|---|---|---|
| 303 遥感（三行输入） | 1×303 | 13.5s | 187 | 名字映射正确 |
| 4000 | 4×1000 | **53.0s** | 1962 | 比自研 339.8s 快 6.4 倍 |
| 16091 全集 | 17 片 4 轮（沙箱内存 6.3GB→5 并行） | **276.4s** | 7440 | 17/17 成功；片 12 首次崩溃后自动重试成功 |

- 每片 1000 颗：40~58s（Import 3-6s + 传感器 20-27s + Cov 8-12s + 报告 3-5s）。
- 健壮性验证：片 12 堆损坏（0xC0000374）→ 重试（尝试 2）成功，无缺失片。
- 用户 16GB 真实机器上 decide_max_inst 会自动给到 min(核数,13,12)；沙箱可用内存 6.3GB 导致只并行 5 片，真实机器会更快（预计全集 3 轮 ~3 分钟）。

**相对自研**：4000 颗 ATK 53s vs 自研 339.8s（快 6.4 倍）；16091 颗 ATK 276s vs 自研线性外推 ~1400s（快 5 倍）。远超"最多慢 50%"要求。
## 10. 复杂传感器覆盖窗口验证（2026-08-13，L1 口径对拍 + L2 三重验证）

> 完整报告见 `docs/ATK传感器验证报告.md`；复现脚本 `experiment/atk/validate_sensor_windows.py` +
> `validate_sensor_worker.py`。样本：24 颗中国遥感 LEO × 8h × 杭州点，2026-08-12 00:00-08:00 UTC。

### 10.1 结论速览

| 项目 | 结果 |
| --- | --- |
| 输出形态 | 所有可计算变体都输出标准覆盖时段报告（序号/开始/结束/时长/覆盖对象），320+ 窗口 0 格式异常 |
| 退化一致 | Conical(0,45,0,360) ≡ SimpleCone45（69/69，边界差 0.01s） |
| 物理合理 | Conical(20,45) 环形足印：88 窗/8382s，100% 落在 S45 内；Rectangular(45,45)：74 窗/13959s 更长；Spinning：大量短窗口（20~130 个跨实例不稳定，相位未锚定历元） |
| L1 对拍 | S45 无约束 vs 自研 fov45/min0：69/70 匹配，最大 4s；地面站约束 Min10/Min30 vs 自研 min10/30：同样 69/70 匹配 |
| **L1 决定性** | SimpleCone80 + 地面站约束 Min30 vs 自研 fov80/min30：80/77，**76 匹配，最大 28s**——证明地面站 ElevationAngle 约束 = 自研 min_elevation 口径 |
| 新指标 | Cov_RM 5 种报告样式 + 16 种 FOM 均为独立可选；实测 CoverageTime=1819s、NAsset Max=1、NumberOfAccesses=9；不影响 Coverage 窗口形态 |
| 区域目标 | 覆盖定义网格计算命令可跑，但 Connect 无窗口报告接口（Cov_RM/FOM 均 NACK）——列遗留，建议 GUI 验证或多地面站近似 |

### 10.2 重要教训（写代码前必看）

1. **仰角约束必须加在地面站（被覆盖方）上**：`SetConstraint */Facility/Hangzhou ElevationAngle Min X`；
   加在传感器上时是传感器本地俯仰角（向下为负），`Min 10` 会把全部窗口滤光（实测 0 窗口）。
2. **CovMulti/Assets 的对象路径不能带引号**（带引号 NACK）；`New/Define/Point/SetConstraint` 带引号正常。
3. **资产登记用逐条 `Cov ... Asset ... Assign` 最可靠**，登记完只发一次 `CovMulti Access Compute` +
   一次 `Cov_RM`；CovMulti/Assets 批量存在命令长度/静默失效坑。
4. **SAR/HalfPower 在 Connect 无头 4.0.1-rc.5 算不出覆盖**（SensorQuery 确认配置成功，
   Access/Cov 均"无分析数据"，宽口径也 0）——最大缺口，待官方确认 GUI 行为或升级版本。
5. **Targeted 指向在 Connect 全 NACK**（所有语法变体），GUI 有该功能，命令库实现有缺口。
6. **ATK 是启动器+子进程结构**：收尾必须 `netstat -ano` 找监听端口 PID 后杀进程树，
   否则残留实例占端口、串场景（本日多次踩坑）。
7. **ATKConnect 连接被杀会带崩同进程 Python Winsock**（WinError 10093）：
   多场景验证必须一个场景一个子进程（worker 模式），不能在同进程连第二个场景。


### 10.3 重要修正与追加（2026-08-13 下午）

1. **SAR/HalfPower 突破（修正 10.1 的"算不出"结论）**：
   - 根因不是模型/版本不支持，而是 `Define` 命令在这版本对这两种模式**静默失效**（SensorQuery 显示
     参数已写入，但 Access/Cov 均"无分析数据"）。
   - **正确路线 = 场景 XML**：Connect 建场景 → `Save / * "x.atk"` → 手改 `<SensorType>` 与
     `<Pattern>` 块 → 新实例 `Load / Scenario "x.atk"` → 计算覆盖。
   - **SensorType 索引实测**：0=SimpleCone、3=Conical、4=HalfPower、5=SAR（与文档列表顺序有偏移，
     之前按文档猜 4=SAR 是错的）。
   - 实测（1 颗 LEO × 8h × 杭州）：SAR(12-70°,30/30,550km) **6 窗口**，其中一次过境被 MaxElev 70°
     拆成 455s+395s 两段（天底孔洞，物理合理）；HalfPower(0.5GHz/0.35m) **5 窗口** ≈ SimpleCone60。
   - 待办：把批量 worker 的传感器配置从 Define 改成"Save→改 XML→Load"路线，才能在大规模里用 SAR/HP。
2. **性能**：传感器变体数量线性影响耗时（24 星：1 变体 6.1s / 9 变体 15.3s，约每变体每星 +0.4s）。
3. **端口冲突**：runner.py 与 validate_sensor_windows.py 已改动态端口（`pick_free_ports()` bind 探测），
   不再硬编码 6655+i；与其他软件端口冲突会自动避让。
4. **Spinning 相位不可复现**：相同 XML（ThetaZero=45、6°/s、30° 锥）两次 Load 窗口时刻完全不同
   （结构一致、相位偏移）——旋转初相未锚定场景历元，跨实例不可精确复现。
5. **覆盖定义报告**：Connect 所有报告路径（Cov_RM/CovMulti_RM/FOMDefine/Exec_Report_RM）与
   CovMulti Objects 全部 NACK；GUI 区域覆盖报告可用（内部模板）。覆盖定义分类在 Connect 命令库
   只有 Cov Grid/Graphics/VO 三个命令，报告接口未开放。替代：区域网格转点阵地面站。
6. **地面站 = 点**：Facility 无半径；"点+半径"需用覆盖定义网格（GUI）或多地面站点阵（Connect）近似。
7. **地面站报告接口出处**：`6-Connect对象命令库/工具/覆盖性命令/Cov_RM Access.html`（在"工具→覆盖性命令"
   分类，不在"地面站"分类）；语法 `Cov_RM <ObjectPath> Access Compute "<ReportStyle>" ...`。

### 10.4 Spinning 控制变量实验 + 覆盖定义品质因子深挖（2026-08-14）

**Spinning 相位（控制变量实验，结论：实例内确定、跨实例不可复现）**
1. 同一实例连续算两次：窗口逐窗口一致（无随机）。
2. 同一实例 Load 两次同一 XML：一致。
3. 分析时段 00-08h → 02-10h 平移：共同时段窗口毫秒级吻合（相位锚定场景内固定参考）。
4. ThetaZero=0/45/90/180 窗口模式不同（参数生效，只做相对偏移）。
5. 两实例并行同时启动（差 2s）Load 同一 XML：7 vs 8 窗口，首窗差 11s → 跨实例基准相位未定义，
   与墙钟无关、不是用法错误。
→ 生产建议：同一实例内连续计算可复现；跨实例只保证窗口数量级/结构，精确时刻需官方确认。

**覆盖定义 + 品质因子（用户提示：GUI 区域覆盖必须先建品质因子）**
- 官方《11-区域覆盖案例》确认：创建覆盖定义 → 创建品质因子（选择覆盖定义）→ 类型=覆盖时间/计算=每日最大
  → 区域覆盖工具计算 → 覆盖时段报告 + 品质因子报告。
- Connect 实验：a) 自建 CD + `New / */CoverageDefinition/CD1/FigureOfMerit FOM1` + `Cov FOMDefine`
  配置（均 ACK）→ Cov_RM 仍 NACK；b) Load 官方示例（中文名）→ Connect 命令全 NACK（中文对象名问题）；
  c) 官方示例 XML 中文名改 ASCII 后 Load → 对象正常、Assign/Compute ACK，但 Cov_RM 全部样式、
  CovMulti Objects CD、CovMulti_RM 全部 NACK。
- 结论：品质因子是 GUI 流程必要条件（确认），但 **Connect 命令库未开放覆盖定义报告接口**（不是品质因子
  缺失）。替代：区域网格转点阵地面站（facility 报告路径）。

**Connect 机制确认**：命令即字符串，Python SDK（ATKConnectModule）atkConnect(con, 命令, 参数串) 发送到
ATK 的 Connect 服务端口，ATK 解析执行返回（ACK/NACK/数据/报告），语法见 6-Connect对象命令库。
**XML 是什么**：ATK 场景保存文件（.atk）是带标签的 XML 文本，Save 命令把整个场景配置序列化进去；
手工改标签再 Load 可配置 Define 命令搞不定的类型（SAR/HalfPower 即此路线）。

### 10.5 Spinning 定案 + 覆盖定义最终证据（2026-08-14 上午）

**Spinning 离散相位状态（完整控制变量实验，见验证报告 §7.4）**
- 同一 XML（ThetaZero=45）反复实验：同一实例内完全确定（连续算/Load 两次/时段平移毫秒级一致）；
  跨实例落在离散状态（3/7/8/9/10/11 窗口模式），同状态毫秒级可复现。
- 已设置并验证：ThetaZero=45（SensorQuery 确认）、显式 AttitudeDefine（VVLH/ECF速度/ECI速度/Fixed，
  类型改变窗口模式）、去掉 Load 后时段重置——均无法消除离散状态。
- Connect 的 AddAttitude（Quat/YPR/CBFQuat）对 TLE 导入卫星全部 NACK，SetAttitude Standard 无效，
  无法在 Connect 层显式固定体坐标姿态。
- 结论：ATK 无头 Connect 的 Spinning 初始相位存在实例级离散状态（疑似 Legacy 姿态体坐标滚转初始化），
  建议 GUI 复测 + 官方反馈。

**覆盖定义最终证据（四轮，见验证报告 §7.5）**
- 品质因子是 GUI 区域覆盖流程的必要部分（官方教程确认），但 Connect 端即使：自建 CD+FOM 子对象
  （New /FigureOfMerit + Cov FOMDefine + Cov Interval + Assign + Compute 全部 ACK）、加载官方示例
  （FomName=2/Compute=19/Enable=1）、把官方示例中文名改 ASCII 后 Load——`Cov_RM` 全部样式、
  `CovMulti Objects CD`、`CovMulti_RM`、`QuickReport_RM GetList` 全部 NACK/空。
- 二次开发案例无区域覆盖示例；综合案例区域覆盖全程 GUI 操作。结论：Connect 命令库未开放
  覆盖定义报告接口；区域目标需点阵地面站近似（facility 报告路径）。

### 10.6 Spinning 最终定案：所有可控参数逐一排除（2026-08-14 下午）

- 从 AtAttitude.dll 提取到真实姿态类型名：AttitudeVVLH / AttitudeVVLHCBI / AttitudeVVLHCBF /
  AttitudeEcfAligndVel / AttitudeEciAligndVel / AttitudeFixedInCBF 等（此前 AttitudeSatelliteVVLH
  无效会被静默忽略）。AttitudeVVLH 生效已由 GetAttitude 确认（"Nadir alignment with ECI velocity
  constraint"）。
- 逐项排除清单（每项 4 个干净实例）：ThetaZero=45（SensorQuery 确认）→ 仍 3/3/7/8；
  正确 VVLH 姿态 → 3/11/7/3；星历步长 60→1s → 3/3/3/7；不重设时段 → 8/7/3/3；
  Point Spinning 重设 → 不改变；SetAnimation CurrentTime 锚定 → 3/3/8/8；不调用 Animate → 无法计算。
- 结论：相位差异来自 ATK 内部未暴露的实例级初始化（疑似旋转相位基准未初始化），非参数可控。
- 实用缓解：同一实例内（含同实例重新 Load）相位不变、结果毫秒级自洽；生产上用单个长驻实例
  跑完整分析；跨实例复现需 GUI 测试或官方反馈。

### 10.7 Spinning 根因找到：Animate Start 快进 + Compute 缺引号（2026-08-14 下午，重大修正）

- **结论反转：Spinning 完全可复现，物理确定，ATK 无随机缺陷——是我们命令用法错了。**
- 根因 1：`Animate * Start`（默认 Forward）让仿真按墙钟狂推进度，旋转相位随 Start 期间推进的
  仿真时间漂移。sleep 实验：Start→Reset 墙钟耗时 0.037s → 10 窗口；0.534s → 3 窗口；
  2.035s → 7 窗口。`Animate * Reset` 只重置时钟显示，不重置旋转相位。
- 根因 2：`Cov ... Access Compute <t0> <t1>` 时间参数必须带引号（`"T0" "T1"`）。此前一直未带
  引号 → Compute 返回 NACK 从未执行，覆盖数据来自 Assign 隐式计算，同样受墙钟影响。
- 正确可复现流程（5/5 实例毫秒级一致，3 窗口 02:50:15.211）：
  Load → SetAnalysisTimePeriod → Animate * Reset（唯一动画命令）→ Assign → 
  Cov Access Compute "T0" "T1"（带引号）→ Cov_RM "Coverage"；**不调用 Animate * Start**。
- 附带确认：卫星位置/速度跨实例逐位一致（无轨道低级错误）；显式 AttitudeVVLH 保持推荐。
- 待办：把 atk_parallel/worker.py 的 Animate Start→Reset 流程改为"仅 Reset + 带引号 Compute"，
  并回归 SimpleCone 对拍（69/70）确认无回归。

### 10.8 回归修正 + 覆盖定义 Report_RM 突破（2026-08-14 下午）

**worker 流程修正（SimpleCone 回归 69/70 通过）**
- 控制变量矩阵实测：新建场景**必须 `Animate * Start` 生成星历**，仅 Reset / Step Forward /
  Save→Load 都无法物化（全部 0 窗口）；`Compute` 时间参数带引号与不带引号结果一致（69 窗口）。
- 因此 worker.py / validate_sensor_worker.py 保留 Start→Reset，Compute 改为带引号
  `CovMulti / Access Compute "t0" "t1"`；validate_sensor_windows.py 对 0 窗口场景自动重试。
- 回归：L1a/L1B/L1C 69/70 匹配 69（max 4s，avg 1.3s）；L1E 80/77 匹配 76（max 28s）；
  D80 无约束 102（与自研 84 不直接对拍，属已知行为）；SPIN 130（跨实例不稳定区间内）。
- SPIN 可复现性边界：**新建场景**因 Start 墙钟依赖跨实例不可复现（V10 的
  SetAnimation CurrentTime 只拨时钟、不锚定相位）；**已物化 XML Load 的小场景**可复现（5/5）。

**覆盖定义报告接口突破（此前"无接口"结论错误）**
- 正确接口：`Report_RM <CDPath> Style "<样式>" TimePeriod "t0" "t1"`（官方 Report_RM.html
  明确"包括覆盖定义对象"）。实测可用：Access Duration / Gap Duration / Percent Coverage /
  Coverage By Assets / Grid Point Visible Assets / Global Coverage。
- 错误接口（全部 NACK）：`Cov_RM <CD> Access Compute ...`（4 种样式）、
  `Cov_RM <CD> FOMDefine ...`、`CovMulti Objects/Assets/Access`、`CovMulti_RM`。
- 坑：`Exec_Report_RM * GetList` 会挂死（勿用）；`Load` 必须 `Load / Scenario "文件"` 且
  不能带 `--new-scenario` 启动；官方示例 ASCII 化后 Load/计算 ACK 但 Report_RM 仍 NACK（待查）。
- 网格配置：分两次 Grid 命令后区域变回全局网格（1008 点），合并选项写法待确认。

### 10.9 用户方案验证：Start→Save→Load 不 Start（2026-08-14 晚）

用户提出"新建场景 Start 生成星历 → 强制保存 → Load 后不 Start"的方案，实测结论：
- **轨道星历可持久化，方案对 SimpleCone 成立**：XML 内含 24 卫星/24 传感器/1 地面站及
  Position/Velocity 状态；Load（不带 `--new-scenario`）+ 不 Start 直接计算 = 69 窗口，
  多次一致，与 A（新建+Start）相同。
- **Spinning 相位仍不可复现，方案对 SPIN 不成立**：同一保存 XML 多轮 Load（不 Start），
  窗口 20/124/129/43/125/123/126 都出现过；offset=45（ThetaZero）也锚不住；
  偶尔连续 4 次都命中 20/00:17:45.822 属概率性，非可靠机制。
- 结论修正：§7.4 曾认为"已物化 XML Load 5/5 一致"可作复现路径，现判定为**概率性命中同一
  离散状态**，不能作为生产依据；Spinning 相位锚定仍需官方确认（疑似未暴露的初始化状态，
  与 Legacy 姿态体坐标初始化相关）。

### 10.10 无头弹窗修复 + 覆盖定义网格口径 + Spinning 漂移再证（2026-08-14 晚）

**无头弹窗修复**
- 实测：`--headless`（等同 `--no-ui`）只关 2D/3D 视图，**ATK 主窗口仍然可见**（标题为版本号），
  这就是"一直弹窗口"的原因。
- 修复：新增 `atk_parallel/headless.py`，启动后枚举本次新起的 ATK 进程并 `ShowWindow(SW_HIDE)`
  隐藏其可见顶层窗口；只隐藏新增进程，不影响用户手动打开的 GUI。已验证隐藏后
  AllInstanceNames / SetAnalysisTimePeriod 等 Connect 命令正常。
- validate_sensor_worker.py 改用 `launch_headless()`。

**覆盖定义网格口径（重要）**
- `Cov <CD> Grid AreaOfInterest LatLonRegion ...` 与 `PointGranularity LatLon ...`
  只改变报告头部元数据（最小/最大纬度、网格精度），**实际点网格固定为 1008 点**：
  2km×2km 小区域、20°×40° 宽区域、Global 均显示 1008 点。
- 因此 `Percent Coverage` 的数值近似"全局覆盖率"（24 颗 SimpleCone45 每分钟 5~8%，
  与 24 个大足迹覆盖地球比例吻合），**不能当作小区域覆盖率使用**；与地面站 69 窗口对比
  无法对齐（地面站是点口径，可靠）。
- 结论：Connect 下覆盖定义的区域报告口径不可控/不可信，区域目标建议：
  点阵地面站近似（可靠）或 GUI 复核网格设置。

**Spinning 漂移再证（固定 Start 时长）**
- 固定 `Animate Start` 与 `Reset` 之间墙钟 0.5s，5 个干净实例窗口 35/113/92/20/20，
  首窗各异 → **漂移不是墙钟时长造成的**，是实例级离散初始化状态。
- 结合 offset=45、Save→Load 均无法锚定：建议 GUI 复测 Spinning 相位，或向官方反馈
  （疑似未暴露的相位初始化状态）。
- 用户指定流程复测（加载已保存 XML → SetAnalysisTimePeriod → **仅 Reset**（不 Start）→
  Assign → 带引号 Compute）：5 个实例窗口 131/125/20/125/124，首窗各异 →
  "仅 Reset 不 Start"同样不可复现，之前 5/5 一致属概率性命中同一离散状态。

### 10.12 Spinning 不可复现最终定案：根因是"Compute 缺引号"，带引号后完全确定（2026-08-14 晚）

> **2026-08-15 作废**：本节"带引号即可复现"已被 §10.14/§10.18 推翻（沙箱掩盖 ASLR 假象）。

**结论反转（最终版）**：Spinning 相位**可复现**，根因只有一个——`Cov ... Access Compute` 的
时间参数**必须带引号**。§10.9/§10.10 记录的"带引号仍 131/125/20/125/124 漂移"是假象，
大概率是当时残留 ATK 实例串场景/陈旧状态导致。

**证据链（本轮干净复测）**：
1. 用户在 GUI 里同一 Spinning 场景跑两遍，结果**逐毫秒一致**
   （01:43:44.322 → 01:45:15.604，91.282s，1 窗口）——GUI 确定。
2. 无头 Connect `Load` 该 GUI 场景（ASCII 化对象名）→ SetAnalysisTimePeriod → Animate Reset →
   Assign → **带引号 Compute** → 精确复现 GUI 结果（1 窗口，同起止，逐毫秒一致）。
3. XML 对比：GUI 与 Connect 的 `<Spinning>` 块只差"旋转轴四元数"（GUI 单位四元数=Elevation 0；
   Connect 绕 Z -90°=Elevation -90）和锥角（20 vs 30）；四元数在所有 Connect 文件里**都相同**
   （确定性），只是改变扫描轴方向，不是漂移源。
4. 决定性对照：新建场景 + `Animate Start`（甚至在 Start→Reset 之间故意 sleep 2s）+
   **带引号 Compute** → 3/3 实例完全一致（4 窗口，首窗 01:45:02.205，逐毫秒一致）。
5. 加载 Connect 生成的 24 星 Spinning 场景 + 仅 Reset + 带引号 Compute → 3/3 一致（20 窗口）。

**白话总结**：之前算 Spinning 时 Compute 的时间没加引号，命令被 ATK 拒了（NACK），从来没真正
执行过；拿到手的窗口其实是"登记资产"那一步顺带算出来的、跟着墙钟走的脏数据，所以每次都不一样。
把时间参数加上引号后，ATK 会老老实实按我们给的时间段重算，旋转相位就钉死在分析起点上，完全确定。

**生产结论**：
- 只要 `CovMulti / Access Compute "t0" "t1"`、`Cov_RM ... "Coverage" "t0" "t1"` 全部带引号，
  Spinning 与 SimpleCone 一样可复现；`Animate Start` 的墙钟推进不影响结果。
- 已修正 `atk_parallel/worker.py` 中"Spinning 相位跨实例不可复现"的误导注释。
- 复现脚本：experiment/atk/spin_fresh_fix.py、spin_quat_bisect.py、spin_gui_probe.py、
  spin_conn_probe.py（留档）。

### 10.13 覆盖定义（区域网格）vs 自研引擎逐分钟对拍：100% 一致（2026-08-14 晚，任务 B）

> **2026-08-15 修正**：该对拍使用 Pro 环境"有效"的 Cov Grid 区域网格；我方环境 Cov Grid 失效，
> 已改用 XML 注入标准路线（§10.17/§10.18），区域口径结论以新路线复测为准。

- 方法：ATK CD 用 `LatLonRegion 30 120 31 121` + `PointGranularity LatLon 0.5` 生成 9 个真实网格点，
  读回这 9 个点；自研引擎 `compute_covered_series` 用**同一批 9 个点**跑多边形模式，
  逐分钟比"被覆盖网格点占比"（= CD 的 Percent Coverage 口径）。
- 样本：NORAD 68196，SimpleCone 45，无仰角约束，2026-08-14 00:00–08:00 UTC。
- 结果：对齐 480 分钟，**覆盖与否一致 480/480（100%）**；覆盖率最大差 0.00039、平均差 2.4e-6。
- 结论：ATK 覆盖定义的**区域百分比覆盖率与自研引擎完全一致**，CD 路线可放心用于区域覆盖；
  逐星窗口仍走地面站点路线（可靠）。
- 复现脚本：experiment/atk/cd_vs_selfengine.py。

### 10.11 覆盖定义区域网格结论修正：区域网格可用，历史"恒 1008"是假象（2026-08-14 晚）

**背景**：§7.5 / §10.10 曾记录"Connect 下 `Cov Grid` 只改报告头部元数据、实际点网格固定 1008 点、
`Percent Coverage` 是全局口径"。本轮用干净单星场景做决定性复测，结论**反转**。

**实测（1 星 + SimpleCone45，`Cov <CD> Grid AreaOfInterest LatLonRegion ...` +
`Cov <CD> Grid PointGranularity LatLon <deg>`，读数来自 `Report_RM <CD> Style "Coverage By Assets"`）**：

| 区域跨度 | 粒度(°) | 网格点数量 |
| --- | --- | --- |
| 0.02° | 0.1 | 1 |
| 0.02° | 0.01 | 9 |
| 0.1° | 0.01 | 81 |
| 0.1° | 0.05 | 9 |
| 0.5° | 0.5 | 1 |
| 0.5° | 0.1 | 18 |
| 1° | 0.5 | 9 |
| 1° | 0.1 | 81 |
| 5° | 0.5 | 81 |
| 10° | 1 | 81 |
| 20° | 2 | 81 |
| Global | 1 | 43424 |

**关键结论**：
1. `Cov <CD> Grid AreaOfInterest LatLonRegion <minLat> <minLon> <maxLat> <maxLon>` +
   `Cov <CD> Grid PointGranularity LatLon <deg>` 能正确生成区域点网格。
2. 点数 ≈ (跨度/粒度 + 1)²，单维上限 9（小区域最多 81 点）；Global 1° 约 4.3 万点。
   0.5°/0.1° 得 18（非 36）是浮点取整边界抖动。
3. `Report_RM <CD> Style "Percent Coverage"` 返回**区域覆盖率**（0.5°×0.5° 单点区域 8h 单星
   max=100%、mean≈4.37% ≈ 21 分钟总覆盖），不是全局覆盖率。
4. Save→Load 正确持久化区域网格（XML `<Grid>`：GridType=1、MinLat/MaxLat/MinLon/MaxLon、
   PointGranularity=2、ValueGranularity=0.5）。
5. 历史"1008 点"无法复现：四组对照（网格设在 Animate 前/后 × 有无 FOM）全部 1 点；
   判定为当时残留实例串场景/陈旧状态的假象，不是真实网格行为。

**生产口径修正**：
- 逐星覆盖窗口 → Facility 点路线（可靠，不变）。
- 区域百分比覆盖率 → CD 区域网格（现确认可用）；"点+半径"可用细粒度小网格近似
  （如 0.1° 跨度、0.05° 粒度 → 9 点）。
- 早先"固定 1008 / 全局口径"记录作废。

**复现脚本**：experiment/atk/cd_grid_scan.py、cd_grid_fom_test.py、cd_grid_order_test.py、
cd_grid_xml_probe.py（脚本在 gitignore 的 experiment/ 下，留档）。

### 10.14 独立复测（2026-08-15 凌晨）：§10.11~§10.13 的"定案"未复现，恢复旧结论

收到 Pro 会话交接（声称 Spinning 与 CD 网格均已定案）后，按"简单分析验证"要求独立复测，
**两个结论均未复现**，且"残留进程串场景"的解释被证伪：

1. **Spinning（`experiment/atk/spin_fresh_fix.py`，同一 TLE=68196，14 Aug 08h）**：
   - `sleep` 模式（Start→睡 2s→Reset→带引号 Compute）×3：窗口 **3 / 2 / 4**，首窗
     01:45:31 / 03:22:57 / 01:43:58，均不同。
   - `forward` 模式 ×3：窗口 **4 / 2 / 3**，首窗 01:43:54 / 01:46:27 / 01:45:31，均不同。
   - 全程：无残留 ATK 进程（每次运行前后都检查）、隐藏窗口、独立端口。
   - 与 Pro 声称"3/3 一致（4 窗口，01:45:02.205）"不符。
2. **CD 网格（`experiment/atk/cd_grid_scan.py`）**：
   - 清理全部 ATK 进程后连跑两轮：**所有区域×粒度组合均返回 1008 点**（0.02°~20° 全一样），
     与 Pro 的表（1/9/81/18...）不符；区域元数据（min_lat）会随命令变化，但点数不变。
3. **CD vs 自研对拍（`experiment/atk/cd_vs_selfengine.py`）**：
   - 脚本打印 ATK 读回点就是全局 1008 点（-56.905 起），480/480 一致是"两边用同一批
     1008 个全局点"的自洽，**不能证明区域覆盖率正确**；max diff 0.0012 也大于 Pro 记录的 0.00039。
4. **教训（重要）**：
   - 即使是另一个会话/更强模型给出的"定案"，也必须**在当前干净环境独立复现**后再采信；
   - "残留进程串场景"这类解释要可证伪：本机清理全部 ATK 进程后，坏现象稳定复现，
     说明旧结论（Spinning 不可复现、CD 恒 1008）才是在本环境可重复的事实；
   - 实验脚本自身可能泄漏 ATK 进程（cd_grid_scan.py 的 finally 只 atkClose、未杀进程树），
     每次实验后必须 `Get-Process` 检查并清理，避免污染下一轮。
5. 现状：SimpleCone + Facility 路线依旧可靠（69 窗口基线）；Spinning 相位与 CD 区域网格
   在 Connect 下仍按旧结论处理（不可复现 / 恒 1008），建议 GUI 复核或转向 Component 模式。

### 10.15 GUI XML 模板路线复测（2026-08-15 凌晨）：同一 XML 跨进程仍落离散状态

- 拿到用户 GUI 保存的场景 `D:\研究生学习资料\02_课题组工作\长期项目\Test for spinning bug.atk`
  （卫星 YAOGAN-50 02__68196、传感器1=SimpleCone20+Spinning 单位四元数/ThetaZero=0）。
- 无头 Connect Load（对象名 ASCII 化：SAT68196/Sensor1/Facility2）+ 仅 Reset + 带引号 Compute ×5：
  - 3 次**精确复现 GUI**：1 窗口 01:43:44.322→01:45:15.604，91.282s；
  - 2 次落在另一离散状态：4 窗口，首窗 01:45:02.861 / 01:45:02.862，两两毫秒一致。
- Load + Start→Reset ×5：窗口 4/1/1/3/1，首窗出现 83.166s、16.915s、29.722s 等更多状态。
- 结论：**离散状态与 XML 内容无关**（同一 XML 跨进程落不同状态），属 Connect 进程级初始化
  （疑似未初始化内存/ASLR）；用户 GUI"两遍一致"为同进程内一致，**跨进程 GUI 一致性未验证**。
- 建议下一步：a) 用户完全关闭 ATK 后重启 GUI 再算 3 次，验证 GUI 跨进程是否一致（分水岭）；
  b) 若 GUI 也跨进程漂移 → 属于 ATK 进程级初始化，评估 retry-until-match 或 Component 模式；
  c) 脚本留档 experiment/atk/gui_xml_template_test.py。

### 10.16 Pro 撤回 + 双方收敛 + CD 网格差异待查（2026-08-15）

- Pro 已撤回 Spinning"定案"，双方一致：Connect 的 Spinning 相位是**进程级离散初始化状态**
  （疑似 ASLR/未初始化内存），无锚定命令；其本环境短时稳定（5/5 一致）是"一段一段稳定"的假象。
- 策略共识：不再押宝 ATK 修复；并行准备**自研旋转扫描确定性模型**（锥轴绕天底匀速旋转，
  瞬时足印=圆锥，覆盖窗口=扫描锥与目标几何判定），ATK 退化为 SimpleCone 交叉验证。
- CD 网格仍系统性不一致（我方全 1008 / Pro 随区域×粒度 1/9/81...）：
  - 我方已提供完整原始报告 + 启动参数（--headless、提权 whoami=yungzzi\lenovo、动态端口）；
  - 我方顺序对照四组（Animate 前/后 × FOM 有无）全 1008；
  - 发现 ATK 默认 CD 模板 `AstroData\Default\DefaultCoverageDefinition.atk` 即全球网格
    （-60~70°），我方 `Cov Grid` 命令无法覆盖它；
  - 待 Pro 回贴其环境完整原文与环境细节（whoami/版本/数据目录/注册表/默认模板内容）逐字段核对。
- 待办：用户 GUI 完全重启跨进程测试（×3）仍未做，是 Spinning 判断的最后分水岭。

### 10.17 CD 网格 XML 注入打通 + VVLH 未锚定 + Component 无 Spinning（2026-08-15）

- **CD 网格 XML 注入成功**：Connect 建 CD（默认网格 1008 点 / min_lat=-60）→ `Save` → 手工改
  `<Grid>` 的 MinLat/MaxLat/MinLon/MaxLon/ValueGranularity → `Load` → 网格点数变为区域值
  （0.5°×0.5°/0.5° 粒度 → 1 点，min_lat=30.000）。**绕过了失效的 `Cov Grid` 命令**，
  CD 区域网格在 ATK 内可用（与 SAR/HalfPower 的 XML 路线同款）。
  - 脚本：experiment/atk/cd_grid_xml_inject.py。
- **VVLH 显式姿态未锚定 Spinning**：把 XML 的 AttitudeDefine 改为 AttitudeVVLH 后
  Load ×5 = 4 次命中 91.291s（1 窗口）+ 1 次漂到 4 窗口（86.719s）→ 姿态类型不是根因。
- **Component 模式确认无 Spinning**：官方枚举 `ESnPattern` 只有 eSnSimpleConic/eSnRectangular，
  `ESnPointing` 只有 eSnPtFixed；C++ 头文件搜不到 Spinning → ATK 路线只剩 Connect/GUI。
- **用户决策**：反对自研旋转扫描模型，继续 ATK 二次开发；自研仅作交叉验证。
- Spinning 剩余可选路径：①单长驻 ATK 进程（进程内确定，跨重启不定）；②GUI 跨进程测试
  （待用户）；③retry-until-match（需参考状态）；④官方反馈。

### 10.18 Spinning 收口定论 + CD 标准路线 + 项目决策（2026-08-15，最终版）

**Spinning 根因锁定（GUI 分水岭 + 沙箱/提权对照）**
- 用户 GUI 完全重启 ×3：窗口 4（216.486s）/ 1（91.282s）/ 3（99.859s），首窗各异 →
  **GUI 跨进程同样漂移**，不是 Connect 独有；此前"GUI 两遍一致"是同进程内一致。
- Pro 对照实验（同一 spin_vvlh_xml_test.py，仅改权限）：沙箱内 Load ×5 = 1/1/1/1/1；
  提权（非沙箱）Load ×5 = 4/4/1/4/3 → **Spinning 初始相位 = ATK 未初始化变量（ASLR 绑定）**，
  沙箱压稳内存布局掩盖了随机性，真机/提权环境暴露真实随机。
- 四路全堵：Point Spinning 的 Offset/ThetaZero 只是相对偏移；相位不序列化入 XML；
  Legacy/VVLH 姿态均不是根因；Component 无 Spinning（ESnPattern 仅 SimpleCone/Rectangular）。
- **定性：ATK 4.0.1-rc.5 缺陷（未初始化初始相位）**，建议附用户 3 次 GUI 结果反馈官方。

**CD 区域网格标准路线（双方统一）**
- 一律走 **XML 注入**：Connect 建 CD → Save → 改 `<Grid>` 的
  MinLat/MaxLat/MinLon/MaxLon/ValueGranularity → Load → 计算。
  绕开两环境不一致的 `Cov Grid` 命令（我方失效、Pro 有效，不再追差异）。

**项目决策**
- 生产兜底：**单长驻 ATK 进程** + 输出时标注"初始相位未指定，结果为一次代表性实现"；
  优先输出**相位不变量**（总覆盖时长、覆盖率%、重访 MRT）；相位敏感量（窗口起止/时长）加注；
  "多进程取范围/聚合"记入待办暂不做。
- SimpleCone + Facility 路线照常推进（确定可靠，69 窗口基线）。
- Spinning 挂起：等官方回复或用户最终拍板（用户当前否决自研旋转模型）。

### 10.19 重启后复测：Spinning 跨进程随机与机器重启无关（2026-08-15）

用户重启计算机后，用 `experiment/atk/gui_xml_template_test.py`（无参 ×5，提权、隐藏窗口、
独立子进程）加载用户 GUI 场景 ASCII 化 XML（SAT68196/Sensor1/Facility2），仅 Reset +
带引号 Compute：

| 实例 | 窗口数 | 首窗时长 |
| --- | --- | --- |
| 1 | 4 | 85.853s（01:43:57.853） |
| 2 | 4 | 23.362s（01:44:44.503） |
| 3 | 4 | 23.275s（01:44:44.207） |
| 4 | 3 | 27.901s（01:45:35.518） |
| 5 | 2 | 17.299s（01:46:27.007） |

- 结论：**与机器重启/墙钟无关**，Spinning 初始相位跨进程随机依旧（§10.18 的
  ASLR/未初始化变量假说成立）；GUI 跨进程 4/1/3 与无头 Connect 一致。
- 同时发现并规避两个环境坑：
  - Connect `ImportTLEFile` 传入三行（带名字行）TLE 会触发堆损坏（0xC0000374）——
    必须纯两行格式（§9.10 再证）；
  - `atkClose()` 偶发堆损坏崩溃（0xC0000374）——新模块一律改为按端口杀 ATK
    进程树收尾，不再调用 atkClose。
- 官方反馈材料已起草：`docs/ATK官方反馈材料.md`；反馈渠道：
  atk_service@163.com、红山开源平台的 ATK 官方项目页（osredm.com/atknudt/atk）、
  gitcode.com/jinke18/atk-doc Issues、工作人员微信 LXKTATK。

### 10.20 CD 区域模块完成 + 我方环境对拍通过（2026-08-15，任务落地）

- 新增可复用模块 `atk_parallel/cd_area.py`：Connect 建 CD → Save → XML 注入
  `<Grid>` 区域（MinLat/MaxLat/MinLon/MaxLon/ValueGranularity）→ Load → Assign →
  带引号 Compute → 读 "Grid Point Visible Assets"（真实网格点）与
  "Percent Coverage"（分钟级区域覆盖率）。
- 我方环境独立对拍（NORAD 68196，SimpleCone45，30~31°N / 120~121°E，0.5° 粒度，
  2026-08-14 00:00–08:00 UTC）：
  - ATK 读回 **9 个真实网格点**（30.167/120.167 起）；自研引擎同一批 9 点
    多边形模式逐分钟对拍；
  - **480 分钟覆盖与否 100% 一致**；覆盖率最大差 0.00039、平均差 2.4e-6；
  - ATK 报告 481 行（含终点），自研 480 行（左闭右开），对齐取交集 480。
- 结论：XML 注入标准路线在我方环境复测通过，与 Pro 环境结论一致；CD 区域模块
  可用于"区域百分比覆盖率"输出项。逐星窗口仍走 Facility 点路线。
- 复现脚本：`experiment/atk/cd_area_validate.py`、`cd_area_raw_probe.py`
  （gitignore，留档）。

### 10.21 worker 支持复杂传感器 + runner 区域覆盖率输出（2026-08-15，A/B 落地）

**A：worker 传感器类型参数化（atk_parallel/worker.py / runner.py）**
- 新增 `--sensor-type`（SimpleCone/Rectangular/HalfPower/Conical/SAR）与
  `--sensor-params`（逗号分隔参数，官方 Define 语法见 Help/传感器/Define.html）。
- 路线分界（关键）：
  - SimpleCone / Rectangular / Conical：走 `Define` 命令（实测正常）；
  - **SAR / HalfPower：走 Save→改 XML→Load 双实例路线**（§7.1：rc.5 的 Define
    对这两种类型静默失效，返回空串但传感器不生效 → 0 窗口）。
    实现：实例 1（new_scenario）建 SimpleCone 占位 → Save → 改 `<Pattern>`
    （SAR=`<SensorType>5</SensorType>`+`<SAR>` 块；HalfPower=`4`+`<HalfPwr>` 块，
    halfAngle 按 70°·λ/D/2 换算）→ 实例 2（不带 new_scenario）Load → 计算。
    调度器对这两种类型不再预启动 ATK，由 worker 自管两个实例。
- worker 收尾不再调用 atkClose（堆损坏 0xC0000374 会丢结果），先落盘再按端口杀进程树。
- 回归与试点（杭州点 30.25/120.17，2026-08-12 00:00–08:00 UTC）：

| 传感器 | 规模 | 路线 | 窗口（两遍） |
| --- | --- | --- | --- |
| SimpleCone45 | 24 星子集 | Define | 69 / 69（基线一致） |
| Rectangular(45,45) | 6 星 | Define | 25 / 25 |
| SAR(12,70,30,30,550km) | 6 星 | XML | 34 / 34（Define 路线为 0） |
| HalfPower(0.5,0.35) | 6 星 | XML | 29 / 29（Define 路线为 0） |

**B：runner 区域百分比覆盖率输出**
- 新增 `--area-region "min_lat,min_lon,max_lat,max_lon"`、`--area-granularity`、
  `--area-fov`（可选，默认不跑）；复用 cd_area 模块（§10.20，XML 注入网格 +
  SAR/HalfPower 自动加传感器 XML 补丁）。
- 输出：`area_coverage.csv`（每分钟覆盖率%）、`area_grid_points.csv`（真实网格点）、
  `area_summary.json`（网格点数/资产/覆盖分钟/均值/最大值）。
- 实测（6 星，30~31°N / 120~121°E，0.5°）：SimpleCone45 → 9 网格点、72/481 覆盖分钟、
  最大 100%；SAR(12,70,30,30,550km) → 232/481 覆盖分钟、assets 正确列出 6 颗。

### 10.22 真正无头（隐藏桌面）+ GUI 复杂传感器（2026-08-15，codex/gui-atk-sensors-headless）

**真正无头：独立非交互桌面**
- 老问题：`--headless` 只关 2D/3D 视图，ATK 主窗口仍会创建；旧机制（SW_HIDE + 启动后
  EnumWindows 隐藏）在窗口创建到隐藏之间会闪窗，且不稳定。
- 修复：`atk_parallel/headless.py` 新增 `launch_on_hidden_desktop()`——用
  `CreateDesktopW` 建独立非交互桌面，`CreateProcessW` 的 `lpDesktop` 指向该桌面，
  ATK 所有窗口都开在用户看不到的桌面上，**从根上不闪窗**；进程退出后由守护线程
  自动 `CloseDesktop` 防泄漏。桌面创建失败时回退旧机制。
- runner 的 `launch_atk`（产品主路径）与 `launch_headless`（SAR/HalfPower XML 路线、
  CD 区域）都走隐藏桌面；根 `atk_parallel/` 与 `src/atk_parallel/` 两份同步。

**GUI 复杂传感器（⑤页）**
- 选"覆盖引擎=ATK"后出现"传感器类型"下拉：SimpleCone / Rectangular / HalfPower /
  Conical / SAR，按类型显示参数输入（矩形双半角、半功率频率+天线直径、复杂圆锥
  内外锥角+时钟角、SAR 入射仰角范围+前后向排除角+父高度）。
- 复杂传感器用统一参数；逐星视场半角/幅宽仅对 SimpleCone 生效；上方"最小仰角"
  仍是额外地面站约束（SAR 想只用自身仰角范围就填 0）。
- 结果区与 analysis_meta.json 记录 sensor_type / sensor_params。

### 10.23 传感器指向 + 约束（2026-08-15，codex/atk-pointing-constraints）

**指向（Pointing）**
- Fixed：Connect `Point <sensor> Fixed <方法> <参数>` 生效，支持 YPR / Euler /
  Quaternion / AzEl / matrix 全方法（GUI ⑤页"传感器指向"折叠区全部提供）。
  实测：SimpleCone 天底 1 窗口 → Fixed YPR 俯仰 30° = 6 窗口（侧摆扩大感知，确定性两遍一致）。
- Targeted：**Connect `Point ... Targeted ...` 在 4.0.1-rc.5 全 NACK**（与 SAR/HalfPower
  Define 同类通道问题）；改走 **XML 注入**：`<PointType>3</PointType>` +
  `<Targeted><TargetMode>0</TargetMode><TrackingMode>2</TrackingMode>
  <AssignedTargets>Facility/Hangzhou</AssignedTargets>`。实测 SimpleCone 1→6 窗口、
  SAR 5→7 窗口，确定性两遍一致。Targeted 自动走 XML 双实例路线（与 SAR/HalfPower 共用）。
- GrazingAlt：`Point <sensor> GrazingAlt <方位角> <入射高度>` 生效（天底 1 → 2 窗口）。
- External：透传外部指向文件路径（GUI 上传 .sp 到 data/user_files/），需要用户自备
  ATK 格式指向文件，未单独验证。
- Spinning：仍禁用（等官方回复）。

**约束（SetConstraint）**
- 作用对象：facility（地面站=目标点）/ sensor（每颗星传感器）/ satellite（每颗星）。
- GUI 常用表单：太阳高度角（SunGroundElevAngle Min）、方位角（AzimuthAngle Min/Max）、
  距离（Range Min/Max，km 自动转 m）、视线开关（LineOfSight On）。
- 高级 JSON 透传：`[{"object":"facility|sensor|satellite","name":"约束名","params":"参数串"}]`，
  params 与官方 SetConstraint 命令参数一致，原样转发给 ATK（覆盖速率/高度/传播延迟/
  月球/太阳排除/地形遮蔽等全部约束）。
- 实测：SunGroundElevAngle Min 80（杭州夏季太阳高度上限约 75°）→ 0 窗口；
  Range ≤100 km → 0 窗口；Min 5 / 宽松距离 → 基线 1 窗口。

**验证矩阵（6 星 × 8h × 杭州 5km，ATK 引擎）**

| 配置 | 窗口数（两遍） |
| --- | --- |
| 天底基线（SimpleCone45） | 1 / 1 |
| Fixed YPR 俯仰 30° | 6 / 6 |
| Targeted 目标跟踪 | 6 / 6 |
| GrazingAlt(90°, 10km) | 2 |
| SAR(12,70,30,30,550km) 天底 | 5 / 5 |
| SAR + Targeted | 7 / 7 |
| SunGroundElevAngle Min 80 | 0 |
| SunGroundElevAngle Min 5 | 1 |
| Range ≤100km | 0 |

注意：Targeted 走 XML 路线时 SimpleCone 用全局半角（逐星视场半角表不参与）。

### 10.24 第 5 页重构 + ATK 逐星参数 + 约束全 GUI（2026-08-16，codex/gui-page5-redesign）

**第 5 页布局**
- 改为**单栏**（st.container(border=True) 分组）：目标区域与时间 / 卫星来源 / 引擎参数。
- **覆盖引擎置顶**（segmented_control），**默认 ATK**（后续大概率走 ATK）。
- **引擎差异化**：
  - 自研：视场半角/最小仰角/步长/边界精化/幅宽开关 + 原"传感器参数表（逐星，自研）"
    （只在此分支显示）；
  - ATK：全局传感器类型+参数、传感器指向、约束（全 GUI 表格，去掉 JSON）、
    **逐星 ATK 参数表**（NORAD/视场半角/最小仰角/传感器类型/参数）。

**ATK 逐星参数（atk_engine）**
- cfg.sat_sensor_overrides：按 NORAD 覆盖视场半角/最小仰角，或整颗星换传感器类型+参数；
  优先级：ATK 逐星表 > 自研逐星表 > 全局。
- 引擎按（最小仰角, 传感器类型, 参数）分组，每组一次 ATK 计算（XML 路线组内同质，兼容 SAR/HalfPower/Targeted）。
- 实测（6 星 × 8h × 杭州 5km，SimpleCone）：HJ-1B fov45 → 1 窗口（72s）；fov10 → 0；
  fov60 → 1 窗口（288s，更长）；min_elevation=30 逐星生效；28890 逐星换 Conical 生效；
  两遍一致。

**约束全 GUI（去掉高级 JSON）**
- 约束页（§10.29 起改为 ATK 同款布局）：按作用对象分页签（目标点/每颗星传感器/每颗星），
  每类约束=启用开关+输入框（Min/Max、On/Off 或单值规避角）；按类型自动拼 SetConstraint
  参数（方位角必填 Min+Max、开关类用 On/Off、排除角类用单值）。
- 实测：SunGroundElevAngle Min 80 → 0 窗口（与 JSON 透传一致）。

### 10.29 约束页改为 ATK 同款「开关+输入框」布局（2026-08-17，codex/constraints-atk-style）

- 去掉 data_editor 表格，改为仿 ATK 约束属性页：作用对象=页签（全局=目标点/每颗星传感器/
  每颗星；逐星=该星传感器/该星），每组内按 ATK 文档分「基本约束」（方位角/仰角/距离/
  三种变化率/角变化率/高度/传播延时/视线/视场/地形）与「光照约束」（太阳仰角/太阳地平仰角/
  月球仰角/四种太阳月球规避角）。
- 每类约束：启用开关 + Min/Max（minmax 型，默认值与 ATK DefaultSensor.atk 一致：
  如仰角 0~90°、距离 0~4e7 m、高度 0~2e8 m）；视线/视场/地形 = On/Off；
  规避角 = 单值（最小夹角）。
- 参数串保持与 SetConstraint 文档一致（`Min X Max Y` / `On|Off` / 单值，
  方位角必须同时给 Min/Max，支持 ExcludeIntervals/IncludeIntervals 附加）；
  数值统一普通十进制（避免科学计数法进命令）。
- 兼容旧配置：已有 {object,name,params} 照常解析回显；旧默认「目标点（逐星=该星传感器）
  太阳地平仰角 Min 10」保留。时间/通信约束仍不实现（页面注明）。
- 验证：AppTest 渲染 3+2 页签无异常；旧参数串→控件→重建参数串逐条一致；
  勾选/填值/取消勾选后输出与预期一致。

### 10.25 不分组单实例逐传感器 + GUI 两栏 + Targeted 误判纠正（2026-08-16，codex/atk-persat-unified）

**重要纠正（覆盖 §10.23 的 Targeted 结论）**
- §10.23 曾记录"Targeted 走 XML 注入（PointType=3）生效"。决定性复测推翻：
  6 星"对杭州仰角>0 连续时段"参考值 = 8 段；枚举 PointType×TargetMode×TrackingMode×
  外层 `<AssignedTarget>` 共 30+ 组合均无法复现 8 窗口；PointType=3 实为 **GrazingAlt**，
  之前"Targeted 生效"的 6 个窗口是 GrazingAlt(0,0) 的假象。
- 结论：Targeted 的真实 XML 写法未确认（Connect `Point ... Targeted` 在 rc.5 全 NACK，
  且无 GUI 生成的样例可对照）→ **GUI 中将 Targeted 置为"待复核，暂不可用"**；
  等拿到 GUI 手动建的 Targeted 场景（.atk）再定 XML 写法。

**不分组、单实例、逐传感器（用户拍板）**
- worker 重写：一次 ATK 实例内每颗星独立配置（类型/参数/指向/约束）：
  - 无 SAR/HalfPower → Define 路线：逐传感器 `Define/Point/SetConstraint`；
  - 有 SAR/HalfPower → XML 路线：实例 1 建 SimpleCone 占位 → Save →
    **逐传感器改 `<Pattern>`**（五种类型块全部确认：SimpleCone=0/Rect=1/Conical=3/
    HalfPower=4/SAR=5）→ 实例 2 Load → 逐传感器 Point/SetConstraint → 计算；
  - 两类传感器可混在同一实例（不再分组）。
- runner 新增 `--sat-cfgs`（按 TLE 顺序，分片切片）；engine 单次调用（临时目录只有 all.tle）。
- 逐星约束作用于该星传感器/卫星级；目标点级约束（含最小仰角）在全局。

**GUI 两栏（仿 ATK）**
- `src/atk_ui.py`：全局/逐星共用"定义（类型+参数+指向）+ 约束（基本+光照）"面板，
  带已保存配置回显；持久化 `data/atk_sat_config.toml`（TOML 读写）。
- ⑤页 ATK 分支：全局面板（默认值）+ 逐星两栏（左：卫星列表单击选中；右：该星定义/约束 +
  保存/清除，清除=回退全局）。
- 约束只做"基本 + 光照（太阳/月球）"，时间约束暂不做（Connect 无命令、需 XML 注入验证）；
  术语统一为"光照"，避免与 ATK 文档对不上。

**验证（6 星 × 8h × 杭州 5km）**
| 用例 | 结果 |
| --- | --- |
| 默认（全局 SimpleCone45，无逐星） | 1 窗口（回归一致） |
| 单实例混合：SimpleCone60/Conical(10,45,0,360)/Rect(40,60)/SAR(12,70,30,30,550km)/SimpleCone10/Fixed YPR30 | 运行成功、临时目录仅 all.tle、两遍一致 |
| 逐星 33321 fov10 | 0（窄锥漏擦边） |
| 逐星 33321 fov60 | 1 窗口更长（288s vs 72s） |
| 光照 SunGroundElevAngle Min 80 | 0 |
| AppTest（强制⑤页）ATK/自研两分支 | 无异常；全局/逐星面板、卫星列表均渲染 |

### 10.26 Targeted 定案：正确 XML 写法（用户 GUI 场景破案）（2026-08-17）

**破案经过**
- 用户用 ATK GUI 手动建了"目标定向"传感器（Test for spinning bug.atk 传感器2），
  导出其 XML 作为 Ground Truth：
  - `PointType = 2`（之前试过 2 但目标路径格式不对）；
  - `<AssignedTargets>` 内是 **`<ObjectsPath Path="/Scenario/<场景名>/Facility/<地面站>"/>`**
    （子元素+完整绝对路径），不是内联文本路径；
  - 追踪 = `TargetMode 0`；转发 = `TrackingMode 2`。
- 之前"Targeted 走 XML 不生效"的根因有二：① AssignedTargets 写成了内联文本；
  ② 场景名从 AllInstanceNames 解析时带**尾随空格**（`/Scenario/Scenario1 /Facility/...`），
  导致 ATK Load 挂死（相对路径 `Facility/Hangzhou` 同样会挂死）。

**验证（6 星 × 8h × 杭州 5km，参考值=仰角>0 连续时段合计 8）**
| 用例 | 结果 |
| --- | --- |
| 全局 Targeted（接收/发射/转发 0/1/2） | 各 8 窗口，且逐星分布与 skyfield 参考完全一致 |
| 转发确定性（两遍） | 8 / 8 一致 |
| 仅 33321 逐星 Targeted | HJ-1B 1 窗口（该星仰角>0=1），其余按全局 |
| 相对路径 / 尾随空格 | ATK Load 挂死（已规避） |

**GUI 瞄准/追踪模式语义（回答用户）**
- 瞄准模式：追踪=实时指向目标；固定指向=相对目标加固定姿态偏移（TargetMode=1，写法待样例）。
- 追踪模式：接收/发射/转发 = 按信号方向调整指向；几何覆盖三者都指向目标（实测窗口一致），
  转发多用于中继。用户选"追踪+转发"正确。

**实现**
- worker：`patch_pointing_targeted` 逐传感器把 Pointing 改为 PointType=2 +
  ObjectsPath 全路径（场景名去空格）；Targeted 强制 XML 路线；Define 路线不出现 Targeted。
- atk_ui：GUI 重新开放"Targeted 目标指向"（瞄准模式=追踪，追踪模式=接收/发射/转发）；
  固定指向仍标注"待复核"。

### 10.27 Targeted 固定指向定案（2026-08-17，用户 GUI 场景传感器2）

**结构（来自 Test for spinning bug.atk 传感器2，瞄准模式=固定指向）**
- `PointType=2` + `<Targeted><TargetMode>1</TargetMode><Fixed>...</Fixed>
  <AssignedTargets><ObjectsPath Path="/Scenario/<场景名>/Facility/<地面站>"/></AssignedTargets></Targeted>`。
- 偏置姿态存在 Targeted 内的 `<Fixed>` 子块（四元数 Qs/Qx/Qy/Qz + PointingMethod + Sequence），
  与 Fixed 指向同一套方法枚举（欧拉角/四元数/YPR/AzEl/matrix）。

**实现（校准传感器方案，避免手搓姿态转换）**
- worker：Targeted+固定指向时，先在该场景里用 Connect `Point Fixed <方法> <参数>`
  建一个"校准传感器"，Save 后从 XML 读出它的 `<Fixed>` 四元数块，原样写进目标传感器的
  `<Targeted><Fixed>` 偏移——由 ATK 自己算姿态，不自己实现欧拉角/四元数转换。
- GUI（atk_ui）：固定指向可选"偏置姿态类型"= YPR/Euler/Quaternion/AzEl/matrix（与 Fixed 同款）。

**语义实测（6 星 × 8h × 杭州 5km）**
| 配置 | 窗口 |
| --- | --- |
| 追踪（参考） | 8 |
| 固定指向 单位偏置（YPR 0 0 0） | 1（=天底） |
| 固定指向 YPR 俯仰 30° | 6（两遍一致；与普通 Fixed 侧摆 30° 完全一致） |
| 固定指向 四元数绕 Z 30° | 1 |

结论：ATK 的"目标定向·固定指向"实际是**机身固定姿态 + 关联目标**（不是相对目标方向叠加偏移）；
覆盖窗口与普通 Fixed 指向相同。界面照此实现，用户可放心使用。
**五组偏置逐星对照（Fixed vs 目标定向·固定指向，每组逐星分布完全一致）**
| 偏置 | Fixed | 目标定向·固定指向 |
| --- | --- | --- |
| YPR 0° | HJ-1B:1 | HJ-1B:1 |
| YPR 15° | HJ-1B:1 | HJ-1B:1 |
| YPR 30° | BJ1:2, HJ1B:1, HY1B:1, YG2:1, HJ1A:1 | 同左 |
| YPR 45° | 同 YPR30 | 同左 |
| 四元数绕Y 30° | HJ-1B:1 | HJ-1B:1 |

结论坐实：ATK 的"目标定向·固定指向"与普通 Fixed 几何完全等价（目标仅登记不参与转向）。

### 10.28 SAR「跟踪父高度」接入 + 实测定案：XML 加载路线不生效（2026-08-17）

**需求**：用户要求 GUI 里补上 ATK 的「跟踪父高度」勾选框（SAR 的 父高度/跟踪父高度 二选一）。

**实现（端到端已接上）**
- atk_ui：SAR 参数改为 6 元 `[MinElev, MaxElev, Fwd, Aft, Altitude, Track(0/1)]`，
  勾选「跟踪父高度」后固定父高度输入被隐藏；TOML 持久化同规则（`sensor_params` 原样写回）。
- worker：`build_pattern_block` 的 SAR 块已按 `TrackParentAlt` 写出（与 ATK 自身 Save 的
  XML 完全一致：`Define ... SAR 12 70 30 30 TrackParentAltitude On` → Save 后即
  `<TrackParentAlt>1</TrackParentAlt>`）。
- GUI 在勾选状态下加了如实说明：本版本该开关经 XML 路线暂不生效（见下），保留选项待官方修复。

**决定性实测（1 颗 YAOGAN-50 02，实际轨道约 963 km，8h × 杭州点，毫秒级窗口对比）**
| 配置 | 结果 | 说明 |
| --- | --- | --- |
| TrackParentAlt=1 + Altitude=550000 | 与固定 550km **逐毫秒一致（0.0s）** | 开关被忽略，按 Altitude 固定值算 |
| TrackParentAlt=1 + Altitude=0 | 与固定 550km 仅差 ~7s | 差异来自 Altitude=0 而非跟踪实际高度 |
| TrackParentAlt=0 + Altitude=550000 | 基准 | 固定 550km |
| TrackParentAlt=0 + Altitude=970000 | 与固定 550km 差最大 208.6s | 父高度确实影响窗口 |

结论：ATK 4.0.1-rc.5 的 SAR 只有 XML 加载路线能算出窗口（纯 Connect `Define` 路线 0 窗口，
§10.1/§10.3），而 **XML 加载时 `<TrackParentAlt>` 标志不生效，实际始终用 `<Altitude>` 固定值**。
与 Spinning 相位一样属 rc.5 的序列化/加载缺陷，建议并入官方反馈材料。

**GUI 待用户复核（可选）**：在 ATK GUI 里对 YAOGAN-50 02 建 3 个 SAR 传感器
（跟踪父高度 / 固定 550km / 固定 970km）算同一时段，若勾选项窗口≈970km 组则 GUI 内存内生效
（仅 XML 加载失效）；若≈550km 组则 GUI 同样失效，一并写进反馈。

**补充复测（2026-08-17 晚，worker 子进程正规路线，两行 TLE）**
- 内存 re-Define 路线也失效：Load 后对同一传感器 `Define ... SAR 12 70 30 30 TrackParentAltitude On`
  → 窗口与 XML 原样（track=1/alt=550km）**逐窗口完全一致**，即 rc.5 连内存 Define 也不认该开关
  （与 §10.3 “Define 对 SAR 静默失效”一致），混合路线排除。
- 对照：固定 970km 首窗 00:49:41、固定 550km 首窗 00:46:12（毫秒级），差别明显。

**工程替代方案（已落地，语义可用）**
- 在 atk_engine 层：SAR 勾选“跟踪父高度”时，ATK 开关虽不生效，但软件自动把 `<Altitude>`
  替换为**该星 TLE 平均轨道高度**（`_tle_mean_altitude_km` ×1000，m），`TrackParentAlt` 写 0；
  全局 SAR 跟踪自动展开为逐星参数，逐星覆盖同样处理；结果页给出说明性 warning。
- 验证（生产路径 run_atk_windows，YG-50-02 ≈963km）：跟踪父高度首窗 **00:49:35** ≈ 固定 970km
  （00:49:41），不再等于固定 550km（00:46:12）；逐星覆盖路径结果一致。
- 语义说明：近圆轨道下平均轨道高度≈实际高度；椭圆轨道是近似（仍远好于全局固定 550km）。
  GUI 勾选提示与结果 warning 均已注明“按 TLE 平均轨道高度近似，待官方修复后切换真跟踪”。

### 10.30 Spinning 旋转指向：单实例方案落地（2026-08-17 晚）

**背景**：§10.18 定案 Spinning 初始相位跨实例不锚定（ASLR 相关），GUI/Connect 均无法锚定。
用户拍板尝试“全部放进同一个 ATK 实例”的兜底方案：利用“同实例内相位确定、结果可复现”的性质。

**实测（2 星 × 8h × 杭州，SimpleCone45 + Spinning 0/-90/30° Continuous 6°/s，offset 360）**
- 同一实例内连续算 3 次（含 Reset 后重算）：**逐毫秒完全一致**（1 vs 2、1 vs 3 均 True）。
- 同一配置两个独立实例（生产 runner 路径，提权真机）：19 vs 18 窗口，首窗 00:47:41 vs 00:47:17
  ——跨实例漂移在生产路径同样存在，符合 §10.18 结论。

**落地**
- worker：`build_point_cmd` 新增 Spinning（Connect `Point ... Spinning <Az> <El> <ConeAngle>
  {Continuous|Unidirectional|Bidirectional [Start Stop]} <Rate> <Offset>`），Define 与 XML 两条路线通用。
- runner：检测到全局或逐星指向含 Spinning 时，**强制单实例单分片**（`per_shard=len(recs)`、
  `max_inst=1`），保证一次分析内所有旋转传感器相位一致、可复现。
- GUI（atk_ui）：重新开放“Spinning 旋转”指向（方位角/高度角/锥角/扫描模式/转速/初始偏差角，
  单向/双向可填开始/结束角），并注明“同实例内可复现；跨实例初始相位不锚定，结果为代表性实现”。
- AppTest：Continuous / Unidirectional 两种模式渲染与返回均正常。

**使用口径**：Spinning 结果视为“一次代表性实现”——同一次分析内确定且可复现；不同次运行
（新 ATK 进程）相位可能不同。需要跨运行稳定比较时，优先用相位不变量（总覆盖时长/覆盖率/重访），
或按待办把“多进程取范围/聚合”做起来。

### 10.31 Spinning 规模化：旋转星单实例 + 普通星并行（A/B 模式，2026-08-18）

**问题**：旋转星强制单实例后，若旋转星很多（成百上千颗），单实例串行会明显变慢（约 0.4s/颗建
传感器+Point+Assign）。用户问“旋转传感器太多怎么办”。

**方案 A（默认）——旋转星聚一个实例，非旋转星照常并行**
- runner 按“是否旋转”分组分片：所有旋转星进同一个分片（一个 ATK 实例，相位一致、可复现），
  非旋转星按 `--per-shard` 并行；两者可在同一轮并行（旋转片单独一个端口/进程）。
- 全局指向为 Spinning 时全部星视为旋转 → 退化为单实例单分片。
- 分片改为按索引组构造，`angles`/`sat_cfgs` 按索引切片（不再要求连续顺序）。

**方案 B（可选）——旋转星也分片并行**
- 开启 `--spin-parallel` 或 Spinning 指向参数 `parallel=true`（GUI 勾选框）后，旋转星按普通
  顺序分片，各片相位独立随机，结果视为代表性实现（快，但失去“同实例相位一致”的强保证）。

**实测（3 星：1 颗逐星 Spinning + 2 颗 SimpleCone，8h × 杭州）**
| 模式 | 分片 | 结果 |
| --- | --- | --- |
| A（默认） | 片0=[旋转星]（1 实例）、片1=[2 颗普通星] 并行 | 旋转星 4 窗口；普通片独立计算，正常合并 |
| B（--spin-parallel） | 片0=[旋转星+1 普通]、片1=[1 普通] | 旋转星 14 窗口（相位与该次运行相关，与 A 不同） |

- GUI：Spinning 指向新增“旋转星分片并行（各片相位独立随机，更快）”勾选框，默认关；
  指向 TOML 持久化支持 bool 字段。

### 10.32 逐星卫星姿态（AddAttitude）尝试：Connect 命令层未实现（2026-08-18）

**目标**：给每颗星配置“整星姿态”（卫星本体偏置，如 YPR 俯仰 30°），与传感器级指向解耦。

**实测（rc.5 Connect）**
- `AddAttitude <Sat> YPR "<t0>" <seq> y p r`、`Attitude`、`GetAttitude`：**全部 NACK**；
- 同路径下 `Position`、`SetState` 也 NACK（卫星命令族整体缺失，文档有、实现无）；
- 仅 `SetAttitude <Sat> Standard` ACK（只设姿态模式，不带数据）。
- 这与当初 Targeted / SAR Define 的情况一致：Connect 命令层未实现，只能走 **XML 注入**。

**XML 结构线索**：`DefaultSatellite.atk` 里有 `<Attitude><AttPropType>0</AttPropType><AttGen>
<AttGenType>0</AttGenType><LHAttOffsetQuats>1</LHAttOffsetQuats><LHAttOffsetQuatx>0</LHAttOffsetQuatx>…`
，但全安装示例里没有非默认 `AttGenType` 的存档，非默认写法无 ground truth。

**结论与下一步**：本分支不保留无效代码（已还原）。要做整星姿态，需要用户像 Targeted 那次一样：
在 ATK GUI 里给卫星设一个姿态（如固定姿态 YPR 30°）保存场景，把 .atk 给我；读 XML 后按
“Save → 改 <Attitude> → Load”路线实现（与 SAR/Pattern、Targeted 同款）。

### 10.33 整星标准姿态族落地（2026-08-18，codex/sat-attitude-std）

**Ground truth 来源**：用户把 `Test for spinning bug.atk` 扩充到 10 颗星，其中 GAOFEN 2/8/9
分别设 AttGenType=1/2/3（GUI 保存），其余为 0；ATK 组件头文件 `ICEnum.h` 给出枚举：
`EVeProfile`：0=Nadir+ECF速度约束（固连系VVLH，默认）、1=Nadir+ECI速度约束（惯性系VVLH）、
2=ECI速度对齐+Nadir约束、3=ECF速度对齐+Nadir约束、4=Sun对齐+Nadir约束。

**实现（XML 注入，与 SAR/Pattern、Targeted 同路线）**
- worker：`build_attitude_xml_block` 生成 `<Attitude><AttPropType>0</AttPropType><AttGen>
  <AttGenType>{0-4}</AttGenType><LHAttOffsetQuats>1</LHAttOffsetQuats>+偏移四元数 qx/qy/qz</AttGen>
  <AttGenArray/></Attitude>`；`patch_satellite_attitude` 按卫星名替换/插入 `<Attitude>` 块；
  `need_xml` 检测到任意星有姿态配置时走 XML 路线。
- atk_engine：逐星 attitude 透传；全局 attitude 注入每颗星（逐星优先）。
- atk_ui：面板新增“卫星姿态（可选）”——姿态类型（0-4 中文名）+ 偏移四元数 qx/qy/qz；
  TOML 持久化（profile 整数、offset 列表）。
- loader_app：全局/逐星 attitude 接入 cfg 与 sat_sensor_overrides。

**验证**
- 单元：块生成正确；替换旧 `<Attitude>` / 无块时插入均正常。
- 端到端（YG-50-02 × 8h × 杭州，SimpleCone45）：profile 0 为 4 窗口，profile 4（太阳对齐）
  为 5 窗口且时刻不同 → **AttGenType 在 Load 后真实生效**（与 TrackParentAlt 不同，XML 姿态
  类型被 ATK 尊重）；profile 0 vs 1 窗口相同属合理（两者 Z 轴都对地，SimpleCone 圆对称看不出
  绕天底自转差）。
- AppTest：面板渲染、初始回显、切换类型正常；TOML 读写往返一致。

**说明**：偏移四元数语义（qs 隐含）尚无非零 ground truth，先按“单位四元数由 ATK 内部归一化”
对待，后续有 GUI 非零样例再校准。非标准姿态（整星固定/旋转/目标指向）仍需 GUI 样例，未做。
