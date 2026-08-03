# 卫星覆盖性分析（Satellite Coverage Analysis）

用纯 Python 完成“哪些卫星在什么时间能覆盖某个地区”的分析，替代 ATK 仿真软件，无商业软件依赖。

## 这是什么（白话）
- **输入**：地区（省/市多边形，比如浙江省）、卫星星座轨道数据（TLE）、时间段
- **输出**：每颗卫星的覆盖时间窗口（开始/结束/时长）+ “可用卫星”汇总 + 甘特图

## 核心概念（白话术语表）
| 名词 | 白话解释 |
|---|---|
| TLE | 卫星轨道的公开标准数据格式（两行数字），描述卫星某时刻的轨道状态。CelesTrak、spacemapper 都能下载 |
| SGP4 | 根据 TLE 预测卫星未来位置的标准算法（已由 skyfield 库实现，不用自己写） |
| 星下点 | 卫星正下方、垂直到地面的那个点 |
| 覆盖圆（足印） | 卫星传感器视场圆锥打到地面形成的圆形区域；半径由轨道高度和视场角决定 |
| 视场半角 | 传感器最大能“歪着头”看的角度（默认 30°） |
| 仰角 | 站在目标点抬头看卫星，视线与水平面的夹角（默认要求 ≥ 10°） |
| 网格代表点 | 在地区内按固定间距撒的采样点，用来代表“地区被覆盖” |

## 目录结构
```
卫星覆盖性分析/
├── data/
│   ├── raw/     # 原始 TLE（不入库，用下载脚本重新获取）
│   └── geo/     # 地区边界 GeoJSON
├── src/
│   └── coverage_analysis.py   # 主程序（中文注释，按步骤划分）
├── output/      # 结果 CSV 与甘特图
├── requirements.txt
├── README.md
└── AGENTS.md    # 项目背景记忆（Codex 自动读取）
```

## 运行方法（MVP）
1. 安装依赖：`python -m venv .venv && .venv\Scripts\pip install -r requirements.txt`
2. 下载数据（TLE 和地区边界）：见 `AGENTS.md` 数据来源；首次由 Codex 代跑
3. 运行：`.venv\Scripts\python src\coverage_analysis.py`
4. 结果在 `output/` 目录

## 当前进度
- [x] 工程初始化（git、目录、README、AGENTS.md）
- [x] MVP：少量遥感/天链卫星 + 浙江 + 1 天 → 覆盖窗口 CSV + 甘特图
- [ ] 扩展：任意地区、几千颗星批量分析
- [ ] 与公开过境预报交叉验证
- [ ] 数据源切换（spacemapper.cn）
- [ ] 神经网络方向（后续阶段）
