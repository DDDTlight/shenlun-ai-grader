"""启动入口 —— 便携版和 exe 都从这里进。

开发时可以直接 `python -m server.main`；打包后必须走这里，因为有三个
**只在打包后才会出现**的问题要处理：

  1. **路径**：冻结环境下 `__file__` 的语义和开发时不同，
     静态文件（`server/static/index.html`）会找不到。
  2. **端口**：朋友的电脑上 8000 可能被别的软件占着，
     直接崩掉他会一脸茫然。这里自动往后找空端口。
  3. **报错要停住** ⚠️ 最关键的一条：
     Windows 上双击 `.bat`，脚本一挂窗口立刻就关了，
     用户只看到黑框一闪，**什么都读不到**，只会说"你这东西打不开"。
     所以任何异常都必须打印完再 `input()` 等一次回车。

用法（便携版）：
    双击 启动.bat
用法（开发）：
    python launcher.py
"""
from __future__ import annotations

import os
import socket
import sys
import traceback
from pathlib import Path


# ══════════════════════════════════════════════════════════
# 路径：让 import 能同时应付三种布局
# ══════════════════════════════════════════════════════════
def app_root() -> Path:
    """应用根目录。

    三种布局都要能对上：

        开发       AI_shenlun/launcher.py            → AI_shenlun/
        便携版     shenlun-win-x64/app/launcher.py   → .../app/
        PyInstaller  解包到临时目录                    → sys._MEIPASS
    """
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


ROOT = app_root()
SERVER_DIR = ROOT / "server"

for p in (ROOT, SERVER_DIR):
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


# ══════════════════════════════════════════════════════════
# 端口
# ══════════════════════════════════════════════════════════
def free_port(start: int, tries: int = 20) -> int:
    """从 start 开始找一个能用的端口。

    不要让朋友去理解「端口被占用」——他只会觉得程序坏了。
    自己往后找就是了。
    """
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start          # 全被占了就硬着头皮用第一个，让 uvicorn 去报错


# ══════════════════════════════════════════════════════════
# 首次运行的自检
# ══════════════════════════════════════════════════════════
def preflight() -> list[str]:
    """返回警告列表。**只警告不阻断** —— 没配 Key 也要能开页面去配。"""
    warns: list[str] = []
    try:
        import config as cfg
        if not cfg.load_api_key():
            warns.append(
                "还没配置 API Key —— 打开页面后点右上角「设置」填进去。"
            )
    except Exception as exc:                       # noqa: BLE001
        warns.append(f"配置模块没加载起来：{exc}")

    # PDF 导入是可选功能，缺了不影响批改
    try:
        import pdf_import
        if not pdf_import.available():
            warns.append("没装 pypdfium2，「从 PDF 导入」按钮会不可用（手动粘贴不受影响）。")
    except Exception:                              # noqa: BLE001
        warns.append("PDF 模块没加载起来，只能手动粘贴文字。")

    page = SERVER_DIR / "static" / "index.html"
    if not page.exists():
        warns.append(f"找不到前端页面：{page}（打包时漏了 static 目录）")

    return warns


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════
def run() -> int:
    import threading
    import webbrowser

    import uvicorn

    import config as cfg
    from main import app

    port = free_port(cfg.PORT)
    url = f"http://127.0.0.1:{port}"

    print("=" * 58)
    print("  申论 AI 批改")
    print("=" * 58)

    for w in preflight():
        print(f"  ⚠️  {w}")
    if port != cfg.PORT:
        print(f"  ℹ️  {cfg.PORT} 被占用，改用 {port}")

    print(f"\n  地址：{url}")
    print("  浏览器会自动打开。关掉这个窗口就是退出程序。\n")
    print("=" * 58)

    # 服务起好再开浏览器 —— 否则可能打开一个"无法连接"的页面
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        print("\n已退出。")
        return 0
    except Exception:                              # noqa: BLE001
        print("\n" + "=" * 58)
        print("  启动失败")
        print("=" * 58)
        traceback.print_exc()
        return 1
    finally:
        # ⚠️ Windows 上双击 bat 时，脚本一结束窗口就关了。
        #    出错时不停住，用户什么都看不到。
        if sys.stdin is not None and sys.stdin.isatty():
            try:
                input("\n按回车键关闭…")
            except (EOFError, KeyboardInterrupt):
                pass


if __name__ == "__main__":
    sys.exit(main())
