# 卫星覆盖与选星计算后端：交付使用说明

这是独立 FastAPI 后端源码包，不需要安装 Skill、Agent 或 ATK。支持覆盖分析、五项选星、
全局及逐星传感器/指向/姿态配置，输出 JSON/GeoJSON，由平台前端绘制地图和表格。
版本与源码提交见 VERSION.json；openapi.json 是构建时从真实应用导出的接口定义，可导入接口工具。
包版本与 API schema 版本独立，前者用于发布，后者用于接口模型。

## 1. 文件与数据口径

- src/coverage_api：HTTP 接口及持久化任务执行。
- src/siriagent_skill：历史命名的共享业务编排，后端确实依赖它，不能删除；使用时无需 Skill 框架。
- src/*.py、data/：必要引擎和冻结城市边界、目录、TLE。
- examples/：从 OpenAPI 导出的13份正式请求示例；日期固定用于历史复现。
- integrations/http/examples/：杭州默认场景和 A/B 幅宽、侧偏三方案。
- docs/current/：部署手册与既有验证记录；integrations/http/DATA_NOTICE.md 说明数据来源与适用范围。
- Dockerfile、compose.yaml：可选的参考部署配置；本包不附镜像。
- SHA256SUMS：包内文件校验；VERSION.json：版本、提交和文件清单。

**内置 TLE 是历史快照，不是实时轨道。** 默认请求若使用今天时间仍会套用快照，不代表今天的可信预测。
正式预测前必须建立 TLE 更新和时效检查。完整边界说明见数据声明及部署手册。

## 2. 直接运行（不使用 Docker）

推荐 Linux/amd64 Python 3.12；Windows Python 3.13 也已验证。先进入解压后的包根目录。
首次安装需要访问 Python 包源，不是完全离线安装包。

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-api.lock
.\.venv\Scripts\python.exe integrations/http/verify_package.py
.\.venv\Scripts\python.exe -m uvicorn coverage_api.app:app --app-dir src --host 127.0.0.1 --port 8000 --workers 1
```

Linux：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-api.lock
.venv/bin/python integrations/http/verify_package.py
.venv/bin/python -m uvicorn coverage_api.app:app --app-dir src --host 127.0.0.1 --port 8000 --workers 1
```

打开 http://127.0.0.1:8000/docs 查看中文说明、Schemas 和请求示例。
在线 Swagger 页面需要加载 CDN 资源；没有网络时可直接阅读 Markdown 或导入 openapi.json，未附离线 Swagger HTML。
不要使用多个 Uvicorn worker 或 --reload。同一状态目录只允许一个服务实例。
需要远端访问时自行设置监听地址，并由平台配置 HTTPS、认证及访问控制。

## 3. Docker（可选）

```bash
docker compose -p satellite-backend up --build -d
docker compose -p satellite-backend ps
docker compose -p satellite-backend logs -f satellite-api
```

默认仅映射本机127.0.0.1:8000，限2CPU/2GiB。Compose 声明的命名卷保存 SQLite 和结果；重建容器不等于删除卷。
停止使用 `docker compose -p satellite-backend down`，不要加 `-v`，除非确实要清空任务和结果。
已有服务占用8000时请先改映射的宿主端口，不要停掉别人的服务。镜像标签由 Compose 示例定义，正式部署可自行调整。

平台后端与计算后端可以是两个容器：放入同一 Docker 网络后，平台通过
`http://satellite-api:8000` 调用计算后端，不需要直接对公网暴露计算容器。
不同 Compose 项目默认网络不相通，需显式连接共享网络；容器里的 localhost 指向自己。

## 4. 接口调用与快速验收

1. GET /readyz 确认 ready；GET /api/v1/capabilities 查看六种操作和参数。
2. POST /api/v1/jobs，Body 为示例 JSON，返回202和job_id、status_url、result_url。
3. GET status_url 轮询 queued/running/completed/failed；完成后 GET result_url 获取JSON/GeoJSON。
4. completed 只表示计算成功；是否达到目标看 feasible；最优性看 optimality。

服务启动后，另开终端，在包根目录执行（Windows 用 .venv/Scripts/python.exe 替换下面 python）：

```bash
python scripts/api_smoke.py --input integrations/siriagent/examples/coverage_quick.json --output output/coverage.json
python scripts/api_smoke.py --input integrations/siriagent/examples/task2_quick.json --output output/task2.json
python scripts/api_smoke.py --input integrations/http/examples/hangzhou-baseline.json --output output/hangzhou-baseline.json
```

前两份采用30km粗网格，仅用于快速跑通调用链；正式示例默认5km、8小时、10秒检测。
地址变化时增加 `--base-url http://127.0.0.1:实际端口`。配置API Key后，客户端和服务端都需设置 SAT_API_TOKEN；脚本自动发送X-API-Key。
全局高级配置、逐星配置和五任务可从 examples/ 或 /docs 的 Examples 下拉菜单选择，先保持历史日期以便复现。

## 5. 前端与部署职责

指标卡使用 summary_cards；卫星使用 satellite_table；窗口使用 city_pass_window_table；
地图使用 coverage_map.geojson；薄弱区使用 weak_areas；保留 assumptions、warnings 和降级/最优性说明。
窗口表示某颗星至少覆盖一个网格代表点的城市过境窗口，不是整市同时覆盖；max_windows 会截断展示条数。
GeoJSON 为 WGS84，经纬度顺序；高德底图需前端统一坐标系。

一个API进程加一个计算进程，当前串行执行。默认最多8个排队/运行任务，总计算时限900秒。
SAT_STATE_DIR 指定本地持久化目录；未设置时为包内 output/api。正式部署须备份、监控磁盘并制定清理策略。
SAT_API_TOKEN 是可选服务级凭据，SAT_CORS_ORIGINS 是允许的前端来源，详见部署手册。
平台侧负责 HTTPS/网关、用户鉴权、调用凭据保管、前端展示和实际主机资源配置。
本版不提供分布式队列、自动扩缩容、自动TLE更新和多用户结果隔离；不承诺所有城市/任务一分钟内完成。

本包用于接入联调；正式上线仍须在目标服务器验收，并落实数据更新、鉴权和备份。
