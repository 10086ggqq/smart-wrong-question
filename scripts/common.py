"""公共工具模块：路径管理、配置加载、JSON/YAML IO、字体探测、日志。

本模块被 scripts/ 下所有脚本共享。禁止在此放置业务逻辑。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 路径管理
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_path(*parts: str) -> Path:
    """返回项目内路径（相对项目根目录拼接）。"""
    return PROJECT_ROOT.joinpath(*parts)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------------
# 自定义异常（面向用户的可理解错误）
# ---------------------------------------------------------------------------

class SkillError(Exception):
    """Skill 业务错误，message 必须使用用户可理解的中文描述。"""


class InputError(SkillError):
    """输入文件无效（不存在、为空、格式不支持、已损坏）。"""


class RenderError(SkillError):
    """图表渲染失败。"""


class PdfGenerationError(SkillError):
    """PDF 生成失败（引擎错误、字体缺失等）。"""


# ---------------------------------------------------------------------------
# 配置与数据 IO
# ---------------------------------------------------------------------------

def load_config(config_path: Path | None = None) -> dict:
    """加载 config/default.yaml；文件缺失时返回内置最小配置。"""
    path = config_path or project_path("config", "default.yaml")
    if not path.exists():
        return _fallback_config()
    try:
        import yaml
    except ImportError as e:  # pragma: no cover
        raise SkillError("缺少依赖 PyYAML，请先执行 pip install -r requirements.txt") from e
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    base = _fallback_config()
    base.update(cfg)
    return base


def _fallback_config() -> dict:
    return {
        "study": {
            "default_days": 7,
            "max_days": 14,
            "default_daily_minutes": 60,
            "min_task_minutes": 10,
            "task_granularity": 5,
            "reserve_ratio": 0.9,
        },
        "priority": {
            "weights": {
                "wrong_frequency": 0.25,
                "recency_repeat": 0.20,
                "error_severity": 0.20,
                "prerequisite_impact": 0.20,
                "retest_gap": 0.15,
            },
            "severity_map": {
                "概念理解不清": 1.0,
                "公式或定理使用错误": 0.9,
                "解题方法选择不当": 0.8,
                "知识点之间联系不清": 0.8,
                "计算或推导错误": 0.6,
                "审题不充分": 0.5,
                "解题步骤不完整": 0.4,
                "暂无法判断": 0.5,
            },
            "missing_value_policy": "conservative",
            "conservative_value": 0.5,
        },
        "graphs": {
            "knowledge_graph": {
                "filename_prefix": "knowledge-graph",
                "formats": ["svg", "png"],
                "dpi": 150,
                "weak_color_threshold": 0.4,
                "medium_color_threshold": 0.7,
                "node_base_size": 800,
                "max_label_length": 12,
            },
            "topic_map": {
                "filename_prefix": "topic-map",
                "formats": ["svg", "png"],
                "dpi": 150,
            },
        },
        "report": {
            "markdown_filename": "analysis.md",
            "pdf_filename": "analysis.pdf",
            "data_filename": "analysis-data.json",
            "assets_subdir": "assets",
            "title": "智能错题分析报告",
        },
        "paths": {
            "knowledge_base": "knowledge/sample_knowledge_base.json",
            "fonts": [
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/msyhbd.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "C:/Windows/Fonts/simsun.ttc",
            ],
        },
        "logging": {"level": "INFO", "mask_personal_info": True},
    }


def read_json(path: Path) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise InputError(f"文件不存在：{path}") from e
    except json.JSONDecodeError as e:
        raise InputError(f"JSON 文件损坏或格式错误：{path}（{e}）") from e


def write_json(path: Path, data: Any) -> Path:
    ensure_dir(path.parent)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def read_text(path: Path) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise InputError(f"无法解码文本文件（尝试 utf-8/gb18030 均失败）：{path}")


# ---------------------------------------------------------------------------
# 日志（默认不输出学生答案等个人信息）
# ---------------------------------------------------------------------------

_LOGGER_CONFIGURED = False


def setup_logger(name: str = "smart-wrong-question") -> logging.Logger:
    global _LOGGER_CONFIGURED
    logger = logging.getLogger(name)
    if not _LOGGER_CONFIGURED:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        _LOGGER_CONFIGURED = True
    return logger


def mask_personal(text: str | None, keep: int = 6) -> str:
    """日志脱敏：仅保留前 keep 个字符。"""
    if not text:
        return "<empty>"
    if len(text) <= keep:
        return f"<{len(text)} chars>"
    return f"<{len(text)} chars> {text[:keep]}…"


# ---------------------------------------------------------------------------
# 字体探测（图表与 PDF 中文显示）
# ---------------------------------------------------------------------------

def find_cn_fonts(candidates: list[str] | None = None) -> dict:
    """探测可用的中文字体。

    返回 {"regular": path|None, "bold": path|None, "source": "system"|"none"}
    regular/bold 为字体文件绝对路径。bold 可与 regular 相同（无独立粗体时）。
    """
    candidates = candidates or load_config()["paths"]["fonts"]
    existing = [p for p in candidates if p and Path(p).exists()]
    if not existing:
        # 常见 Linux 路径的补充探测
        for p in (
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/arphic/uming.ttc",
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
        ):
            if Path(p).exists():
                existing.append(p)
    if not existing:
        return {"regular": None, "bold": None, "source": "none"}
    regular = existing[0]
    bold = next((p for p in existing if "bd" in Path(p).name.lower()
                 or "bold" in Path(p).name.lower()), regular)
    return {"regular": regular, "bold": bold, "source": "system"}


def register_matplotlib_cn_font() -> str | None:
    """注册中文字体到 matplotlib，返回 family 名；无中文字体时返回 None。"""
    try:
        import matplotlib
        from matplotlib import font_manager
    except ImportError as e:  # pragma: no cover
        raise SkillError("缺少依赖 matplotlib，请先执行 pip install -r requirements.txt") from e

    fonts = find_cn_fonts()
    if fonts["regular"] is None:
        return None
    try:
        font_manager.fontManager.addfont(fonts["regular"])
        if fonts["bold"] != fonts["regular"] and Path(fonts["bold"]).exists():
            font_manager.fontManager.addfont(fonts["bold"])
        family = font_manager.FontProperties(fname=fonts["regular"]).get_name()
        matplotlib.rcParams["font.family"] = family
        matplotlib.rcParams["axes.unicode_minus"] = False
        return family
    except Exception:  # pragma: no cover
        return None


def register_reportlab_cn_fonts() -> tuple[str, str] | None:
    """注册中文字体到 reportlab，返回 (regular_name, bold_name)；无字体返回 None。"""
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
    except ImportError as e:  # pragma: no cover
        raise SkillError("缺少依赖 reportlab，请先执行 pip install -r requirements.txt") from e

    fonts = find_cn_fonts()
    if fonts["regular"] is None:
        return None
    try:
        regular_name, bold_name = "SkillCN", "SkillCN-Bold"
        _register_ttfont(pdfmetrics, TTFont, regular_name, fonts["regular"])
        bold_path = fonts["bold"] if Path(fonts["bold"]).exists() else fonts["regular"]
        _register_ttfont(pdfmetrics, TTFont, bold_name, bold_path)
        return regular_name, bold_name
    except Exception as e:  # pragma: no cover
        raise PdfGenerationError(f"中文字体注册失败：{e}") from e


def _register_ttfont(pdfmetrics, TTFont, name: str, path: str) -> None:
    """注册 TTF/TTC 字体；TTC 使用 subfontIndex=0。"""
    lower = path.lower()
    if lower.endswith(".ttc"):
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
    else:
        pdfmetrics.registerFont(TTFont(name, path))
