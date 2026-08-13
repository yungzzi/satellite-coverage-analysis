# -*- coding: utf-8 -*-
"""ATK 无头并行覆盖工具：TLE 读取 / 分片 / 名字映射。"""
from __future__ import annotations
import os
from dataclasses import dataclass


@dataclass
class SatRec:
    norad: str
    name: str
    l1: str
    l2: str


def read_tle(path: str) -> list[SatRec]:
    """读取 TLE 文件，支持三行（名字+两行）与纯两行两种格式。

    三行格式：名字行 + "1 ..." + "2 ..."（CelesTrak/Space-Track 常见）。
    两行格式：直接 "1 ..." + "2 ..."，名字取 NORAD 占位。
    返回按文件顺序的 SatRec 列表。
    """
    with open(path, "r", encoding="ascii", errors="replace") as f:
        raw = [ln.rstrip("\r\n") for ln in f if ln.strip()]
    recs: list[SatRec] = []
    i = 0
    while i + 1 < len(raw):
        if raw[i].startswith("1 ") and raw[i + 1].startswith("2 "):
            l1, l2 = raw[i], raw[i + 1]
            norad = l1[2:7].strip()
            recs.append(SatRec(norad= norad, name=f"NORAD-{norad}", l1=l1, l2=l2))
            i += 2
        elif i + 2 < len(raw) and raw[i + 1].startswith("1 ") and raw[i + 2].startswith("2 "):
            name = raw[i].strip()
            l1, l2 = raw[i + 1], raw[i + 2]
            norad = l1[2:7].strip()
            recs.append(SatRec(norad=norad, name=name, l1=l1, l2=l2))
            i += 3
        else:
            i += 1
    return recs


def validate_lines(recs: list[SatRec]) -> list[str]:
    """检查 TLE 行是否为 69 字符（ATK 要求），返回问题列表。"""
    problems = []
    for r in recs:
        for tag, line in (("L1", r.l1), ("L2", r.l2)):
            if len(line) != 69:
                problems.append(f"{r.norad} {tag} len={len(line)}")
    return problems


def to_two_line_file(recs: list[SatRec], path: str) -> None:
    """把分片记录写成纯两行文件（ATK ImportTLEFile 要求）。"""
    with open(path, "w", encoding="ascii") as f:
        for r in recs:
            f.write(r.l1 + "\n" + r.l2 + "\n")


def make_shards(recs: list[SatRec], per_shard: int) -> list[list[SatRec]]:
    """按每片颗数切分（顺序保持）。"""
    return [recs[i:i + per_shard] for i in range(0, len(recs), per_shard)]


def shard_norad_ranges(shard: list[SatRec]) -> str:
    if not shard:
        return "(空)"
    first, last = shard[0].norad, shard[-1].norad
    return f"第{first}~第{last}（共{len(shard)}颗）" if first != last else f"第{first}（共{len(shard)}颗）"
