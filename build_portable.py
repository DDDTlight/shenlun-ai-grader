"""组装 Windows 便携版 —— 解压即用，不需要装 Python。

    python build_portable.py                 # 组装
    python build_portable.py --clean         # 先清掉上次的产物
    python build_portable.py --keep-dist     # 保留中间目录，方便检查

产物：
    dist/shenlun-<版本>-win-x64-portable.zip

────────────────────────────────────────────────────────────
为什么在 Linux 上也能做

PyInstaller **不能**交叉编译（Linux 上打不出 Windows exe）。
但便携版不需要「执行」Windows 的 Python —— 它只是：

    下载 python-embed zip → 解压 → 把 Windows 版 wheel 装进去 → 拼装 → 压缩

全程都是**文件操作**，不涉及运行目标平台的代码。
Linux / macOS / Windows 上跑这个脚本，产出的 zip 完全一样。

（Windows 的 wheel 靠 `pip download --platform win_amd64` 拿，
  纯 Python 包直接下，带二进制的包下对应的 win_amd64 版本。）
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"

# ── 目标环境 ──
PY_VER = "3.13.10"                 # python-embed 的版本（同一 minor 的 wheel 通用）
PKG_NAME = "shenlun"
APP_VERSION = "1.0.0"
BUILD_DIR = DIST / f"{PKG_NAME}-{APP_VERSION}-win-x64"
ZIP_PATH = DIST / f"{PKG_NAME}-{APP_VERSION}-win-x64-portable.zip"

EMBED_URL = (f"https://www.python.org/ftp/python/{PY_VER}"
             f"/python-{PY_VER}-embed-amd64.zip")

# 装进便携包的生产依赖。**不含 pytest** —— 朋友不需要跑测试。
# 版本范围与 requirements.txt 保持一致，避免两处漂移。
#
# ⚠️ **不要写 `uvicorn[standard]`。**
#    standard extra 里有 `uvloop; sys_platform != 'win32'` 这样的**平台标记**，
#    而 `pip install --platform win_amd64` 判断标记用的是**当前机器**的平台，
#    不是目标平台的 —— 在 Linux 上它会坚持要装 uvloop，
#    而 uvloop 根本没有 Windows wheel，于是整个依赖解析直接失败：
#
#        ERROR: ResolutionImpossible
#
#    基础版 uvicorn 用 asyncio + h11，功能完全一样，
#    只是并发性能低一些 —— 对「本机单人用」的小工具毫无影响。
DEPS = [
    "openai>=1.60.0",
    "httpx>=0.27.0",
    "pypdfium2>=4.30.0",
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",              # ← 不带 [standard]，原因见上
    "python-multipart>=0.0.9",
]

# 拷进 app/ 的东西。用白名单而不是黑名单 ——
# 黑名单会漏（将来加了新目录就悄悄打进去），白名单漏了会立刻报错。
COPY_FILES = ["launcher.py", "llm.py", "pdf_import.py", "pipeline.py",
              "prompts_comprehensive.py", "prompts_summary.py",
              "prompts_zhixing_fangan.py", "prompts_zhixing_format.py",
              "prompts_zhixing_pinglun.py", "prompts_zhixing_xuanchuan.py",
              "prompts_zhixing_zongjie.py", "routing.py", "rules_check.py",
              "scoring.py", "validators.py"]
COPY_DIRS = ["server"]

# 放在便携包**根目录**（和 启动.bat 同级）的说明文件。
#
# ⚠️ LICENSE 是**必须**带的，不是可选：
#    AGPL v3 第 4 条要求，分发本程序时必须给接收者一份许可证副本。
#    便携包就是分发行为，所以缺了它就是违反自己发布的协议。
COPY_ROOT = ["LICENSE", "版权声明.md"]


def log(msg: str) -> None:
    print(f"  {msg}", flush=True)


def step(n: int, total: int, title: str) -> None:
    print(f"\n[{n}/{total}] {title}")


# ══════════════════════════════════════════════════════════
def sh(cmd: list[str]) -> None:
    """跑外部命令，失败就抛 —— 静默失败是打包脚本最坑的地方。"""
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-2000:])
        print(r.stderr[-2000:], file=sys.stderr)
        raise SystemExit(f"命令失败（{r.returncode}）：{' '.join(cmd[:4])} …")
    if r.stdout.strip():
        for line in r.stdout.strip().splitlines()[-3:]:
            log(line)


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"下载 {url.rsplit('/', 1)[-1]}")
    with urllib.request.urlopen(url, timeout=180) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while chunk := r.read(1 << 16):
            f.write(chunk)
            got += len(chunk)
        if total:
            log(f"  {got / 1048576:.1f} / {total / 1048576:.1f} MB")


# ══════════════════════════════════════════════════════════
def fetch_embed(pydir: Path) -> None:
    """下载并解压 python-embed。"""
    cache = DIST / "_cache" / f"python-{PY_VER}-embed-amd64.zip"
    if not cache.exists():
        download(EMBED_URL, cache)
    else:
        log(f"用缓存 {cache.name}")

    pydir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(cache) as z:
        z.extractall(pydir)
    log(f"解压到 {pydir.relative_to(ROOT)}")


def patch_pth(pydir: Path) -> None:
    """改 `._pth`，否则 embed Python 找不到 site-packages。

    embed 包默认的 `python3XX._pth` 长这样：

        python313.zip
        .
        #import site          ← 注释掉了！

    所以它是「隔离模式」：不认 PYTHONPATH，也不扫 site-packages。
    必须显式写上路径 + 放开 import site。
    """
    pths = list(pydir.glob("python*._pth"))
    if not pths:
        raise SystemExit(f"没找到 ._pth 文件（{pydir}），embed 包结构变了？")

    content = "\n".join([
        f"python{PY_VER.split('.')[0]}{PY_VER.split('.')[1]}.zip",
        ".",
        r"Lib\site-packages",
        "import site",
    ]) + "\n"
    pths[0].write_text(content, encoding="utf-8")
    log(f"改了 {pths[0].name}（加 site-packages + 放开 import site）")


def install_deps(pydir: Path) -> None:
    """把 **Windows 版** 依赖装进 python/Lib/site-packages。"""
    target = pydir / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "install",
        "--target", str(target),
        "--platform", "win_amd64",
        "--python-version", PY_VER.rsplit(".", 1)[0],
        "--only-binary=:all:",          # 必须是预编译包，不能混进源码要现场编译
        "--no-compile",                 # .pyc 让目标机器自己生成，省体积也避免版本不合
        "--upgrade",
        "--quiet",
        "--disable-pip-version-check",
        *DEPS,
    ]
    log("装 Windows 依赖（可能要一两分钟）…")
    sh(cmd)

    n = sum(1 for _ in target.rglob("*") if _.is_file())
    size = sum(f.stat().st_size for f in target.rglob("*") if f.is_file())
    log(f"{n} 个文件，{size / 1048576:.1f} MB")

    # 抽查二进制是不是 Windows 的 —— 装错平台是这里最容易出的错，
    # 而它要到朋友机器上才会暴露。
    dlls = list(target.rglob("*.dll")) + list(target.rglob("*.pyd"))
    if not dlls:
        raise SystemExit("site-packages 里没有 .dll/.pyd —— 依赖可能是源码包，装错了")
    win_pyd = [d for d in dlls if "win_amd64" in d.name]
    log(f"抽查：{len(dlls)} 个二进制，其中 {len(win_pyd)} 个带 win_amd64 标记")
    if not win_pyd:
        raise SystemExit("二进制里没有 win_amd64 —— 下成了 Linux 版")


def copy_app(appdir: Path) -> None:
    """拷代码。用白名单，漏了什么会立刻看出来。"""
    appdir.mkdir(parents=True, exist_ok=True)
    missing = []

    for name in COPY_FILES:
        src = ROOT / name
        if not src.exists():
            missing.append(name)
            continue
        shutil.copy2(src, appdir / name)

    for name in COPY_DIRS:
        src = ROOT / name
        if not src.is_dir():
            missing.append(name + "/")
            continue
        dst = appdir / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst,
                        ignore=shutil.ignore_patterns(
                            "__pycache__", "*.pyc", "tests", ".pytest_cache"))

    if missing:
        raise SystemExit(f"这些文件不存在，检查 COPY_FILES：{missing}")

    # 静态文件必须真的在里面 —— 漏了前端就是白屏
    page = appdir / "server" / "static" / "index.html"
    if not page.exists():
        raise SystemExit("server/static/index.html 没被拷进去")
    log(f"代码已拷入（含前端页面 {page.stat().st_size / 1024:.0f} KB）")

    # 根目录的说明文件（LICENSE 是协议要求，不能漏）
    root_missing = []
    for name in COPY_ROOT:
        src = ROOT / name
        if not src.exists():
            root_missing.append(name)
            continue
        shutil.copy2(src, appdir.parent / name)
    if root_missing:
        raise SystemExit(
            f"根目录文件缺失：{root_missing}\n"
            f"  ⚠️ LICENSE 缺失会导致分发时违反 AGPL v3 第 4 条"
        )
    log(f"协议文件已放到根目录（{'、'.join(COPY_ROOT)}）")


def write_bat(build: Path) -> None:
    """生成启动.bat。

    ⚠️ **内容全用 ASCII。**
    Windows cmd 的默认代码页是 GBK，UTF-8 的中文批处理会乱码，
    而且乱码出现在 `if` / `goto` 上时可能直接让脚本行为异常。
    中文提示统一由 launcher.py 负责输出。
    """
    bat = build / "启动.bat"
    # CRLF 换行 —— cmd.exe 对 LF 时好时坏
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        "set PYTHONIOENCODING=utf-8",
        'cd /d "%~dp0"',
        "title Shenlun AI Grader",
        "",
        'if not exist "python\\python.exe" (',
        "    echo.",
        "    echo [ERROR] python\\python.exe not found.",
        "    echo         The zip may not be fully extracted.",
        "    echo         Please extract the WHOLE zip, then run this again.",
        "    echo.",
        "    pause",
        "    exit /b 1",
        ")",
        "",
        'if not exist "app\\launcher.py" (',
        "    echo.",
        "    echo [ERROR] app\\launcher.py not found.",
        "    echo         Please extract the WHOLE zip, then run this again.",
        "    echo.",
        "    pause",
        "    exit /b 1",
        ")",
        "",
        '"python\\python.exe" "app\\launcher.py"',
    ]
    bat.write_bytes(("\r\n".join(lines) + "\r\n").encode("ascii"))
    log("生成 启动.bat")


def write_readme(build: Path) -> None:
    text = f"""申论 AI 批改 · 便携版 v{APP_VERSION}
================================================

【怎么用】

  1. 把整个文件夹解压出来（别只在压缩包里双击）
  2. 双击「启动.bat」
  3. 会弹出一个黑色窗口 —— 这是正常的，**别关它**
  4. 浏览器会自动打开。如果没开，手动访问 http://127.0.0.1:8000
  5. 用完关掉黑窗口就是退出

【第一次用要配 API Key】

  打开页面 → 右上角「设置」→ 填入 sk- 开头的 DeepSeek API Key

  Key 存在这里：C:\\Users\\你的用户名\\.shenlun\\.env
  程序不会把它上传到任何地方，只用于调用 DeepSeek 官方接口。

【需要联网】

  批改时题目和答卷会发送到 DeepSeek 的服务器。

【支持哪些题型】

  概括归纳题 / 综合分析题 / 提出对策题 / 贯彻执行题

  不支持文章写作（大作文）—— 议论文写法多样，无法逐条量化，
  强行批改只会给出一个没有意义的分数。

【常见问题】

  · 黑窗口一闪就没了
      说明启动出错。再双击一次，窗口会停在错误信息上，把内容截图发我。

  · Windows 弹防火墙提示
      点「允许」。程序只监听本机 127.0.0.1，不对局域网/外网开放。

  · 浏览器没自动打开
      手动访问 http://127.0.0.1:8000

  · 「从 PDF 导入」报错
      可能压缩包不完整，重新解压一次试试。手动粘贴文字不受影响。

  · 想彻底卸载
      删掉这个文件夹，再删掉 C:\\Users\\你的用户名\\.shenlun 即可。
      不写注册表、不留后台服务。

================================================
许可

  本项目采用 AGPL v3 协议，详见同目录下的 LICENSE 文件。
  简单说：

    · 自己用、改了自己用 —— 随便
    · 转发给别人 —— 请把整个文件夹一起给，别只给 exe
    · 不能删掉作者署名，不能说成是自己写的
    · 不能闭源商用

  人话版见「版权声明.md」。

================================================
本程序仅供个人学习使用。
批改结果仅供参考，不构成任何评分承诺。
"""
    (build / "使用说明.txt").write_text(text, encoding="utf-8")
    log("生成 使用说明.txt")


def verify_dynamic(appdir: Path) -> None:
    """验证 **importlib 动态加载的规则库** 都在包里。

    ⚠️ 这是整个打包最容易漏、也最难查的一处。

    `routing.py` 用字符串模块名加载规则库：

        RULE_LIBS = {"综合分析题": "prompts_comprehensive", ...}
        importlib.import_module(name)

    **静态分析看不见这些名字** —— 就算文件没拷进去，打包也不会报错，
    直到用户在界面上选到那个题型才炸。PyInstaller 那边同理，
    要靠 hiddenimports 手工声明。

    所以这里不靠「文件清单对不对」，而是**在子进程里真的加载一遍**：
    枚举 routing 声明的全部规则库，逐个 import 并检查规则非空。

    用子进程是为了不把 app/ 的东西 import 进本进程 ——
    否则 `sys.modules` 会被污染，重复构建时行为不一致。
    """
    code = r'''
import sys
sys.path.insert(0, ".")
try:
    import routing
except Exception as exc:
    print(f"FAIL|routing 导不进来: {type(exc).__name__}: {exc}")
    raise SystemExit(1)

bad = []
for t in routing.available_types():
    try:
        if t == routing.TYPE_ZHIXING:
            for st in [None, *routing.ZHIXING_SUBTYPE_LIBS]:
                if not routing.load_rules(t, st)["judge_rules"].strip():
                    bad.append(f"{t}/{st} 规则为空")
        elif not routing.load_rules(t)["judge_rules"].strip():
            bad.append(f"{t} 规则为空")
    except Exception as exc:
        bad.append(f"{t}: {type(exc).__name__}: {exc}")

n = len(routing.ZHIXING_SUBTYPE_LIBS) + 1
print(f"OK|{len(routing.available_types())} 个题型 / 公文 {n} 个子类"
      + ("|" + "; ".join(bad) if bad else ""))
raise SystemExit(1 if bad else 0)
'''
    r = subprocess.run([sys.executable, "-c", code], cwd=appdir,
                       capture_output=True, text=True)
    out = (r.stdout or "").strip()

    if out.startswith("FAIL|"):
        raise SystemExit(f"动态规则库验证失败：{out[5:]}")
    if not out.startswith("OK|"):
        print(r.stderr[-1500:], file=sys.stderr)
        raise SystemExit("动态规则库验证没跑起来")

    parts = out.split("|")
    log(f"动态规则库：{parts[1]}")
    if len(parts) > 2 and parts[2]:
        raise SystemExit(f"动态规则库有问题：{parts[2]}")


def clean_pycache(build: Path) -> int:
    """删掉包里所有 __pycache__。

    ⚠️ 必须做，而且必须在**验证之后**做。

    `verify_dynamic()` 会起一个子进程 `import routing` —— 那个 import
    会在 `app/` 里生成 `__pycache__/*.pyc`。如果不清，它们就被打进包了：

      · 是构建机器（Linux）上编译的，对 Windows 毫无用处
      · 白占体积
      · 看起来不干净

    放在压缩之前统一扫一遍，是因为将来任何一步只要碰了 import，
    都可能再生成一批。
    """
    n = 0
    for d in list(build.rglob("__pycache__")):
        if d.is_dir():
            n += sum(1 for _ in d.iterdir())
            shutil.rmtree(d, ignore_errors=True)
    # 顺手清掉零散的 .pyc
    for f in list(build.rglob("*.pyc")):
        f.unlink(missing_ok=True)
        n += 1
    return n


def make_zip(build: Path) -> None:
    ZIP_PATH.parent.mkdir(parents=True, exist_ok=True)
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    log(f"压缩（{build.name}）…")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for f in sorted(build.rglob("*")):
            if f.is_file():
                z.write(f, f.relative_to(build.parent))
    log(f"→ {ZIP_PATH.relative_to(ROOT)}  "
        f"（{ZIP_PATH.stat().st_size / 1048576:.1f} MB）")


# ══════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="组装 Windows 便携版")
    ap.add_argument("--clean", action="store_true", help="先清掉上次的产物")
    ap.add_argument("--keep-dist", action="store_true", help="保留中间目录")
    args = ap.parse_args()

    if args.clean:
        for p in (BUILD_DIR, ZIP_PATH):
            if p.is_dir():
                shutil.rmtree(p)
            elif p.exists():
                p.unlink()
        print("已清理上次产物")

    print("=" * 62)
    print(f"  组装 Windows 便携版 v{APP_VERSION}")
    print(f"  Python {PY_VER} (embed) + {len(DEPS)} 个依赖")
    print("=" * 62)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    pydir = BUILD_DIR / "python"
    appdir = BUILD_DIR / "app"

    step(1, 7, "下载并解压 python-embed")
    fetch_embed(pydir)
    patch_pth(pydir)

    step(2, 7, "安装 Windows 依赖")
    install_deps(pydir)

    step(3, 7, "拷贝程序代码")
    copy_app(appdir)

    step(4, 7, "验证动态加载的规则库")
    verify_dynamic(appdir)

    step(5, 7, "生成启动脚本和说明")
    write_bat(BUILD_DIR)
    write_readme(BUILD_DIR)

    step(6, 7, "清理构建残留")
    n = clean_pycache(BUILD_DIR)
    log(f"清掉 {n} 个字节码缓存文件" + ("（没有需要清的）" if n == 0 else ""))

    step(7, 7, "打包 zip")
    make_zip(BUILD_DIR)

    # ── 自检 ──
    print("\n" + "=" * 62)
    print("  产物自检")
    print("=" * 62)
    must = ["启动.bat", "使用说明.txt", "LICENSE", "版权声明.md",
            "python/python.exe", "app/launcher.py",
            "app/server/static/index.html", "app/routing.py"]
    for rel in must:
        p = BUILD_DIR.joinpath(*rel.split("/"))
        print(f"  {'✓' if p.exists() else '✗'} {rel}")

    print(f"\n  解压后 {BUILD_DIR.name}/ 约 "
          f"{sum(f.stat().st_size for f in BUILD_DIR.rglob('*') if f.is_file()) / 1048576:.0f} MB")
    print(f"  压缩包 {ZIP_PATH.name}  {ZIP_PATH.stat().st_size / 1048576:.1f} MB")
    print(f"\n  ⚠️ 这是给 Windows 用的。在 Windows 上解压后双击「启动.bat」验证。")

    if not args.keep_dist:
        print(f"\n  （中间目录保留在 {BUILD_DIR.relative_to(ROOT)}，"
              f"想清掉加 --clean 重跑）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
