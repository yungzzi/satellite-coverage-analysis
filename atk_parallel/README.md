# ATK 无头并行覆盖工具（atk_parallel）

基于 ATK 4.0.1 无头模式（`ATK.exe --headless --port N`）的并行覆盖分析工具：
动态分片 → 多实例并行 → 失败重试 → 合并输出（含 NORAD/名字映射）。

## 用法

```bash
cd <atk-experiment 根目录>
.venv/Scripts/python.exe -m atk_parallel.runner \
    --tle data/china_remote_sensing_all.tle \
    --t0 "12 Aug 2026 00:00:00.000" --t1 "12 Aug 2026 08:00:00.000" \
    --lat 30.25 --lon 120.17 --fov 45 \
    --out-dir output
```

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| --tle | 必填 | TLE 文件，**三行（含名字）或两行均可**，自动转两行并保留名字映射 |
| --t0 / --t1 | 必填 | 分析时段（UTC，`"12 Aug 2026 00:00:00.000"` 格式） |
| --lat / --lon | 30.25 / 120.17 | 目标点（ATK 点地面站） |
| --fov | 45 | 默认传感器半角（度，SimpleCone） |
| --per-shard | 1000 | 每片卫星数（实测 500~1000 为性能/稳健甜区） |
| --max-inst | 自动 | 并行实例上限 = min(核数, 可用内存/1.2GB, 12) |
| --retries | 2 | 失败片自动重试次数 |
| --shard-timeout | 1200 | 单片超时（秒） |
| --out-dir | output | 输出目录 |

## 输出

- `output/merged_windows.csv`：全量窗口（norad, name, start_utc, end_utc, duration_s），名字已从 ATK 乱名映射回正式名
- `output/summary.json`：汇总（成功片/失败片/窗口数）
- `output/shards/`：分片两行 TLE 文件
- `output/shard_<port>.json`：每片原始结果（含 asset_order 用于名字映射）

## 健壮性

- 每片独立落盘，失败片不影响成功片结果
- 完整性校验：导入卫星数 == 预期
- 失败自动重试（≤ --retries 次），仍失败则列入 summary.failed_shards 并输出缺失 NORAD 范围
- 每片 1000 颗：4000 颗约 2.4 分钟（4 实例），16091 颗全集约 5 分钟（17 片 2 轮），内存约 1GB/实例

## 实测参考（2026-08-13）

| 规模 | 分片 | 墙钟 | 内存 | 窗口 |
|---|---|---|---|---|
| 303 遥感 | 1×303 | ~13s | <1GB | ~187 |
| 4000 | 8×500 | 62s | 4.9GB | 1962 |
| 16091 全集 | 8×2012（大分片） | 21min（2 片崩） | 8.2GB | 5556（缺 2 片） |
| 16091 全集（工具默认 1000/片） | 17×~950 | 待验收 | — | — |

### 传感器与区域输出（2026-08-15 新增）

```bash
# 复杂传感器（SimpleCone/Rectangular/Conical 走 Define；
# SAR/HalfPower 自动走 Save→改XML→Load 双实例路线）
python -m atk_parallel.runner --tle data/test_data/jilin1_all.tle \
    --t0 "12 Aug 2026 00:00:00.000" --t1 "12 Aug 2026 08:00:00.000" \
    --sensor-type SAR --sensor-params "12,70,30,30,550000" --out-dir output_sar

# 附加区域百分比覆盖率（输出 area_coverage.csv / area_grid_points.csv / area_summary.json）
python -m atk_parallel.runner --tle data/test_data/jilin1_all.tle \
    --t0 "12 Aug 2026 00:00:00.000" --t1 "12 Aug 2026 08:00:00.000" \
    --area-region "30,120,31,121" --area-granularity 0.5 --area-fov 45 --out-dir output_area
```

| 参数 | 默认 | 说明 |
|---|---|---|
| --sensor-type | SimpleCone | SimpleCone/Rectangular/HalfPower/Conical/SAR |
| --sensor-params | 无 | 逗号分隔：如 "45"、"40,60"、"0.5,0.35"、"0,45,0,360"、"12,70,30,30,550000" |
| --area-region | 无 | 附加区域覆盖率：min_lat,min_lon,max_lat,max_lon |
| --area-granularity | 0.5 | 区域网格粒度（度） |
| --area-fov | 45 | 区域分析传感器半角（度） |
