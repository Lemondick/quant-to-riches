"""Keep exchange-listed stocks distinct from six-digit OTC fund codes."""

import re

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.akshare_symbols import stock_exchange


def canonical_symbol(value: str, asset_type: AssetType | str, exchange: str | None = None) -> str:
    if not isinstance(value, str):
        raise TypeError("标的代码必须是字符串")
    text = str(value).strip().upper()
    if not text:
        raise ValueError("标的代码不能为空")
    kind = getattr(asset_type, "value", asset_type)
    if kind != "stock":
        return text
    match = re.fullmatch(r"(\d{6})(?:\.(SH|SZ|BJ))?", text)
    if not match:
        return text  # Preserve named/imported instruments outside the A-share universe.
    code, suffix = match.groups()
    selected = suffix or (exchange if exchange in {"SH", "SZ", "BJ"} else stock_exchange(code))
    return f"{code}.{selected}"
