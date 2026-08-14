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
