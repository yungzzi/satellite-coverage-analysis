# 卫星覆盖与选星计算后端

2026-11-04 起，主交付方式改为传统 HTTP 后端。前端不再需要 Agent、Skill 注册、slug 或 script_id。
原 Skill/CLI 保留用于兼容和回归。覆盖、传感器、姿态与五项选星仍调用原业务编排，没有重写物理算法。

## 1. 本机运行

在开发代码仓库根目录执行，使用其独立虚拟环境，不要在外层资料目录运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-api.txt
.\.venv\Scripts\python.exe -m uvicorn coverage_api.app:app --app-dir src --host 127.0.0.1 --port 8000 --workers 1
```

浏览器访问 `http://127.0.0.1:8000/docs`。在 Swagger 中选择 `POST /api/v1/jobs`，点 Try it out，提交：

页面现在内置完整参数手册，并有 **13 个命名示例**：展开 POST 后，在 Request body 的 **Examples** 下拉菜单
选择覆盖分析、五项选星、固定侧偏、形心跟踪、旋转/往复扫描、掠射、附加约束或 A/B 逐星配置。
可以[直接定位提交接口](http://127.0.0.1:8000/docs#/analysis/submit_api_v1_jobs_post)。
下方 Schemas 可展开 SensorProfile、PointingConfig、AttitudeConfig、ObservationConstraint 和 SatelliteProfileOverride。
Swagger 是 JSON 调试编辑器，不是动态表单，切换 pointing.type 不会自动补齐必填值；优先从对应示例开始。

```json
{
  "operation": "coverage_only",
  "city_adcode": "330100",
  "start_utc": "2026-08-30T00:00:00Z",
  "analysis_horizon_h": 8,
  "grid_size_km": 5,
  "detection_step_s": 10,
  "max_windows": 100
}
```

该例使用历史冻结 TLE 做杭州可重复验收，不是今天的未来预测。省略 start_utc 时使用提交当下 UTC 整分钟，
并在入队前固定，排队不会移动分析时段。生产预测必须另行建立 TLE 更新与时效检查，目前不自动下载新 TLE。

返回 `202` 和 `job_id`，按返回的 `status_url` 轮询。状态为 completed 后，再 GET `result_url`。
在 `GET /api/v1/capabilities` 查看任务、各任务必填字段、预设、城市、默认值和服务资源限制。

## 2. 接口约定

| 请求 | 用途 |
| --- | --- |
| GET /healthz | 轻量存活检查，不运行引擎 |
| GET /readyz | worker、CBC、必需数据和 SQLite 可用性检查 |
| GET /api/v1/capabilities | 全部任务与参数，支持 operation 查询参数 |
| POST /api/v1/jobs | 校验并排队，返回 202、Location、job_id |
| GET /api/v1/jobs/{job_id} | queued / running / completed / failed，UTC 状态时间及错误码 |
| GET /api/v1/jobs/{job_id}/result | 完成的原始 JSON/GeoJSON，未就绪返回 409 |

输入沿用原字段：operation、city_adcode、start_utc、analysis_horizon_h、卫星名单、目录筛选、
profile_slug、sensor_profile、satellite_profile_overrides、精度和各任务参数。
未知顶层字段拒绝，不开放任意服务器文件路径 tle_path，也不接收直接上传 TLE。
可选字段为 null 时按未提供处理。整数不接受小数，布尔值使用 JSON true/false。

五任务 operation 的完整值如下，也兼容 task1～task5 缩写：

| operation | 额外必填参数 |
| --- | --- |
| coverage_only | 无 |
| task_1_min_satellites_temporal | temporal_coverage_target_pct |
| task_2_min_satellites_mrt | mrt_target_minutes |
| task_3_max_qualified_area | n_budget、temporal_coverage_target_pct |
| task_4_min_area_quantile_mrt | n_budget |
| task_5_min_area_weighted_art | n_budget，method 仅 greedy |

其中时间覆盖率门槛是每个网格在分析时段内的时间比例。area_target_pct 是达标面积比例。
mrt_target_minutes 是最长连续不可见时间上限，不是最小重访时间。任务⑤使用面积加权平均等待。
默认 8 小时、5 km、10 秒检测、60 秒决策、最大分辨率 10 m，默认值仍由原 Schema 维护。

状态与错误：422 参数/缺参/资源限制，401 API Key 错误，404 不存在，413 请求体过大，429 队列满，
503 未就绪。错误使用 detail.code 和 detail.message；字段类型错误另给 detail.fields，条件缺参给 missing_fields。
计算异常使任务 failed，错误详情只写服务器日志。目标物理不可行仍属于 completed，结果的 feasible=false。
不要把 completed、feasible、optimality 三者混为一谈。

## 3. 高级传感器与姿态

仍支持全局 sensor_profile 和用 norad_id 标识的逐星 satellite_profile_overrides。
pointing、attitude 和 constraints 都是整体替换，不做深合并。默认目录下，传入全局 sensor_profile 后，
原有代码进入统一高级模式，不再自动派生目录逐星幅宽；因此只想保留目录几何、改部分星的指向时，
应省略全局 sensor_profile，使用逐星覆盖。统一高级模式请明确模型和几何参数，不能只传一个 pointing 就假定仍沿用目录幅宽。
模型包括 SimpleCone、Rectangular、Conical、HalfPower、SAR；指向包括 Fixed、GrazingAlt、Spinning、Targeted。
姿态使用 attitude.profile 和 attitude.offset；给定参数进入几何计算，不自动优化机动或拍摄排程。
Targeted 是整座区域共享面积加权网格形心，不是每格独立指向。

光学幅宽使用矩形足印，swath_km 表示名义天底跨轨全宽。SAR 必须给原生入射角带，不能退回圆锥。
目录中缺参 SAR 继续按原策略排除/拒绝；接口化没有补出真实载荷参数。
具体到现有引擎，SAR 数组约束的是目标点仰角带和前后排除角，不是直接输入入射角；
入射角范围需要按同一模型的竖直方向换算为仰角范围。完整字段顺序与限制见 /docs。
TerrainMask 仍是未生效占位，不代表已有 DEM 遮挡；新增参数文档不会自动补齐这类物理能力。
结果的 profile.effective_config 回显实际配置。分辨率只筛选候选，不改变几何。

## 4. 前端展示约定

- summary_cards：城市汇总指标。
- satellite_table：卫星与窗口统计。
- city_pass_window_table：逐星城市过境窗口。某星至少覆盖一个代表点即计入，**不表示整市同时覆盖**。
  返回数量受 max_windows 限制（默认 10，最多 100）；前端应明确“最多显示多少条”，不能当作无限制完整窗口下载。
- coverage_map.geojson：正式地图数据，前端按 available_metrics 切换时间覆盖率、最长等待、平均等待、达标状态。
  GeoJSON 坐标为 WGS84，经纬度顺序，叠加高德底图时前端必须统一坐标系。
- weak_areas：最薄弱区域的定位坐标及指标。
- assumptions、warnings、optimality、used_area_target_pct：必须保留的口径和严格/降级说明。
- debug_artifacts：仅调试时返回，普通展示无需开启。

结果不再依赖 view_html。API 仅将服务器 TLE 绝对路径替换成文件名，其他业务字段保持原语义。

## 5. Docker 部署

仓库根目录执行：

```powershell
docker compose up --build -d
docker compose ps
docker compose logs -f satellite-api
```

默认仅发布 `127.0.0.1:8000`，适合本机验收或由 Nginx 反向代理。生产不要未经鉴权直接开放到公网。
镜像用 requirements-api.lock 固定依赖版本，本机也可用 `pip install -r requirements-api.lock` 复现。
遇到 PyPI 下载缓慢，可执行 `docker compose build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` 后再启动。
镜像只复制最小运行模块与冻结数据，不包含 ATK、GUI、Excel、资料或本机 .venv。容器使用非 root 用户。
SQLite 与结果保存在命名卷 satellite-results，重建镜像不丢结果。`docker compose down` 不删除卷，
不要使用 `down -v`，除非明确要清空任务与结果。正式上线应备份该卷并监控磁盘；首版未自动清理历史任务。

部署参数使用环境变量：

| 变量 | 默认值 / 作用 |
| --- | --- |
| SAT_STATE_DIR | 本机 output/api，镜像内 /var/lib/satellite-coverage |
| SAT_API_TOKEN | 空；设置后业务接口要求 X-API-Key，Swagger 可用 Authorize |
| SAT_CORS_ORIGINS | 空；跨源前端域名以逗号分隔，不默认放行所有来源 |
| SAT_MAX_PENDING | 8，queued 和 running 合计上限 |
| SAT_JOB_TIMEOUT_S | 900，从开始执行计时，含加载、覆盖、选星和结果写出 |
| SAT_MIN_GRID_KM | 2.5，API 最小网格边长 |
| SAT_MAX_HORIZON_H | 24，API 最大分析时长 |
| SAT_MIN_DETECTION_STEP_S | 5，最小覆盖检测步长 |
| SAT_MAX_GRID_CELLS | 5000，裁剪网格数量上限 |
| SAT_MAX_GRID_TIME_CELLS | 2000000，网格数乘决策时间片数量上限 |

这些是服务资源保护，超出明确返回 422，不静默降低精度。引擎本身支持范围没有改动。
Compose 示例只映射 token、CORS 和总时限，如需覆盖其他变量，在 environment 中添加对应项。
外部 HTTP 身份认证/TLS/限流/用户隔离由平台网关负责；当前 API Key 是服务级凭据，不是完整多用户权限系统。
不要把服务级 API Key 写进前端 JavaScript。推荐由平台后端/网关持有凭据并转发请求。
Vercel 前端使用 HTTPS 时，对外 API 也需要 HTTPS 入口，避免浏览器阻止混合内容请求。
本轮容器验收环境为 Linux/amd64；其他 CPU 架构需要另测 CBC 与数值依赖。

## 6. 进程、恢复与容量边界

### 关于 Vercel

截至 2026-11-04，Vercel 已支持 Dockerfile 容器运行时，但仍属于无状态 Functions，
不能据此推断本项目“常驻 worker + 本地 SQLite + 持久化卷”可以不改动直接运行。
当前 Docker 版以普通常驻 Linux 主机为验收目标，前端仍可留在 Vercel 经 HTTP 调用。
若后端也必须放 Vercel，需要另行验证运行时限制，并适配外部持久化存储和任务执行方式；本轮不擅自切换架构。
依据：[Vercel 容器部署说明](https://vercel.com/kb/guide/does-vercel-support-docker-deployments)。

### 当前任务执行方式

一个 Uvicorn API 进程，加一个常驻计算进程，计算串行执行。轻量监督线程检测退出和超时。
只能 `--workers 1`，不要使用 --reload；相同数据目录的第二个实例会被文件锁拒绝。
常驻 worker 可复用原来最近两份覆盖窗口缓存，API 进程不持有窗口对象。

任务状态写 SQLite，结果先写临时文件再原子替换。重启时已排队任务继续，运行中任务明确失败，
不自动重算或伪装成功，完成结果仍可查询。总时限超过会终止计算进程树（含 CBC），随后重建 worker。
服务退出会结束本服务进程，不影响其他软件。客户端断开连接不会取消已接受任务。
暂不提供取消任务、自动重试、分布式队列、多机共享卷或跨主机 SQLite。需要并发扩容时再替换调度层。

## 7. 自动验收

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-api-test.txt
.\.venv\Scripts\python.exe -m pytest tests/test_coverage_api.py -q
.\.venv\Scripts\python.exe src/validate_siriagent_skill.py
.\.venv\Scripts\python.exe scripts/api_smoke.py
```

前两项不要求服务器已启动；api_smoke.py 要求目标 API 正在运行。smoke 默认使用历史杭州粗网格验收样例，
仅检验调用链，不代表正式 5 km 精度。通过 --input 指定其他 JSON，--base-url 指定地址，--output 保存原始结果。
设置了服务 API Key 时，客户端也设置同值 SAT_API_TOKEN。Windows 与 Linux 必须分别检查 CBC 和真实计算，
不能把健康检查或镜像构建成功当成物理/业务验收通过。

高级配置现在通过嵌套模型校验：错误的 YPR 数组长度、未知约束名、非法扫角区间等在入队前返回 422。
原规范 JSON 格式不变，没有增加另一个“高级配置协议”；未提供的字段不会被文档默认值自动写入计算参数。

`integrations/http/examples/` 提供历史杭州默认全池、20 星基线、B 组加宽幅宽、B 组固定侧摆四份输入。
例如：`python scripts/api_smoke.py --input integrations/http/examples/hangzhou-swing.json`。
需要与历史 PPT 数据核对时，先按三方案分别保存为 `output/api-hangzhou-baseline.json`、
`output/api-hangzhou-swath.json`、`output/api-hangzhou-swing.json`，再运行
`python scripts/compare_api_demo.py --reference-dir <历史 parameter-demo-hangzhou 目录>`。
历史大结果不重复加入仓库；实测结果见 [API 验证记录](计算后端API验证记录.md)。

参考：[FastAPI 请求模型](https://fastapi.tiangolo.com/tutorial/body/)、
[HTTP 测试](https://fastapi.tiangolo.com/tutorial/testing/)、
[Python multiprocessing](https://docs.python.org/3/library/multiprocessing.html)。
