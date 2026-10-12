"""形式违规检查 —— 纯代码，不调模型。

**为什么必须用代码**：让模型数字数，同一篇会这次判 298、下次 302，扣分跟着飘。
确定性的事交给代码，模型只做语义判断（与 scoring.py 同一条原则）。

只做**确定性**判定，不做需要语义判断的：
  ✅ 字数（上限 / 下限）
  ✅ 是否分条列点
  ✅ 编号是否重复 / 断号
  ✅ 段落数
  ❌ 错别字（需要词典，后做）
  ❌ 「总括词前置」「逻辑层次」—— 这些要读意思，仍归模型判

对应规格书 §7 的第 ③.5 步。
"""
from __future__ import annotations

import re

__all__ = ["count_chars", "detect_itemization", "check_form", "FORM_DEFAULTS"]


# ══════════════════════════════════════════════════════════
# 默认扣分标准（可按题型覆盖）
# ══════════════════════════════════════════════════════════
FORM_DEFAULTS = {
    # 字数超出：每超 10% 扣 1 分，最多扣 3 分
    "超字": {"每档比例": 0.10, "每档扣分": 1.0, "扣分上限": 3.0},
    # 字数不足：每少 10% 扣 1 分，最多扣 3 分
    "少字": {"每档比例": 0.10, "每档扣分": 1.0, "扣分上限": 3.0},
    # 题干要求分条而没分条
    "未分条": {"扣分": 1.0},
    # 序号重复（如出现两个「3.」）
    "编号重复": {"每处扣分": 0.5, "扣分上限": 1.5},
}


# ══════════════════════════════════════════════════════════
# 计数
# ══════════════════════════════════════════════════════════
def count_chars(text: str) -> int:
    """按申论习惯计字：去掉空白和换行，其余（含标点）都算。

    申论是数格子，标点占格，所以标点要计入。
    """
    return len(re.sub(r"\s", "", text or ""))


# ══════════════════════════════════════════════════════════
# 分条检测
# ══════════════════════════════════════════════════════════
# 序号可能的写法。
# ⚠️ 关键：**不能只匹配行首** —— 很多考生把答案写成一段，序号跟在句末标点之后
# （"…满足需求。2.缺乏政策指引。"），只扫行首会全部漏掉。
CN2NUM = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
          "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫"

ITEM_RE = re.compile(
    r"(?:^|(?<=[。！？；\n]))\s*"
    r"(?P<val>"
    r"[一二三四五六七八九十]{1,3}、"
    r"|\d{1,2}[\.、]"
    r"|[（(]\d{1,2}[)）]"
    r"|[（(][一二三四五六七八九十]{1,3}[)）]"
    r"|[" + CIRCLED + r"]"
    r")",
    re.M,
)


def _normalize(value: str) -> tuple[str, str]:
    """把各种序号写法归一成 (样式, 规范值)，用于跨写法识别重复。"""
    if value[0] in CIRCLED:
        return "圈号", str(CIRCLED.index(value[0]) + 1)
    if value[0] in "（(":
        inner = value.strip("（）()")
        return "括号", CN2NUM.get(inner, inner)
    if value[0].isdigit():
        return "阿拉伯数字", value.rstrip(".、")
    raw = value.rstrip("、")
    return "中文顿号", CN2NUM.get(raw, raw)


def detect_itemization(text: str) -> dict:
    """检测答卷的分条情况。

    返回 {"有条目": bool, "条目数": int, "序号": [...], "重复": [...], "样式": str}

    **整篇正则扫描**，不按行 —— 见 ITEM_RE 上方的说明。
    """
    from collections import Counter

    found = [_normalize(m.group("val")) for m in ITEM_RE.finditer(text or "")]

    by_style: dict[str, list[str]] = {}
    for style, canon in found:
        by_style.setdefault(style, []).append(canon)

    dup: list[str] = []
    for style, values in by_style.items():
        for value, count in Counter(values).items():
            if count > 1:
                dup.append(f"{style} {value}（出现 {count} 次）")

    return {
        "有条目": bool(found),
        "条目数": len(found),
        "序号": [c for _, c in found],
        "重复": dup,
        "样式": "、".join(sorted(by_style)),
    }


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════
def check_form(
    answer: str,
    *,
    max_chars: int | None = None,
    min_chars: int | None = None,
    require_items: bool = False,
    weights: dict | None = None,
) -> list[dict]:
    """检查形式违规，返回扣分项列表（可能为空）。

    每项形如：
        {"类型": "超字", "理由": "共 512 字，超出上限 300 字 71%", "扣分": 3.0}

    :param max_chars:    字数上限（题干里写的「不超过 XXX 字」）
    :param min_chars:    字数下限（题干里写的「不少于 XXX 字」）
    :param require_items: 题干是否要求分条列点
    :param weights:      覆盖 FORM_DEFAULTS，便于按题型调
    """
    w = {**FORM_DEFAULTS, **(weights or {})}
    n = count_chars(answer)
    out: list[dict] = []

    # ① 超字
    if max_chars and n > max_chars:
        over = (n - max_chars) / max_chars
        cfg = w["超字"]
        tiers = int(over / cfg["每档比例"]) + (1 if over % cfg["每档比例"] else 0)
        penalty = min(tiers * cfg["每档扣分"], cfg["扣分上限"])
        out.append({
            "类型": "超字",
            "理由": f"共 {n} 字，超出上限 {max_chars} 字（{over:.0%}）",
            "扣分": round(penalty, 2),
            "数值": n,
        })

    # ② 少字
    if min_chars and n < min_chars:
        under = (min_chars - n) / min_chars
        cfg = w["少字"]
        tiers = int(under / cfg["每档比例"]) + (1 if under % cfg["每档比例"] else 0)
        penalty = min(tiers * cfg["每档扣分"], cfg["扣分上限"])
        out.append({
            "类型": "少字",
            "理由": f"共 {n} 字，少于要求 {min_chars} 字（{under:.0%}）",
            "扣分": round(penalty, 2),
            "数值": n,
        })

    # ③ 分条
    items = detect_itemization(answer)
    if require_items and not items["有条目"]:
        out.append({
            "类型": "未分条",
            "理由": "题干要求分条列点，答卷未见任何序号",
            "扣分": round(w["未分条"]["扣分"], 2),
        })

    # ④ 编号重复
    if items["重复"]:
        cfg = w["编号重复"]
        penalty = min(len(items["重复"]) * cfg["每处扣分"], cfg["扣分上限"])
        out.append({
            "类型": "编号重复",
            "理由": "序号重复：" + "；".join(items["重复"]),
            "扣分": round(penalty, 2),
        })

    return out


# ══════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")

    a = "一、无人经济满足需求。\n二、缺乏政策指引。\n三、存在安全隐患。"
    b = "1.无人经济满足需求。2.缺乏政策指引。3.存在安全隐患。3.监管不到位。"
    c = "这是一段没有分条的答案，一直写下去不带任何序号，用来测试未分条和超字。"

    for name, text in [("规范分条", a), ("编号重复", b), ("没分条", c)]:
        info = detect_itemization(text)
        print(f"\n【{name}】{count_chars(text)} 字")
        print(f"  分条: {info['有条目']}  条目数: {info['条目数']}  样式: {info['样式']}")
        if info["重复"]:
            print(f"  重复: {info['重复']}")
        for p in check_form(text, max_chars=20, require_items=True):
            print(f"  ⚠ {p['类型']}: {p['理由']}  扣 {p['扣分']}")
