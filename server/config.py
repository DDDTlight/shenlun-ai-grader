"""服务配置 —— 配置从哪读、API Key 存哪。

⚠️ **为什么不能只读项目根的 .env**

打包成 exe 后，「当前目录」是不可靠的 ——
PyInstaller 会把代码解包到临时目录，且**每次运行的路径可能不同**。
把 Key 写在项目根，用户配一次、下次运行就没了。

所以 Key 写到**用户目录**：

    ~/.shenlun/.env                 ← 打包模式（Windows: %USERPROFILE%\\.shenlun\\.env）
    <项目根>/.env                    ← 开发模式（和现在一致，不用改习惯）

**查找顺序：环境变量 > 用户目录 > 项目根。**

这个要一开始就定好，否则打包时返工。
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

#: 用户配置目录 —— Key 存这里，跨运行稳定
APP_DIR = Path.home() / ".shenlun"
USER_ENV = APP_DIR / ".env"

#: 项目根 .env（开发模式）
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"


# ══════════════════════════════════════════════════════════
# API Key
# ══════════════════════════════════════════════════════════
KEY_NAME = "DEEPSEEK_API_KEY"


def _parse_env(path: Path) -> dict[str, str]:
    """极简 .env 解析。兼容 `export KEY=...` 写法。"""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def load_api_key() -> str:
    """按 环境变量 > 用户目录 > 项目根 的顺序找 Key。找不到返回空串。"""
    key = (os.environ.get(KEY_NAME) or "").strip()
    if key:
        return key
    for path in (USER_ENV, PROJECT_ENV):
        key = _parse_env(path).get(KEY_NAME, "").strip()
        if key:
            return key
    return ""


def save_api_key(key: str) -> Path:
    """把 Key 写进**用户目录**（跨运行稳定）。返回写入路径。

    不写项目根 —— 打包后那个路径下次运行就没了。
    """
    APP_DIR.mkdir(parents=True, exist_ok=True)
    others = {k: v for k, v in _parse_env(USER_ENV).items() if k != KEY_NAME}
    lines = [f"{k}={v}" for k, v in others.items()]
    if key.strip():
        lines.append(f"{KEY_NAME}={key.strip()}")
    USER_ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # 立刻生效，不用重启服务
    os.environ[KEY_NAME] = key.strip()
    return USER_ENV


def mask(key: str) -> str:
    """打码显示。前端只该看到这个，不该拿到明文。"""
    if not key:
        return ""
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "已配置"


# ══════════════════════════════════════════════════════════
# 服务参数
# ══════════════════════════════════════════════════════════
HOST = os.environ.get("SHENLUN_HOST", "127.0.0.1")
PORT = int(os.environ.get("SHENLUN_PORT", "8000"))

#: 一次批改可能 30–60 秒。浏览器那边等得住，但别让它无限等。
GRADE_TIMEOUT_HINT = 180

#: PDF 上传大小上限（MB）。教材级 PDF 常有几十 MB。
MAX_PDF_MB = 60
