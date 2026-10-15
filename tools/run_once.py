"""手动调试脚本：四种输入方式 × 两种题型。

⚠️ **这不是单元测试** —— 里面没有 test_ 函数，pytest 不会收集它。
   它会真的调用模型（花钱、要联网），所以只能手动跑。
   自动化测试在 tests/ 和 server/tests/ 下。

用法：
    python tools/run_once.py                       # 概括归纳题内置样例（默认）
    python tools/run_once.py --case 综合分析题          # 综合分析题内置样例
    python tools/run_once.py --case list           # 看有哪些内置样例
    python tools/run_once.py 题目.pdf               # 从 PDF 抽
    python tools/run_once.py 题目.pdf --pages 5-38  # 只读 PDF 的某几页
    python tools/run_once.py --text 资料.txt         # 从文本文件
    python tools/run_once.py --no-free              # 只跑结构化路径
    python tools/run_once.py --weight-a 1.0         # 完全不用自由推理的分数

依赖：pip install openai
      （要读 PDF 才需要 pypdfium2）

两个内置样例都用于**对照已知答案**：
  · 概括归纳题：样例答卷故意漏掉 2 个要点，用于验证判点与扣分逻辑
  · 综合分析题：用于验证题型路由和 B 编号规则是否生效
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# ══════════════════════════════════════════════════════════
# 内置样例（全部自拟，非真题）
# ══════════════════════════════════════════════════════════
SAMPLE_SINGLE = """示例题（自拟，非真题）

请根据"给定资料"，概括A市推动中小企业数字化转型的主要做法。（20分）
要求：概括全面、准确，条理清晰。不超过200字。

给定资料

[1] 中小企业是A市经济的基本盘，贡献了全市 80% 的就业和 65% 的税收。但长期以来，不少企业受制于资金、技术和人才，数字化转型"不敢转、不会转"。近年来，A市把推动中小企业数字化转型作为产业升级的突破口，摸索出一套行之有效的办法。

[2] "过去我们最怕的是不知道该往哪儿转。"B机械制造公司负责人说。针对这种顾虑，A市组织专家团队逐户走访规模以上中小企业，为企业做"数字化体检"，一家一策开出转型建议清单。截至目前，已累计为 1200 余家企业提供了诊断服务。

[3] 转型需要真金白银。A市设立了中小企业数字化转型专项资金，对购买云服务、工业软件的企业按合同金额的 30% 给予补贴，单户最高 50 万元。同时，市里联合多家银行推出"数字贷"产品，以企业数据资产作为授信参考，累计发放贷款 37 亿元。

[4] A市还搭建了区域工业互联网平台，接入企业 5000 余家，提供设备联网、产能共享、订单撮合等服务。中小企业不必自建系统，按需购买平台服务即可，转型门槛大幅降低。

[5] 人才是另一个瓶颈。A市依托本地职业院校开设数字化技能培训班，每年培训产业工人 2 万人次；同时与高校合作设立"企业数字官"研修班，帮助企业管理层补齐认知短板。

[6] 该市工信局负责人介绍，下一步将推动产业链上下游协同转型，让龙头企业带动配套中小企业一起上"云"。

参考答案：
1. 开展诊断服务。组织专家逐户走访，为企业做"数字化体检"，一户一策制定转型建议。
2. 加强资金支持。设立专项资金，按合同金额给予补贴；联合银行推出"数字贷"，以数据资产作为授信参考。
3. 搭建服务平台。建设区域工业互联网平台，提供设备联网、产能共享、订单撮合等服务，降低转型门槛。
4. 强化人才支撑。依托职业院校开展技能培训，与高校合作举办管理层研修班。
5. 推动协同转型。由龙头企业带动产业链上下游配套企业协同上"云"。
"""

# 样例答卷：故意漏掉「推动协同转型」，且第 2 点细则不全（未提"数字贷"）
ANSWER_SINGLE = """1.开展诊断服务。组织专家逐户走访，为企业做"数字化体检"，一户一策制定转型建议。
2.加强资金支持。设立中小企业数字化转型专项资金，按合同金额给予补贴。
3.搭建服务平台。建设区域工业互联网平台，提供设备联网、产能共享、订单撮合等服务。
4.强化人才支撑。依托职业院校开展技能培训，与高校合作举办管理层研修班。"""

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
