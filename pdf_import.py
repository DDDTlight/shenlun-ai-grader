"""PDF → 文字。

用 pypdfium2 —— 比 pypdf 快，中文支持也好。

⚠️ **这是可选依赖**：不用 PDF 输入的人不需要装它。
    没装时 available() 返回 False，调用方据此降级（只用粘贴的文字）。
"""
from __future__ import annotations

from pathlib import Path

__all__ = ["available", "extract_text", "extract_pages"]


def available() -> bool:
    """pypdfium2 装了没。没装就走纯粘贴输入。"""
    try:
        import pypdfium2  # noqa: F401
        return True
    except ImportError:
        return False


def extract_pages(source: str | Path, *, first: int = 1, last: int | None = None) -> list[str]:
    """逐页抽文字。返回每页的文本列表。

    :param first: 起始页（1-based）
    :param last:  结束页（含）。None = 到末尾
    """
    if not available():
        raise RuntimeError(
            "没装 pypdfium2，无法读 PDF。\n"
            "  要么装它：pip install pypdfium2\n"
            "  要么跳过 PDF，直接把文字粘贴进来"
        )

    import pypdfium2 as pdfium

    path = Path(source)
    if not path.exists():
        raise FileNotFoundError(f"找不到 PDF：{path}")

    doc = pdfium.PdfDocument(str(path))
    total = len(doc)
    start = max(1, first)
    end = total if last is None else min(last, total)

    return [doc[i - 1].get_textpage().get_text_range() for i in range(start, end + 1)]


def extract_text(source: str | Path, *, first: int = 1, last: int | None = None) -> str:
    """抽成一段文字。页与页之间空一行。"""
    return "\n\n".join(extract_pages(source, first=first, last=last))
