"""批改编排 —— 把两条路径串起来（对应规格书 §7）。

   示例资料 ──extract_structure──► 题目 + 材料 + 要点表
                                       │
                   ┌───────────────────┼───────────────────┐
                   ▼                                       ▼
             路径 A（结构化）                        路径 B（自由推理）
             逐条要点判定                            模型自己找点给分
             validate + 必要时重出                   直接给分
                   │                                       │
             rules_check（形式扣分）                        │
                   │                                       │
             get_score → score_A                     score_B
                   └───────────────────┬───────────────────┘
                                       ▼
                               combine_scores → 最终得分

分工铁律：**模型只输出判定，分数一律由 scoring.py 算。**
"""
from __future__ import annotations

import sys
import time

sys.stdout.reconfigure(encoding="utf-8")

import llm
import prompts_summary as P
import routing
from rules_check import check_form, count_chars, detect_itemization
from scoring import combine_scores, get_score
from validators import (
    check_answer_quality, collect_rule_ids, repair_judgment,
    sum_penalties, validate_judgment, validate_rule_ids,
)

__all__ = ["grade", "grade_from_material", "grade_input", "format_report"]

# ══════════════════════════════════════════════════════════
# 模型调度策略（规格书 §3.3）
# ══════════════════════════════════════════════════════════
# 提取：机械活，**温度必须为 0** —— 它是分母的来源，飘一次分数就飘。
# 实测踩过：同一份资料两次跑出 10 个 / 9 个要点，同一份答卷差了 1.8 分。
CFG_EXTRACT = dict(thinking=False, model=llm.MODEL, temperature=0, max_tokens=16384)
# 判点：关思考 + 低温 —— 思考模式下 temperature 会被忽略，必须关掉才能控温
CFG_JUDGE = dict(thinking=False, model=llm.MODEL, temperature=0.1, max_tokens=16384)
# 自由推理：开思考 —— 需要真的去想。它本来就不可复现，温度不设也无所谓
CFG_FREE = dict(thinking=True, model=llm.MODEL_PRO, max_tokens=8192)


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ══════════════════════════════════════════════════════════
# 路径 A：结构化判点
# ══════════════════════════════════════════════════════════
def _judge_structured(question, material, answer, points, total_score,
                      reference, pack: dict, warnings: list[str]) -> tuple[dict, str]:
    """判点 + 校验 + 必要时带问题重出。返回 (判定结果, 原始文本)。

    :param pack: 规则包（routing.load_rules 的返回值），含 judge_rules /
                 role / sub_label —— 题型差异全靠这几个字段传，不靠复制代码。
    """
    judge_rules = pack["judge_rules"]
    sys_p, usr_p = P.build_judge_prompt(
        question, material, answer, points, total_score, reference,
        judge_rules=judge_rules,
        role=pack.get("role") or None,
        sub_label=pack.get("sub_label") or None,
    )

    raw = llm.chat(sys_p, usr_p, json_mode=True, **CFG_JUDGE)
    judged = llm.extract_json(raw)

    # 第一道校验：编号完整性 + 命中必须有原文
    problems = validate_judgment(judged, len(points))

    if problems:
        _log(f"    判定不合格，带问题重出：{problems[:2]} …")
        fix_note = (
            "\n\n## ⚠️ 你上一次的输出有以下问题，请**修正后重新输出完整判定**\n"
            + "\n".join(f"- {x}" for x in problems)
        )
        raw = llm.chat(sys_p, usr_p + fix_note, json_mode=True, **CFG_JUDGE)
        judged = llm.extract_json(raw)
        problems = validate_judgment(judged, len(points))

    # 第二道：重出仍不合格 → 按从严口径兜底（漏的一律判未命中）
    if problems:
        judged, added = repair_judgment(judged, len(points))
        warnings.append(f"判定经重出仍不合格，已兜底补 {added} 为未命中：{problems}")
        _log(f"    ⚠️ 兜底修复，补编号 {added}")

    # 第三道：规则编号真实性
    bad = validate_rule_ids(judged, collect_rule_ids(judge_rules))
    if bad:
        warnings.append(f"模型引用了不存在的规则编号：{bad}")
        _log(f"    ⚠️ {bad[0]} …（已记录，不影响计分）")

    return judged, raw


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def grade(
    question: str,
    material: str,
    points: list[dict],
    answer: str,
    *,
    total_score: float,
    reference: str = "",
    use_free: bool = True,
    weight_a: float = 1.0,
    max_chars: int | None = None,
    min_chars: int | None = None,
    require_items: bool = False,
    type_name: str | None = None,
    sub_type: str | None = None,
) -> dict:
    """跑一次完整批改。

    :param points:      [{"编号": int, "考点": str, "要点": str}] —— 要点是给分单位
    :param total_score: 满分
    :param use_free:    是否跑自由推理路径（双评的第二条）
    :param weight_a:    结构化路径的权重。**默认 1.0** —— 分数完全由 A 决定。
                        B（自由推理）只用来发现分歧，不参与计分。
                        实测 B 系统性偏松（4 次平均比 A 高 1.25 分，且自身波动 1.5），
                        而 A 稳定精确。等基准集跑出数据再决定要不要开 B。
    :param type_name:   题型。None = 从题干自动判别
    :param sub_type:    公文子类（仅贯彻执行题用）。None = 只用通用格式库
    """
    warnings: list[str] = []
    t0 = time.time()

    # ── 题型路由 ──
    # 规则库**不能混用** —— 综合分析题的规则会拿「释义 + 分析要素」
    # 去要求一道概括题；公文的格式规则会去要求一道概括题写落款。
    type_name = type_name or routing.detect_type(question)
    try:
        pack = routing.load_rules(type_name, sub_type)
    except (ValueError, NotImplementedError) as exc:
        warnings.append(f"题型「{type_name}」还没有规则库，本次退回概括归纳题：{exc}")
        type_name = routing.TYPE_SUMMARY
        pack = routing.load_rules(type_name)

    tag = f"{type_name}"
    if pack.get("sub_label"):
        tag += f" / {pack['sub_label']}"
    _log(f"题型：{tag}（规则库 {len(pack['judge_rules']):,} 字符，源 {pack['module']}）")

    # ── 参考答案质量提醒 ──
    # 要点偏少 → 分母小 → 分数虚高。这是最危险的一种错：不报错，但分数是假的。
    for w in check_answer_quality(points, reference, question):
        warnings.append(w)
        _log(f"    ⚠️ {w}")

    # ── 路径 A：结构化判点 ──
    _log(f"路径A 判点（{len(points)} 个要点）…")
    judged, _ = _judge_structured(
        question, material, answer, points, total_score, reference,
        pack, warnings)

    hits = sum(1 for j in judged.get("判定", []) if j.get("命中"))
    all_points = len(points)

    # ── 形式检查（纯代码）──
    form_items = check_form(
        answer, max_chars=max_chars, min_chars=min_chars, require_items=require_items)
    form_penalty = sum(float(x.get("扣分") or 0) for x in form_items)

    # ── 模型判的结构违规（也算形式分）──
    rule_penalty = sum_penalties(judged, keys=("违规",))

    lose = form_penalty + rule_penalty
    score_a = get_score({
        "did_point": hits,
        "all_point": all_points,
        "total_score": total_score,
        "lose_score": lose,
    })
    _log(f"    命中 {hits}/{all_points}，形式扣 {lose:.2f} → A = {score_a}")

    # ── 路径 B：自由推理 ──
    score_b = None
    free_reason = ""
    if use_free:
        _log("路径B 自由推理…")
        try:
            sys_f, usr_f = P.build_free_prompt(
                question, material, answer, total_score, reference,
                judge_rules=pack["judge_rules"],
                role=pack.get("role") or None,
                sub_label=pack.get("sub_label") or None,
                extra_notes=pack.get("extra_notes") or (),
            )
            free = llm.chat_json(sys_f, usr_f, **CFG_FREE)
            score_b = float(free.get("得分") or 0)
            free_reason = str(free.get("理由") or "")
            _log(f"    B = {score_b}")
        except Exception as exc:                      # noqa: BLE001
            warnings.append(f"自由推理路径失败，本次只用结构化路径：{exc}")
            _log(f"    ✗ {type(exc).__name__}：{exc}")

    # ── 合成 ──
    combined = combine_scores(score_a, score_b, weight_a=weight_a,
                              total_score=total_score)

    if combined["高分歧"]:
        warnings.append(
            f"A/B 分歧较大：A={score_a} B={score_b}（差 {combined['分歧']}）。"
            f"建议人工看一眼 B 的理由。"
        )

    return {
        "得分": combined["最终得分"],
        "得分A": score_a,
        "得分B": score_b,
        "分歧": combined["分歧"],
        "高分歧": combined["高分歧"],
        "满分": total_score,
        "题型": type_name,
        "子类": pack.get("sub_label", ""),
        "规则来源": pack.get("module", ""),
        "命中要点数": hits,
        "要点总数": all_points,
        "要点表": points,            # ← 报告渲染要用它显示标准要点
        "判定": judged.get("判定", []),
        "形式扣分": form_items,
        "模型判违规": judged.get("违规", []),
        "自由推理理由": free_reason,
        "字数": count_chars(answer),
        "分条": detect_itemization(answer),
        "警告": warnings,
        "耗时秒": round(time.time() - t0, 1),
    }


def _guess_total(question: str, fallback: float = 10.0) -> float:
    """从题干里正则取满分，如「（20分）」。取不到用 fallback。"""
    import re

    m = re.search(r"[（(](\d+)\s*分[)）]", question or "")
    return float(m.group(1)) if m else fallback


def _extract_reference(raw: str) -> str:
    """从资料原文里截出「参考答案」那一段。"""
    import re

    m = re.search(r"参考答案[：:](.*)$", raw, re.S)
    ref = m.group(1).strip()[:4000] if m else ""
    # 示例资料尾部常带公众号文章，切掉
    for stop in ("那种瞒着父母", "扫码关注", "微信号"):
        if stop in ref:
            ref = ref.split(stop)[0].strip()
    return ref


def grade_input(
    answer: str,
    *,
    from_pdf: str | None = None,
    pdf_pages: tuple[int, int] | None = None,
    raw_text: str = "",
    question: str = "",
    material: str = "",
    points: list[dict] | None = None,
    reference: str = "",
    total_score: float | None = None,
    **grade_kwargs,
) -> dict:
    """混合输入 —— PDF / 纯文字 / 两者拼接，都支持。

    设计原则：**PDF 是「导入方式」，不是「输入模式」。**
    导入之后一切都是可编辑的文本，用户可以改。

    四种典型用法::

        # ① 只有 PDF
        grade_input(答卷, from_pdf="题目.pdf")

        # ② 只有粘贴的文字（资料里含题目+材料+参考答案）
        grade_input(答卷, raw_text=粘贴的内容)

        # ③ 混合：PDF 抽材料，题干自己改过
        grade_input(答卷, from_pdf="题目.pdf", question="我改过的题干")

        # ④ 字段都齐了，完全不用抽，省一次模型调用
        grade_input(答卷, question=..., material=..., points=[...], total_score=15)

    字段优先级：**显式给的字段 > 从资料里抽出来的**。
    """
    # ── 组装资料原文 ──
    chunks: list[str] = []
    if from_pdf:
        import pdf_import

        if not pdf_import.available():
            raise RuntimeError(
                "没装 pypdfium2，读不了 PDF。\n"
                "  要么 pip install pypdfium2，要么改用 raw_text 粘贴文字"
            )
        first, last = pdf_pages if pdf_pages else (1, None)
        _log(f"读 PDF：{from_pdf}" + (f"（第 {first}-{last} 页）" if pdf_pages else ""))
        chunks.append(pdf_import.extract_text(from_pdf, first=first, last=last))

    if raw_text.strip():
        chunks.append(raw_text.strip())

    raw = "\n\n".join(chunks).strip()

    # ── 字段齐了就直接用，跳过抽取 ──
    if question and material and points:
        total = total_score if total_score is not None else _guess_total(question)
        _log(f"字段齐全，跳过抽取（{len(points)} 个要点，满分 {total}）")
        return grade(
            question, material, points, answer,
            total_score=total,
            reference=reference or raw[:4000],
            **grade_kwargs,
        )

    if not raw:
        raise ValueError(
            "没有资料。至少给一样：\n"
            "  · from_pdf（PDF 路径）\n"
            "  · raw_text（粘贴的资料）\n"
            "  · question + material + points（字段齐全）"
        )

    # ── 从资料里抽，再用显式字段覆盖 ──
    _log("抽取结构…")
    sys_e, usr_e = P.extract_structure(raw)
    structured = llm.chat_json(sys_e, usr_e, **CFG_EXTRACT)

    q = question or structured.get("question", "")
    m = material or structured.get("material", "")
    pts = points or structured.get("points", []) or []

    if not pts:
        raise ValueError("没能从资料里抽出要点。检查资料里有没有「参考答案」段。")

    total = total_score if total_score is not None else _guess_total(q)

    # 模型判的题型比正则准 —— 它读了题干和参考答案。拿不到时 grade() 会正则兜底。
    detected = str(structured.get("type") or "").strip()
    if detected and "type_name" not in grade_kwargs:
        grade_kwargs["type_name"] = detected

    # 公文子类同样是模型判的 —— 正则只能看出"这是一份公文"，
    # 看不出是总结类还是评论类（那取决于题干要求的行文目的）。
    sub = str(structured.get("subtype") or "").strip()
    if sub and "sub_type" not in grade_kwargs:
        grade_kwargs["sub_type"] = sub

    _log(f"  题目 {len(q)} 字，材料 {len(m)} 字，要点 {len(pts)} 个，满分 {total}"
         + (f"，公文子类 {sub}" if sub else ""))

    return grade(
        q, m, pts, answer,
        total_score=total,
        reference=reference or _extract_reference(raw),
        **grade_kwargs,
    )


def grade_from_material(raw_text: str, answer: str, **kwargs) -> dict:
    """【保留兼容】从整块资料里抽结构再批改。

    等价于 ``grade_input(answer, raw_text=...)``，新代码建议直接用 grade_input。
    """
    return grade_input(answer, raw_text=raw_text, **kwargs)


# ══════════════════════════════════════════════════════════
# 报告渲染（对齐参考批改报告的形态）
# ══════════════════════════════════════════════════════════
def format_report(result: dict, points: list[dict] | None = None) -> str:
    """把结果渲染成人能读的批改报告。

    形态对齐参考批改报告：每条同时给出「标准要点」「考生表述」「材料出处」。
    """
    # 要点表：优先用传入的，其次用 result 里带的
    table = points or result.get("要点表") or []
    by_id = {p.get("编号"): p for p in table}

    def label(idx) -> str:
        p = by_id.get(idx) or {}
        tag = p.get("考点", "")
        body = p.get("要点", "")
        if tag and body:
            return f"[{tag}] {body}"
        return body or tag

    out: list[str] = []
    type_tag = f"【{result['题型']}】" if result.get("题型") else ""
    if result.get("子类"):
        type_tag = f"【{result['题型']} · {result['子类']}】"
    out.append("=" * 62)
    out.append(f"{type_tag}满分 {result['满分']:.0f} 分 | 本次得分 {result['得分']}")
    if result.get("规则来源"):
        out.append(f"  规则库：{result['规则来源']}")
    if result.get("得分B") is not None:
        out.append(f"  （结构化 A={result['得分A']} ｜ 自由推理 B={result['得分B']}"
                   f" ｜ 分歧 {result['分歧']}）")
    out.append("=" * 62)
    out.append(f"命中要点 {result['命中要点数']}/{result['要点总数']}"
               f"    字数 {result['字数']}")

    hit = [j for j in result["判定"] if j.get("命中")]
    miss = [j for j in result["判定"] if not j.get("命中")]

    out.append(f"\n── 命中（{len(hit)}）" + "─" * 44)
    for j in hit:
        idx = j["编号"]
        out.append(f"  ✓ {idx}. {label(idx)}")          # 标准要点
        if j.get("答卷原文"):
            out.append(f"      你写的：{j['答卷原文']}")  # 考生表述
        if j.get("材料出处"):
            out.append(f"      {j['材料出处'][:74]}")

    out.append(f"\n── 未命中（{len(miss)}）" + "─" * 42)
    for j in miss:
        idx = j["编号"]
        out.append(f"  ✗ {idx}. {label(idx)}")          # 标准要点（漏了什么）
        if j.get("材料出处"):
            out.append(f"      {j['材料出处'][:74]}")

    if result["形式扣分"] or result["模型判违规"]:
        out.append("\n── 扣分项" + "─" * 48)
        for x in result["形式扣分"]:
            out.append(f"  · {x['类型']}：{x['理由']}  扣 {x['扣分']}")
        for x in result["模型判违规"]:
            if float(x.get("扣分") or 0) > 0:
                out.append(f"  · {x.get('规则编号')}：{str(x.get('理由'))[:60]}"
                           f"  扣 {x.get('扣分')}")

    if result.get("自由推理理由"):
        out.append("\n── 自由推理路径的意见" + "─" * 34)
        out.append(f"  {result['自由推理理由'][:300]}")

    if result["警告"]:
        out.append("\n⚠️ 警告" + "─" * 50)
        for w in result["警告"]:
            out.append(f"  · {w}")

    return "\n".join(out)
