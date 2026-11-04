"""直接显示在 /docs 中的操作手册。内容以当前实现为准，不改变算法。"""

API_GUIDE = """
## 使用顺序

1. 展开 **POST /api/v1/jobs**，在 **Examples** 下拉菜单选择完整示例，再点击 **Try it out / Execute**。
2. 返回202后，复制 **job_id** 到 **GET /api/v1/jobs/{job_id}** 查询状态。
3. 状态为completed后，使用 **GET /api/v1/jobs/{job_id}/result** 取JSON/GeoJSON。
   completed仅表示计算完成；feasible=false是业务目标未满足，不等于服务器出错。
4. **Schemas → AnalysisInput / SensorProfile / PointingConfig / AttitudeConfig / ObservationConstraint**
   可展开查看每个字段的中文含义、单位与条件。Swagger是JSON调试界面，不是完整配置表单，切换模式不会自动改写JSON。

示例用2026-08-30的历史时段和冻结TLE做复现，**不是今天的未来预测**。
不传start_utc时，在提交时固定当前UTC整分钟；正式预测还需另行配置新鲜TLE。
坐标为WGS84；地图前端叠加其他坐标系底图时需统一坐标系。

## 默认值与五项选星

省略可选项时：8小时、5 km、10秒检测、60秒决策、可见光、分辨率不差于10 m、K=1。
分辨率只筛候选卫星，不进入几何。K目前支持1、2、3。
参数与默认值的机器可读版本见 **GET /api/v1/capabilities**；可传operation只查一项任务。
数值百分比写5表示5%，不要写0.05。所有操作均必须提供operation和city_adcode。

| operation（兼容缩写） | 目标 | 额外必填 |
| --- | --- | --- |
| coverage_only | 全池覆盖，不选星 | 无 |
| task1 | 时间覆盖率达标下最少卫星 | temporal_coverage_target_pct |
| task2 | 最长等待达标下最少卫星 | mrt_target_minutes |
| task3 | 给定N，最大化达标面积 | n_budget、temporal_coverage_target_pct |
| task4 | 给定N，缩短目标面积比例的最长等待 | n_budget |
| task5 | 给定N，缩短面积加权平均等待 | n_budget；method只能greedy |

temporal_coverage_target_pct是**每格在整个分析时段内的时间覆盖率**，不是城市面积比例。
area_target_pct是达标面积比例。mrt_target_minutes是最长连续不可见时间（分钟），不是最小重访时间。
任务①②默认先全部面积，再显式尝试90%降级；allow_area_fallback=false关闭该自动尝试。
method通常支持greedy/ilp/both，任务④默认ilp，任务⑤仅greedy。
selection_time_limit_s是每次ILP时限，**不是整个HTTP任务的总时限**。
贪心结果不自动证明最优，离散最优也不等于原始连续窗口全局最优，需查看optimality。

## 全局与逐星配置

`sensor_profile`覆盖全局Profile；`satellite_profile_overrides`数组按`norad_id`覆盖指定星。
允许部分Profile字段，未给字段继承profile_slug的基础配置。**pointing、attitude、constraints作为整个对象/列表替换，
不做逐字段深合并**。不传某字段表示继承；pointing/attitude显式null可清除，constraints用[]清除。
norad_id不会自动把卫星加入候选池；如提供satellite_ids白名单，也必须包含该星。
**重要：默认目录仅在未提供全局sensor_profile时，才自动派生逐星矩形幅宽参数。传入全局sensor_profile后进入统一高级模式，
不再自动派生目录几何；不能只传一个pointing就假定仍使用原目录幅宽。** 统一高级模式应明确sensor_type以及swath_km或sensor_params。
如希望保留目录几何、只改某些星的指向，用satellite_profile_overrides，省略全局sensor_profile。
目录模式SAR缺参不会降为圆锥。高级参数是场景配置，不代表真实厂家参数；更换sensor_type不会自动改变目录筛选类别。

### 传感器几何

| sensor_type | sensor_params顺序 | 单位 |
| --- | --- | --- |
| SimpleCone | [视场半角] | ° |
| Rectangular | [跨轨半角, 沿轨半角] | ° |
| Conical | [内半角, 外半角, 起始clock角, 结束clock角] | ° |
| HalfPower | [频率, 口径] | GHz、m |
| SAR | [目标点最小仰角, 最大仰角, 前向排除角, 后向排除角] | ° |

矩形可改用swath_km（名义天底跨轨**全宽km**）与along_track_half_angle_deg。
前者优先于sensor_params[0]，后者优先于sensor_params[1]；侧偏后实际地面宽度由几何决定。
当前SAR数组直接约束目标点仰角ε，**不是直接输入入射角i**。相对当地法线的入射角满足i=90°−ε，
例如入射角30°～45°对应仰角45°～60°。SAR分支不叠加min_elevation_deg字段的门槛；需要额外门槛可用ElevationAngle约束。
当前SAR是仰角带与前/后向排除角模型，未把指向转换成完整左右视/斜视成像模式；不要据此宣称支持完整SAR行为调度。

### 传感器指向 pointing

| type | 字段和顺序 | 行为 |
| --- | --- | --- |
| Fixed | method + params；YPR示例 ["YPR",0,0,10] | 相对体轴固定，随轨道移动 |
| Targeted | type即可；不要传target/target_lat/target_lon | 整个区域共同跟踪网格代表点的面积加权形心 |
| Spinning | azimuth/elevation、cone_angle、rate、offset、scan | 绕指定轴连续转动、到边界停留或往复扫描 |
| GrazingAlt | azimuth（°）、altitude（m） | 按给定掠射高度确定视线，可能没有地面窗口 |

Fixed默认method=YPR，params必须含**4项**：顺序标记、yaw、pitch、roll，后3项单位°。
例：["YPR",0,0,10]，不要删掉第一个"YPR"。其他表示法见PointingConfig.params说明。
在attitude.profile=0下，全零朝天底，roll=+10表示本模型的固定侧偏，不代表真实卫星已执行机动。

Spinning的**rate单位rad/s**，其余角度字段单位°；0.001745329252 rad/s约为0.1°/s。
elevation=-90表示旋转轴沿体+Z；profile=0时朝天底。cone_angle是视轴离轴角，不是视场半角。
scan=Continuous连续转动；Unidirectional到stop_angle后停留；Bidirectional在起止角间往复。
有界模式须stop_angle>start_angle。offset是从分析起点锚定的相位，往复模式offset=0从start_angle开始。
速度太快、检测步长太大会漏短窗口；请结合视场选步长，服务默认最小检测间隔5秒，限制值见capabilities。

### 卫星参考姿态 attitude

| profile | 体轴参考 |
| --- | --- |
| 0 | 天底+地固速度水平投影（默认） |
| 1 | 天底+惯性速度水平投影 |
| 2 | 惯性速度定向+天底约束 |
| 3 | 地固速度定向+天底约束 |
| 4 | 太阳对齐+天底约束 |

attitude.offset可用[qx,qy,qz]三分量四元数（不是4项），或展开的YPR/Euler/Quaternion对象。
YPR对象示例：{"type":"YPR","seq":"YPR","params":[0,0,5]}；注意它是3个角度，
与Fixed.params含顺序标记的4项格式不同。姿态偏置和pointing偏置叠加。
这些是参考体轴/给定规律，不包含自动机动寻优、稳定时间、姿态遥测或任务排程。

### 附加约束 constraints

每项为name与params，例如 {"name":"SunGroundElevAngle","params":"Min 10"}。
范围格式：Min 数值 Max 数值，可省略其中一个界；可追加ExcludeIntervals取反。
各约束逐时刻求交，不是放宽并集。

| 名称 | 含义和单位 |
| --- | --- |
| ElevationAngle / AzimuthAngle | 目标点观测仰角/方位角，° |
| SunGroundElevAngle | 目标点太阳高度，°；白天条件可用Min 10 |
| SunElevationAngle / LunarElevationAngle | 卫星处太阳/月球高度，°，不是目标点太阳高度 |
| Range / RangeRate | 星地距离km / 距离变化率km/s |
| AzimuthRate / ElevationRate / AngularRate | 角速度，°/s |
| Altitude | 目标点相对球面高度km，不是轨道高度 |
| PropagationDelay | 传播延迟s |
| LOSSunExclusion / LOSLunarExclusion / BSSunExclusion / BSLunarExclusion | 视线或视轴的日/月避让角，° |
| LineOfSight / fieldofview | 不取消固有地球遮挡/视场判定底线 |
| TerrainMask | 兼容占位，**DEM未实现，此项不产生地形遮挡效果** |

## 输出和服务边界

结果包含summary_cards、satellite_table、city_pass_window_table、weak_areas与coverage_map.geojson。
某星至少覆盖一个网格代表点就计入该星的城市窗口，不表示全市同时覆盖。
max_windows默认10、最大100，是截取的窗口条数上限。真实配置见profile.effective_config。
错误422是输入/资源校验，429表示队列满；失败任务查status和error，不将其当成物理不可行。
API仅支持服务器预设TLE源，不开放任意文件路径。
此版按常驻服务器Docker部署；Vercel运行时需另行适配持久化和任务执行。
"""
