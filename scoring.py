from decimal import Decimal, ROUND_HALF_UP


# 这是一个用于测试的score_dict值，score_dict是一个从别处传来的字典，里面包含did_point命中数，all_point总数，total_score满分，lose_score扣分
#score_dict = {"did_point": 30, "all_point": 33, "total_score": 15, "lose_score":1}



def get_score(score_dict) -> Decimal:
    did_point = Decimal(str(score_dict["did_point"]))
    all_point = Decimal(str(score_dict["all_point"]))
    total_score = Decimal(str(score_dict["total_score"]))
    lose_score = Decimal(str(score_dict["lose_score"]))

    if all_point == 0:
        raw = Decimal("0")
    elif did_point > all_point:
        raw = total_score 
    else:
        raw = did_point/all_point * total_score

    
    final_score = raw - lose_score
    if final_score < 0:
        final_score = Decimal("0.0")
    final_score = final_score.quantize(Decimal("0.1"),rounding=ROUND_HALF_UP)

    return final_score


# ══════════════════════════════════════════════════════════
# 双评结合
# ══════════════════════════════════════════════════════════
def combine_scores(score_a, score_b=None, *, weight_a=1.0,
                   total_score=None, diverge_threshold=0.15) -> dict:
    """把两条路径的分数合成一个。

    路径 A（结构化）：逐条要点判定 —— **可复现、可审计**
    路径 B（自由推理）：模型自己找点给分 —— **不可复现**，但能发现 A 漏掉的东西

    设计取向：**以 A 为主**。B 只做有限修正 ——
    直接平均会让不可复现的 B 污染 A 的稳定性。

    ⚠️ 默认 1.0：**B 只做分歧提示，不参与计分**。
       实测 B 系统性偏松（4 次平均比 A 高 1.25 分，自身波动 1.5），而 A 稳定精确。
       等基准集跑出数据，再决定要不要给 B 权重。

    :param score_a: 结构化路径的分数
    :param score_b: 自由推理路径的分数。None 表示这条路径没跑（只用 A）
    :param weight_a: A 的权重。1.0 = 完全不用 B
    :param total_score: 满分，用于算分歧比例
    :param diverge_threshold: |A−B| 超过满分的这个比例算「高分歧」

    :returns: {"最终得分", "得分A", "得分B", "分歧", "高分歧"}
    """
    a = Decimal(str(score_a))

    if score_b is None:
        return {
            "最终得分": a.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
            "得分A": a,
            "得分B": None,
            "分歧": Decimal("0"),
            "高分歧": False,
        }

    b = Decimal(str(score_b))
    w = Decimal(str(weight_a))
    combined = a * w + b * (Decimal("1") - w)

    # 夹到 [0, 满分]
    if total_score is not None:
        full = Decimal(str(total_score))
        if combined < 0:
            combined = Decimal("0")
        elif combined > full:
            combined = full

    diff = abs(a - b)
    ratio = diff / Decimal(str(total_score)) if total_score else Decimal("0")

    return {
        "最终得分": combined.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
        "得分A": a,
        "得分B": b,
        "分歧": diff,
        "高分歧": ratio > Decimal(str(diverge_threshold)),
    }



