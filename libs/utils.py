"""通用小工具。"""

from __future__ import annotations

from typing import Any

# 只剥两侧：引号 + 中英文逗号/句号/叹号/问号；中间撇号保留
_EDGE_STRIP = (
    "\"'"
    "“”„‟«»"
    "‘’‚‛‹›"
    "「」『』｢｣"
    "〝〞〟"
    "＂＇"
    ",，.。!！?？"
)


def clean_text(value: Any) -> str:
    """去掉首尾空白、引号、中英文逗号/句号/叹号/问号。"""
    if not isinstance(value, str):
        return ""
    return value.strip().strip(_EDGE_STRIP).strip()
