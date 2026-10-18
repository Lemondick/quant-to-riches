"""OCR field, date, and account-statement parsing."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from quant_assistant.models.entities import AssetType
from quant_assistant.providers.ocr import OcrResult
from quant_assistant.services.account_snapshot import validate_complete_snapshot
from quant_assistant.services.photo_import._models import (
    _AVAILABLE_QUANTITY_LABELS,
    _CASH_VALUE_KEYWORDS,
    _COST_LABELS,
    _DATE_RE,
    _DELIMITERS,
    _FUND_KEYWORDS,
    _HEADER_MARKERS,
    _MARKET_VALUE_LABELS,
    _NUMBER_RE,
    _QUANTITY_LABELS,
    _SUMMARY_IGNORE_MARKERS,
    _SUMMARY_IGNORED_LINES,
    _SUMMARY_NAME_HINTS,
    _SUMMARY_NUMBER_RE,
    _SUMMARY_SUFFIX_RE,
    _TRANSACTION_ACTIONS,
    _TRANSACTION_IGNORE_MARKERS,
    FundHoldingHint,
    FundHoldingsSummary,
    PhotoImportError,
    _ParsedAccount,
)
from quant_assistant.services.photo_import._parsing_primitives import (
    _asset_type,
    _first_code,
    _is_cash_line,
    _labelled_number_pattern,
    _normalise_line,
    _normalise_symbol,
    _numeric_only,
)
from quant_assistant.services.portfolio_commands import PositionInput


def parse_fund_holdings_summary(ocr: OcrResult, as_of_date: date) -> FundHoldingsSummary:
    """Parse Yangjibao/Alipay-style fund summary rows without inventing cash.

    Summary pages expose market value, yesterday's P&L, holding P&L and a return
    percentage, but not units, cost basis, or account cash.  Only rows whose
    numbers reconcile to the displayed return percentage are accepted.
    """

    if not isinstance(ocr, OcrResult):
        raise TypeError("ocr must be OcrResult")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be date")
    lines = tuple(_normalise_line(line) for line in ocr.lines if _normalise_line(line))
    if not _looks_like_fund_summary(lines):
        return FundHoldingsSummary((), (), 0.0)
    starts = _summary_row_starts(lines)
    if not starts:
        raise PhotoImportError("图片中未识别到完整的基金摘要持仓行，请保留名称、金额和收益率后重试")

    holdings: list[FundHoldingHint] = []
    warnings: list[str] = []
    for start_index, next_start in zip(starts, (*starts[1:], len(lines)), strict=True):
        hint, row_warnings = _parse_summary_row(lines[start_index:next_start])
        if hint is None:
            warnings.extend(row_warnings)
            continue
        holdings.append(hint)
        warnings.extend(row_warnings)
    if not holdings:
        details = "；".join(tuple(dict.fromkeys(warnings))[:3])
        suffix = f"：{details}" if details else ""
        raise PhotoImportError(f"图片中的基金摘要行无法安全推导{suffix}")
    confidence = min(0.83, 0.55 + 0.05 * min(len(holdings), 5))
    if warnings:
        confidence = max(0.4, confidence - 0.05 * min(len(warnings), 4))
    return FundHoldingsSummary(
        tuple(holdings),
        tuple(dict.fromkeys(warnings)),
        round(confidence, 2),
    )


def _looks_like_fund_summary(lines: Sequence[str]) -> bool:
    text = " ".join(lines)
    return "金额" in text and "持有收益" in text


def _summary_row_starts(lines: Sequence[str]) -> tuple[int, ...]:
    starts: list[int] = []
    for index, line in enumerate(lines):
        if not _is_summary_name_fragment(line):
            continue
        if _summary_has_unsigned_amount(line) or _summary_has_unsigned_amount_before_signed(
            lines, index
        ):
            starts.append(index)
    return tuple(starts)


def _is_summary_name_fragment(line: str) -> bool:
    cleaned = line.strip(" •●·")
    if not cleaned or cleaned in _SUMMARY_IGNORED_LINES:
        return False
    if any(marker in cleaned for marker in _SUMMARY_IGNORE_MARKERS):
        return False
    if _SUMMARY_SUFFIX_RE.fullmatch(cleaned):
        return False
    if _numeric_only(cleaned):
        return False
    upper = cleaned.upper()
    if any(keyword in upper for keyword in _FUND_KEYWORDS):
        return True
    return len(re.findall(r"[\u4e00-\u9fff]", cleaned)) >= 4 and any(
        marker in upper for marker in _SUMMARY_NAME_HINTS
    )


def _summary_has_unsigned_amount_before_signed(lines: Sequence[str], start: int) -> bool:
    for line in lines[start + 1 : start + 8]:
        tokens = _summary_tokens(line)
        if not tokens:
            if _is_summary_name_fragment(line) and line != lines[start]:
                continue
            if line in _SUMMARY_IGNORED_LINES:
                continue
            continue
        if _summary_has_unsigned_amount(line):
            return True
        if any(signed or percent for _, signed, percent in tokens):
            return False
    return False


def _summary_has_unsigned_amount(line: str) -> bool:
    return any(
        not signed and not percent and value > 0 for value, signed, percent in _summary_tokens(line)
    )


def _parse_summary_row(lines: Sequence[str]) -> tuple[FundHoldingHint | None, tuple[str, ...]]:
    amount_location: tuple[int, int] | None = None
    amount: Decimal | None = None
    for line_index, line in enumerate(lines):
        tokens = _summary_tokens(line)
        if amount is None:
            for value, signed, percent in tokens:
                if not signed and not percent and value > 0:
                    amount = value
                    match = _SUMMARY_NUMBER_RE.search(line)
                    if match is not None:
                        amount_location = (line_index, match.start())
                    break
    if amount is None:
        return None, ("基金摘要行缺少明确的持有金额",)

    name_parts: list[str] = []
    amount_line_index = amount_location[0] if amount_location else len(lines)
    for index, line in enumerate(lines[: amount_line_index + 1]):
        fragment = line
        if index == amount_line_index and amount_location is not None:
            fragment = line[: amount_location[1]]
        fragment = _clean_summary_name_fragment(fragment)
        if fragment:
            name_parts.append(fragment)
    for line in lines[amount_line_index + 1 :]:
        if _SUMMARY_SUFFIX_RE.fullmatch(line.strip()):
            name_parts.append(line.strip())
        elif _is_summary_name_fragment(line) and not _summary_tokens(line):
            fragment = _clean_summary_name_fragment(line)
            if fragment:
                name_parts.append(fragment)
    name = "".join(name_parts)
    if not name:
        return None, ("基金摘要行缺少明确名称",)

    rate_tokens = [value for value, _, percent in _summary_tokens(" ".join(lines)) if percent]
    displayed_rate = rate_tokens[-1] / Decimal("100") if rate_tokens else None
    profit_candidates = [
        value
        for value, signed, percent in _summary_tokens(" ".join(lines))
        if not percent and value != amount and signed
    ]
    if not profit_candidates:
        profit_candidates = [
            value
            for value, signed, percent in _summary_tokens(" ".join(lines))
            if not percent and value != amount
        ]
    if not profit_candidates:
        return None, (f"基金 {name} 缺少持有收益",)

    selected_profit: Decimal | None = None
    if displayed_rate is not None:
        scored: list[tuple[Decimal, Decimal]] = []
        for candidate in profit_candidates:
            cost_basis = amount - candidate
            if cost_basis <= 0:
                continue
            difference = abs(candidate / cost_basis - displayed_rate)
            scored.append((difference, candidate))
        if not scored:
            return None, (f"基金 {name} 的收益无法形成正成本金额",)
        scored.sort(key=lambda item: item[0])
        # Rates normally display two decimal percentage places. Allow 0.05
        # percentage points for rounding, not a 2.5-point discrepancy that can
        # accidentally select yesterday's profit as the holding profit.
        tolerance = Decimal("0.0005")
        if scored[0][0] > tolerance:
            return None, (f"基金 {name} 的持有收益与收益率不一致",)
        if len(scored) > 1 and scored[1][0] - scored[0][0] < Decimal("0.0005"):
            return None, (f"基金 {name} 的持有收益列不明确",)
        selected_profit = scored[0][1]
    else:
        return None, (f"基金 {name} 缺少收益率，无法核对持有收益列",)

    assert selected_profit is not None
    cost_basis = amount - selected_profit
    if cost_basis <= 0:
        return None, (f"基金 {name} 推导出的成本金额必须为正",)
    return FundHoldingHint(name, amount, selected_profit, displayed_rate), ()


def _clean_summary_name_fragment(line: str) -> str:
    cleaned = line.strip(" •●·")
    if not cleaned or cleaned in _SUMMARY_IGNORED_LINES:
        return ""
    if any(marker in cleaned for marker in _SUMMARY_IGNORE_MARKERS):
        return ""
    if _SUMMARY_SUFFIX_RE.fullmatch(cleaned):
        return cleaned
    return cleaned.strip(_DELIMITERS)


def _summary_tokens(value: str) -> list[tuple[Decimal, bool, bool]]:
    result: list[tuple[Decimal, bool, bool]] = []
    for match in _SUMMARY_NUMBER_RE.finditer(value):
        token = match.group("number").replace(",", "")
        try:
            parsed = Decimal(token)
        except InvalidOperation:
            continue
        if not parsed.is_finite():
            continue
        if match.group("sign") == "-":
            parsed = -parsed
        result.append((parsed, bool(match.group("sign")), bool(match.group("percent"))))
    return result


def parse_fund_name_lines(ocr: OcrResult) -> tuple[str, ...]:
    """Extract fund-name candidates from a crop without requiring account values."""

    if not isinstance(ocr, OcrResult):
        raise TypeError("ocr must be OcrResult")
    lines = tuple(_normalise_line(line) for line in ocr.lines if _normalise_line(line))
    names: list[str] = []
    for index, line in enumerate(lines):
        if _DATE_RE.search(line) or any(action in line for action in _TRANSACTION_ACTIONS):
            continue
        candidate_line = _DATE_RE.sub(" ", line)
        for action_label in _TRANSACTION_ACTIONS:
            candidate_line = candidate_line.replace(action_label, " ")
        if _is_name_lookup_ignored(candidate_line):
            continue
        code_match = _first_code(candidate_line)
        candidate = candidate_line
        if code_match is not None:
            candidate = (line[: code_match[2]] + line[code_match[3] :]).strip(_DELIMITERS)
        candidate = _clean_name_lookup_fragment(candidate)
        if not candidate or not _looks_like_fund_name(candidate):
            continue
        # OCR often places ``(LOF)C`` on a separate line.  Keep it attached to
        # the preceding name so share-class matching remains deterministic.
        if _SUMMARY_SUFFIX_RE.fullmatch(candidate) and names:
            names[-1] = f"{names[-1]}{candidate}"
            continue
        if index + 1 < len(lines) and _SUMMARY_SUFFIX_RE.fullmatch(lines[index + 1].strip()):
            candidate = f"{candidate}{lines[index + 1].strip()}"
        if candidate not in names:
            names.append(candidate)
    return tuple(names)


def _is_name_lookup_ignored(line: str) -> bool:
    cleaned = line.strip(" •●·")
    if not cleaned or cleaned in _SUMMARY_IGNORED_LINES:
        return True
    if any(
        marker in cleaned for marker in (*_SUMMARY_IGNORE_MARKERS, *_TRANSACTION_IGNORE_MARKERS)
    ):
        return True
    if _numeric_only(cleaned) or _DATE_RE.search(cleaned):
        return True
    return any(label in cleaned for label in _HEADER_MARKERS)


def _clean_name_lookup_fragment(value: str) -> str:
    cleaned = re.sub(r"[|,:：;；]+", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(_DELIMITERS)
    return cleaned


def _looks_like_fund_name(value: str) -> bool:
    upper = value.upper()
    if not any(keyword in upper for keyword in _FUND_KEYWORDS):
        return False
    # A name-only crop may contain a small amount of UI text.  Requiring at
    # least two CJK characters or a six-digit code prevents it becoming a
    # broad fuzzy-search endpoint for arbitrary text.
    return len(re.findall(r"[\u4e00-\u9fff]", value)) >= 2 or _first_code(value) is not None


def parse_account_statement(
    ocr: OcrResult, as_of_date: date, *, holdings_only: bool = False
) -> _ParsedAccount:
    """Parse common broker/fund statement OCR lines conservatively.

    The parser intentionally requires a cash line and at least three numeric values
    per security row.  It uses explicit row labels or nearby table headers when
    available, and refuses ambiguous column layouts instead of guessing a cost or
    market value.
    """

    if not isinstance(ocr, OcrResult):
        raise TypeError("ocr must be OcrResult")
    if not isinstance(as_of_date, date):
        raise TypeError("as_of_date must be date")
    lines = tuple(_normalise_line(line) for line in ocr.lines if _normalise_line(line))
    header_context = _header_context(lines)
    cash_value = _cash_value(lines)
    candidates: dict[str, PositionInput] = {}
    warnings: list[str] = []
    row_failures: list[str] = []
    for line in _row_lines(lines):
        if _is_cash_line(line):
            continue
        match = _first_code(line)
        if match is None:
            continue
        code, suffix, code_start, code_end = match
        if _DATE_RE.search(line):
            continue
        after = line[code_end:]
        before = line[:code_start]
        values = _numbers(after)
        if len(values) < 3:
            row_failures.append(f"{code}行缺少数量、成本或市值")
            continue
        first_number = _NUMBER_RE.search(after)
        name = (after[: first_number.start()] if first_number else "").strip(_DELIMITERS)
        if name in (*_QUANTITY_LABELS, *_AVAILABLE_QUANTITY_LABELS):
            name = ""
        if not name:
            name = before.strip(_DELIMITERS).split()[-1] if before.strip(_DELIMITERS) else code
        asset_type = _asset_type(code, suffix, name)
        symbol = _normalise_symbol(code, suffix, asset_type)
        extracted = _extract_position_values(line, values, asset_type, header_context)
        if extracted is None:
            row_failures.append(f"{symbol}行的数量、成本和市值列不明确")
            continue
        quantity, average_cost, market_value = extracted
        if quantity <= 0 or average_cost <= 0 or market_value <= 0:
            row_failures.append(f"{symbol}行的数量、成本和市值必须为正")
            continue
        item = PositionInput(symbol, asset_type, quantity, average_cost, market_value, as_of_date)
        previous = candidates.get(symbol)
        if previous is not None and previous != item:
            row_failures.append(f"{symbol}重复行的数量、成本或市值不一致")
        elif previous is None:
            candidates[symbol] = item

    if cash_value is None and holdings_only:
        if (
            row_failures
            or not candidates
            or any(p.asset_type is not AssetType.FUND for p in candidates.values())
        ):
            raise PhotoImportError("基金持仓行未完整识别，请补充包含份额、成本、市值的截图")
        return _ParsedAccount(
            tuple(sorted(candidates.values(), key=lambda item: item.symbol)), tuple(warnings), 0.8
        )
    if cash_value is None:
        raise PhotoImportError("图片中未识别到明确现金余额，请上传包含可用资金的完整账户截图")
    if cash_value < 0:
        raise PhotoImportError("识别到的现金余额不能为负数")
    if row_failures:
        details = "；".join(tuple(dict.fromkeys(row_failures))[:4])
        raise PhotoImportError(f"图片中的持仓行未完整识别：{details}，请保留表头后重试")
    cash = PositionInput(
        "CASH.CNY",
        AssetType.CASH,
        cash_value,
        Decimal("1"),
        cash_value,
        as_of_date,
    )
    rows = tuple(sorted((*candidates.values(), cash), key=lambda item: item.symbol))
    try:
        validate_complete_snapshot(rows)
    except (TypeError, ValueError) as exc:
        raise PhotoImportError(f"图片未形成完整账户快照：{exc}") from exc
    if not candidates:
        warnings.append("未识别到证券或基金持仓，仅识别到现金")
    confidence = min(0.94, 0.62 + 0.04 * min(len(candidates), 5))
    if warnings:
        confidence = max(0.35, confidence - 0.06 * min(len(warnings), 4))
    return _ParsedAccount(rows, tuple(dict.fromkeys(warnings)), round(confidence, 2))


def _row_lines(lines: Sequence[str]) -> tuple[str, ...]:
    """Join cell-level OCR fragments until a security row has enough numbers."""

    result: list[str] = []
    consumed: set[int] = set()
    for index, line in enumerate(lines):
        if index in consumed:
            continue
        match = _first_code(line)
        if (
            match is None
            or (_numeric_only(line) and index > 0 and _is_cash_line(lines[index - 1]))
            or (_numeric_only(line) and not _looks_like_standalone_code(lines, index))
            or _is_cash_line(line)
        ):
            continue
        _, _, _, code_end = match
        combined = line
        if len(_numbers(line[code_end:])) < 3:
            for following_index, following in enumerate(
                lines[index + 1 : index + 8], start=index + 1
            ):
                if _is_cash_line(following):
                    break
                if _first_code(following) is not None and (
                    not _numeric_only(following)
                    or _looks_like_standalone_code(lines, following_index)
                ):
                    break
                combined = f"{combined} {following}"
                consumed.add(following_index)
        consumed.add(index)
        result.append(combined)
    return tuple(result)


def _cash_value(lines: Sequence[str]) -> Decimal | None:
    for index, line in enumerate(lines):
        keyword = next((item for item in _CASH_VALUE_KEYWORDS if item in line), None)
        if keyword is None or not _is_cash_line(line):
            continue
        values = _numbers(line[line.find(keyword) + len(keyword) :])
        if not values:
            for following in lines[index + 1 : index + 4]:
                if _numeric_only(following) and _looks_like_standalone_code(lines, index + 1):
                    break
                if _first_code(following) is not None and not _numeric_only(following):
                    break
                values.extend(_numbers(following))
                if values:
                    break
        if len(values) == 1:
            return values[0]
        if len(values) > 1:
            raise PhotoImportError("现金行包含多个金额，请明确可用现金余额后重试")
    return None


def _numbers(value: str) -> list[Decimal]:
    result: list[Decimal] = []
    for match in _NUMBER_RE.finditer(value):
        token = match.group(0).replace(",", "").rstrip("%")
        try:
            parsed = Decimal(token)
        except InvalidOperation:
            continue
        if parsed.is_finite():
            result.append(parsed)
    return result


def _header_context(lines: Sequence[str]) -> str:
    """Collect table-header hints without treating data rows as headers."""

    headers = [
        line
        for line in lines
        if _first_code(line) is None and any(marker in line for marker in _HEADER_MARKERS)
    ]
    return " ".join(headers)


def _extract_position_values(
    line: str,
    values: Sequence[Decimal],
    asset_type: AssetType,
    header_context: str,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """Extract quantity, cost, and market value from one OCR row.

    Explicit labels win.  For unlabelled table cells, the nearby header determines
    whether an available-quantity column or a profit column is present.  A four- or
    five-number stock row without enough evidence is deliberately rejected.
    """

    if len(values) < 3:
        return None
    labelled = _labelled_values(line)
    quantity = labelled.get("quantity", values[0])
    market_value = labelled.get("market_value", values[-1])
    cost = labelled.get("cost")
    if cost is None:
        cost_index = _cost_index(values, asset_type, header_context)
        if cost_index is None or cost_index >= len(values):
            return None
        cost = values[cost_index]
    return quantity, cost, market_value


def _labelled_values(line: str) -> dict[str, Decimal]:
    """Read values written as ``数量 100``/``市值=1025`` in OCR text."""

    patterns = {
        "quantity": _labelled_number_pattern((*_QUANTITY_LABELS, *_AVAILABLE_QUANTITY_LABELS)),
        "cost": _labelled_number_pattern(_COST_LABELS),
        "market_value": _labelled_number_pattern(_MARKET_VALUE_LABELS),
    }
    result: dict[str, Decimal] = {}
    for field, pattern in patterns.items():
        match = pattern.search(line)
        if match is None:
            continue
        token = match.group("value").replace(",", "").rstrip("%")
        try:
            value = Decimal(token)
        except InvalidOperation:
            continue
        if value.is_finite():
            result[field] = value
    return result


def _cost_index(
    values: Sequence[Decimal], asset_type: AssetType, header_context: str
) -> int | None:
    if asset_type is AssetType.FUND:
        return 1
    if len(values) == 3:
        return 1
    if "可用" in header_context or "股票余额" in header_context:
        return 2 if len(values) >= 4 else 1
    if "盈亏" in header_context and not any(
        marker in header_context for marker in ("可用", "股票余额")
    ):
        return 1
    if len(values) >= 6:
        return 2
    if len(values) in {4, 5}:
        # With no header, distinguish the common quantity/available/cost layout
        # from quantity/cost/current-price by the integer share-count column.  If
        # both candidates look alike, refuse the row instead of importing a wrong cost.
        first_candidate = values[1]
        second_candidate = values[2]
        if first_candidate == first_candidate.to_integral_value() and (
            second_candidate != second_candidate.to_integral_value()
        ):
            return 2
        if first_candidate != first_candidate.to_integral_value() and (
            second_candidate == second_candidate.to_integral_value()
        ):
            return 1
    return None


def _looks_like_standalone_code(lines: Sequence[str], index: int) -> bool:
    value = lines[index].strip() if 0 <= index < len(lines) else ""
    if not re.fullmatch(r"\d{6}", value):
        return False
    if index + 1 >= len(lines):
        return False
    following = lines[index + 1].strip()
    if not following or _numeric_only(following):
        return False
    return not any(marker in following for marker in _HEADER_MARKERS)
    return 1


def _provider_date(value: object) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    date_method = getattr(value, "date", None)
    if callable(date_method):
        converted = date_method()
        if isinstance(converted, date):
            return converted
    text = str(value or "").strip().replace("/", "-")
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _provider_decimal(value: object) -> Decimal | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"nan", "nat", "none", "<na>"}:
        return None
    try:
        result = Decimal(text.replace(",", ""))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


__all__ = [
    "parse_fund_holdings_summary",
    "parse_fund_name_lines",
    "parse_account_statement",
]
