# ATK 二次开发实验交接文档（Q183）

> 用途：给"ATK 二次开发 PoC"新对话的启动上下文。新对话从本工作树（codex/atk-poc）
> 开始，先读本文件 + AGENTS.md，再动手。

## 1. 背景与目标

- 老师要求更复杂的传感器建模（"卫星姿态改变之后，他能感知的范围更大"，传感器类型/指向都需要扩展）。
- 用户倾向：先试 **ATK 二次开发**，验证可行再决定；不行就回退到自研精细建模扩展。
- 本实验在独立工作树 `codex/atk-poc` 进行，**main 分支不受影响**，随时可交付。

## 2. 已调研的事实（2026-08-12）

### 2.1 ATK 能力（官方文档，见 `reference/atk-doc`）

- 传感器类型 5 种：`SimpleCone`（简单圆锥）、`Rectangular`（矩形）、`HalfPower`（半功率）、
  `Conical`（复杂圆锥：内外半锥角+时钟角）、`SAR`（入射角范围+前后向排除角）。
- 指向模式 6 种：`Fixed`（AzEl/四元数/欧拉角/YPR/矩阵）、`Spinning`（旋转扫描）、
  `Targeted`（目标跟踪）、`GrazingAlt`（入射高度）、`External`（外部文件）。
- 约束：仰角/方位角/距离/角速率/视线/地形遮蔽/太阳约束等。
- 覆盖工具 16 种品质因子（含覆盖时间、多重覆盖、访问次数、重访时间、数据时龄），
  正好覆盖我们选星需要的指标。
- 报告：覆盖时段报告 / 覆盖间隔报告（起止时间+时长，毫秒精度）→ 直接就是选星输入格式。
- 二次开发三种模式：Connect（连 GUI）、Component（嵌入式库）、MBSE；支持 C++/Java/Python/MATLAB。

### 2.2 本机实测结果

| 项目 | 结果 |
| --- | --- |
| Connect 模块（`_ATKConnectModule.pyd`）在项目 Python 3.13 导入 | 成功 |
| Connect 连接运行中的 ATK | 失败：`atkOpen()` 返回 -1 / Socket Error |
| Component 模块在项目 Python 3.13 导入 | 成功 |
| Component 无头创建 根→场景→卫星→SGP4 传播器 | 成功（不需要开 ATK GUI） |
| Component 无头导入 TLE（`AddSegsFromFile`） | 硬崩溃（缺 AstroData 数据目录） |
| `ATKServerConsole.exe` 无头服务端 | 失败：需要独立注册码（机器码绑定），未注册 |

结论：
- **Connect 模式**：需要 ATK GUI 打开，且每次要在菜单"集成→客户端窗口"打开 Connect 服务，
  Python 才能连上（这是 Socket Error 的原因）。
- **Component 模式是最有前景的路线**（免 GUI、可嵌入我们的进程），但数据目录配置没解决。
- TLE 导入崩溃原因：ATK 找不到 `AstroData`（约 1.2 GB，在 ATK 根目录）。官方文档要求把
  `AstroData` 复制到 `IntegratingWithATK/component/bin` 旁；GUI 启动时自动配置，无头嵌入需要
  自己解决（复制 AstroData 或找配置机制，如注册表/环境变量/启动文件 `atk_startup_file.txt`）。

### 2.3 关键风险

1. **许可**：官方条款明确"仅限个人与非商业用途，不得修改/复制/分发/创建衍生作品（除非个人
   非商业用途）"。交付客户前必须邮件确认授权（`atk_service@163.com`）；注册码与机器码绑定，
   换电脑要重新申请，每台客户机都要人工走一遍。
2. **性能无公开基准**：公开论文最大案例是 7 颗×3 天；没有 100/1000/10766 颗的基准。
3. **文档少、版本迭代快**：本机装 v4.0.1-rc.5，官方已到 v4.1；接口可能有变化。

## 3. 环境信息

- ATK 安装：`D:\ATK航天任务设计工作箱\ATK-v4.0.1-rc.5-windows-x64\ATK-4.0.1-rc.5`
- Connect Python 模块：`<ATK根>\IntegratingWithATK\connect\Python\`（`ATKConnectModule.py` + `_ATKConnectModule.pyd`）
- Component Python 模块：`<ATK根>\ATKComponentPythonModule.py` + `_ATKComponentPythonModule.pyd`
- 参考文档：本工作树 `reference/atk-doc`（来自 gitcode.com/jinke18/atk-doc，MIT 协议；已 gitignore，不入库）
- 实验脚本：本工作树 `experiment/atk/`（冒烟测试、逐步定位脚本等）
- 注意：stdin 管道传中文路径会乱码，实验脚本要写成 UTF-8 BOM 文件再运行；
  模块拷贝到纯 ASCII 路径（如 `%TEMP%\atkpy`）可绕开中文路径问题。

## 4. PoC 验收标准（4 条，都过了才值得迁移）

1. **链路跑通**：Python 3.13 内用 Component 模式建场景→导入 100 颗 TLE→加传感器→点目标
   8 小时覆盖→拿到覆盖窗口列表。
2. **正确性对拍**：与自研引擎/N2YO 对同一颗星同一时段，窗口起止偏差应在秒级。
3. **性能三档**：100 颗、1000 颗、10766 颗（星链）分别计时；若 1000 颗要跑几十分钟则否决。
4. **许可确认**：邮件询问 ATK 官方"作为项目交付给客户，客户自行注册安装 ATK、我们的软件
   调用其接口是否允许"，答复留档。

## 5. 关键 API 备忘（Component 模式）

```python
ATKModule.SetSaveFileBasePath(os.getcwd())
ATKModule.SetCallCodeType("python")
root = ATKModule.IAtkObjectRoot()
sc = root.GetChildren().New(ATKModule.eScenario, "Name")
sc.SetTimePeriod("12 Aug 2026 00:00:00.000", "12 Aug 2026 08:00:00.000")
sat = sc.GetChildren().New(ATKModule.eSatellite, "JILIN-1")
sat.SetPropagatorType(ATKModule.ePropagatorSGP4)
prop = sat.GetPropagator()                      # IVePropagatorSGP4
prop.GetCommonTasks().AddSegsFromFile("40961", tle_path)   # 目前卡在这里
sensor = sat.GetChildren().New(ATKModule.eSensor, "Sensor1")
sensor.GetCommonTasks().SetPatternSimpleConic(10, 0.1)
fac = sc.GetChildren().New(ATKModule.eFacility, "Hangzhou")
fac.AssignGeodetic(30.25, 120.17, 0.0)
acc = sensor.GetAccessToObject(fac)
acc.SetAccessTimePeriod(start, stop)
acc.ComputeAccess()
acc.ComputedAccessIntervalTimes()
```

Connect 模式命令（需要 GUI + 客户端窗口）：`New` / `SetAnalysisTimePeriod` / `ImportTLEFile` /
`Define`（传感器）/ `Cov ... Asset ... Assign` / `Cov ... Access Compute` / `Cov_RM`（报告），
详见 `reference/atk-doc/二次开发教程/2-二次开发CONNECT模式/`。

## 6. 遗留问题

- AstroData 无头配置方案（复制 1.2 GB 到 component/bin，或找配置机制）。
- 卫星名含空格（如 "JILIN-1 03"）在对象路径里的引用问题。
- 大批量 TLE 导入后逐个加传感器的耗时。
- 区域（多边形）覆盖 vs 点覆盖在 ATK 里的实现方式。
- 传感器指向（Spinning/Targeted 等）的命令/API 用法确认。

## 7. 与主工程的关系

- **复用**：`satellite_loader.py`（数据层）、`satellite_selection.py`（选星层，只吃覆盖窗口）。
- **替换**：`coverage_analysis.py` 的几何引擎部分（SGP4 外推+圆锥判定）换成 ATK 覆盖计算。
- AGENTS.md 第 4 节"弃用 ATK 仿真方案"的旧记录，等 PoC 结论出来后更新，本实验期间不改。
