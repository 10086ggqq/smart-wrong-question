"""错因多选数据结构（v3.1.0 引入，兼容 v3.0.0 单选）。

背景：v3.0.0 及以前，一道题的错因为**单选**（`error_code`：1-5 的整数）。
实际学习中错因常常是复合的，例如「概念不清 + 计算失误」「计算失误 + 方法没掌握」，
单选无法表达，也会把复合问题错误地归到单一原因上。v3.1.0 起错因改为**多选**：

- 规范字段：`error_codes`（list[int]，升序去重，取值 1-5，空列表 = 未标注）；
- 兼容字段：`error_code`（int | None）= `error_codes` 的第一个元素（主错因）。
  旧版仅含 `error_code` 的数据可无损升级为 `error_codes`（见 `upgrade_legacy_error_cause`）；
- 候选错因（代码固定，不增删）：

  ====  ====
  代码  标签
  ====  ====
  1     概念不清
  2     方法没掌握
  3     计算失误
  4     时间不够
  5     其他
  ====  ====

- 多选规则：一道题可同时标注多个错因，**任意组合**（含五类全选）；
  选 5（其他）时的补充描述仍存 `other_error_detail`，归类始终为「其他」。

本模块是全流程唯一的错因代码真源（SKILL.md 主流程与 scripts/ 批处理共用），
提供：用户自由文本解析、存储数据校验、标签/展示格式化、旧版数据升级、
交互错因 → 批处理严重度标签映射。确保多选值的**存储、解析、回显**一致。

解析接受格式（大小写不敏感，分隔符混用亦可）：
    "3"            -> [3]
    "1,3" / "1，3" -> [1, 3]
    "1+3" / "1＋3" -> [1, 3]
    "1、3" / "1 3" -> [1, 3]
    "1和3" / "1及3" / "1与3" -> [1, 3]
    "1/3"          -> [1, 3]
    "概念不清+计算失误" -> [1, 3]     （直接给标签也可）
    "全部" / "全选" -> [1, 2, 3, 4, 5]
    "无" / "没有"   -> []
"""
from __future__ import annotations

import re
from typing import Any

from common import InputError

# ---------------------------------------------------------------------------
# 候选错因（代码 -> 标签），全流程唯一真源
# ---------------------------------------------------------------------------

ERROR_CAUSES: dict[int, str] = {
    1: "概念不清",
    2: "方法没掌握",
    3: "计算失误",
    4: "时间不够",
    5: "其他",
}
VALID_CODES: frozenset[int] = frozenset(ERROR_CAUSES)
ALL_CODES: list[int] = sorted(ERROR_CAUSES)  # [1, 2, 3, 4, 5]

# 标签 -> 代码（含常见别名，便于用户直接打字标签）
_LABEL_ALIASES: dict[str, int] = {
    "概念不清": 1, "概念理解不清": 1, "概念模糊": 1,
    "方法没掌握": 2, "方法未掌握": 2, "方法不会": 2, "方法不熟": 2,
    "计算失误": 3, "计算错误": 3, "算错": 3, "粗心": 3,
    "时间不够": 4, "时间不足": 4, "来不及": 4,
    "其他": 5, "其它": 5,
}

# 全选 / 未选同义词
_ALL_TOKENS = {"全部", "所有", "全选", "全部错因", "所有错因", "all"}
_NONE_TOKENS = {"", "无", "没有", "未提供", "不知道", "不确定", "none", "null"}

# 分隔符：中英文逗号/顿号/加号/分号/斜杠/空白，以及「和」「及」「与」
_SPLIT_RE = re.compile(r"[,，、+＋;；/／\s]+|和|及|与")


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------

def _coerce_int(token: str) -> int | None:
    """把单个 token 转成错因代码；无法识别返回 None。"""
    t = token.strip().strip("。.．:：;；,，、")
    # 去掉「错因1」「原因3」这类前缀
    t = re.sub(r"^(错因|原因|选项)\s*", "", t)
    if not t:
        return None
    if t.isdigit():
        code = int(t)
        return code if code in VALID_CODES else None
    return _LABEL_ALIASES.get(t) or _LABEL_ALIASES.get(t.lower())


def parse_error_causes(raw: Any, strict: bool = False) -> list[int]:
    """把用户自由文本/任意输入解析为错因代码列表（升序去重）。

    参数：
        raw: 用户输入。支持 None、int、float（整数值）、str、list/tuple（可嵌套）。
             字符串内支持逗号/加号/顿号/斜杠/空白/「和」「及」「与」分隔，
             支持「全部/全选」与标签直填（如「概念不清+计算失误」）。
        strict: True 时遇到无法识别的 token 抛 InputError（用于校验存储数据）；
                False 时静默跳过无法识别的 token（用于解析用户自由文本）。

    返回：
        升序去重的代码列表；无有效输入时返回 []。
    """
    if raw is None:
        return []
    if isinstance(raw, bool):  # bool 是 int 子类，必须先排除
        if strict:
            raise InputError(f"错因值必须是 1-5 的整数代码，收到布尔值：{raw}")
        return []
    if isinstance(raw, int):
        return [raw] if raw in VALID_CODES else _invalid([raw], strict)
    if isinstance(raw, float):
        if raw.is_integer() and int(raw) in VALID_CODES:
            return [int(raw)]
        return _invalid([raw], strict)
    if isinstance(raw, str):
        text = raw.strip()
        if text in _NONE_TOKENS:
            return []
        if text.lower() in _ALL_TOKENS:
            return list(ALL_CODES)
        tokens = [t for t in _SPLIT_RE.split(text) if t.strip()]
        if not tokens:
            return []
        codes: list[int] = []
        invalid: list[str] = []
        for tok in tokens:
            code = _coerce_int(tok)
            if code is None:
                invalid.append(tok.strip())
            elif code not in codes:
                codes.append(code)
        if invalid and strict:
            raise InputError(
                f"无法识别的错因：{invalid}。有效代码 1-5："
                + " / ".join(f"{c} {l}" for c, l in sorted(ERROR_CAUSES.items()))
            )
        return sorted(codes)
    if isinstance(raw, (list, tuple, set)):
        codes = []
        invalid: list[str] = []
        for item in raw:
            for code in parse_error_causes(item, strict=False):
                if code not in codes:
                    codes.append(code)
            if isinstance(item, str) and not parse_error_causes(item):
                invalid.append(str(item))
            elif isinstance(item, (int, float)) and not isinstance(item, bool) \
                    and int(item) not in VALID_CODES:
                invalid.append(str(item))
        if invalid and strict:
            raise InputError(f"错因代码超出 1-5 范围：{invalid}")
        return sorted(codes)
    if strict:
        raise InputError(f"不支持的错因数据类型：{type(raw).__name__}")
    return []


def _invalid(values: list, strict: bool) -> list[int]:
    if strict:
        raise InputError(
            f"错因代码超出 1-5 范围：{values}。有效代码："
            + " / ".join(f"{c} {l}" for c, l in sorted(ERROR_CAUSES.items()))
        )
    return []


def normalize_error_codes(value: Any, strict: bool = True) -> list[int]:
    """校验/归一化**已存储**的错因字段（error_codes / error_code）。

    与 parse_error_causes 的区别：默认 strict=True——存储数据出现越界代码
    （如 9、0、-1）必须报错，不允许静默吞掉。
    """
    return parse_error_causes(value, strict=strict)


def validate_error_codes(codes: Any) -> list[int]:
    """严格校验 error_codes：必须是 1-5 的整数代码列表，去重升序。

    空列表合法（表示未标注错因）。校验失败抛 InputError。
    """
    if codes is None:
        return []
    if isinstance(codes, (int, str)) and not isinstance(codes, bool):
        codes = [codes]
    if not isinstance(codes, (list, tuple)):
        raise InputError(
            f"error_codes 必须是列表（v3.1.0 多选字段），收到：{type(codes).__name__}"
        )
    for c in codes:
        if isinstance(c, bool) or not isinstance(c, int):
            raise InputError(f"error_codes 含非整数代码：{c!r}（应为 1-5 的整数）")
        if c not in VALID_CODES:
            raise InputError(
                f"error_codes 含越界代码：{c}（有效代码 1-5："
                + " / ".join(f"{c} {l}" for c, l in sorted(ERROR_CAUSES.items())) + "）"
            )
    return sorted(set(codes))


# ---------------------------------------------------------------------------
# 展示与回显
# ---------------------------------------------------------------------------

def error_cause_labels(codes: Any) -> list[str]:
    """代码列表 -> 标签列表（跳过无法识别的值）。"""
    labels = []
    for code in parse_error_causes(codes, strict=False):
        label = ERROR_CAUSES.get(code)
        if label and label not in labels:
            labels.append(label)
    return labels


def format_error_causes(codes: Any, with_code: bool = False,
                        empty: str = "未提供") -> str:
    """错因展示格式化。

    with_code=False: "概念不清 + 计算失误"
    with_code=True:  "[1,3] 概念不清 + 计算失误"
    空列表:          "未提供"（可用 empty 自定义）
    """
    normalized = parse_error_causes(codes, strict=False)
    if not normalized:
        return empty
    labels = " + ".join(ERROR_CAUSES[c] for c in normalized)
    if with_code:
        return f"[{','.join(str(c) for c in normalized)}] {labels}"
    return labels


def primary_error_code(codes: Any) -> int | None:
    """主错因（= 多选列表第一个代码）；未标注返回 None。

    用于兼容旧版单选字段 error_code 的回显。
    """
    normalized = parse_error_causes(codes, strict=False)
    return normalized[0] if normalized else None


def cause_menu() -> str:
    """Step1 询问用的错因菜单行（与 SKILL.md §4 五类保持一致）。"""
    return " / ".join(f"{c} {l}" for c, l in sorted(ERROR_CAUSES.items()))


# ---------------------------------------------------------------------------
# 旧版单选数据兼容
# ---------------------------------------------------------------------------

def upgrade_legacy_error_cause(record: dict) -> dict:
    """旧版单选数据无损升级：仅含 error_code 时补全 error_codes。

    - 已有 error_codes 时不覆盖其有效值（以新字段为准）；
    - error_code 越界时按无法识别处理（error_codes 置空、error_code 置 null，
      保证两个字段始终一致，不残留越界值）；
    - 记录中两个字段都不存在时原样返回（不新增字段）。
    """
    if not isinstance(record, dict):
        return record
    legacy = record.get("error_code")
    if record.get("error_codes") is None and legacy is not None:
        record["error_codes"] = parse_error_causes(legacy, strict=False)
    if record.get("error_codes") is None and legacy is None:
        return record  # 旧版数据未使用错因代码字段，保持原状
    codes = parse_error_causes(record.get("error_codes"), strict=False)
    record["error_codes"] = codes
    record["error_code"] = primary_error_code(codes)
    return record


# ---------------------------------------------------------------------------
# 交互错因 -> 批处理严重度标签（config: error_causes.severity_labels）
# ---------------------------------------------------------------------------

def to_severity_labels(codes: Any, severity_labels: dict[str, str]) -> list[str]:
    """把交互五类错因代码映射为批处理 severity_map 的标签键。

    severity_labels 形如 {"概念不清": "概念理解不清", ...}（见 config/default.yaml）。
    「其他」无实质错因信息，按配置映射到保守标签（默认「暂无法判断」），
    调用方负责把无信息标签排除出观察值。
    """
    labels: list[str] = []
    for code in parse_error_causes(codes, strict=False):
        label = ERROR_CAUSES.get(code)
        mapped = severity_labels.get(label) if label else None
        if mapped and mapped not in labels:
            labels.append(mapped)
    return labels
