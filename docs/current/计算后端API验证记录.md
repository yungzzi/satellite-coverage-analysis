# 计算后端 API 验证记录

验收日期：2026-11-04。开发基线 main/fd8895c，功能分支 codex/fastapi-backend。
验证对象是新增 HTTP/任务/部署适配，不是重新证明轨道模型精度，也不是研究院云端上线验收。

## 1. 范围与结果

| 检查 | 结果 |
| --- | --- |
| Windows Python 3.13 API 测试 | 31/31 通过，24.26 秒 |
| Linux/amd64 Python 3.12 容器 API 测试 | 31/31 通过，22.96 秒 |
| 旧同机 Skill 自检 | 3/3 通过 |
| 原区域指标自检 | 3/3 通过 |
| 原区域选星穷举/回归 | 7/7 通过 |
| Linux Docker 镜像构建 | 通过；使用锁定依赖和非 root 用户 |
| Linux CBC 实际整数规划求解 | 通过，二元变量约束/目标结果正确 |
| Linux 真实 HTTP 覆盖与任务② | 完成，第二请求命中已有机会窗口缓存 |
| 容器重启、删除并以同卷重建 | 原已完成任务及结果仍可查询 |
| Compose 配置与健康检查 | 配置通过，验收容器 healthy |

API 测试覆盖：六种 operation 与旧入口结果一致、任务② ILP、全局/逐星高级配置和 Targeted、
缺参/非法值/NaN/路径穿越拒绝、64 KiB 请求体限制、API Key/CORS、有界并发入队、
入队前固定 UTC 时间、网格/矩阵规模限制、未完成结果 409、未知 UUID、数据库未就绪 503、
单实例锁、任务超时、进程树清理、worker 崩溃恢复、排队和已完成任务的重启保留。

测试未忽略失败。依赖产生了 Shapely、PuLP 和 Starlette 测试客户端的弃用警告；
本轮没有为了消除警告改写物理引擎，运行依赖已记录到 requirements-api.lock。

## 2. 杭州历史演示复现

输入来自项目实验资料中保留的 parameter-demo-hangzhou 三份输入，复制为
`integrations/http/examples/hangzhou-baseline.json`、`hangzhou-swath.json`、`hangzhou-swing.json`。
固定同一批 20 星、786 个 5 km 网格，2026-08-30 00:00–08:00 UTC，10 秒检测。
Windows 原生 HTTP 服务和 Linux 容器 HTTP 服务都重新计算，不是把历史结果直接作为接口响应。

| 方案 | 城市面积加权时间覆盖率 | 城市过境窗口数 | Linux 引擎总耗时 |
| --- | ---: | ---: | ---: |
| 基线：两组名义天底幅宽 20 km，固定侧偏 0° | 0.1455% | 1 | 8.9898 s |
| 幅宽：B 组改为 30 km | 0.2108% | 1 | 9.1923 s |
| 侧摆：两组 20 km，B 组固定侧偏 +10° | 0.4751% | 3 | 9.1265 s |

对照脚本同时比较 summary_cards、satellite_table、city_pass_window_table、weak_areas、coverage_map、
profile、reproducibility、city 八个结果块。Windows 和 Linux 均与历史快照匹配。
数字比较绝对容差为 1e-6，窗口时间字符串与其他离散字段严格比较。请求 UUID、运行耗时和服务器路径不参与物理结果比较。

这些场景只计算几何机会。结果较低与固定窄幅/少量输入星有关；不能把它当成云量或可采购影像预测。

## 3. 当前默认全池的附加冒烟

Linux 容器限制 2 CPU、2 GiB 内存。使用杭州、100 颗可见光且分辨率不差于 10 m 的候选卫星、
8 小时、5 km、10 秒检测、60 秒决策，覆盖分析完成。

- 网格数：786。
- 返回城市过境窗口：26 条（本次 max_windows=100）。
- 覆盖机会计算：40.9480 秒。
- 引擎总耗时：41.4338 秒；不含排队和 HTTP 传输。
- 机会缓存未命中，属于一次冷覆盖计算。

这是一台本机 Docker 环境、一个城市和一个历史时段的观测，不是所有任务/城市的性能保证。
五项选星的执行时间和可行性依赖目标、K、网格与求解方法，仍须按实际平台负载验收。

## 4. 证据与剩余边界

原始输出保留在开发树 `output/`，不加入 Git：api-tests.xml、api-tests-linux.xml、
api-hangzhou-*.json、linux/api-hangzhou-*.json、api-docker-coverage.json、api-docker-task2.json、
api-docker-hangzhou-default.json。临时验收服务使用 18765/18766 端口，收尾时停止，不作为正式服务地址。

尚未完成或不在本轮范围：研究院实际部署、前端页面集成、Vercel Functions 适配、正式 TLE 更新策略、
真实载荷参数补齐、多用户鉴权、分布式队列与集群并发压测。当前不承诺 Dockerfile 可以直接部署到 Vercel，
也不将离散最优标成连续窗口全局最优。

旧引擎的覆盖地图 description 文案固定写“5 km”。使用其他网格精度时，前端应以
input_summary.grid_size_km 及 city.grid_cell_count 为准；本轮为保持旧入口兼容，没有修改该文案。
