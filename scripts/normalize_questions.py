"""题目结构化：把提取的原文切分为统一 JSON 题目结构。

支持两种输入形态：
1. Markdown 结构：`## Q001` 分节 + 「题干/标准答案/学生答案/学生解题过程」字段标签。
2. 纯文本：按题号（1. / 一、/ Q1 等）切分，按「答案：」等标签识别字段。

原则：
- 缺失信息一律 null 或 []，禁止补全原题缺失条件。
- 保留原始题干文本与 LaTeX 公式，方便核验。
- 字段值中的「（无）/无/未提供」归一化为 null 并在 recognition_issues 中说明。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import PROJECT_ROOT, SkillError, read_text, setup_logger, write_json

# 字段标签归一化映射（用户可能使用不同叫法）
FIELD_PATTERNS = {
    "question_text": r"(?:题干|题目|题目内容)",
    "standard_answer": r"(?:标准答案|参考答案|正确答案|答案)",
    "student_answer": r"(?:学生答案|我的答案|学生作答|作答)",
    "student_solution": r"(?:学生解题过程|解题过程|学生过程|作答过程|过程)",
    "student_note": r"(?:学生自评|自评|备注|备注信息)",
}
EMPTY_MARKERS = {"（无）", "(无)", "无", "未提供", "未作答", "空", "无作答", "N/A", "n/a", "-", "—"}

# 注意：全部使用 [ \t] 而非 \s，避免正则跨行吃掉下一行内容（题干行丢失 bug）
RE_MD_QUESTION = re.compile(
    r"^#{1,4}[ \t]*(?:第?[ \t]*([0-9一二三四五六七八九十]+)[ \t]*题|(Q[ \t]*\d+))[ \t]*[:：]?[ \t]*(.*)$",
    re.M,
)
RE_TEXT_QUESTION = re.compile(
    r"^[ \t]*(?:(Q[ \t]*\d+)|(?:第?[ \t]*(\d{1,3})[ \t]*[\.、．]))[ \t]*[\.、．:：]?[ \t]*",
    re.M,
)
RE_LATEX_DISPLAY = re.compile(r"\$\$(.+?)\$\$", re.S)
RE_LATEX_INLINE = re.compile(r"(?<!\$)\$(?!\$)(.+?)(?<!\$)\$(?!\$)", re.S)
RE_LATEX_BRACKET = re.compile(r"\\\[(.+?)\\\]|\\\((.+?)\\\)", re.S)
RE_OPTION_LINE = re.compile(r"^[ \t]*([A-H])[ \t]*[\.、．\)][ \t]*(.+)$", re.M)


def _normalize_field_value(raw: str | None) -> tuple[str | None, str | None]:
    """归一化字段值：空标记 -> (None, 原因)。"""
    if raw is None:
        return None, None
    text = raw.strip()
    if not text:
        return None, None
    if text in EMPTY_MARKERS:
        return None, f"学生标记为「{text}」，按未提供处理"
    return text, None


def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """按 Markdown 题目标题或文本题号切分章节，返回 [(题号或None, 章节文本)]。"""
    matches = list(RE_MD_QUESTION.finditer(text))
    if not matches:
        matches = [
            m for m in RE_TEXT_QUESTION.finditer(text)
        ]
    if not matches:
        return [(None, text)]
    sections: list[tuple[str | None, str]] = []
    # 第一个题目标题之前的内容（通常是标题/说明），尝试提取学科信息
    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append((None, preamble))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end():end].strip()
        number = (m.group(1) or m.group(2) or "").strip() or None
        sections.append((number, body))
    return sections


def _parse_fields(body: str) -> dict[str, str | None]:
    """在一个题目章节内按字段标签切分。"""
    lines = body.splitlines()
    # 找到所有字段标签位置
    tag_positions: list[tuple[int, str]] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        for field, pattern in FIELD_PATTERNS.items():
            if re.match(rf"^{pattern}\s*[：:]", stripped):
                tag_positions.append((idx, field))
                break
    if not tag_positions:
        # 无字段标签：整段作为题干
        return {"question_text": body.strip() or None}

    # 标签前的零散内容并入题干
    first_idx = tag_positions[0][0]
    prefix = "\n".join(lines[:first_idx]).strip()
    fields: dict[str, str | None] = {}
    if prefix:
        fields["question_text"] = prefix
    for pos, (idx, field) in enumerate(tag_positions):
        line = lines[idx]
        m = re.match(rf"^{FIELD_PATTERNS[field]}\s*[：:]\s*(.*)$", line.strip())
        inline_value = m.group(1).strip() if m else ""
        end_idx = tag_positions[pos + 1][0] if pos + 1 < len(tag_positions) else len(lines)
        rest = "\n".join(lines[idx + 1:end_idx]).strip()
        value = (inline_value + "\n" + rest).strip() if rest else inline_value
        # 同一字段多次出现时合并（少见）
        if fields.get(field):
            fields[field] = f"{fields[field]}\n{value}".strip()
        else:
            fields[field] = value or None
    return fields


def _extract_latex(*texts: str | None) -> list[str]:
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for pattern in (RE_LATEX_DISPLAY, RE_LATEX_BRACKET, RE_LATEX_INLINE):
            for m in pattern.finditer(text):
                expr = (m.group(1) or m.group(2) or "").strip()
                if expr and expr not in found:
                    found.append(expr)
    return found


def _extract_options(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    options: list[dict[str, str]] = []
    for m in RE_OPTION_LINE.finditer(text):
        options.append({"label": m.group(1), "content": m.group(2).strip()})
    return options


def _detect_subject(preamble: str | None) -> str | None:
    if not preamble:
        return None
    m = re.search(r"(数学|物理|化学|生物|英语|语文|历史|地理|政治)", preamble)
    return m.group(1) if m else None


def normalize_questions(
    text: str,
    source_file: str,
    subject_hint: str | None = None,
    logger=None,
) -> dict[str, Any]:
    """把原文结构化为题目 JSON。"""
    logger = logger or setup_logger()
    sections = _split_sections(text)
    preamble = sections[0][1] if sections and sections[0][0] is None else None
    subject = subject_hint or _detect_subject(preamble)

    questions: list[dict[str, Any]] = []
    number_seen = 0
    for number, body in sections:
        if number is None:
            continue  # 前言/说明区
        if not body.strip():
            continue
        number_seen += 1
        qid = re.sub(r"\s+", "", number).upper()
        if not qid.startswith("Q"):
            qid = f"Q{qid.zfill(3)}"
        fields = _parse_fields(body)
        q_text, q_issue = _normalize_field_value(fields.get("question_text"))
        s_ans, _ = _normalize_field_value(fields.get("standard_answer"))
        stu_ans, stu_ans_issue = _normalize_field_value(fields.get("student_answer"))
        stu_sol, stu_sol_issue = _normalize_field_value(fields.get("student_solution"))
        stu_note, _ = _normalize_field_value(fields.get("student_note"))

        issues: list[str] = []
        for issue in (q_issue, stu_ans_issue, stu_sol_issue):
            if issue and issue not in issues:  # 去重（学生答案与过程同时为「（无）」时只记一次）
                issues.append(issue)
        if q_text is None:
            issues.append("未能识别到题干文本，请人工核验")
        if s_ans is None:
            issues.append("缺少标准答案（题目核对可能不完整）")

        latex = _extract_latex(q_text, s_ans, stu_sol)
        options = _extract_options(q_text)

        questions.append({
            "question_id": qid,
            "source_file": source_file,
            "question_text": q_text,
            "latex": latex,
            "options": options,
            "standard_answer": s_ans,
            "student_answer": stu_ans,
            "student_solution": stu_sol,
            "student_note": stu_note,
            "ocr_confidence": None,
            "recognition_issues": issues,
            "knowledge_points": [],
            "error_analysis": {},
            "difficulty": None,
            "review_status": "pending",
        })

    if not questions:
        raise SkillError(
            "未能从输入文本中识别出任何题目。请确认文件包含题号（如「1.」「Q1」）"
            "或 Markdown 题目标题（如「## Q001」），或手动补充题目文本。"
        )

    # 题号重复检测
    ids = [q["question_id"] for q in questions]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        logger.warning("检测到重复题号：%s，已按出现顺序保留", dup)

    return {
        "meta": {
            "subject": subject,
            "source_file": source_file,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "extraction_method": "markdown-normalize",
            "question_count": len(questions),
        },
        "questions": questions,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="结构化题目为统一 JSON")
    parser.add_argument("--input", required=True, help="提取结果 JSON 或原文 txt/md 路径")
    parser.add_argument("--output", required=True, help="输出 questions.json 路径")
    parser.add_argument("--subject", default=None, help="学科提示（可选）")
    args = parser.parse_args()

    logger = setup_logger()
    in_path = Path(args.input)
    if in_path.suffix.lower() == ".json":
        payload = json.loads(in_path.read_text(encoding="utf-8"))
        text = payload.get("text", "")
        source_file = payload.get("source_file", in_path.name)
    else:
        text = read_text(in_path)
        source_file = in_path.name

    try:
        result = normalize_questions(text, source_file, subject_hint=args.subject, logger=logger)
    except SkillError as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False, indent=2))
        return 2

    write_json(Path(args.output), result)
    print(json.dumps({
        "ok": True,
        "question_count": result["meta"]["question_count"],
        "subject": result["meta"]["subject"],
        "question_ids": [q["question_id"] for q in result["questions"]],
        "output": str(args.output),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
