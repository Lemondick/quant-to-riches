"""OCR transaction-row parsing."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.ocr import OcrResult, extract_detected_dates

from ._models import (
    _DATE_RE,
    _DELIMITERS,
    _NUMBER_RE,
    _SHANGHAI_NOON,
    _SHANGHAI_TZ,
    _SUMMARY_QUANTUM,
    _TRANSACTION_ACTIONS,
    _TRANSACTION_AMOUNT_LABELS,
    _TRANSACTION_FEE_LABELS,
    _TRANSACTION_ID_LABELS,
    _TRANSACTION_IGNORE_MARKERS,
    _TRANSACTION_PRICE_LABELS,
    _TRANSACTION_QUANTITY_LABELS,
    PhotoImportError,
    PhotoTransactionHint,
)
from ._parsing_primitives import (
    _asset_type,
    _first_code,
    _is_cash_line,
    _labelled_number_pattern,
    _normalise_line,
    _normalise_symbol,
    _numeric_only,
)
from .code_matching import _fund_raw_symbol, _match_fund_name_row


def parse_transaction_statement(
    ocr: OcrResult,
    as_of_date: date,
) -> tuple[PhotoTransactionHint, ...]:
    """Parse common fund/broker transaction rows conservatively.

    The parser requires an action, a quantity (or an amount that can be divided
    by a labelled price), and a positive price.  Ambiguous numeric-only rows are
    skipped rather than guessed.  A service can subsequently resolve fund names
    to codes through its configured external market provider.
    """

    if not isinstance(ocr, OcrResult):
        raise TypeError("ocr must be OcrResult")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be date")
    lines = tuple(_normalise_line(line) for line in ocr.lines if _normalise_line(line))
    groups = _transaction_row_groups(lines)
    hints: list[PhotoTransactionHint] = []
    for group in groups:
        hint = _parse_transaction_group(group, as_of_date)
        if hint is not None:
            hints.append(hint)
    return tuple(hints)


def _transaction_row_groups(lines: Sequence[str]) -> tuple[str, ...]:
    """Join cell-level OCR fragments into likely transaction rows."""

    groups: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if _is_transaction_header(line):
            index += 1
            continue
        if not _transaction_row_starter(line):
            index += 1
            continue
        combined = line
        next_index = index + 1
        # A row generally has at most one date/name/action cell followed by a
        # handful of numeric cells.  Stop at the next explicit action or code.
        while next_index < len(lines) and next_index < index + 8:
            following = lines[next_index]
            if _is_transaction_header(following):
                break
            if (
                next_index > index
                and _transaction_row_starter(following)
                and _transaction_numbers(combined, _first_code(combined))
            ):
                break
            if _first_code(following) is not None and _first_code(combined) is not None:
                break
            combined = f"{combined} {following}"
            next_index += 1
            if _transaction_group_complete(combined):
                break
        groups.append(combined)
        index = max(next_index, index + 1)
    return tuple(groups)


def _is_transaction_header(line: str) -> bool:
    if any(action in line for action in _TRANSACTION_ACTIONS) or _first_code(line) is not None:
        return False
    if _DATE_RE.search(line):
        return False
    return any(marker in line for marker in _TRANSACTION_IGNORE_MARKERS) or (
        "确认份额" in line and "确认净值" in line
    )


def _transaction_row_starter(line: str) -> bool:
    if _is_transaction_header(line) or _is_cash_line(line):
        return False
    if any(action in line for action in _TRANSACTION_ACTIONS):
        return True
    if _first_code(line) is not None and not _numeric_only(line):
        return True
    return bool(_DATE_RE.search(line) and not _numeric_only(line))


def _transaction_group_complete(value: str) -> bool:
    return bool(
        _labelled_transaction_number(value, _TRANSACTION_QUANTITY_LABELS) is not None
        and _labelled_transaction_number(value, _TRANSACTION_PRICE_LABELS) is not None
        and _labelled_transaction_number(value, _TRANSACTION_FEE_LABELS) is not None
    )


def _parse_transaction_group(group: str, as_of_date: date) -> PhotoTransactionHint | None:
    action_label = next((label for label in _TRANSACTION_ACTIONS if label in group), None)
    if action_label is None:
        return None
    action = _TRANSACTION_ACTIONS[action_label]
    code_match = _first_code(group)
    code = None
    suffix = None
    if code_match is not None:
        code, suffix, _, _ = code_match
    # Remove code, dates, action labels and numeric cells before retaining the
    # human-readable name.  The external lookup path handles code-less funds.
    name = group
    if code_match is not None:
        name = name[: code_match[2]] + name[code_match[3] :]
    for label in _TRANSACTION_ACTIONS:
        name = name.replace(label, "")
    for label in sorted(
        {
            *_TRANSACTION_QUANTITY_LABELS,
            *_TRANSACTION_PRICE_LABELS,
            *_TRANSACTION_FEE_LABELS,
            *_TRANSACTION_AMOUNT_LABELS,
            *_TRANSACTION_ID_LABELS,
        },
        key=len,
        reverse=True,
    ):
        name = name.replace(label, "")
    name = _DATE_RE.sub("", name)
    name = re.sub(_NUMBER_RE, " ", name)
    name = re.sub(r"[|,:：;；=]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(_DELIMITERS)
    if not name:
        name = code or "未命名标的"
    asset_type = _asset_type(code or "", suffix, name)
    symbol = _normalise_symbol(code, suffix, asset_type) if code else None
    values = _transaction_numbers(group, code_match)
    quantity = _labelled_transaction_number(group, _TRANSACTION_QUANTITY_LABELS)
    price = _labelled_transaction_number(group, _TRANSACTION_PRICE_LABELS)
    fees = _labelled_transaction_number(group, _TRANSACTION_FEE_LABELS)
    amount = _labelled_transaction_number(group, _TRANSACTION_AMOUNT_LABELS)
    if quantity is None or price is None:
        inferred = _infer_transaction_values(values, amount, quantity, price)
        if inferred is None:
            return None
        quantity, price = inferred
    if amount is not None and quantity is None and price is not None:
        quantity = amount / price
    if quantity is None or price is None or quantity <= 0 or price <= 0:
        return None
    if fees is not None and fees < 0:
        raise PhotoImportError("交易费用为负数，请核对识别结果后手动录入")
    traded_at = _transaction_datetime(group, as_of_date)
    if traded_at is None:
        return None
    external_id = _labelled_transaction_id(group)
    return PhotoTransactionHint(
        name=name,
        symbol=symbol,
        asset_type=asset_type,
        action=action,
        quantity=quantity.quantize(_SUMMARY_QUANTUM),
        price=price.quantize(_SUMMARY_QUANTUM),
        fees=fees.quantize(Decimal("0.0001")) if fees is not None else None,
        traded_at=traded_at,
        external_id=external_id,
    )


def _transaction_numbers(
    value: str, code_match: tuple[str, str | None, int, int] | None
) -> list[Decimal]:
    excluded: list[tuple[int, int]] = []
    if code_match is not None:
        excluded.append((code_match[2], code_match[3]))
    excluded.extend((match.start(), match.end()) for match in _DATE_RE.finditer(value))
    result: list[Decimal] = []
    for match in _NUMBER_RE.finditer(value):
        if any(start <= match.start() < end for start, end in excluded):
            continue
        token = match.group(0).replace(",", "").rstrip("%")
        try:
            parsed = Decimal(token)
        except InvalidOperation:
            continue
        if not parsed.is_finite() or parsed == 0:
            continue
        # Do not interpret components of an explicit YYYY-MM-DD as values.
        result.append(parsed)
    return result


def _labelled_transaction_number(value: str, labels: Sequence[str]) -> Decimal | None:
    pattern = _labelled_number_pattern(labels)
    match = pattern.search(value)
    if match is None or match.group("value").endswith("%"):
        return None
    try:
        parsed = Decimal(match.group("value").replace(",", "").rstrip("%"))
    except InvalidOperation:
        return None
    return parsed if parsed.is_finite() else None


def _infer_transaction_values(
    values: Sequence[Decimal],
    amount: Decimal | None,
    quantity: Decimal | None,
    price: Decimal | None,
) -> tuple[Decimal, Decimal] | None:
    if quantity is not None and price is not None:
        return quantity, price
    if amount is not None and price is not None and price > 0:
        return amount / price, price
    if len(values) < 2:
        return None
    # Prefer an amount/quantity/price triple when the arithmetic reconciles;
    # Otherwise use the quantity/price pair; unlabelled fees remain unknown.
    if len(values) >= 3:
        for candidate_amount, candidate_quantity, candidate_price in (
            (values[0], values[1], values[2]),
            (values[-3], values[-2], values[-1]),
        ):
            if candidate_quantity > 0 and candidate_price > 0:
                difference = abs(candidate_amount - candidate_quantity * candidate_price)
                scale = max(abs(candidate_amount), Decimal("1"))
                if difference / scale <= Decimal("0.03"):
                    return candidate_quantity, candidate_price
    if len(values) == 3 and values[0] > values[1] and values[1] < Decimal("1000"):
        return (values[0], values[1])
    quantity, price = values[-2], values[-1]
    return (quantity, price) if quantity > 0 and price > 0 else None


def _transaction_datetime(value: str, fallback: date) -> datetime | None:
    date_match = _DATE_RE.search(value)
    selected_date = fallback
    if date_match is not None:
        raw_date = date_match.group(0).replace("/", "-")
        raw_date = re.sub(r"年|月|日", "-", raw_date).strip("-")
        try:
            parsed = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return None
        selected_date = parsed
    time_match = re.search(r"(?<!\d)(\d{1,2}):(\d{2})(?::(\d{2}))?(?!\d)", value)
    if time_match is None:
        selected_time = _SHANGHAI_NOON
    else:
        try:
            selected_time = time(
                int(time_match.group(1)),
                int(time_match.group(2)),
                int(time_match.group(3) or 0),
            )
        except ValueError:
            return None
    return datetime.combine(selected_date, selected_time).replace(tzinfo=_SHANGHAI_TZ)


def _labelled_transaction_id(value: str) -> str | None:
    alternatives = "|".join(re.escape(label) for label in _TRANSACTION_ID_LABELS)
    match = re.search(rf"(?:{alternatives})\s*[:：=]?\s*([A-Za-z0-9_-]+)", value, re.I)
    return match.group(1).strip() if match else None


def _resolve_transaction_symbol(
    hint: PhotoTransactionHint, fund_rows: Sequence[dict[str, object]]
) -> tuple[str, str]:
    if hint.symbol:
        return hint.symbol, hint.name
    if hint.asset_type is not AssetType.FUND:
        raise PhotoImportError(f"交易记录缺少股票代码：{hint.name}")
    row = _match_fund_name_row(fund_rows, hint.name)
    raw_symbol = _fund_raw_symbol(row.get("symbol"))
    if raw_symbol is None:
        raise PhotoImportError(f"外部基金接口未返回有效代码：{hint.name}")
    return raw_symbol, str(row.get("name") or hint.name).strip()


def _ocr_dates(ocr: OcrResult) -> tuple[date, ...]:
    detected = tuple(getattr(ocr, "detected_dates", ()) or ())
    return detected or extract_detected_dates(ocr.lines)


def _date_warnings(selected: date, detected: Sequence[date]) -> tuple[str, ...]:
    if not detected:
        return ()
    warnings: list[str] = []
    if len(detected) > 1:
        warnings.append(
            "图片中识别到多个日期（"
            + "、".join(item.isoformat() for item in detected)
            + "），请确认当前选择的截图对应日期。"
        )
    if selected not in detected:
        warnings.append(f"当前选择日期为{selected.isoformat()}，与OCR识别日期不一致，请人工确认。")
    return tuple(warnings)


def _select_photo_date(as_of_date: date | None, detected_dates: Sequence[date]) -> date:
    if as_of_date is not None:
        if not isinstance(as_of_date, date) or isinstance(as_of_date, datetime):
            raise TypeError("as_of_date must be date")
        return as_of_date
    if len(detected_dates) == 1:
        return detected_dates[0]
    if not detected_dates:
        raise PhotoImportError("无法从图片识别日期，请手动选择截图对应日期")
    raise PhotoImportError("图片中存在多个日期，请手动选择截图对应日期")


__all__ = ["parse_transaction_statement"]
