"""服务配置 —— 静态文件路径、监听地址等。

⚠️ **API Key 的读写不在这里**，在 `llm.py`。

这里只是把它转出来给服务层用。原因：Key 是「调用模型」这件事的一部分，
`llm.py` 才是真正用它的地方。曾经两边各写了一套查找逻辑，结果：

    server/config.py   写/读  ~/.shenlun/.env    ← 界面「设置」用这个
    llm.py             只读    <代码目录>/.env    ← 调模型用这个

**两边不一致 → 用户在界面上配完 Key，批改依然失败。**
而且源码模式下 <代码目录>/.env 正好存在（开发者自己建的），
所以这个问题只在打包后才暴露。

现在只有一处实现，两边共用。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# ══════════════════════════════════════════════════════════
# 路径
# ══════════════════════════════════════════════════════════
def is_frozen() -> bool:
    """是不是打包后的 exe。"""
    return getattr(sys, "frozen", False)


#: PyInstaller 解包目录（开发模式下就是本文件所在目录）
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


# ══════════════════════════════════════════════════════════
# API Key —— 实现全在 llm.py，这里只做转出
# ══════════════════════════════════════════════════════════
def _llm():
    """延迟导入 llm。

    config 会被 main.py 在很早的时候导入，而 llm 依赖 httpx / openai。
    延迟导入让「没配 Key 也能启动服务、打开页面去配」这件事成立。
    """
    import llm
    return llm


def load_api_key() -> str:
    """按 环境变量 > 用户目录 > 代码目录 找 Key。找不到返回空串。"""
    return _llm().load_key()


def save_api_key(key: str) -> Path:
    """把 Key 写进用户目录（跨运行稳定）。返回写入路径。"""
    return _llm().save_key(key)


def mask(key: str) -> str:
    """打码显示。前端只该看到这个，不该拿到明文。"""
    if not key:
        return ""
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "已配置"


def user_env_path() -> Path:
    """用户目录下的 .env 路径（给界面显示用）。"""
    return _llm().USER_ENV


def key_name() -> str:
    return _llm().KEY_NAME


# ══════════════════════════════════════════════════════════
# 服务参数
# ══════════════════════════════════════════════════════════
HOST = os.environ.get("SHENLUN_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHENLUN_PORT", "8000"))

#: 一次批改可能 30–60 秒。浏览器那边等得住，但别让它无限等。
GRADE_TIMEOUT_HINT = 180

#: PDF 上传大小上限（MB）。教材级 PDF 常有几十 MB。
MAX_PDF_MB = 60
