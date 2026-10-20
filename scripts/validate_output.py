"""输出验证：检查交付物完整性、可打开性与约束满足。

验证项：
1. 必需文件存在且非空（analysis.md / analysis.pdf / analysis-data.json / 图表）；
2. Markdown 必需章节齐全（11 项）；
3. Markdown 中引用的图片文件真实存在；
4. PDF 可被解析（pypdf）且页数 > 0；
5. 复习计划每日任务总时长不超过预算；
6. JSON 数据文件可解析。

任何验证失败都明确列出失败项，不允许虚假成功。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import SkillError, load_config, read_json, setup_logger
from error_causes import ERROR_CAUSES, format_error_causes, validate_error_codes

REQUIRED_SECTIONS = [
    "报告基本信息", "错题整体概览", "主要薄弱知识点", "错题明细表",
    "错因分析及证据", "知识依赖图", "题型地图", "复习计划",
    "复测记录", "后续学习建议", "数据来源与不确定性说明",
]
RE_MD_IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def _check_error_causes(data: dict, label: str) -> tuple[bool, str]:
    """校验错因多选字段（v3.1.0）。

    规则：
    - error_codes 若存在必须是 1-5 的整数代码列表（空列表 = 未标注，合法）；
    - 旧版 error_code 若存在必须等于 error_codes 的第一个元素（主错因）；
    - 两个字段都不存在时合法（旧版数据，未使用多选字段）。
    """
    problems: list[str] = []
    for q in data.get("questions", []):
        qid = q.get("question_id", "?")
        ea = q.get("error_analysis") or {}
        if "error_codes" not in ea and "error_code" not in ea:
            continue  # 旧版数据未使用错因代码字段
        try:
            codes = validate_error_codes(ea.get("error_codes"))
        except SkillError as e:
            problems.append(f"{qid}: {e}")
            continue
        legacy = ea.get("error_code")
        if legacy is not None:
            expected = codes[0] if codes else None
            if legacy != expected:
                problems.append(
                    f"{qid}: error_code={legacy} 与 error_codes={codes} 不一致"
                    f"（主错因应为 {expected}）"
                )
    if problems:
        return False, "；".join(problems)
    return True, f"{label} 错因多选字段合法（代码 1-5：{format_error_causes(sorted(ERROR_CAUSES))}）"


def validate_output(output_dir: Path, config: dict, logger=None) -> dict[str, Any]:
    logger = logger or setup_logger()
    output_dir = Path(output_dir)
    rconf = config["report"]
    checks: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: str, severity: str = "error"):
        checks.append({"check": name, "ok": ok, "detail": detail, "severity": severity})

    md_path = output_dir / rconf["markdown_filename"]
    pdf_path = output_dir / rconf["pdf_filename"]
    data_path = output_dir / rconf["data_filename"]
    assets_dir = output_dir / rconf["assets_subdir"]

    # 1. 文件存在与非空
    for path, label in ((md_path, "Markdown 报告"), (pdf_path, "PDF 报告"), (data_path, "结构化数据")):
        if not path.exists():
            record(f"{label}存在", False, f"文件不存在：{path}")
        elif path.stat().st_size == 0:
            record(f"{label}非空", False, f"文件为空：{path}")
        else:
            record(f"{label}存在且非空", True, f"{path}（{path.stat().st_size} 字节）")

    # 2. 图表文件
    for prefix in ("knowledge-graph", "topic-map"):
        found = [f for f in (assets_dir / f"{prefix}.svg", assets_dir / f"{prefix}.png") if f.exists()]
        if found:
            record(f"图表 {prefix}", True, "、".join(str(p.name) for p in found))
        else:
            record(f"图表 {prefix}", False, f"未找到 {prefix}.svg/png", severity="warning")

    # 3. Markdown 章节
    if md_path.exists():
        md_text = md_path.read_text(encoding="utf-8")
        missing = [s for s in REQUIRED_SECTIONS if s not in md_text]
        record("Markdown 章节完整", not missing,
               "全部 11 个必需章节齐全" if not missing else f"缺少章节：{missing}")

        # 4. 图片引用
        broken_images = []
        for src in RE_MD_IMAGE.findall(md_text):
            img_path = (md_path.parent / src).resolve()
            if not img_path.exists():
                broken_images.append(src)
        record("Markdown 图片引用有效", not broken_images,
               f"共 {len(RE_MD_IMAGE.findall(md_text))} 处引用全部有效"
               if not broken_images else f"失效引用：{broken_images}")
    else:
        record("Markdown 章节完整", False, "Markdown 报告不存在，跳过章节检查")
        record("Markdown 图片引用有效", False, "Markdown 报告不存在，跳过图片检查")

    # 5. PDF 可打开
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        try:
            from pypdf import PdfReader
            reader = PdfReader(str(pdf_path))
            pages = len(reader.pages)
            record("PDF 可解析", pages > 0, f"共 {pages} 页")
        except Exception as e:
            record("PDF 可解析", False, f"pypdf 打开失败：{type(e).__name__}: {e}")

    # 6. 复习计划时间预算
    plan_path = output_dir / "study_plan.json"
    if plan_path.exists():
        try:
            plan = read_json(plan_path)
            violations = [d["day"] for d in plan.get("days", [])
                          if d["total_minutes"] > d["budget_minutes"]]
            record("复习计划时间预算", not violations,
                   "每日任务均在预算内" if not violations
                   else f"第 {violations} 天任务超出预算")
        except SkillError as e:
            record("复习计划时间预算", False, f"计划文件无法解析：{e}")
    else:
        record("复习计划时间预算", False, "study_plan.json 不存在", severity="warning")

    # 7. 错因多选字段（v3.1.0）：analysis-data.json 与 questions.json 中的
    #    error_codes / error_code 合法性及主错因一致性
    for name in (rconf["data_filename"], "questions.json"):
        path = output_dir / name
        if not path.exists():
            continue
        try:
            payload = read_json(path)
        except SkillError as e:
            record(f"错因多选字段（{name}）", False, f"文件无法解析：{e}")
            continue
        ok, detail = _check_error_causes(payload, name)
        record(f"错因多选字段（{name}）", ok, detail)

    failed = [c for c in checks if not c["ok"] and c["severity"] == "error"]
    warnings = [c for c in checks if not c["ok"] and c["severity"] == "warning"]
    return {
        "ok": not failed,
        "output_dir": str(output_dir),
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["ok"]),
            "failed": len(failed),
            "warnings": len(warnings),
        },
        "checks": checks,
        "failed_items": failed,
        "warning_items": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="验证输出文件完整性")
    parser.add_argument("--output-dir", required=True, help="输出目录")
    args = parser.parse_args()

    logger = setup_logger()
    result = validate_output(Path(args.output_dir), load_config(), logger)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
