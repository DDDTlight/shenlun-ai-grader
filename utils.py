"""通用工具：健壮的 JSON 解析。

为什么需要它：模型输出的 JSON 经常带前后缀或围栏，直接 json.loads 会失败。
实测踩过的坑：
  · 前面加一句"好的，结果如下：{...}"
  · 用 ```json 包裹
  · 内层引号转义不一致导致整体非法（这种只能重试，解析救不了）

四层兜底：直接解 → 剥围栏 → 逐 { 位置尝试 raw_decode。
最后一层用 raw_decode 而不是贪婪正则 {.*}，因为贪婪匹配会把
"说明文字{...}后续文字" 里的多个花括号连起来，必然失败。
"""
from __future__ import annotations

import json
import re

__all__ = ["parse_json", "try_parse_json"]


def parse_json(text: str) -> dict:
    """解析模型返回的 JSON 对象。失败抛 ValueError。"""
    s = (text or "").strip()
    if not s:
        raise ValueError("模型返回空内容")

    # ① 直接解
    try:
        o = json.loads(s)
        if isinstance(o, dict):
            return o
    except json.JSONDecodeError:
        pass

    # ② 剥 markdown 围栏
    if "```" in s:
        for part in s.split("```")[1::2]:
            b = part.strip()
            if b[:4].lower() == "json":
                b = b[4:].strip()
            if b.startswith("{"):
                try:
                    o = json.loads(b)
                    if isinstance(o, dict):
                        return o
                except json.JSONDecodeError:
                    pass

    # ③ 逐个 { 位置尝试 raw_decode（比贪婪匹配可靠）
    for m in re.finditer(r"\{", s):
        try:
            o, _ = json.JSONDecoder().raw_decode(s, m.start())
            if isinstance(o, dict) and o:
                return o
        except json.JSONDecodeError:
            continue

    raise ValueError(f"无法解析 JSON：{s[:200]}")


def try_parse_json(text: str) -> dict | None:
    """解析失败返回 None，不抛异常。"""
    try:
        return parse_json(text)
    except ValueError:
        return None
