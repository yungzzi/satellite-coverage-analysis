# 文档导航

本目录按“当前主线、长期参考、历史归档”分层。先看本页，再只打开与当前任务有关的文件；
不要把历史资料当成当前需求。

## 当前主线

- [当前项目状态与决策](当前项目状态与决策.md)：一页式目标、边界和下一步。
- [当前答疑记录](答疑记录.md)：Q184 起的 SIRIAgent/区域覆盖决策。
- [SIRIAgent 区域时间覆盖与选星实现方案](current/SIRIAgent区域时间覆盖与选星实现方案.md)：开发顺序和模块接口。
- [SIRIAgent 区域时间覆盖与选星数学依据](current/SIRIAgent区域时间覆盖与选星数学依据.md)：区域指标与选星约束。
- [SIRIAgent 同机 Skill 使用与验收](current/SIRIAgent同机Skill使用与验收.md)：当前可调用入口、验收命令、Agent 用法和局限。
- [浙江 11 地市边界与网格基线](current/浙江11地市边界与网格基线.md)：边界来源、GCJ-02→WGS84 转换、哈希与全市 5 km 网格验收。
- [SIRIAgent 后端部署白话说明](current/SIRIAgent后端部署白话说明.md)：当前同机决策，以及 FastAPI、Docker、Vercel 等跨机备用概念。

## 长期参考

- [理论报告](reference/理论报告.md)：TLE/SGP4、覆盖与旧选星的物理和数学依据。
- [验证报告](reference/验证报告.md)：现有引擎的验证结论。
- [自研复杂传感器理论报告](reference/自研复杂传感器理论报告.md) 与
  [白话导读](reference/自研复杂传感器报告·白话导读.md)：当前自研几何模型依据。
- [缓存结构说明](reference/loader/缓存结构说明.md)：TLE 加载器仍在使用的缓存规则。
- [文献目录](reference/文献/README.md)：理论报告所依据的本地文献与获取状态。

## 历史归档

`history/` 保留可追溯证据，不是默认阅读入口：

- `history/答疑记录-Q001-Q183.md`：SIRIAgent 前的问答与设计过程；
- `history/ATK/`：ATK 实验、对拍与官方资料；仅用于本机对照；
- `history/旧单点流程/`：旧流程图、演示材料与过期待办。

历史资料只有在用户要求追溯旧决定、核对 ATK 对拍，或当前文档无法解释问题时才读取。
