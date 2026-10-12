"""判点结果校验。

模型会犯错，而有些错必须由代码兜住 —— 光靠提示词写「必须」是拦不住的：

  · **漏编号** —— 某个要点既不在命中、也不在漏掉，凭空消失（没人发现）
  · **判命中却不给证据** —— 无法核查，也违反「命中必须引原文」的口径
  · **引用不存在的规则编号** —— 编造规则

设计原则：**不盲目重试**。把问题清单反馈给模型，让它针对性地修。
重试仍不合格时，按「不确定时判不命中」的从严口径兜底，并留下记录。
"""
from __future__ import annotations

import re

__all__ = [
    "validate_judgment", "repair_judgment", "validate_rule_ids",
    "collect_rule_ids", "sum_penalties", "check_answer_quality",
]


# ══════════════════════════════════════════════════════════
# 参考答案质量检查
# ══════════════════════════════════════════════════════════
# 少于这个要点数，任何题都算太少
MIN_POINTS = 4
# 每个要点平均少于这个字数，像是光有标题、没有展开
MIN_CHARS_PER_POINT = 8


def check_answer_quality(points: list[dict], reference: str,
                         question: str = "") -> list[str]:
    """检查参考答案拆出的要点是否可能偏少。

    **为什么要查**：分母 all_point 由参考答案的详细程度决定。
    答案越简略 → 要点越少 → 分母越小 → **分数虚高**。

        详细版「一、增强话语权。召开座谈会，征求企业意见。」→ 2 个要点
        简略版「一、增强话语权。」                        → 1 个要点

    同一份答卷答到「召开座谈会」，对前者 1/2（10分），对后者 1/1（20分）。

    ⚠️ 这是**提醒不是报错** —— 有些题确实只有 4~5 个要点。
       看到这个警告时，人工看一眼要点表合不合理即可。
    """
    n = len(points or [])
    if n == 0:
        return ["没能从参考答案里拆出任何要点，请检查资料里有没有「参考答案」段"]

    warns: list[str] = []

    # 信号一：绝对数量太少
    if n < MIN_POINTS:
        warns.append(
            f"参考答案只拆出 {n} 个要点，偏少。分母小会让分数虚高 —— "
            f"建议核对这份答案是否只有标题、没有展开内容。"
        )

    # 信号二：每个要点平均字数太少（说明没有展开）
    ref = re.sub(r"\s", "", reference or "")
    if ref:
        per = len(ref) / n
        if per < MIN_CHARS_PER_POINT:
            warns.append(
                f"参考答案平均每个要点只有 {per:.0f} 字，看起来像只有标题、没有展开。"
                f"这种情况下实际采分点会更多，本次分数可能偏高。"
            )

    return warns


# ══════════════════════════════════════════════════════════
# 判点结果校验
# ══════════════════════════════════════════════════════════
def _iter_judgments(data: dict):
    """从两种输出格式里统一取出 [(编号, 命中, 证据)]。

    新格式（推荐）：{"判定": [{"要点": 1, "命中": true, "证据": "…"}]}
    旧格式（兼容）：{"命中大点": [1,2], "漏掉大点": [3]}
    """
    if isinstance(data.get("判定"), list):
        for j in data["判定"]:
            if not isinstance(j, dict):
                continue
            idx = j.get("编号", j.get("要点", j.get("大点")))
            # 证据字段的三种叫法都认（答卷原文是当前口径）
            evidence = j.get("答卷原文") or j.get("证据") or ""
            yield idx, bool(j.get("命中")), str(evidence)

    elif isinstance(data.get("命中大点"), list) or isinstance(data.get("漏掉大点"), list):
        for idx in data.get("命中大点") or []:
            yield idx, True, ""          # 旧格式没有证据字段
        for idx in data.get("漏掉大点") or []:
            yield idx, False, ""


def validate_judgment(data: dict, n_points: int) -> list[str]:
    """检查判点结果是否完整合规。返回问题列表，空列表 = 通过。

    :param n_points: 要点总数，编号应为 1..n_points
    """
    problems: list[str] = []

    if not isinstance(data, dict):
        return ["返回的不是 JSON 对象"]

    items = list(_iter_judgments(data))
    if not items:
        return ["既没有「判定」数组，也没有「命中大点 / 漏掉大点」"]

    idxs = [i for i, _, _ in items]

    # ① 编号完整性：必须恰好覆盖 1..n_points，不重不漏不多
    expected = set(range(1, n_points + 1))
    got = {i for i in idxs if isinstance(i, int)}

    missing = sorted(expected - got)
    extra = sorted(got - expected)
    seen: dict[int, int] = {}
    for i in idxs:
        if isinstance(i, int):
            seen[i] = seen.get(i, 0) + 1
    dup = sorted(i for i, c in seen.items() if c > 1)

    if missing:
        problems.append(f"漏了编号 {missing}（必须逐条给出全部 {n_points} 条的判定）")
    if extra:
        problems.append(f"出现了无效编号 {extra}（有效范围是 1~{n_points}）")
    if dup:
        problems.append(f"编号重复 {dup}（每个编号只能出现一次）")
    if any(not isinstance(i, int) for i in idxs):
        problems.append("有判定的编号不是整数")

    # ② 命中必须有原文证据
    for idx, hit, evidence in items:
        if hit and not evidence.strip():
            problems.append(f"编号 {idx} 判了命中，但没有给出答卷原文证据")

    return problems


def repair_judgment(data: dict, n_points: int) -> tuple[dict, list[int]]:
    """兜底修复：把漏掉的编号补成「未命中」。

    口径依据：准确度文档 第一优先 第 5 条 —— **不确定时判不命中**。
    漏判的代价是考生少得几分；误判的代价是标准失效。

    返回 (修好的 data, 被补上的编号列表)。
    """
    items = list(_iter_judgments(data))
    got = {i for i, _, _ in items if isinstance(i, int)}
    missing = sorted(set(range(1, n_points + 1)) - got)

    if not missing:
        return data, []

    # 统一成新格式
    new_items = [
        {"要点": i, "命中": hit, "证据": ev}
        for i, hit, ev in items
        if isinstance(i, int) and i in set(range(1, n_points + 1))
    ]
    for i in missing:
        new_items.append({
            "要点": i, "命中": False, "证据": "",
            "原因": "模型未输出该编号，按未命中处理（从严口径）",
        })
    new_items.sort(key=lambda x: x["要点"])

    fixed = dict(data)
    fixed.pop("命中大点", None)
    fixed.pop("漏掉大点", None)
    fixed["判定"] = new_items
    return fixed, missing


# ══════════════════════════════════════════════════════════
# 规则编号校验
# ══════════════════════════════════════════════════════════
def collect_rule_ids(rules_text: str, prefix: str | None = None) -> set[str]:
    """从规则文本里抽出所有合法编号。

    **不要写死前缀** —— 不同题型用不同字母（概括归纳题/提出对策题 A、综合分析题 B、将来的 C/D…）。
    prefix=None 时自动识别任意字母前缀。
    """
    pattern = rf"^({prefix}\d{{4}})\s" if prefix else r"^([A-Z]\d{4})\s"
    return set(re.findall(pattern, rules_text, re.M))


def validate_rule_ids(data: dict, valid_ids: set[str]) -> list[str]:
    """检查「违规」里引用的规则编号是否真实存在。

    模型编造规则编号是很常见的 —— 它见过编号的格式，就会顺着编。
    """
    problems: list[str] = []
    for v in data.get("违规") or []:
        if not isinstance(v, dict):
            continue
        rid = str(v.get("规则编号") or "").strip()
        if rid and rid not in valid_ids:
            problems.append(f"引用了不存在的规则编号 {rid}")
    return problems


# ══════════════════════════════════════════════════════════
# 扣分汇总
# ══════════════════════════════════════════════════════════
def sum_penalties(data: dict, *, keys=("违规", "结构扣分")) -> float:
    """把各处的「扣分」加起来。负数忽略（扣分为负等于加分，属数据错误）。"""
    total = 0.0
    for key in keys:
        for item in data.get(key) or []:
            if isinstance(item, dict):
                try:
                    value = float(item.get("扣分") or 0)
                except (TypeError, ValueError):
                    continue
                if value > 0:
                    total += value
    return total
