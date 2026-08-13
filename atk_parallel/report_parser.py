# -*- coding: utf-8 -*-
"""Cov_RM 覆盖报告解析（GBK 编码 + 窗口行提取）。"""
from __future__ import annotations
import re

ROW_RE = re.compile(r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3})\s+([\d.]+)\s+(.+)$")


def decode_report(raw: str) -> str:
    """SWIG 返回的 GBK 报告（带 surrogateescape 字符）转正常文本。"""
    if not isinstance(raw, str):
        return str(raw)
    try:
        return raw.encode("utf-8", "surrogateescape").decode("gbk", errors="replace")
    except Exception:
        return raw


def parse_coverage_report(text: str) -> list[dict]:
    """从解码后的报告文本提取窗口行。

    返回 [{asset, start, end, duration_s}, ...]，asset 形如 "tle-40961U-S45"。
    找不到报告段时返回空列表。
    """
    m = re.search(r"Hangzhou的覆盖时段报告[\s\S]*", text) or re.search(r"覆盖时段报告[\s\S]*", text)
    seg = m.group(0) if m else text
    out = []
    for ln in seg.splitlines():
        mm = ROW_RE.match(ln)
        if mm:
            out.append({
                "asset": mm.group(5).strip(),
                "start": mm.group(2),
                "end": mm.group(3),
                "duration_s": float(mm.group(4)),
            })
    return out
