# test_data 说明
- jilin1_all.tle：吉林一号（长光）全量 TLE 测试文件，共 58 颗，选星/覆盖测试基准。
  构成：JILIN-1 正式命名 36 + JILIN-01 宽幅02B 六颗 + JILIN GAOFEN OBJECT A~H 八颗
        + 2026-134A~H 八颗（2026-06-15 发射的文物01/安铁03/彩云光学01/利川红等，未命名占位）。
- 快照时间：2026-08-06（来自当日 CelesTrak active 全集）。
- TLE 会随时间老化；算法开发用此固定文件即可，正式分析前建议重新生成
  （从最新全集按"名字含 JILIN 或 NORAD 69538~69545"提取，见 Q96/Q99）。

- china_remote_sensing_all.tle：中国遥感卫星全集快照（测试基准，303 颗，快照 2026-08-08，来自 CelesTrak active 全集）。
  构成：{'BEIJING': 2, 'HAIYANG': 10, 'YAOGAN': 163, 'HUANJING': 2, 'ZIYUAN': 4, 'GAOFEN': 36, 'JILIN': 50, 'SUPERVIEW': 13, 'ZHUHAI': 5, 'LUOJIA': 1, 'TIANHUI': 14, 'LUDI TANCE': 3}。
  口径：名字以 YAOGAN/GAOFEN/JILIN/HAIYANG/ZIYUAN/TIANHUI/SIWEI/ZHUHAI/LUOJIA/LUDI TANCE/HUANJING/QIANSHENG/BEIJING/SUPERVIEW/XINJIAN/CHONGXIN 开头。
  注意：仅含公开 TLE 的在轨星；不含未公开编目/已退役星。

- siriagent_110_snapshot.tle：SIRIAgent 平台 110 星目录的固定 TLE 快照，共 110 颗，冻结于
  2026-08-31。平台 bootstrap 只提供 NORAD 与 TLE 可用状态、不提供两行正文；本文件按 NORAD 从
  当日本地 CelesTrak active.tle 匹配，110/110 完整。必须与
  data/platform/siriagent_110_catalog.json 的 tle_snapshot_sha256 一起验收，不用于替代正式运行时的
  TLE 更新策略。
