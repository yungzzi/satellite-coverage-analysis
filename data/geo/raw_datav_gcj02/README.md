# DataV 原始边界快照

本目录保存阿里 DataV.GeoAtlas `areas_v3` API 返回的原始字节，数据来源为高德开放平台，
版本标注为 2021.5，坐标语义为 **GCJ-02**。这些文件只用于审计和重新生成，不能直接与
WGS84 卫星轨道地面点混合计算。

覆盖计算使用上一级 `data/geo/<adcode>.json`；它们由
`src/prepare_zhejiang_boundaries.py` 显式转换为 WGS84。原始和转换后哈希记录在
`data/geo/metadata/zhejiang_boundaries.json`。
