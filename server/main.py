"""FastAPI 应用入口：定义 app、路由、错误翻译。

数据流长这样：

    浏览器 --POST /api/grade-->  本服务 --函数调用--> pipeline.grade_input()
             {"answer":...}                            ↓
             <--{"score":...}--                    DeepSeek API

**本层只做三件事**（和 ollama-chat-api 同一个思路）：

  1. **校验** —— Pydantic 挡掉非法请求。批改一次几十秒还要花钱，
     "答卷忘了传"这种错不该白跑一趟。
  2. **转换** —— HTTP 请求 → `grade_input()` 的参数；结果 → JSON
     （`Decimal` 不能直接序列化，这是最容易踩的坑）。
  3. **承载** —— 返回页面、收上传文件、管配置。

**不在这里做任何批改逻辑** —— 那些全在 pipeline / scoring / rules_check 里。
这一层薄了，下面才能独立测试。

启动：
    python -m server.main           # 或 cd server && python main.py
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# pipeline 在上一级目录 —— 打包后 _MEIPASS 才是根，所以两处都加
_HERE = Path(__file__).resolve().parent
for p in (_HERE.parent, _HERE):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import config as cfg
from models import (
    GradeRequest, GradeResponse, HealthResponse,
    PdfResponse, SettingsRequest, SettingsResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("shenlun")

VERSION = "0.1.0"

app = FastAPI(
    title="申论 AI 批改",
    version=VERSION,
    description="本地批改工具。模型判、代码算。",
)

# 前端可能从 file:// 直接打开（不走本服务的 /），放通本地来源
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$|^null$",
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════
# 页面
# ══════════════════════════════════════════════════════════
def _static_dir() -> Path:
    """静态文件目录。打包后要用 _MEIPASS，不能靠 __file__。"""
    return cfg.BASE_DIR / "static"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> HTMLResponse:
    page = _static_dir() / "index.html"
    if not page.exists():
        return HTMLResponse(
            f"<h1>找不到前端页面</h1><p>期望路径：{page}</p>"
            f"<p>API 文档在 <a href='/docs'>/docs</a></p>",
            status_code=500,
        )
    return HTMLResponse(page.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════
# 健康检查
# ══════════════════════════════════════════════════════════
@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    import routing
    return HealthResponse(
        ok=True,
        version=VERSION,
        has_key=bool(cfg.load_api_key()),
        supported_types=routing.available_types(),
        excluded_types=routing.excluded_types(),
    )


# ══════════════════════════════════════════════════════════
# 批改
# ══════════════════════════════════════════════════════════
@app.post("/api/grade", response_model=GradeResponse)
def grade_endpoint(req: GradeRequest) -> GradeResponse:
    """跑一次批改。

    ⚠️ **同步 `def` 而不是 `async def`** —— 批改要 30–60 秒，
    期间几乎全在等 DeepSeek 的网络响应。写成 async 会占住事件循环，
    服务在等待期间无法响应别的请求（比如健康检查）。
    写成同步 `def`，FastAPI 会自动把它扔进线程池。
    """
    if not cfg.load_api_key():
        raise HTTPException(
            status_code=400,
            detail="还没配置 API Key。请在页面右上角「设置」里填，或写进 ~/.shenlun/.env",
        )
    if not req.has_material():
        raise HTTPException(status_code=400, detail="没有资料：至少要给题干、材料或整块资料")

    # 延迟导入 —— 让服务能在没配 Key 时也启动起来（能开页面去配 Key）
    from pipeline import grade_input

    kwargs: dict = {
        "use_free": req.use_free,
        "require_items": req.require_items,
    }
    if req.full_score is not None:
        kwargs["total_score"] = req.full_score
    if req.max_chars is not None:
        kwargs["max_chars"] = req.max_chars
    if req.sample.strip():
        kwargs["reference"] = req.sample.strip()
    if req.question.strip():
        kwargs["question"] = req.question.strip()
    if req.material.strip():
        kwargs["material"] = req.material.strip()

    t0 = time.time()
    try:
        result = grade_input(req.answer, raw_text=req.merged_raw(), **kwargs)
    except ValueError as exc:                 # 明确不做的题型、资料抽不出要点等
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:                  # noqa: BLE001
        logger.exception("批改失败")
        raise HTTPException(status_code=500, detail=f"批改失败：{exc}") from exc

    return _to_response(result, time.time() - t0)


def _to_response(r: dict, elapsed: float) -> GradeResponse:
    """把 pipeline 的 dict 转成响应模型。

    ⚠️ `得分` / `分歧` 是 `Decimal` —— 不能直接 JSON 序列化。
    Pydantic 的 float 字段会帮忙转，但别在这里再手工塞进裸 dict。
    """
    return GradeResponse(
        ok=True,
        score=float(r["得分"]),
        full_score=float(r["满分"]),
        type=r.get("题型", ""),
        subtype=r.get("子类", ""),
        rule_source=r.get("规则来源", ""),
        hit_count=int(r.get("命中要点数", 0)),
        total=int(r.get("要点总数", 0)),
        points=r.get("要点表", []),
        judgments=r.get("判定", []),
        deductions=[*r.get("形式扣分", []), *r.get("模型判违规", [])],
        warnings=r.get("警告", []),
        char_count=int(r.get("字数", 0)),
        elapsed=round(elapsed, 1),
        score_a=float(r["得分A"]) if r.get("得分A") is not None else None,
        score_b=float(r["得分B"]) if r.get("得分B") is not None else None,
        divergence=float(r.get("分歧", 0)),
    )


# ══════════════════════════════════════════════════════════
# PDF 导入
# ══════════════════════════════════════════════════════════
# ⚠️ FastAPI 在**定义**用到 File() 的路由时会检查 python-multipart，
#    缺了直接抛 RuntimeError —— 整个服务起不来。
#    一个可选的 PDF 功能不该让服务瘫痪，所以改成条件注册。
def _has_multipart() -> bool:
    for name in ("python_multipart", "multipart"):
        try:
            __import__(name)
            return True
        except ImportError:
            continue
    return False


if _has_multipart():
    from fastapi import File, UploadFile

    @app.post("/api/import-pdf", response_model=PdfResponse)
    async def import_pdf(file: UploadFile = File(...)) -> PdfResponse:
        """上传 PDF，返回提取的文字。

        **只抽文字，不批改。** 抽完由用户在前端校对、编辑，再提交批改 ——
        PDF 抽取常有错乱（双栏、页眉页脚），不该直接喂给模型。
        """
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="只支持 .pdf 文件")

        data = await file.read()
        if len(data) > cfg.MAX_PDF_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400, detail=f"文件太大（上限 {cfg.MAX_PDF_MB} MB）")

        import pdf_import

        if not pdf_import.available():
            raise HTTPException(
                status_code=500,
                detail="没装 pypdfium2，读不了 PDF。请 pip install pypdfium2",
            )

        # 写到临时文件再抽 —— pypdfium2 只认路径
        import tempfile

        tmp = Path(tempfile.mkstemp(suffix=".pdf")[1])
        try:
            tmp.write_bytes(data)
            text = pdf_import.extract_text(tmp)
            pages = len(pdf_import.extract_pages(tmp))
        except Exception as exc:                  # noqa: BLE001
            logger.exception("PDF 抽取失败")
            raise HTTPException(status_code=400, detail=f"PDF 抽取失败：{exc}") from exc
        finally:
            tmp.unlink(missing_ok=True)

        if not text.strip():
            return PdfResponse(
                ok=False, pages=pages,
                message="抽不出文字 —— 这大概率是扫描版 PDF，需要先做 OCR",
            )
        return PdfResponse(ok=True, text=text, pages=pages, chars=len(text))

else:
    @app.post("/api/import-pdf", response_model=PdfResponse)
    async def import_pdf_disabled() -> PdfResponse:
        """没装 python-multipart 时的占位 —— 只禁用这个功能，服务照常跑。"""
        return PdfResponse(
            ok=False,
            message="没装 python-multipart，无法接收上传。"
                    "请 pip install python-multipart；纯文字粘贴不受影响。",
        )


# ══════════════════════════════════════════════════════════
# 配置
# ══════════════════════════════════════════════════════════
@app.get("/api/settings", response_model=SettingsResponse)
def read_settings() -> SettingsResponse:
    key = cfg.load_api_key()
    import llm
    return SettingsResponse(
        has_key=bool(key),
        api_key_masked=cfg.mask(key),
        env_path=str(cfg.USER_ENV),
        model=llm.MODEL,
        model_pro=llm.MODEL_PRO,
    )


@app.post("/api/settings", response_model=SettingsResponse)
def write_settings(req: SettingsRequest) -> SettingsResponse:
    """保存 API Key 到**用户目录**（跨运行稳定，打包后也有效）。"""
    if req.api_key is not None:
        path = cfg.save_api_key(req.api_key)
        logger.info("API Key 已写入 %s", path)
    return read_settings()


# ══════════════════════════════════════════════════════════
# 直接运行：起服务 + 自动开浏览器
# ══════════════════════════════════════════════════════════
def run() -> None:
    import threading
    import webbrowser

    import uvicorn

    url = f"http://{cfg.HOST}:{cfg.PORT}"
    logger.info("申论批改服务启动：%s", url)
    if not cfg.load_api_key():
        logger.warning("还没配置 API Key —— 打开页面后在「设置」里填")

    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=cfg.HOST, port=cfg.PORT, log_level="info")


if __name__ == "__main__":
    run()
