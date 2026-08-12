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