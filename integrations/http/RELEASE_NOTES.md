# 卫星覆盖与选星计算后端 0.1.0-rc.1

独立FastAPI后端联调版，替代平台必须调用Agent/Skill的接入方式；保留原物理与选星算法。

- 覆盖分析及五项选星，支持全局/逐星传感器、指向、姿态和已实现的约束。
- 提交任务、查询状态、获取JSON/GeoJSON结果；包含城市指标、卫星表、过境窗口和薄弱区域地图数据。
- 单计算worker、有界队列、SQLite状态与结果持久化；服务级API Key和可配置CORS。
- 中文Swagger说明、13份命名请求样例、导出的openapi.json、锁定依赖及参考Docker/Compose。
- 交付源码与冻结数据，不附预构建镜像、虚拟环境、研究资料或本机任务结果。

下载本Release人工上传的 `satellite-coverage-backend-0.1.0-rc.1.zip` 并阅读包根 README.md。
GitHub自动生成的Source code是完整开发仓库，不是本精简交付包。

## 验证与限制

既有Windows/Linux各74项API回归、杭州20星三方案历史结果复现已通过，见包内验证记录。
本Release独立解压包验收的实际证据另附 ACCEPTANCE.md；源码校验见VERSION.json及SHA256SUMS。
若Release未附ACCEPTANCE.md，则不能据本文件宣称独立包验收已经通过。

数据是历史快照，不是今日轨道。正式运行前需要TLE更新、实际主机验收、HTTPS/用户鉴权和持久化备份。
当前单实例串行计算；不支持多Uvicorn worker、无状态实例磁盘持久化或分布式调度。
本版为Pre-release联调版，不代表研究院正式部署和前端集成已验收。
