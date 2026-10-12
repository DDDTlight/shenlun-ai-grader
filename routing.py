"""题型判别与规则库路由。

**为什么需要它**：两套规则库的判点规则**不能混用**。
综合分析题的规则会拿「释义 + 分析要素」去要求一道概括题，反之亦然 —— 规则互相干扰，
模型不知道该听谁的。

判别优先级：**公文写作 > 文章写作 > 综合分析题 > 提出对策题 > 概括归纳题**

理由：公文的标志词最独特（「写一份」「草拟」），最不容易误判；
而「分析」这类词在概括归纳题和综合分析题里都会出现（分析原因 vs 分析这一举措），
必须放到后面，让更独特的模式先匹配。

⚠️ 正则只能判个大概。`extract_structure` 会额外输出一个 `题型` 字段（模型判），
   `grade_input` 优先用模型的判断，正则只在拿不到时兜底。
"""
from __future__ import annotations

import importlib
import re

__all__ = [
    "detect_type", "load_rules", "available_types", "pending_types",
    "TYPE_SUMMARY", "TYPE_SOLUTION", "TYPE_COMPREHENSIVE",
    "TYPE_ZHIXING", "TYPE_ESSAY",
]

# ══════════════════════════════════════════════════════════
# 题型
# ══════════════════════════════════════════════════════════
TYPE_SUMMARY = "概括归纳题"
TYPE_SOLUTION = "提出对策题"
TYPE_COMPREHENSIVE = "综合分析题"
TYPE_ZHIXING = "贯彻执行题"
TYPE_ESSAY = "文章写作"

# 规则库注册表：题型 → 模块名
#
# ⚠️ 概括归纳题和提出对策题**暂时共用** prompts_summary。
#    规则库内部其实已经按要素分好了章节（问题类/影响类 vs 对策类），
#    但按题型拆库还没做 —— 共用不影响正确性，只是提出对策题会多读一些
#    与它无关的规则。将来拆的时候只改这张表。
RULE_LIBS: dict[str, str] = {
    TYPE_SUMMARY: "prompts_summary",
    TYPE_SOLUTION: "prompts_summary",
    TYPE_COMPREHENSIVE: "prompts_comprehensive",
}

# 规则库还没做的题型
PENDING_LIBS = (TYPE_ZHIXING, TYPE_ESSAY)


# ══════════════════════════════════════════════════════════
# 判别模式（按优先级排列，先匹配到的算数）
# ══════════════════════════════════════════════════════════
PATTERNS: list[tuple[str, re.Pattern]] = [
    # ══ 第 1 层：贯彻执行题 —— 标志词最独特 ══
    #    ⚠️ 这里**不能**放「写一篇」这种通用词：它会吃掉「写一篇议论文」。
    #    公文说自己"写一份"，文章才说"写一篇"。
    (TYPE_ZHIXING, re.compile(
        r"写一份|草拟|拟写|撰写一份|"
        r"发言稿|发言提纲|讲话稿|倡议书|公开信|建议书|宣传稿|新闻稿|"
        r"短评|编者按|工作方案|活动方案|汇报提纲|讲解稿|致辞|导言|"
        r"招募公告|通知|启事|回复|回信"
    )),

    # ══ 第 2 层：文章写作 ══
    (TYPE_ESSAY, re.compile(
        r"写一篇文章|自拟标题|自选角度|自定立意|议论文|文章写作|"
        r"结合.{0,10}资料.{0,10}(写|围绕).{0,10}(文章|议论)"
    )),

    # ══ 第 3 层：强标志 —— 明确的作答动词，直接定题型 ══
    #    综合分析题强标志：问的是「一个对象」（"对 X 的理解"）
    #    放在最前面，因为它比后面任何名词都可靠 ——
    #    「谈谈对某现象的看法，并分析原因」也是综合分析题，不该被"原因"抢走。
    (TYPE_COMPREHENSIVE, re.compile(
        r"谈谈.{0,12}(理解|认识|看法|启示|感悟)|"
        r"对.{1,24}的(理解|认识|评价|看法)|"
        r"(这句话|这一句).{0,6}(理解|含义|意思)|"
        r"比较.{0,14}(不同|异同|差异)|异同点|不同之处|"
        r"有何启示|的启示|怎么看"
    )),

    #    提出对策题强标志：问的是「要做什么」
    #    ⚠️ 必须排在概括归纳题之前 —— 「请就材料中的问题提出对策」含"问题"，
    #    但那不是让你概括问题，是让你给对策。
    (TYPE_SOLUTION, re.compile(
        r"提出.{0,8}(对策|建议|措施|意见|办法|方案)|"
        r"如何解决|怎么解决|解决办法|解决思路|"
        r"经验教训|有何启示|的启示|借鉴"
    )),

    #    概括归纳题强标志：问的是「一个要素」，且有明确的归纳动词
    (TYPE_SUMMARY, re.compile(
        r"概括|归纳|概述|简述|梳理|提炼|总结|综述"
    )),

    # ══ 第 4 层：要素词 —— 说明问的是一个「要素」而不是「一个对象」══
    #    ⚠️ 这一层必须在综合分析题弱标志之前。
    #    「分析出现'蹲族'现象的主要原因」含"现象"也含"原因" ——
    #    有明确要素词（原因）的，是概括归纳题。
    (TYPE_SOLUTION, re.compile(
        r"对策|建议|措施|办法|出路"
    )),
    (TYPE_SUMMARY, re.compile(
        r"原因|问题|影响|意义|危害|成效|作用|做法|经验|表现|现状|特点|困境"
    )),

    # ══ 第 5 层：综合分析题弱标志 —— 「分析 + 对象词」，但题干里没有要素词 ══
    (TYPE_COMPREHENSIVE, re.compile(
        r"分析.{0,20}(观点|现象|举措|案例|这句话|前述)|"
        r"阐释|解释|评析|评价"
    )),
]


# ══════════════════════════════════════════════════════════
# 判别
# ══════════════════════════════════════════════════════════
def detect_type(question: str) -> str:
    """从题干判题型。

    拿不准时返回 `概括归纳题` —— 它的规则覆盖面最广，
    误判成综合分析题的代价（拿「释义+分析」去要求概括题）比反过来大。
    """
    q = question or ""
    for type_name, pattern in PATTERNS:
        if pattern.search(q):
            return type_name
    return TYPE_SUMMARY


# ══════════════════════════════════════════════════════════
# 规则库加载
# ══════════════════════════════════════════════════════════
_rules_cache: dict[str, tuple[str, str]] = {}


def load_rules(type_name: str) -> tuple[str, str]:
    """按题型加载规则库，返回 (judge_rules, extract_rules)。

    模块级缓存 —— 规则库是静态常量，加载一次就够。
    """
    if type_name in _rules_cache:
        return _rules_cache[type_name]

    module_name = RULE_LIBS.get(type_name)
    if not module_name:
        if type_name in PENDING_LIBS:
            raise NotImplementedError(
                f"「{type_name}」的规则库还没做。\n"
                f"  已就绪：{'、'.join(RULE_LIBS)}"
            )
        raise ValueError(f"未知题型「{type_name}」。已就绪：{'、'.join(RULE_LIBS)}")

    module = importlib.import_module(module_name)
    result = (module.JUDGE_RULES, module.EXTRACT_RULES)
    _rules_cache[type_name] = result
    return result


def available_types() -> list[str]:
    """规则库已就绪的题型。"""
    return list(RULE_LIBS)


def pending_types() -> list[str]:
    """规则库还没做的题型。"""
    return list(PENDING_LIBS)


# ══════════════════════════════════════════════════════════
# 自测
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")

    # 全部为自拟题干，覆盖六种题型的判定特征
    cases = [
        ("概括归纳题", "请根据给定资料，概括A市推动中小企业数字化转型的主要做法。（20分）"),
        ("概括归纳题", "根据给定资料，分析当前农村环境污染的原因。（15分）"),
        ("提出对策题", "请就给定资料反映的问题提出对策建议。（20分）"),
        ("综合分析题", "请根据给定资料，谈谈你对“慢下来，才能走得更远”这句话的理解。（15分）"),
        ("综合分析题", "请根据给定资料，分析“一窗受理”这一改革举措。（20分）"),
        ("综合分析题", "请根据给定资料，比较甲市与乙市在社区养老上的不同之处。（15分）"),
        ("综合分析题", "“给定资料3”中提到“便民小推车”体现了什么？请谈谈你的认识。（20分）"),
        ("贯彻执行题", "请就A市推动夜间经济健康发展的做法，写一份发言提纲。（20分）"),
        ("贯彻执行题", "请草拟一份关于社区垃圾分类的倡议书。（15分）"),
        ("文章写作", "请结合给定资料，以“守正与创新”为主题，自拟标题，写一篇文章。（40分）"),
        ("文章写作", "联系实际，自选角度，写一篇议论文。（40分）"),
    ]

    ok = 0
    for expect, q in cases:
        got = detect_type(q)
        mark = "✓" if got == expect else "✗"
        if got == expect:
            ok += 1
        print(f"  {mark} [{expect}] → {got}   {q[:44]}…")

    print(f"\n  {ok}/{len(cases)} 判对")
    print(f"  已就绪题型：{available_types()}")
    print(f"  待做题型：  {pending_types()}")
    for t in available_types():
        j, e = load_rules(t)
        print(f"    {t}: 判点 {len(j):,} 字符 / 采点 {len(e):,} 字符")
