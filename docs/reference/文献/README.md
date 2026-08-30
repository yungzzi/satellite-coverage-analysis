# 参考文献清单与获取状态

> 整理日期：2026-08-10。本清单对应 `docs/reference/理论报告.md` 的参考文献。
> 原则：只收录/下载**合法公开**来源；付费墙文献给出完整著录 + DOI/官方链接，可由学校图书馆或知网订阅获取。

## 已下载（本目录内有 PDF）

| 序号 | 文献 | 本地文件 | 来源（合法公开） | 与本工程的关系 |
| --- | --- | --- | --- | --- |
| 1 | Hoots F R, Roehrich R L. Spacetrack Report No.3: Models for Propagation of NORAD Element Sets[R]. 1980. | `01_Spacetrack_Report_No3_1980.pdf`（484 KB） | celestrak.com（NORAD 官方编目配套文档，公开） | SGP4 算法原始出处，第 1 章轨道外推的理论依据 |
| 2 | Vallado D A, Crawford P, Hujsak R, Kelso T S. Revisiting Spacetrack Report #3[C]. AIAA 2006-6753, 2006. | `02_Revisiting_Spacetrack_Report3_2006.pdf`（1.18 MB） | celestrak.org（作者官方公开） | SGP4/SDP4 现代实现与验证基准，第 1 章 |
| 9 | Williamson D P, Shmoys D B. The Design of Approximation Algorithms[M]. Cambridge University Press, 2011. | `09_Williamson_Shmoys_ApproxAlgorithms_免费教材.pdf`（2.4 MB） | designofapproxalgs.com（作者官网，CC 授权免费公开） | 集合覆盖贪心 ln n 界、最大覆盖 1−1/e、Feige ln n 下界的**合法替代参考**（含完整证明） |

## 付费墙（无合法开放全文，给出官方获取途径）

| 序号 | 文献 | DOI / 链接 | 获取途径 | 与本工程的关系 |
| --- | --- | --- | --- | --- |
| 3 | Chvátal V. A Greedy Heuristic for the Set-Covering Problem[J]. Mathematics of Operations Research, 1979, 4(3): 233-235. | DOI: 10.1287/moor.4.3.233（INFORMS / ACM DL） | 学校图书馆 INFORMS 期刊库 | 贪心 ln n 近似比的理论来源，第 4.8 节 |
| 4 | Nemhauser G L, Wolsey L A, Fisher M L. An Analysis of Approximations for Maximizing Submodular Set Functions—I[J]. Mathematical Programming, 1978, 14(1): 265-294. | DOI: 10.1007/BF01588971（Springer） | 学校图书馆 Springer 期刊库 | 最大覆盖贪心 1−1/e 界的理论来源，第 4.8 节 |
| 5 | Feige U. A Threshold of ln n for Approximating Set Cover[J]. Journal of the ACM, 1998, 45(4): 634-652. | DOI: 10.1145/285055.285059（ACM DL） | 学校图书馆 ACM 期刊库 | ln n 界不可改进性证明，第 4.8 节 |
| 6 | Cordeau J F, Furini F, Ljubić I. Benders Decomposition for Very Large Scale Partial Set Covering and Maximal Covering Location Problems[J]. European Journal of Operational Research, 2019, 275(3): 882-896. | 预印本：optimization-online.org（6665 号）；HAL: hal-02152294；正式版 DOI: 10.1016/j.ejor.2018.12.021 | 预印本链接可公开访问（本机网络被源站限速/反爬，未能落盘）；正式版走图书馆 | PSCLP 大规模精确求解，第 4.4/4.8 节 |
| 7 | 李君, 邢立宁, 彭观胜, 徐运保. 考虑多类型时间依赖资源约束的敏捷卫星调度优化[J]. 控制理论与应用, 2024, 41(6): 1038-1046. | 期刊官网 jcta.cnjournals.com（file_no=CCTA220914）；知网 KZLY202406007；DOI: 10.7641/CTA.2023.20914 | 期刊官网/知网（官网 PDF 有反爬守卫，需浏览器访问） | 卫星调度 ILP 复杂性的国内佐证，第 4 章取舍依据 |
| 8 | 罗鑫. 面向复杂观测任务的多星组网研究[D]. 武汉: 中国地质大学（武汉）, 2018. | 知网学位论文号 1018817230 | 知网（学校账号可下载） | 多星组网/重访指标的方法论参考 |

## 说明

1. 序号 3~5 是 1979~1998 年的经典优化理论论文，出版方（INFORMS/Springer/ACM）均无合法开放全文，Unpaywall 接口确认无 OA 副本；其核心结论（ln n、1−1/e）已在报告中给出，不影响本工程的引用与复算。
2. **序号 3~5 的结论有合法替代全文**：Williamson-Shmoys《The Design of Approximation Algorithms》免费教材（序号 9）第 2 章（集合覆盖贪心 H(k) 界）、第 3 章（最大覆盖 (1−1/e)）及第 16 章（Feige 下界讨论）覆盖了报告引用的全部定理，含完整证明，可作为论文答辩引用的可靠来源。
3. 序号 6 的预印本（optimization-online）与 HAL 副本是合法公开的，本次因源站限速/反爬未能下载到本地；换网络或浏览器可直接访问。
4. 序号 7、8 的国内文献建议在校园网用知网下载；若老师需要，可由其单位图书馆代下。
5. 需要英文文献全文时，图书馆的"文献传递"服务（如 CALIS）也可作为兜底；向论文作者发邮件索取副本是常见的合法途径（研究者通常乐意提供）。
6. **本工程明确不使用 sci-hub、libgen 等盗版渠道获取文献**（版权侵权，且答辩引用盗版来源有学术诚信风险）；缺文献一律走上述合法途径。

## 引用可信度说明（诚实声明）

理论报告中涉及各文献的**具体论断级别**如下，避免读者误以为所有引文都经过全文核对：

| 文献 | 引用内容 | 核实方式 |
| --- | --- | --- |
| 1/2（SGP4 两篇） | SGP4 算法原理、现代实现 | **已下载原文 PDF 并阅读**（本目录内） |
| 3 Chvátal 1979 | 贪心集合覆盖近似比 ≤ 1 + ln k | 标准定理（Vazirani、Williamson-Shmoys 教材均有叙述）；著录（卷期页/DOI）经 zbMATH 与出版商记录核实，**未读付费全文** |
| 4 Nemhauser 1978 | 单调子模最大化贪心 (1 − 1/e) 界 | 同上，标准定理，**未读付费全文** |
| 5 Feige 1998 | ln n 下界不可改进（P≠NP） | 同上，标准定理，**未读付费全文** |
| 6 Cordeau 2019 | PSCLP 大规模可用精确分解算法 | optimization-online 预印本摘要/正文片段经检索核实，**全文未落盘** |
| 7/8（中文两篇） | 卫星调度 ILP 的复杂性与多星组网方法论 | 著录经知网/期刊页核实；仅作"业界确有此方向"的佐证引用，**未引用其内部结论** |

结论：报告引用的优化理论论断均为该领域教科书级标准结果，不依赖付费全文的内部细节；如需在论文/答辩中逐字引用或深挖证明，建议用学校图书馆获取 3~6 全文后再核对。
