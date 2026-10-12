"""端到端测试：四种输入方式 × 两种题型。

用法：
    python test_api.py                       # 概括归纳题内置样例（默认）
    python test_api.py --case 综合分析题          # 综合分析题内置样例
    python test_api.py --case list           # 看有哪些内置样例
    python test_api.py 题目.pdf               # 从 PDF 抽
    python test_api.py 题目.pdf --pages 5-38  # 只读 PDF 的某几页
    python test_api.py --text 资料.txt         # 从文本文件
    python test_api.py --no-free              # 只跑结构化路径
    python test_api.py --weight-a 1.0         # 完全不用自由推理的分数

依赖：pip install openai
      （要读 PDF 才需要 pypdfium2）

两个内置样例都用来**对照已知答案**：
  · 概括归纳题：机构 AI 给这份答卷 16/20
  · 综合分析题：用于验证题型路由和 B 编号规则是否生效
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ══════════════════════════════════════════════════════════
# 内置样例
# ══════════════════════════════════════════════════════════
SAMPLE_SINGLE = """2025年广东省公考《申论》题（省市）

请根据材料4，概括S市强化企业科技创新主体地位的主要做法。（20分）
要求：概括全面、准确，条理清晰。篇幅不超过200字。

给定材料
材料4
[1] 企业是广东S市科技创新当之无愧的主体和主力军，该市近年来的一组数据足以证明：约90%的科研机构、90%的科研人员、90%的研发经费、90%的发明专利申请都来源于企业。
[2] Y科技公司是S市人工智能大模型代表企业，AI与3D技术服务国内领先，旗下VR大空间品牌领跑行业，已落地全国30多个城市。日前，S市召开人工智能与机器人产业发展座谈会，该公司创始人受邀出席座谈会并发言，就行业发展问题谈认识、说体会、提建议。记者见到他时，他依然激动不已，"在S市，政府制定科技政策时，征求我们企业的意见已经是一个'规定动作'。"市有关单位负责人表示，我们就是要让"听得见市场'炮声'的人"有话语权。
[3] 近年来，S市不断健全以企业研发投入为主，财政投入、信贷融资、个人投入并举的多元化科技创新投入体系。2024年，该市全社会研发投入2236.61亿元，其中企业研发投入占比93.3%。
[5] S市某商业银行信贷部周经理说："我们银行助力企业研发，也尊重企业的主体地位。我们探索建立了科技型企业全生命周期金融服务机制，为企业提供多元化接力式金融服务，受到广大企业欢迎。今年以来，我们已经为上百家企业提供了研发融资。"该市的相关经验做法被新闻媒体竞相报道。
[6] 企业面向社会"发榜"，是S市近年来兴起的一种科研组织模式。该模式强调创新需求由市场提出、创新主体由市场选择、创新成果由市场验证，构建起以企业为主体的科研组织体系。该市创新研究院依靠"企业出题、研究院遴选、高校揭榜"工作机制，2024年以来共遴选出300多个企业课题与高校对接合作。
[7] 为了让创新成果更好地从科研人员的办公桌、实验室"走"向工厂车间，该市制造业重镇R镇率先出台以企业为主体的产学研专项政策，有效打通产学研融合的"最后一公里"。G公司是扎根R镇的优秀制造业企业，公司总经理余女士表示，公司是最早一批受惠于R镇产学研政策的企业，政府搭建平台聚合企业需求，科研机构、高校依托自身强大的研发能力有针对性地帮助企业解决问题。余女士说："产学研合作，'产'是第一位的，我们企业要掌握成果转化的主动权。"

参考答案：
1. 增强企业话语权。召开座谈会邀请企业参会发言，制定科技政策时征求企业意见。
2. 健全创新投入体系。以企业研发投入为主，财政投入、信贷融资、个人投入并举。
3. 优化金融服务。建立科技型企业全生命周期金融服务机制，提供多元化接力式金融服务。
4. 创新科研组织模式。推行"企业出题、研究院遴选、高校揭榜"工作机制。
5. 促进产学研融合。出台以企业为主体的产学研专项政策，搭建平台聚合企业需求，企业主导成果转化。
"""

# 机构报告里那份答卷，机构 AI 给 16/20
ANSWER_SINGLE = """1.征求企业意见。召开发展座谈会，邀请企业人员参加并发言，赋予企业人员话语权。
2.号召企业加大研发投入。健全企业研发投入为主多元投入并举的投入体系。
3.银行助力企业研发。建立科技型企业全生命周期服务机制，提供多元化接力式金融服务，提供研发融资。
4.构建企业为主体的科研组织体系。建立企业出题，研究院遴选，高校揭榜工作机制，鼓励企业公布难题，尊重企业家。
5.出台企业为主体的产学研专项政策。搭建平台，聚合企业需求。"""

INSTITUTION_SCORE = 16.0


# ── 综合分析题样例 ──
# ⚠️ 这是**自拟的示例题**，不是真题 —— 只用来验证链路（题型路由、B 编号规则、
#    释义/分析/总结三段结构）。真实使用时请换成你自己的题。
SAMPLE_COMPREHENSIVE = """示例题（自拟，非真题）

请根据"给定资料"，谈谈你对"城市更新不是大拆大建"这句话的理解。（15分）
要求：观点明确，分析透彻，不超过300字。

给定资料
[1] 近年来，一些城市在更新改造中热衷于推倒重来，成片拆除老街区，代之以整齐划一的高楼。结果是城市面貌趋同，历史文脉被割裂，原有社区关系也随之瓦解。
[2] 某市在老城区改造中采取了另一种做法：保留原有街巷肌理，只对危房进行修缮加固，把腾退出的空间改造成社区食堂、口袋公园。居民不用搬走，生活反而更方便了。
[3] 有专家指出，渐进式改造虽然见效慢，但避免了大规模拆迁带来的财政压力和社会矛盾。更重要的是，它守住了城市的记忆，让老居民留得下、新市民进得来。
[4] "城市更新不是推倒重来，而是让城市在保留中生长。"这位专家说，"改造的最终目的，是让生活在这里的人过得更好，而不是让城市看起来更整齐。"

参考答案：
1. 释义：城市更新不是推倒重来，而是以保留利用为主、拆除重建为辅的渐进式改造。
2. 分析危害：大拆大建会割裂历史文脉，瓦解社区关系，增加财政压力。
3. 分析意义：渐进式改造能守住城市记忆，缓解财政压力，改善居民生活。
4. 总结评价：城市更新应因地制宜，实现经济效益与社会效益的统一。
"""

# 故意漏掉「总结评价」里的"经济效益与社会效益的统一"
ANSWER_COMPREHENSIVE = """"城市更新不是大拆大建"意为：城市更新应以保留利用为主、拆除重建为辅，
采取渐进式改造，而非推倒重来。
因为大拆大建会割裂历史文脉、瓦解社区关系，还会增加财政压力。
而渐进式改造能够守住城市记忆，缓解财政压力，让居民生活得到改善。"""


SAMPLES = {
    "概括归纳题": (SAMPLE_SINGLE, ANSWER_SINGLE.strip()),
    "综合分析题": (SAMPLE_COMPREHENSIVE, ANSWER_COMPREHENSIVE.strip()),
}


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    use_free = "--no-free" not in sys.argv
    weight_a = 1.0
    if "--weight-a" in sys.argv:
        weight_a = float(sys.argv[sys.argv.index("--weight-a") + 1])

    case = "概括归纳题"
    if "--case" in sys.argv:
        case = sys.argv[sys.argv.index("--case") + 1]
    if case == "list":
        print("内置样例：" + "、".join(SAMPLES))
        return
    if case not in SAMPLES:
        print(f"✗ 没有「{case}」这个样例。可选：{'、'.join(SAMPLES)}")
        sys.exit(1)

    from pipeline import format_report, grade_input

    kwargs: dict = {"use_free": use_free, "weight_a": weight_a, "require_items": True}
    sample_text, sample_answer = SAMPLES[case]

    # ── 组装输入（三种方式）──
    if "--text" in sys.argv:
        txt = Path(sys.argv[sys.argv.index("--text") + 1])
        print(f"输入方式：文本文件 {txt.name}")
        kwargs["raw_text"] = txt.read_text(encoding="utf-8")
    elif args:
        pdf = Path(args[0])
        if not pdf.exists():
            print(f"✗ 找不到 {pdf}")
            sys.exit(1)
        print(f"输入方式：PDF {pdf.name}")
        kwargs["from_pdf"] = str(pdf)
        if "--pages" in sys.argv:
            spec = sys.argv[sys.argv.index("--pages") + 1]
            a, b = spec.split("-")
            kwargs["pdf_pages"] = (int(a), int(b))
    else:
        print(f"输入方式：内置样例（{case}）")
        kwargs["raw_text"] = sample_text

    # ── 跑 ──
    print("=" * 76)
    print("批改")
    print("=" * 76)
    result = grade_input(sample_answer, **kwargs)

    # ── 报告 ──
    print()
    print("=" * 76)
    print("批改报告")
    print("=" * 76)
    print(format_report(result))

    # ── 对照 ──
    if kwargs.get("raw_text") == SAMPLE_SINGLE:
        print()
        print("=" * 76)
        print("与机构 AI 对照（同一份答卷、同一道题）")
        print("=" * 76)
        ours = float(result["得分"])
        print(f"  机构 AI : {INSTITUTION_SCORE}")
        print(f"  我们    : {ours}   （结构化 A={result['得分A']}"
              f" ｜ 自由推理 B={result['得分B']}）")
        print(f"  差值    : {ours - INSTITUTION_SCORE:+.1f}")
        print(f"\n  命中要点 {result['命中要点数']}/{result['要点总数']}"
              f"（机构报告也是 8/10 → 16 分）")

    # ── 自检 ──
    print()
    print("=" * 76)
    print("自检")
    print("=" * 76)
    n = result["要点总数"]
    ids = sorted(j["编号"] for j in result["判定"])
    no_ev = [j["编号"] for j in result["判定"] if j.get("命中") and not j.get("答卷原文")]
    print(f"  题型          : {result['题型']}")
    print(f"  编号完整性    : {'✓' if ids == list(range(1, n + 1)) else '✗ ' + str(ids)}")
    print(f"  命中都有原文  : {'✓' if not no_ev else '✗ 缺 ' + str(no_ev)}")
    print(f"  分数未越界    : {'✓' if 0 <= float(result['得分']) <= result['满分'] else '✗'}")
    print(f"  耗时          : {result['耗时秒']}s")


if __name__ == "__main__":
    main()
