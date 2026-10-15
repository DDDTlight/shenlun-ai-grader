"""基准集测量 —— 回答「这套系统到底准到什么程度」。

**它不是什么**：不是单元测试（测代码逻辑）、不是端到端测试（测能不能跑通）。
**它测的是准确度**，需要人工标注当标准。

    python tools/measure.py --benchmark /path/to/benchmark
    python tools/measure.py --benchmark ... --only Q001
    python tools/measure.py --benchmark ... --dry-run      # 只看数据，不调 API
    python tools/measure.py --benchmark ... --repeat 3     # 附带稳定性测试

────────────────────────────────────────────────────────────
数据契约（这就是标注时要产出的东西）

    benchmark/questions/Q001_xxx/
    ├── prompt.txt          题干
    ├── material.txt        给定材料
    ├── reference.md        机构参考答案（可选；本地私有，见规格书 §9.3）
    ├── gold.json           人工冻结的采分点 ← 必须有
    └── answers/
        ├── a1.md           答卷原文
        ├── a1.label.json   人工逐点标注     ← 必须有
        └── ...

gold.json 用 `id`，代码内部用 `编号` —— 本脚本负责映射，两边都不用改。

────────────────────────────────────────────────────────────
两个关键设计

1. **判点用 gold 的要点表，不用系统采的。**
   否则采点的误差会污染判点指标，出了低分你不知道该修哪一头。
   采点质量单独用 --with-mining 测。

2. **逐点一致率是主指标，不是分数。**
   判断「这句话算不算答到了」比判断「总分该多少」客观得多。
   分数是从逐点派生的，逐点准了分数自然准。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 目标值（规格书 §9.2）
TARGET_AGREE = 0.80        # 逐点判定一致率
TARGET_DEV = 0.10          # 分数平均绝对偏差（占满分比例）


# ══════════════════════════════════════════════════════════
# 读写基准集
# ══════════════════════════════════════════════════════════
def _read(path: Path, default: str = "") -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else default


def load_question(qdir: Path) -> dict | None:
    """读一道题。返回 None = 数据不全，跳过（并说明原因）。"""
    gold_path = qdir / "gold.json"
    if not gold_path.exists():
        print(f"  ⏭ {qdir.name}：缺 gold.json，跳过")
        return None

    try:
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"  ⏭ {qdir.name}：gold.json 解析失败（{exc}），跳过")
        return None

    points = []
    for p in gold.get("采分点", []):
        text = str(p.get("要点", "")).strip()
        if not text:
            continue
        points.append({"编号": int(p.get("id", len(points) + 1)),
                       "考点": str(p.get("考点", "")).strip(),
                       "要点": text})
    if not points:
        print(f"  ⏭ {qdir.name}：采分点为空的，跳过")
        return None

    return {
        "id": gold.get("question_id", qdir.name),
        "dir": qdir,
        "prompt": _read(qdir / "prompt.txt"),
        "material": _read(qdir / "material.txt"),
        "reference": _read(qdir / "reference.md"),
        "total_score": float(gold.get("满分", 20)),
        "max_chars": gold.get("字数上限") or None,
        "points": points,
        "gold": gold,
    }


def load_answers(qdir: Path, qid: str) -> list[dict]:
    """读一份题下的所有答卷 + 标注。缺标注的跳过。"""
    adir = qdir / "answers"
    if not adir.exists():
        return []

    out = []
    for ans_path in sorted(adir.glob("*.md")):
        label_path = ans_path.with_suffix(".label.json")
        if not label_path.exists():
            print(f"      ⏭ {ans_path.name}：缺 {label_path.name}，跳过")
            continue
        try:
            label = json.loads(label_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"      ⏭ {ans_path.name}：标注解析失败（{exc}），跳过")
            continue

        # 人工判定：id → 命中
        human = {}
        for d in label.get("判定", []):
            if "id" in d:
                human[int(d["id"])] = bool(d.get("命中"))

        out.append({
            "name": ans_path.name,
            "text": _read(ans_path),
            "human": human,
            "label": label,
            "人工整体分": label.get("整体印象分"),
        })
    return out


# ══════════════════════════════════════════════════════════
# 单份答卷的比对
# ══════════════════════════════════════════════════════════
def compare(result: dict, ans: dict, total_score: float) -> dict:
    """系统判定 vs 人工标注。返回这一份的指标。

    ⚠️ **一致率的分母是「全部点」，不是「双边都给了判定的点」。**
    单边缺失（系统漏判 / 人工漏标）本身就是不一致，排除掉会算出
    「漏了 3 个点但一致率 100%」这种误导性数字。
    """
    sys_judge = {int(j["编号"]): bool(j.get("命中"))
                 for j in result.get("判定", []) if "编号" in j}
    human = ans["human"]

    ids = sorted(set(sys_judge) | set(human))
    both = [i for i in ids if i in sys_judge and i in human]
    agree = sum(1 for i in both if sys_judge[i] == human[i])
    denom = len(ids)                      # ← 全部点，含单边缺失

    sys_score = float(result["得分"])
    # 人工分优先用「整体印象分」（独立判断）；没填就从逐点派生
    human_score = ans.get("人工整体分")
    if human_score in (None, "", 0):
        hit = sum(1 for v in human.values() if v)
        human_score = round(hit / len(human) * total_score, 1) if human else 0.0
    human_score = float(human_score)

    return {
        "答卷": ans["name"],
        "总点数": denom,
        "可比点数": len(both),
        "一致点数": agree,
        "一致率": agree / denom if denom else 0.0,
        "系统分": sys_score,
        "人工分": human_score,
        "偏差": round(sys_score - human_score, 2),
        "系统命中数": sum(1 for v in sys_judge.values() if v),
        "人工命中数": sum(1 for v in human.values() if v),
        "单边缺失": denom - len(both),
        "警告": result.get("警告", []),
    }


# ══════════════════════════════════════════════════════════
# 采点质量（可选）
# ══════════════════════════════════════════════════════════
def measure_mining(question: dict) -> dict | None:
    """跑一次系统采点，和 gold 比。用模型做配对（措辞不同，纯文本比对不可靠）。"""
    import llm
    import prompts_summary as P
    from pipeline import CFG_EXTRACT

    raw = f"{question['prompt']}\n\n{question['material']}"
    if question["reference"]:
        raw += f"\n\n参考答案：\n{question['reference']}"

    sys_e, usr_e = P.extract_structure(raw)
    structured = llm.chat_json(sys_e, usr_e, **CFG_EXTRACT)
    mined = structured.get("points", []) or []
    if not mined:
        return None

    gold_txt = "\n".join(f"G{p['编号']}. {p['要点']}" for p in question["points"])
    mine_txt = "\n".join(f"M{i}. {m.get('要点', '')}" for i, m in enumerate(mined, 1))

    ask = f"""对照下面两组要点，判断哪些是**同一个要点**。

## 人工冻结的要点（标准）
{gold_txt}

## 系统采出的要点
{mine_txt}

逐一判断每个系统要点是否命中了某个标准要点（意思相同即可，措辞不同不影响）。
只输出 JSON，不要围栏：
{{"配对": [{{"系统": "M1", "标准": "G3"}}], "未配上": ["M5"], "漏掉": ["G7"]}}"""
    try:
        paired = llm.chat_json(
            "你是申论要点的比对助手，只做配对判断，不做评价。", ask,
            thinking=False, model=llm.MODEL, temperature=0)
    except Exception as exc:                          # noqa: BLE001
        print(f"      ⚠️ 采点配对失败：{exc}")
        return None

    hit = len(paired.get("配对", []))
    n_gold, n_mine = len(question["points"]), len(mined)
    return {
        "标准数": n_gold,
        "系统数": n_mine,
        "配对数": hit,
        "召回": hit / n_gold if n_gold else 0.0,
        "精确": hit / n_mine if n_mine else 0.0,
        "漏掉": paired.get("漏掉", []),
        "多采": paired.get("未配上", []),
    }


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def main() -> None:
    ap = argparse.ArgumentParser(description="基准集测量")
    ap.add_argument("--benchmark", default=str(ROOT / "benchmark"),
                    help="基准集目录（默认 ./benchmark）")
    ap.add_argument("--only", default="", help="只跑题号含该字串的题")
    ap.add_argument("--dry-run", action="store_true", help="只检查数据格式，不调 API")
    ap.add_argument("--with-mining", action="store_true", help="附带测采点质量")
    ap.add_argument("--repeat", type=int, default=1, help="每份答卷跑几次（测稳定性）")
    ap.add_argument("--no-free", action="store_true", help="跳过自由推理路径")
    args = ap.parse_args()

    bench = Path(args.benchmark).expanduser().resolve()
    qroot = bench / "questions"
    if not qroot.is_dir():
        print(f"✗ 找不到题目目录：{qroot}")
        return

    qdirs = sorted(d for d in qroot.iterdir() if d.is_dir())
    if args.only:
        qdirs = [d for d in qdirs if args.only in d.name]
    if not qdirs:
        print(f"✗ 没有匹配的题目（{qroot}）")
        return

    print("=" * 66)
    print("基准集测量")
    print(f"  基准集：{bench}")
    print(f"  题目：  {len(qdirs)} 道")
    if args.dry_run:
        print("  模式：  DRY-RUN（只检查数据，不调 API）")
    print("=" * 66)

    questions: list[dict] = []
    for qdir in qdirs:
        q = load_question(qdir)
        if not q:
            continue
        q["answers"] = load_answers(qdir, q["id"])
        if not q["answers"]:
            print(f"  ⏭ {qdir.name}：没有带标注的答卷，跳过")
            continue
        questions.append(q)

    if not questions:
        print("\n✗ 没有可测的题。检查是否有 gold.json 和 answers/*.label.json")
        return

    if args.dry_run:
        print("\n数据检查：")
        for q in questions:
            print(f"\n  【{q['id']}】{q['dir'].name}")
            print(f"    题干 {len(q['prompt'])} 字 / 材料 {len(q['material'])} 字"
                  f" / 参考答案 {'有' if q['reference'] else '无（本地私有，可缺）'}")
            print(f"    采分点 {len(q['points'])} 个，满分 {q['total_score']:.0f}")
            for a in q["answers"]:
                n = len(a["human"])
                miss = len(q["points"]) - n
                flag = "" if miss == 0 else f"  ⚠️ 比采分点少 {miss} 个"
                print(f"    答卷 {a['name']:16s} 标注 {n} 点{flag}")
        print("\n（dry-run 结束，没有调用模型）")
        return

    from pipeline import grade

    all_rows: list[dict] = []
    mining_rows: list[tuple[str, dict]] = []

    for q in questions:
        print(f"\n{'─'*66}\n【{q['id']}】{q['dir'].name}")
        print(f"  采分点 {len(q['points'])} 个   满分 {q['total_score']:.0f}")

        if args.with_mining:
            print("  采点质量…")
            m = measure_mining(q)
            if m:
                mining_rows.append((q["id"], m))
                print(f"    标准 {m['标准数']} / 系统 {m['系统数']} / 配上 {m['配对数']}"
                      f"   召回 {m['召回']:.0%}   精确 {m['精确']:.0%}")

        for a in q["answers"]:
            runs = []
            for i in range(args.repeat):
                t0 = time.time()
                try:
                    r = grade(
                        q["prompt"], q["material"], q["points"], a["text"],
                        total_score=q["total_score"], reference=q["reference"],
                        max_chars=q["max_chars"], use_free=not args.no_free,
                    )
                except Exception as exc:              # noqa: BLE001
                    print(f"    ✗ {a['name']}：{type(exc).__name__}: {exc}")
                    runs = []
                    break
                runs.append((r, round(time.time() - t0, 1)))

            if not runs:
                continue

            row = compare(runs[0][0], a, q["total_score"])
            row["题号"] = q["id"]
            row["耗时"] = runs[0][1]
            if args.repeat > 1:
                scores = [float(r["得分"]) for r, _ in runs]
                row["稳定性极差"] = round(max(scores) - min(scores), 2)
            all_rows.append(row)

            line = (f"    {a['name']:16s} 一致 {row['一致点数']:>2}/{row['总点数']:<2}"
                    f" ({row['一致率']:>5.0%})   系统 {row['系统分']:>5.1f}"
                    f"  人工 {row['人工分']:>5.1f}   偏差 {row['偏差']:+.1f}")
            if "稳定性极差" in row:
                line += f"   极差 {row['稳定性极差']:.1f}"
            print(line)
            if row["单边缺失"]:
                print(f"      ⚠️ {row['单边缺失']} 个点只有单边判定"
                      f"（系统漏判或人工漏标），已按**不一致**计入")

    if not all_rows:
        print("\n✗ 没有跑出任何结果")
        return

    # ── 汇总 ──
    n_pts = sum(r["总点数"] for r in all_rows)
    n_agr = sum(r["一致点数"] for r in all_rows)
    agree = n_agr / n_pts if n_pts else 0.0
    devs = [abs(r["偏差"]) for r in all_rows]
    given = [r for r in all_rows if r["人工分"]]
    dev_avg = sum(devs) / len(devs) if devs else 0.0
    max_total = max(q["total_score"] for q in questions)
    dev_ratio = dev_avg / max_total if max_total else 0.0

    def verdict(ok: bool) -> str:
        return "✅" if ok else "❌"

    print(f"\n{'='*66}\n汇总\n{'='*66}")
    print(f"  答卷数            {len(all_rows)}")
    print(f"  逐点一致率        {agree:>6.1%}  ({n_agr}/{n_pts})"
          f"      目标 ≥{TARGET_AGREE:.0%}   {verdict(agree >= TARGET_AGREE)}")
    print(f"  分数平均绝对偏差  {dev_avg:>6.2f} 分"
          f"（满分的 {dev_ratio:.1%}）  目标 ≤{TARGET_DEV:.0%}   {verdict(dev_ratio <= TARGET_DEV)}")

    if len(given) < len(all_rows):
        print(f"  ⚠️ {len(all_rows) - len(given)} 份没填「整体印象分」，"
              f"人工分是从逐点命中派生的（参考价值略低）")

    if args.repeat > 1:
        spreads = [r["稳定性极差"] for r in all_rows if "稳定性极差" in r]
        if spreads:
            worst = max(spreads)
            print(f"  稳定性（跑 {args.repeat} 次）  最大极差 {worst:.2f} 分"
                  f"  目标 ≤1.5   {verdict(worst <= 1.5)}")

    if mining_rows:
        print(f"\n  采点质量（{len(mining_rows)} 道题）：")
        for qid, m in mining_rows:
            print(f"    {qid}  召回 {m['召回']:.0%}  精确 {m['精确']:.0%}"
                  f"   漏 {len(m['漏掉'])} 个 / 多采 {len(m['多采'])} 个")
        r = sum(m["召回"] for _, m in mining_rows) / len(mining_rows)
        p = sum(m["精确"] for _, m in mining_rows) / len(mining_rows)
        print(f"    平均   召回 {r:.0%}  精确 {p:.0%}")

        worst_q, worst_m = max(mining_rows, key=lambda x: len(x[1]["漏掉"]))
        if worst_m["漏掉"]:
            print(f"\n  ⚠️ 采点漏得最多的（{worst_q}）：")
            for x in worst_m["漏掉"][:5]:
                print(f"      · {x}")

    print(f"\n{'='*66}")
    print("  逐点一致率是主指标 —— 分数是从它派生的，逐点准了分数自然准。")
    print("  若一致率低：先看采点召回（--with-mining），再判点。")
    print("=" * 66)


if __name__ == "__main__":
    main()
