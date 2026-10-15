"""Pydantic 模型 —— 请求与响应的形状。

**为什么要有这一层**：非法请求在**到达 pipeline 之前**就被 422 挡掉。
批改一次要几十秒、要花钱，不该因为"答卷忘了传"白跑一趟。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


# ══════════════════════════════════════════════════════════
# 批改
# ══════════════════════════════════════════════════════════
class GradeRequest(BaseModel):
    """批改请求。字段名对齐规格书 §11.2。"""

    answer: str = Field(..., min_length=1, description="考生答卷（必填）")

    # ── 两种给资料的方式，任选其一 ──
    question: str = Field("", description="题干与作答要求")
    material: str = Field("", description="给定材料")
    raw_text: str = Field("", description="整块资料（题干+材料+参考答案），PDF 抽的或粘贴的")

    full_score: float | None = Field(None, gt=0, le=100, description="满分。不传则从题干正则提取")
    sample: str = Field("", description="机构参考答案（可选，用于定采分点）")
    analysis: str = Field("", description="机构解析（可选）")

    # ── 形式检查参数 ──
    require_items: bool = Field(False, description="题干是否要求分条列点")
    max_chars: int | None = Field(None, gt=0, description="字数上限（题干里写的）")

    use_free: bool = Field(True, description="是否跑自由推理路径（只做分歧提示，不影响分数）")

    @field_validator("answer")
    @classmethod
    def _answer_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("答卷不能为空")
        return v.strip()

    def merged_raw(self) -> str:
        """把分开给的字段拼成整块资料。

        raw_text 优先；否则用 question + material + sample/analysis 拼。
        pipeline 只认一个整块，所以拼接在服务层做。
        """
        if self.raw_text.strip():
            return self.raw_text.strip()
        parts = [self.question.strip(), self.material.strip()]
        tail = "\n\n".join(x for x in (self.sample.strip(), self.analysis.strip()) if x)
        if tail:
            parts.append("参考答案：\n" + tail)
        return "\n\n".join(p for p in parts if p)

    def has_material(self) -> bool:
        return bool(self.raw_text.strip() or self.material.strip() or self.question.strip())


class Judgment(BaseModel):
    """一条判定。字段名对齐 pipeline 的输出（编号 / 命中 / 答卷原文 / 材料出处）。"""

    编号: int
    命中: bool
    答卷原文: str = ""
    材料出处: str = ""
    原因: str = ""

    model_config = {"extra": "allow"}      # 模型多给的字段别丢，前端可能要用


class Point(BaseModel):
    """一个采分点。"""

    编号: int
    考点: str = ""
    要点: str = ""

    model_config = {"extra": "allow"}


class GradeResponse(BaseModel):
    """批改响应。字段名对齐规格书 §11.3，并补上题型/子类等实际会用到的东西。"""

    ok: bool = True

    score: float = 0
    full_score: float = 0
    type: str = ""                       # 题型（概括归纳题 / 贯彻执行题 …）
    subtype: str = ""                    # 公文子类（仅贯彻执行题）
    rule_source: str = ""                # 用了哪个规则库（排查路由问题用）

    hit_count: int = 0
    total: int = 0
    points: list[Point] = Field(default_factory=list)
    judgments: list[Judgment] = Field(default_factory=list)

    deductions: list[dict[str, Any]] = Field(default_factory=list)   # 扣分项
    warnings: list[str] = Field(default_factory=list)                 # 校验提醒

    char_count: int = 0
    elapsed: float = 0

    # ── 双评（自由推理只做分歧提示，不参与计分）──
    score_a: float | None = None
    score_b: float | None = None
    divergence: float = 0

    message: str = ""                    # ok=False 时的原因


# ══════════════════════════════════════════════════════════
# PDF 导入
# ══════════════════════════════════════════════════════════
class PdfResponse(BaseModel):
    ok: bool = True
    text: str = ""
    pages: int = 0
    chars: int = 0
    message: str = ""


# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════
class SettingsResponse(BaseModel):
    """读配置。**永远不返回明文 Key** —— 前端只需要知道"配了没"。"""

    has_key: bool = False
    api_key_masked: str = ""
    env_path: str = ""
    model: str = ""
    model_pro: str = ""


class SettingsRequest(BaseModel):
    api_key: str | None = Field(None, description="留空 = 不修改；空字符串 = 清除")


# ══════════════════════════════════════════════════════════
# 健康检查
# ══════════════════════════════════════════════════════════
class HealthResponse(BaseModel):
    ok: bool = True
    version: str = ""
    has_key: bool = False
    supported_types: list[str] = Field(default_factory=list)
    excluded_types: list[str] = Field(default_factory=list)
