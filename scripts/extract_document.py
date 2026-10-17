"""文件读取与文本提取。

支持：
- .txt / .md           直接读取
- .pdf                 优先提取文本层；无文本层时标记为扫描件（需视觉识别）
- 图片                 本地无 OCR 时标记需视觉识别（由 Agent 接管）

不对已有文本层的 PDF 执行 OCR。不默认调用任何付费 OCR 服务。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import InputError, PROJECT_ROOT, SkillError, read_text, setup_logger, write_json

SUPPORTED_TEXT = {".txt", ".md", ".markdown"}
SUPPORTED_PDF = {".pdf"}
SUPPORTED_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
MIN_PDF_TEXT_CHARS = 20  # 少于该字数判定为扫描 PDF


def extract_document(path: Path, logger=None) -> dict[str, Any]:
    """读取输入文件，返回提取结果字典。

    返回结构：
    {
      "source_file": str,
      "file_type": "text" | "pdf" | "image",
      "method": "direct-read" | "pdf-text-layer" | "needs-vision",
      "text": str,                 # 提取到的文本（needs-vision 时为空）
      "page_count": int | None,
      "issues": [str],             # 用户可理解的问题说明
      "next_step": "normalize" | "vision-recognition" | "manual-input"
    }
    """
    logger = logger or setup_logger()
    path = Path(path)
    if not path.exists():
        raise InputError(f"输入文件不存在：{path}")
    size = path.stat().st_size
    if size == 0:
        raise InputError(f"输入文件为空文件（0 字节）：{path.name}，请检查文件是否完整")

    suffix = path.suffix.lower()

    if suffix in SUPPORTED_TEXT:
        text = read_text(path)
        if not text.strip():
            raise InputError(f"文本文件没有任何有效内容：{path.name}")
        return {
            "source_file": path.name,
            "file_type": "text",
            "method": "direct-read",
            "text": text,
            "page_count": None,
            "issues": [],
            "next_step": "normalize",
        }

    if suffix in SUPPORTED_PDF:
        return _extract_pdf(path, logger)

    if suffix in SUPPORTED_IMAGE:
        return {
            "source_file": path.name,
            "file_type": "image",
            "method": "needs-vision",
            "text": "",
            "page_count": 1,
            "issues": [
                "图片格式错题需要视觉识别：当前环境未安装本地 OCR（tesseract），"
                "请由 Agent 多模态能力识别，或用户手动补充题目文本。"
            ],
            "next_step": "vision-recognition",
        }

    raise InputError(
        f"不支持的文件格式：{path.suffix or '(无扩展名)'}。"
        f"支持的格式：{sorted(SUPPORTED_TEXT | SUPPORTED_PDF | SUPPORTED_IMAGE)}"
    )


def _extract_pdf(path: Path, logger) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise SkillError("缺少依赖 pypdf，请先执行 pip install -r requirements.txt") from e

    try:
        reader = PdfReader(str(path))
    except Exception as e:
        raise InputError(f"PDF 文件损坏或无法解析：{path.name}（{type(e).__name__}: {e}）") from e

    page_count = len(reader.pages)
    if page_count == 0:
        raise InputError(f"PDF 文件不包含任何页面：{path.name}")

    texts: list[str] = []
    failed_pages: list[int] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception as e:  # 单页失败不影响整体
            logger.warning("第 %d 页文本提取失败：%s", i, e)
            failed_pages.append(i)
            t = ""
        texts.append(t)
    full_text = "\n\n".join(texts).strip()

    if len(full_text) < MIN_PDF_TEXT_CHARS:
        return {
            "source_file": path.name,
            "file_type": "pdf",
            "method": "needs-vision",
            "text": full_text,
            "page_count": page_count,
            "issues": [
                f"PDF 共 {page_count} 页，但提取到的文本仅 {len(full_text)} 字符，"
                "判定为扫描 PDF（无文本层）。请由 Agent 多模态能力识别页面内容，"
                "或用户手动补充题目文本。"
            ],
            "next_step": "vision-recognition",
        }

    issues = []
    if failed_pages:
        issues.append(
            f"以下页面文本提取失败（已跳过，需视觉识别或手动补充）：第 {failed_pages} 页"
        )
    return {
        "source_file": path.name,
        "file_type": "pdf",
        "method": "pdf-text-layer",
        "text": full_text,
        "page_count": page_count,
        "issues": issues,
        "next_step": "normalize",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="读取错题文件并提取文本")
    parser.add_argument("--input", required=True, help="输入文件路径（pdf/txt/md/图片）")
    parser.add_argument("--output", required=True, help="输出 JSON 路径")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        result = extract_document(Path(args.input), logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    write_json(Path(args.output), result)
    print(json.dumps({
        "ok": True,
        "method": result["method"],
        "next_step": result["next_step"],
        "text_length": len(result["text"]),
        "issues": result["issues"],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
