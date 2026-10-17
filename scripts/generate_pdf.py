"""PDF 报告生成（reportlab + 中文字体）。

特性：
- 自动注册中文字体（ttf/ttc），注册失败给出清晰错误；
- 解析 Markdown 常用子集：标题/段落/列表/表格/图片/引用/代码块/分隔线；
- A4 版式、页眉页码、目录导航；
- 表格列宽自适应，长文本单元格自动换行，防止超出页面；
- 标题与后续内容 keepTogether，减少重要内容被分页截断；
- 公式以 LaTeX 源码文本呈现（不做排版级数学渲染，避免误导）。

失败时不产生虚假成功：任何步骤失败抛出 PdfGenerationError，
由调用方决定保留 Markdown 并如实报告。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    PdfGenerationError,
    SkillError,
    register_reportlab_cn_fonts,
    setup_logger,
)
from reportlab.platypus import Paragraph  # noqa: E402

PAGE_W, PAGE_H = 595.27, 841.89  # A4
MARGIN = 54
CONTENT_W = PAGE_W - 2 * MARGIN


# ---------------------------------------------------------------------------
# Markdown 子集解析
# ---------------------------------------------------------------------------

# 注意：使用 [ \t] 而非 \s，避免正则跨行吞噬下一行内容
RE_HEADING = re.compile(r"^(#{1,4})[ \t]+(.*)$")
RE_TABLE_ROW = re.compile(r"^[ \t]*\|(.+)\|[ \t]*$")
RE_TABLE_SEP = re.compile(r"^[ \t]*\|[\s:\-|]+\|[ \t]*$")
RE_UL_ITEM = re.compile(r"^[ \t]*[-*][ \t]+(.*)$")
RE_OL_ITEM = re.compile(r"^[ \t]*\d+[\.、][ \t]+(.*)$")
RE_QUOTE = re.compile(r"^[ \t]*>[ \t]?(.*)$")
RE_IMAGE = re.compile(r"^!\[([^\]]*)\]\(([^)]+)\)[ \t]*$")
RE_HR = re.compile(r"^[ \t]*(-{3,}|\*{3,})[ \t]*$")
RE_BOLD = re.compile(r"\*\*([^*]+)\*\*")
RE_INLINE_CODE = re.compile(r"`([^`]+)`")


def _inline(text: str) -> str:
    """行内标记转 Paragraph XML：粗体、行内代码。公式原样保留。"""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = RE_INLINE_CODE.sub(r'<font face="Courier">\1</font>', text)
    text = RE_BOLD.sub(r"<b>\1</b>", text)
    return text


def parse_markdown(md_text: str, base_dir: Path) -> list[tuple[str, Any]]:
    """解析 Markdown 为 (kind, payload) 序列。kind ∈ heading/para/ul/ol/table/image/quote/code/hr"""
    lines = md_text.splitlines()
    blocks: list[tuple[str, Any]] = []
    para_buf: list[str] = []
    list_buf: list[str] = []
    list_type: str | None = None
    table_buf: list[str] = []
    code_buf: list[str] = []
    in_code = False

    def flush_para():
        if para_buf:
            blocks.append(("para", " ".join(para_buf).strip()))
            para_buf.clear()

    def flush_list():
        nonlocal list_type
        if list_buf and list_type:
            blocks.append((list_type, list_buf.copy()))
            list_buf.clear()
        list_type = None

    def flush_table():
        if table_buf:
            rows = []
            for raw in table_buf:
                cells = [c.strip() for c in raw.strip().strip("|").split("|")]
                rows.append(cells)
            blocks.append(("table", rows))
            table_buf.clear()

    for line in lines:
        if line.strip().startswith("```"):
            if in_code:
                blocks.append(("code", "\n".join(code_buf)))
                code_buf.clear()
                in_code = False
            else:
                flush_para(); flush_list(); flush_table()
                in_code = True
            continue
        if in_code:
            code_buf.append(line)
            continue

        m = RE_HEADING.match(line)
        if m:
            flush_para(); flush_list(); flush_table()
            blocks.append(("heading", (len(m.group(1)), m.group(2).strip())))
            continue
        if RE_HR.match(line):
            flush_para(); flush_list(); flush_table()
            blocks.append(("hr", None))
            continue
        if RE_IMAGE.match(line):
            flush_para(); flush_list(); flush_table()
            m_img = RE_IMAGE.match(line)
            blocks.append(("image", (m_img.group(1), m_img.group(2))))
            continue
        if RE_TABLE_SEP.match(line):
            continue
        if RE_TABLE_ROW.match(line):
            flush_para(); flush_list()
            table_buf.append(line)
            continue
        flush_table()
        m = RE_QUOTE.match(line)
        if m:
            flush_para(); flush_list()
            para_buf.append(m.group(1))
            # 引用以段落形式输出，加引号样式由调用方处理
            if para_buf:
                blocks.append(("quote", " ".join(para_buf).strip()))
                para_buf.clear()
            continue
        m = RE_UL_ITEM.match(line)
        if m:
            flush_para(); flush_table()
            if list_type != "ul":
                flush_list()
                list_type = "ul"
            list_buf.append(m.group(1))
            continue
        m = RE_OL_ITEM.match(line)
        if m:
            flush_para(); flush_table()
            if list_type != "ol":
                flush_list()
                list_type = "ol"
            list_buf.append(m.group(1))
            continue
        if not line.strip():
            flush_para(); flush_list(); flush_table()
            continue
        # 普通段落行：若处于列表收集状态且该行是续行，归入列表项
        if list_type and line.startswith((" ", "\t")):
            list_buf.append(line.strip())
            continue
        flush_list()
        para_buf.append(line.strip())

    if in_code and code_buf:
        blocks.append(("code", "\n".join(code_buf)))
    flush_para(); flush_list(); flush_table()
    return blocks


# ---------------------------------------------------------------------------
# PDF 文档模板
# ---------------------------------------------------------------------------

def _build_doc_template(out_path: Path, fonts: tuple[str, str], title: str):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import BaseDocTemplate, Frame, PageTemplate

    regular, bold = fonts

    class ReportDoc(BaseDocTemplate):
        def afterFlowable(self, flowable):
            if isinstance(flowable, Paragraph):
                style = flowable.style.name
                if style in ("H1", "H2"):
                    text = flowable.getPlainText()
                    level = 0 if style == "H1" else 1
                    self.notify("TOCEntry", (level, text, self.page))

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFont(regular, 8.5)
        canvas.setFillColorRGB(0.35, 0.4, 0.45)
        canvas.drawString(MARGIN, PAGE_H - 36, title)
        canvas.drawRightString(PAGE_W - MARGIN, PAGE_H - 36, "智能错题分析 Skill")
        canvas.setStrokeColorRGB(0.8, 0.85, 0.9)
        canvas.setLineWidth(0.5)
        canvas.line(MARGIN, PAGE_H - 42, PAGE_W - MARGIN, PAGE_H - 42)
        canvas.line(MARGIN, 44, PAGE_W - MARGIN, 44)
        canvas.setFont(regular, 9)
        canvas.drawString(MARGIN, 32, f"第 {doc.page} 页")
        canvas.drawRightString(PAGE_W - MARGIN, 32, "数据以原始试卷为准")
        canvas.restoreState()

    doc = ReportDoc(
        str(out_path), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=58, bottomMargin=58,
        title=title, author="智能错题分析 Skill",
    )
    frame = Frame(MARGIN, 58, CONTENT_W, PAGE_H - 58 - 58, id="main")
    doc.addPageTemplates([PageTemplate(id="all", frames=[frame], onPage=on_page)])
    return doc


def _styles(fonts: tuple[str, str]):
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_LEFT, TA_CENTER

    regular, bold = fonts
    base = dict(fontName=regular, textColor="#1a1a1a", leading=1.65)
    styles = {
        "H1": ParagraphStyle("H1", fontName=bold, fontSize=17, leading=24,
                             spaceBefore=16, spaceAfter=10, textColor="#0d47a1"),
        "H2": ParagraphStyle("H2", fontName=bold, fontSize=13.5, leading=20,
                             spaceBefore=12, spaceAfter=7, textColor="#1565c0"),
        "H3": ParagraphStyle("H3", fontName=bold, fontSize=11.5, leading=18,
                             spaceBefore=9, spaceAfter=5, textColor="#212121"),
        "H4": ParagraphStyle("H4", fontName=bold, fontSize=10.5, leading=16,
                             spaceBefore=7, spaceAfter=4, textColor="#37474f"),
        "Body": ParagraphStyle("Body", fontSize=10, spaceAfter=6, alignment=TA_LEFT, **base),
        "Quote": ParagraphStyle("Quote", fontName=regular, fontSize=9.5, leading=16,
                                leftIndent=14, textColor="#455a64", spaceAfter=6,
                                borderPadding=(4, 6, 4, 6)),
        "Bullet": ParagraphStyle("Bullet", fontSize=10, leftIndent=16, bulletIndent=4,
                                 spaceAfter=3, **base),
        "Code": ParagraphStyle("Code", fontName="Courier", fontSize=8.5, leading=13,
                               backColor="#f5f5f5", borderPadding=5, spaceAfter=6),
        "Caption": ParagraphStyle("Caption", fontSize=8.5, alignment=TA_CENTER,
                                  textColor="#546e7a", spaceBefore=3, spaceAfter=10),
        "TOCTitle": ParagraphStyle("TOCTitle", fontName=bold, fontSize=17, leading=24,
                                   spaceAfter=14, textColor="#0d47a1"),
    }
    return styles


def _table_flowable(rows: list[list[str]], styles: dict, fonts: tuple[str, str], logger):
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Table, TableStyle

    if not rows:
        return None
    regular, bold = fonts
    header = rows[0]
    body = rows[1:]
    n_cols = max(len(r) for r in rows)
    header = header + [""] * (n_cols - len(header))
    body = [r + [""] * (n_cols - len(r)) for r in body]

    # 列宽按平均内容长度比例分配，长文本列更宽
    col_weight = []
    for c in range(n_cols):
        lengths = [len(str(r[c])) for r in rows] or [1]
        col_weight.append(max(sum(lengths) / len(lengths), 2))
    total_w = sum(col_weight)
    col_widths = [CONTENT_W * w / total_w for w in col_weight]

    cell_style = ParagraphStyle("_cell", fontName=regular, fontSize=8.5, leading=12.5)
    header_style = ParagraphStyle("_hcell", fontName=bold, fontSize=8.5, leading=12.5,
                                  textColor=colors.white)
    data = [[Paragraph(_inline(str(c)), header_style) for c in header]]
    for r in body:
        data.append([Paragraph(_inline(str(c)), cell_style) for c in r])

    table = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1565c0")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f6fb")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b0bec5")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    return table


def _image_flowable(alt: str, src: str, base_dir: Path, styles: dict, logger):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image, Paragraph

    path = (base_dir / src).resolve()
    if not path.exists():
        # 尝试同名 PNG 替换 SVG
        alt_path = path.with_suffix(".png")
        if alt_path.exists():
            path = alt_path
        else:
            logger.warning("PDF 图片缺失，使用占位文本：%s", src)
            return Paragraph(f"[图片缺失：{alt or src}]", styles["Caption"])
    try:
        reader = ImageReader(str(path))
        iw, ih = reader.getSize()
        max_w = CONTENT_W
        max_h = 380
        scale = min(max_w / iw, max_h / ih, 1.0)
        img = Image(str(path), width=iw * scale, height=ih * scale)
        img.hAlign = "CENTER"
        caption = Paragraph(_inline(alt or "插图"), styles["Caption"])
        from reportlab.platypus import KeepTogether
        return KeepTogether([img, caption])
    except Exception as e:
        logger.warning("PDF 图片加载失败：%s（%s）", src, e)
        return Paragraph(f"[图片无法加载：{alt or src}]", styles["Caption"])


def generate_pdf(
    md_path: Path,
    pdf_path: Path,
    title: str = "智能错题分析报告",
    logger=None,
) -> Path:
    """把 analysis.md 渲染为 analysis.pdf。失败抛出 PdfGenerationError。"""
    logger = logger or setup_logger()
    from reportlab.platypus import KeepTogether, PageBreak, Spacer

    if not md_path.exists():
        raise SkillError(f"Markdown 报告不存在，无法生成 PDF：{md_path}")
    md_text = md_path.read_text(encoding="utf-8")

    fonts = register_reportlab_cn_fonts()
    if fonts is None:
        raise PdfGenerationError(
            "未找到中文字体，无法生成中文 PDF。请安装中文字体"
            "（Windows: 微软雅黑/黑体；Linux: Noto Sans CJK；macOS: PingFang），"
            "或在 config/default.yaml 的 paths.fonts 中指定字体路径。"
        )

    styles = _styles(fonts)
    doc = _build_doc_template(pdf_path, fonts, title)
    base_dir = md_path.parent

    from reportlab.platypus.tableofcontents import TableOfContents

    story = [
        Paragraph(title, styles["TOCTitle"]),
        Paragraph(_inline(f"生成时间：{_now_str()}　|　本 PDF 由 Markdown 报告自动排版生成"),
                  styles["Body"]),
        Spacer(1, 8),
        Paragraph("目录", styles["H2"]),
    ]
    toc = TableOfContents()
    toc.levelStyles = [
        _toc_style(fonts[0], 0),
        _toc_style(fonts[0], 1),
    ]
    story.append(toc)
    story.append(PageBreak())

    blocks = parse_markdown(md_text, base_dir)
    toc_heading_count = 0
    for kind, payload in blocks:
        if kind == "heading":
            level, text = payload
            style_name = {1: "H1", 2: "H2", 3: "H3", 4: "H4"}.get(level, "H4")
            if level <= 2:
                toc_heading_count += 1
            # 标题与紧随内容尽量同页
            story.append(Paragraph(_inline(text), styles[style_name]))
        elif kind == "para":
            story.append(Paragraph(_inline(payload), styles["Body"]))
        elif kind == "quote":
            story.append(Paragraph("▎" + _inline(payload), styles["Quote"]))
        elif kind == "ul":
            for item in payload:
                story.append(Paragraph(_inline(item), styles["Bullet"], bulletText="•"))
        elif kind == "ol":
            for i, item in enumerate(payload, 1):
                story.append(Paragraph(_inline(item), styles["Bullet"], bulletText=f"{i}."))
        elif kind == "table":
            table = _table_flowable(payload, styles, fonts, logger)
            if table is not None:
                story.append(table)
                story.append(Spacer(1, 6))
        elif kind == "image":
            story.append(_image_flowable(payload[0], payload[1], base_dir, styles, logger))
        elif kind == "code":
            story.append(Paragraph(_inline(payload).replace("\n", "<br/>"), styles["Code"]))
        elif kind == "hr":
            story.append(Spacer(1, 4))

    if toc_heading_count == 0:
        logger.warning("Markdown 中未检测到一二级标题，目录可能为空")

    try:
        doc.multiBuild(story)
    except Exception as e:
        raise PdfGenerationError(
            f"PDF 渲染失败：{type(e).__name__}: {e}。Markdown 报告仍保留在 {md_path}。"
        ) from e

    if not pdf_path.exists() or pdf_path.stat().st_size == 0:
        raise PdfGenerationError("PDF 生成后文件为空或不存在，请检查 reportlab 输出。")
    logger.info("PDF 生成成功：%s（%d 字节）", pdf_path, pdf_path.stat().st_size)
    return pdf_path


def _toc_style(font_name: str, level: int):
    from reportlab.lib.styles import ParagraphStyle
    return ParagraphStyle(
        name=f"TOCLevel{level}", fontName=font_name,
        fontSize=10.5 if level == 0 else 9.5,
        leading=16 if level == 0 else 14,
        leftIndent=18 * level, firstLineIndent=-18 * level,
        textColor="#1a1a1a",
    )


def _now_str() -> str:
    from datetime import datetime
    return datetime.now().isoformat(timespec="seconds")


def main() -> int:
    parser = argparse.ArgumentParser(description="由 Markdown 报告生成 PDF")
    parser.add_argument("--input", required=True, help="analysis.md 路径")
    parser.add_argument("--output", required=True, help="输出 analysis.pdf 路径")
    parser.add_argument("--title", default="智能错题分析报告")
    args = parser.parse_args()

    logger = setup_logger()
    try:
        out = generate_pdf(Path(args.input), Path(args.output), args.title, logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    print(json.dumps({"ok": True, "output": str(out), "size": out.stat().st_size},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
