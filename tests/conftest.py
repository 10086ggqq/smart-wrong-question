"""pytest 共享配置：路径注入、示例数据、环境探测。

运行方式（项目根目录）：
    python -m pytest tests/ -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
for p in (str(PROJECT_ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from common import find_cn_fonts  # noqa: E402

# 场景 A 示例：纯文本错题（含标准答案与学生解题过程）
SAMPLE_MD = """# 数学错题集（测试数据）

## Q001
题干：已知函数 $f(x) = x^3 - 3x$，求函数的极值。
标准答案：求导得 $f'(x) = 3x^2 - 3$，令 $f'(x) = 0$ 得 $x = \\pm 1$，极大值为 2，极小值为 -2。
学生答案：极大值为 2，极小值为 -2。
学生解题过程：求导后令导数为零，代入得极值，没有判断导数符号变化。

## Q002
题干：判断函数 $f(x) = x^2 + 1$ 的奇偶性。
标准答案：$f(-x) = f(x)$，为偶函数。
学生答案：奇函数。
学生解题过程：直接判断为奇函数，没有验证定义域。
"""

# 场景 D 示例：只有题干，无学生作答
SAMPLE_MD_NO_STUDENT = """# 数学错题集（测试数据）

## Q001
题干：利用导数求函数 $f(x) = x^3 - 3x$ 在区间 $[-2, 2]$ 上的最大值与最小值。
标准答案：比较端点与驻点函数值，最大值为 2，最小值为 -2。
"""

# 场景 B 示例：带公式题目（公式错误可标记）
SAMPLE_MD_FORMULA = """# 数学错题集（测试数据）

## Q001
题干：利用导数求函数 $f(x) = x^3 - 3x$ 的极值。
标准答案：求导得 $f'(x) = 3x^2 - 3$，令 $f'(x) = 0$ 得 $x = \\pm 1$，极大值为 2，极小值为 -2。
学生答案：$f'(x) = 3x^2$，极大值为 2，极小值为 -2。
学生解题过程：求导得到 $3x^2$ 后直接令其为零。
"""


@pytest.fixture()
def sample_md_file(tmp_path: Path) -> Path:
    p = tmp_path / "sample_questions.md"
    p.write_text(SAMPLE_MD, encoding="utf-8")
    return p


@pytest.fixture()
def sample_md_no_student(tmp_path: Path) -> Path:
    p = tmp_path / "no_student.md"
    p.write_text(SAMPLE_MD_NO_STUDENT, encoding="utf-8")
    return p


@pytest.fixture()
def sample_md_formula(tmp_path: Path) -> Path:
    p = tmp_path / "formula.md"
    p.write_text(SAMPLE_MD_FORMULA, encoding="utf-8")
    return p


@pytest.fixture()
def chinese_font_available() -> bool:
    return find_cn_fonts()["regular"] is not None


def make_pdf(path: Path, pages_text: list[str]) -> Path:
    """用 reportlab 生成测试 PDF（每页一段文本）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdfcanvas

    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    for text in pages_text:
        c.setFont("Helvetica", 11)
        y = 800
        for line in text.splitlines():
            c.drawString(72, y, line)
            y -= 16
        c.showPage()
    c.save()
    return path


def make_blank_pdf(path: Path) -> Path:
    """生成无可提取文本的 PDF（模拟扫描件）。"""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdfcanvas

    c = pdfcanvas.Canvas(str(path), pagesize=A4)
    c.showPage()  # 空白页，无文本
    c.save()
    return path
