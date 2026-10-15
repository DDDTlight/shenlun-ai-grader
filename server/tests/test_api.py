"""服务层测试。

**只测这一层的职责**：校验、转换、状态码翻译。
不测批改本身 —— 那要花钱调 API，而且属于 pipeline 的测试范围。

跑：
    pytest server/tests -v
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config as cfg
import main


@pytest.fixture
def client():
    return TestClient(main.app)


@pytest.fixture
def with_key(monkeypatch):
    """假装配了 Key —— 否则批改端点在第一步就被 400 挡掉。"""
    monkeypatch.setattr(cfg, "load_api_key", lambda: "sk-test-not-real")


# ══════════════════════════════════════════════════════════
# 健康检查
# ══════════════════════════════════════════════════════════
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert "概括归纳题" in d["supported_types"]
    assert "文章写作" in d["excluded_types"]      # 明确不做的要能报出来


# ══════════════════════════════════════════════════════════
# 页面
# ══════════════════════════════════════════════════════════
def test_index_serves_html(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "申论" in r.text
    assert "<script>" in r.text


# ══════════════════════════════════════════════════════════
# 配置：永远不回明文 Key
# ══════════════════════════════════════════════════════════
def test_settings_never_returns_plain_key(client, monkeypatch):
    monkeypatch.setattr(cfg, "load_api_key", lambda: "sk-abcdef1234567890wxyz")
    d = client.get("/api/settings").json()
    assert d["has_key"] is True
    assert "sk-abcdef1234567890wxyz" not in str(d)     # 明文不能出现在响应里
    assert "…" in d["api_key_masked"]                  # 只能是打码形式


def test_settings_reports_missing_key(client, monkeypatch):
    monkeypatch.setattr(cfg, "load_api_key", lambda: "")
    d = client.get("/api/settings").json()
    assert d["has_key"] is False
    assert d["api_key_masked"] == ""


# ══════════════════════════════════════════════════════════
# 批改：校验要在花钱之前挡住
# ══════════════════════════════════════════════════════════
def test_grade_rejects_blank_answer(client, with_key):
    """答卷为空 → 422。Pydantic 在到达 pipeline 之前就挡住。"""
    r = client.post("/api/grade", json={"answer": "   ", "raw_text": "资料"})
    assert r.status_code == 422


def test_grade_rejects_missing_material(client, with_key):
    """有答卷但没资料 → 400。"""
    r = client.post("/api/grade", json={"answer": "我写的答案"})
    assert r.status_code == 400
    assert "资料" in r.json()["detail"]


def test_grade_requires_api_key(client, monkeypatch):
    """没配 Key → 400，且提示要能指路。"""
    monkeypatch.setattr(cfg, "load_api_key", lambda: "")
    r = client.post("/api/grade",
                    json={"answer": "我写的答案", "raw_text": "题干和材料"})
    assert r.status_code == 400
    assert "API Key" in r.json()["detail"]


def test_grade_rejects_excluded_type(client, with_key):
    """文章写作是明确不做的题型 → 400，而不是静默回退成小题。"""
    r = client.post("/api/grade", json={
        "answer": "随便写一篇文章",
        "raw_text": "请结合给定资料，自拟标题，写一篇文章。（40分）",
        "full_score": 40,
    })
    assert r.status_code == 400
    assert "文章写作" in r.json()["detail"]


# ══════════════════════════════════════════════════════════
# 请求体的拼接逻辑
# ══════════════════════════════════════════════════════════
def test_merged_raw_prefers_raw_text():
    from models import GradeRequest
    req = GradeRequest(answer="a", raw_text="整块资料",
                       question="题干", material="材料")
    assert req.merged_raw() == "整块资料"


def test_merged_raw_joins_fields():
    from models import GradeRequest
    req = GradeRequest(answer="a", question="题干", material="材料",
                       sample="参考答案内容")
    merged = req.merged_raw()
    assert "题干" in merged and "材料" in merged
    assert "参考答案" in merged and "参考答案内容" in merged


def test_has_material():
    from models import GradeRequest
    assert GradeRequest(answer="a", question="题干").has_material()
    assert GradeRequest(answer="a", material="材料").has_material()
    assert GradeRequest(answer="a", raw_text="资料").has_material()
    assert not GradeRequest(answer="a").has_material()


# ══════════════════════════════════════════════════════════
# PDF 端点：没装 multipart 时也不能把服务带崩
# ══════════════════════════════════════════════════════════
def test_pdf_endpoint_exists(client):
    """路由必须存在 —— 缺 multipart 时是降级实现，不是消失。"""
    paths = {r.path for r in main.app.routes if hasattr(r, "path")}
    assert "/api/import-pdf" in paths


def test_pdf_endpoint_behaviour_depends_on_multipart(client):
    """两种环境下的行为都要对。

    这是**环境相关**的测试 —— 装了 python-multipart 和没装，
    端点是两个不同的函数实现。
    """
    r = client.post("/api/import-pdf")
    if main._has_multipart():
        assert r.status_code == 422          # 缺 file 字段 = 客户端错误
    else:
        assert r.status_code == 200          # 降级实现：不报错，只说明原因
        d = r.json()
        assert d["ok"] is False
        assert "multipart" in d["message"]


@pytest.mark.skipif(not main._has_multipart(),
                    reason="没装 python-multipart，走的是降级实现")
def test_pdf_rejects_non_pdf(client):
    """非 .pdf 文件要挡在前面，不要等抽取时才失败。"""
    r = client.post(
        "/api/import-pdf",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert r.status_code == 400
    assert ".pdf" in r.json()["detail"]
