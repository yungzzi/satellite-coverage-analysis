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

1. **SAR / HalfPower：Connect 无头模式算不出覆盖**。`SensorQuery` 确认参数已正确写入
   （SAR: MinElev 12/MaxElev 70/FwdAft 30/Alt 550000；HalfPower: 14.5GHz/1m），
   但 Access 与 Cov 两条计算路径都返回"无分析数据"，连宽口径（SAR 0–90°、60° 波束）也是 0 窗口。
   疑似 4.0.1-rc.5 Connect 计算内核未实现这两种模式（GUI 是否可用待验证；建议向官方确认）。
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
- **SAR/HalfPower/Targeted 在 Connect 无头模式不可用**（配置成功但算不出/命令 NACK），
  这是目前替代自研的最大缺口；若老师要求必须支持这三类，需要向 ATK 官方确认 GUI 行为或升级版本，
  或接受"这几类暂用自研扩展建模"的混合方案。

