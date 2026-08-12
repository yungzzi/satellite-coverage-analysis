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
