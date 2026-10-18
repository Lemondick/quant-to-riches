"""Review, confirmation, and ledger orchestration for photo import."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import AssetType
from quant_assistant.models.repositories.database import Database
from quant_assistant.providers.ocr import (
    OcrError,
    OcrProvider,
    OcrResult,
    create_ocr_provider_from_env,
)
from quant_assistant.services.account_snapshot import validate_complete_snapshot
from quant_assistant.services.portfolio_commands import (
    InstrumentInput,
    PositionInput,
    TransactionInput,
)

from ._models import (
    _SUMMARY_QUANTUM,
    FundHoldingHint,
    FundHoldingInference,
    FundHoldingsSummary,
    FundNameLookupPreview,
    FundNameMatch,
    MarketInstrumentProvider,
    PhotoImportError,
    PhotoTransactionPreview,
)
from ._transaction_parsing import (
    _date_warnings,
    _ocr_dates,
    _resolve_transaction_symbol,
    _select_photo_date,
    parse_transaction_statement,
)
from .code_matching import (
    _fund_name_score,
    _fund_nav_as_of,
    _fund_raw_symbol,
    _fund_rows,
    _fund_share_class,
    _match_fund_metadata,
    _match_fund_name_row,
    _match_stock_metadata,
)
from .ocr_parsing import (
    _cash_value,
    parse_account_statement,
    parse_fund_holdings_summary,
    parse_fund_name_lines,
)


@dataclass(frozen=True, slots=True)
class PhotoImportPreview:
    """Non-persistent review object produced before the user confirms an import."""

    as_of_date: date
    provider: str
    image_sha256: str
    positions: tuple[PositionInput, ...]
    instrument_metadata: tuple[InstrumentInput, ...]
    ocr_lines: tuple[str, ...]
    warnings: tuple[str, ...]
    confidence: float
    requires_cash: bool = False
    inferences: tuple[FundHoldingInference, ...] = ()
    detected_dates: tuple[date, ...] = ()

    def with_positions(self, positions: tuple[PositionInput, ...]) -> PhotoImportPreview:
        """Return a review copy after a user-approved row edit."""

        if not isinstance(positions, tuple):
            raise TypeError("positions must be tuple")
        return replace_photo_positions(self, positions)


class PhotoAccountImportService:
    """Turn an account screenshot into a reviewable, externally verified snapshot."""

    def __init__(
        self,
        database: Database,
        ocr_provider: OcrProvider,
        market_provider: MarketInstrumentProvider | None = None,
    ) -> None:
        if not isinstance(database, Database):
            raise TypeError("database must be Database")
        self._database = database
        self._ocr_provider = ocr_provider
        self._market_provider = market_provider

    @classmethod
    def from_env(
        cls,
        database: Database,
        *,
        market_provider: MarketInstrumentProvider | None = None,
    ) -> PhotoAccountImportService:
        if market_provider is None:
            # Keep photo verification on the same bounded failover boundary as
            # history/bootstrap jobs.  Import lazily to avoid a composition
            # root cycle during application startup.
            from quant_assistant.services.composition import build_market_data_provider

            market_provider = build_market_data_provider()
        return cls(
            database,
            create_ocr_provider_from_env(),
            market_provider=market_provider,
        )

    def preview(
        self,
        image: bytes,
        *,
        filename: str,
        as_of_date: date | None = None,
        holdings_only: bool = False,
    ) -> PhotoImportPreview:
        try:
            ocr = self._ocr_provider.recognize(image, filename=filename)
        except OcrError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise PhotoImportError("图片识别失败") from exc
        detected_dates = _ocr_dates(ocr)
        date_warning = ()
        if holdings_only and as_of_date is None and len(detected_dates) != 1:
            selected_date = datetime.now(SHANGHAI_TZ).date()
            date_warning = ("截图日期不唯一或未显示，暂按今天记录；请核对截图日期。",)
        else:
            selected_date = _select_photo_date(as_of_date, detected_dates)
        if selected_date > datetime.now(SHANGHAI_TZ).date():
            raise PhotoImportError("截图日期不能晚于今天")
        summary = parse_fund_holdings_summary(ocr, selected_date)
        if summary.holdings:
            result = self._preview_summary(ocr, summary, selected_date, detected_dates)
            return replace(result, warnings=(*result.warnings, *date_warning))
        parsed = parse_account_statement(ocr, selected_date, holdings_only=holdings_only)
        metadata = self._resolve_instruments(parsed.positions, selected_date)
        return PhotoImportPreview(
            as_of_date=selected_date,
            provider=ocr.provider,
            image_sha256=ocr.image_sha256,
            positions=parsed.positions,
            instrument_metadata=metadata,
            ocr_lines=ocr.lines,
            warnings=tuple(
                dict.fromkeys(
                    (
                        *parsed.warnings,
                        *_date_warnings(selected_date, detected_dates),
                        *date_warning,
                    )
                )
            ),
            confidence=parsed.confidence,
            detected_dates=detected_dates,
        )

    def lookup_fund_names(
        self,
        image: bytes,
        *,
        filename: str = "fund-name.png",
    ) -> FundNameLookupPreview:
        """Resolve names from a crop without pretending it is an account snapshot."""

        try:
            ocr = self._ocr_provider.recognize(image, filename=filename)
        except OcrError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise PhotoImportError("图片识别失败") from exc
        names = parse_fund_name_lines(ocr)
        if not names:
            raise PhotoImportError("图片中未识别到基金名称")
        provider = self._market_provider
        if provider is None:
            raise PhotoImportError("基金名称查码需要配置外部基金列表接口")
        try:
            rows = _fund_rows(provider.fetch_fund_daily())
        except Exception as exc:
            raise PhotoImportError("外部基金列表接口调用失败，请稍后重试") from exc
        if not rows:
            raise PhotoImportError("外部基金列表为空，无法通过名称匹配基金代码")
        matches: list[FundNameMatch] = []
        warnings: list[str] = []
        for name in names:
            row = _match_fund_name_row(rows, name)
            raw_symbol = _fund_raw_symbol(row.get("symbol"))
            if raw_symbol is None:
                warnings.append(f"{name}的外部代码无效")
                continue
            share_class = _fund_share_class(name)
            symbol = raw_symbol
            matched_name = str(row.get("name") or name).strip()
            score = _fund_name_score(name, matched_name)
            matches.append(FundNameMatch(name, symbol, matched_name, share_class, round(score, 4)))
        if not matches:
            raise PhotoImportError("未能安全匹配基金代码")
        if len(matches) != len(names):
            warnings.append("部分名称未能匹配，未匹配项不会被导入")
        confidence = min(0.96, max(item.score for item in matches))
        return FundNameLookupPreview(
            provider=ocr.provider,
            image_sha256=ocr.image_sha256,
            matches=tuple(matches),
            ocr_lines=ocr.lines,
            detected_dates=_ocr_dates(ocr),
            warnings=tuple(dict.fromkeys(warnings)),
            confidence=round(confidence, 2),
        )

    def preview_transactions(
        self,
        image: bytes,
        *,
        filename: str,
        as_of_date: date | None = None,
    ) -> PhotoTransactionPreview:
        """Parse transaction-record screenshots for manual, batch review."""

        try:
            ocr = self._ocr_provider.recognize(image, filename=filename)
        except OcrError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise PhotoImportError("图片识别失败") from exc
        detected_dates = _ocr_dates(ocr)
        selected_date = _select_photo_date(as_of_date, detected_dates)
        hints = parse_transaction_statement(ocr, selected_date)
        if not hints:
            raise PhotoImportError("图片中未识别到完整交易记录，请保留日期、动作、数量和价格")
        missing_fees = [str(index) for index, hint in enumerate(hints, 1) if hint.fees is None]
        if missing_fees:
            raise PhotoImportError(
                f"第{'、'.join(missing_fees)}笔交易费用未识别，不能按0入账；"
                "请保留明确费用栏重新识别，或在账本中核对后手动录入（免收也须填写0）"
            )
        metadata: list[InstrumentInput] = []
        transactions: list[TransactionInput] = []
        provider = self._market_provider
        fund_rows: list[dict[str, object]] = []
        if any(item.symbol is None for item in hints):
            if provider is None:
                raise PhotoImportError("名称交易记录查码需要配置外部行情接口")
            try:
                fund_rows = _fund_rows(provider.fetch_fund_daily())
            except Exception as exc:
                raise PhotoImportError("外部基金列表接口调用失败，请稍后重试") from exc
        for index, hint in enumerate(hints, start=1):
            assert hint.fees is not None
            symbol, name = _resolve_transaction_symbol(hint, fund_rows)
            if hint.asset_type is AssetType.STOCK and hint.symbol is None:
                raise PhotoImportError(f"股票 {hint.name} 缺少代码，无法安全导入")
            if provider is not None and symbol not in self._known_instruments():
                try:
                    if hint.asset_type is AssetType.STOCK:
                        resolved_metadata = _match_stock_metadata(
                            provider.fetch_stock_universe(), {symbol}, selected_date
                        )
                    else:
                        resolved_metadata = _match_fund_metadata(
                            provider.fetch_fund_daily(), {symbol}, selected_date
                        )
                    metadata.extend(resolved_metadata)
                except Exception as exc:
                    raise PhotoImportError(f"外部行情未能校验交易标的：{symbol}") from exc
            external_id = hint.external_id or f"photo-tx:{ocr.image_sha256[:16]}:{index:03d}"
            transactions.append(
                TransactionInput(
                    symbol=symbol,
                    asset_type=hint.asset_type,
                    action=hint.action,
                    quantity=hint.quantity,
                    price=hint.price,
                    fees=hint.fees,
                    traded_at=hint.traded_at,
                    external_id=external_id,
                    source="photo",
                    note=hint.note or f"交易截图识别：{name}",
                )
            )
        warnings = [
            "交易截图只生成待复核成交记录；确认前不会写入本地账本，也不会向任何账户下单。",
            "成交编号缺失时使用图片指纹和行号生成幂等编号；如同一截图重复识别，仍会阻止重复入账。",
            *_date_warnings(selected_date, detected_dates),
        ]
        return PhotoTransactionPreview(
            as_of_date=selected_date,
            provider=ocr.provider,
            image_sha256=ocr.image_sha256,
            transactions=tuple(transactions),
            instrument_metadata=tuple(
                sorted(
                    {item.symbol: item for item in metadata}.values(),
                    key=lambda item: item.symbol,
                )
            ),
            ocr_lines=ocr.lines,
            detected_dates=detected_dates,
            warnings=tuple(dict.fromkeys(warnings)),
            confidence=round(min(0.92, 0.62 + 0.05 * min(len(transactions), 5)), 2),
        )

    def _preview_summary(
        self,
        ocr: OcrResult,
        summary: FundHoldingsSummary,
        as_of_date: date,
        detected_dates: tuple[date, ...] = (),
    ) -> PhotoImportPreview:
        if self._market_provider is None:
            raise PhotoImportError("基金摘要识别需要配置外部行情接口来匹配基金代码和历史净值")
        try:
            positions, external_metadata, inferences = self._infer_summary_positions(
                summary.holdings, as_of_date
            )
        except PhotoImportError:
            raise
        except Exception as exc:
            raise PhotoImportError("外部基金代码或历史净值校验失败，请稍后重试") from exc

        known = self._known_instruments()
        metadata = [item for item in external_metadata if item.symbol not in known]
        if "CASH.CNY" not in known:
            metadata.append(
                InstrumentInput("CASH.CNY", AssetType.CASH, "人民币现金", "LOCAL", as_of_date)
            )
        cash_value = _cash_value(ocr.lines)
        if cash_value is not None:
            if cash_value < 0:
                raise PhotoImportError("截图现金余额不能为负数")
            positions += (
                PositionInput(
                    "CASH.CNY", AssetType.CASH, cash_value, Decimal(1), cash_value, as_of_date
                ),
            )
        warnings = list(summary.warnings)
        warnings.append(
            "基金代码由名称匹配；份额及成本按截图金额、持有收益和历史净值推导，请核对。"
        )
        if cash_value is None:
            warnings.append("截图未包含现金，可先保存基金持仓；完整账户基线仍待核对现金。")
        warnings.extend(_date_warnings(as_of_date, detected_dates))
        confidence = min(summary.confidence, 0.86)
        return PhotoImportPreview(
            as_of_date=as_of_date,
            provider=ocr.provider,
            image_sha256=ocr.image_sha256,
            positions=positions,
            instrument_metadata=tuple(sorted(metadata, key=lambda item: item.symbol)),
            ocr_lines=ocr.lines,
            warnings=tuple(dict.fromkeys(warnings)),
            confidence=round(confidence, 2),
            requires_cash=cash_value is None,
            inferences=inferences,
            detected_dates=detected_dates,
        )

    def _infer_summary_positions(
        self,
        holdings: tuple[FundHoldingHint, ...],
        as_of_date: date,
    ) -> tuple[
        tuple[PositionInput, ...], tuple[InstrumentInput, ...], tuple[FundHoldingInference, ...]
    ]:
        provider = self._market_provider
        if provider is None:
            raise PhotoImportError("基金摘要识别需要外部行情接口")
        try:
            daily = provider.fetch_fund_daily()
        except Exception as exc:
            raise PhotoImportError("外部基金列表接口调用失败，请稍后重试") from exc
        rows = _fund_rows(daily)
        if not rows:
            raise PhotoImportError("外部基金列表为空，无法通过名称匹配基金代码")

        positions: list[PositionInput] = []
        metadata: list[InstrumentInput] = []
        inferences: list[FundHoldingInference] = []
        nav_cache: dict[str, tuple[Decimal, date] | None] = {}
        seen_symbols: set[str] = set()
        for hint in holdings:
            row = _match_fund_name_row(rows, hint.name)
            raw_symbol = _fund_raw_symbol(row.get("symbol"))
            if raw_symbol is None:
                raise PhotoImportError(f"外部基金接口未返回有效代码：{hint.name}")
            symbol = raw_symbol
            if symbol in seen_symbols:
                raise PhotoImportError(f"截图中基金重复或名称匹配到同一代码：{symbol}")
            seen_symbols.add(symbol)
            cached_nav = nav_cache.get(raw_symbol)
            if raw_symbol not in nav_cache:
                cached_nav = _fund_nav_as_of(provider, raw_symbol, row, as_of_date)
                nav_cache[raw_symbol] = cached_nav
            if cached_nav is None:
                raise PhotoImportError(
                    f"基金 {hint.name} 没有不晚于 {as_of_date.isoformat()} 的有效单位净值"
                )
            unit_nav, nav_date = cached_nav
            cost_basis = hint.cost_basis
            if cost_basis <= 0:
                raise PhotoImportError(f"基金 {hint.name} 推导出的成本金额必须为正")
            quantity = (hint.market_value / unit_nav).quantize(_SUMMARY_QUANTUM)
            average_cost = (cost_basis / quantity).quantize(_SUMMARY_QUANTUM)
            if quantity <= 0 or average_cost <= 0:
                raise PhotoImportError(f"基金 {hint.name} 推导出的份额或平均成本无效")
            positions.append(
                PositionInput(
                    symbol,
                    AssetType.FUND,
                    quantity,
                    average_cost,
                    hint.market_value,
                    as_of_date,
                )
            )
            raw_name = row.get("name")
            name = str(raw_name).strip() if raw_name is not None else ""
            name = name or hint.name
            metadata.append(InstrumentInput(symbol, AssetType.FUND, name, "OTC", as_of_date))
            inferences.append(
                FundHoldingInference(
                    name=name,
                    symbol=symbol,
                    market_value=hint.market_value,
                    holding_profit=hint.holding_profit,
                    cost_basis=cost_basis,
                    quantity=quantity,
                    average_cost=average_cost,
                    unit_nav=unit_nav,
                    nav_date=nav_date,
                )
            )
        return (
            tuple(sorted(positions, key=lambda item: item.symbol)),
            tuple(sorted(metadata, key=lambda item: item.symbol)),
            tuple(sorted(inferences, key=lambda item: item.symbol)),
        )

    def resolve_review_instruments(self, positions, as_of_date):
        """Verify user-edited fund codes and return authoritative display names."""
        metadata = {p.symbol: p for p in self._resolve_instruments(positions, as_of_date)}
        known = self._database.query_df("SELECT symbol, asset_type, name FROM instruments")
        for row in known.itertuples(index=False):
            if row.symbol in {p.symbol for p in positions}:
                if row.asset_type != "fund":
                    raise ValueError(f"{row.symbol}不是基金")
                metadata[row.symbol] = InstrumentInput(
                    row.symbol, AssetType.FUND, row.name, None, as_of_date
                )
        return tuple(metadata.values())

    def _resolve_instruments(
        self,
        positions: tuple[PositionInput, ...],
        as_of_date: date,
    ) -> tuple[InstrumentInput, ...]:
        known = self._known_instruments()
        missing = {
            item.symbol: item.asset_type
            for item in positions
            if item.asset_type is not AssetType.CASH and item.symbol not in known
        }
        cash_missing = "CASH.CNY" not in known
        if not missing and not cash_missing:
            return ()
        if missing and self._market_provider is None:
            raise PhotoImportError("本地未登记截图中的标的，且未配置外部行情校验")

        metadata: list[InstrumentInput] = []
        stock_symbols = {symbol for symbol, kind in missing.items() if kind is AssetType.STOCK}
        fund_symbols = {symbol for symbol, kind in missing.items() if kind is AssetType.FUND}
        if self._market_provider is not None:
            try:
                if stock_symbols:
                    frame = self._market_provider.fetch_stock_universe()
                    metadata.extend(_match_stock_metadata(frame, stock_symbols, as_of_date))
                if fund_symbols:
                    frame = self._market_provider.fetch_fund_daily()
                    metadata.extend(_match_fund_metadata(frame, fund_symbols, as_of_date))
            except Exception as exc:
                raise PhotoImportError("外部行情校验失败，请稍后重试") from exc

        resolved = {item.symbol for item in metadata}
        unresolved = sorted(set(missing).difference(resolved).difference({"CASH.CNY"}))
        if unresolved:
            raise PhotoImportError("外部行情未找到标的：" + ",".join(unresolved))
        if cash_missing:
            metadata.append(
                InstrumentInput("CASH.CNY", AssetType.CASH, "人民币现金", "LOCAL", as_of_date)
            )
        return tuple(sorted(metadata, key=lambda item: item.symbol))

    def _known_instruments(self) -> dict[str, str]:
        frame = self._database.query_df("SELECT symbol, asset_type FROM instruments")
        if frame.empty:
            return {}
        return {
            str(row.symbol).strip().upper(): str(row.asset_type).strip().lower()
            for row in frame.itertuples(index=False)
        }


def replace_photo_positions(
    preview: PhotoImportPreview,
    positions: tuple[PositionInput, ...],
) -> PhotoImportPreview:
    """Validate edited rows and return a new preview without mutating session state."""

    if not isinstance(preview, PhotoImportPreview):
        raise TypeError("preview must be PhotoImportPreview")
    if not isinstance(positions, tuple) or not positions:
        raise ValueError("编辑后至少需要一行持仓")
    symbols: set[str] = set()
    for item in positions:
        if not isinstance(item, PositionInput):
            raise TypeError("编辑后的持仓必须使用PositionInput")
        if item.symbol in symbols:
            raise ValueError(f"编辑后的持仓包含重复标的：{item.symbol}")
        symbols.add(item.symbol)
        if item.as_of_date != preview.as_of_date:
            raise ValueError("编辑后的持仓日期必须与截图日期一致")
        if item.quantity <= 0 or item.average_cost <= 0 or item.market_value <= 0:
            raise ValueError(f"{item.symbol}的数量、平均成本和市值必须为正")
        if (
            item.asset_type is AssetType.STOCK
            and item.quantity != item.quantity.to_integral_value()
        ):
            raise ValueError(f"{item.symbol}股票数量必须是整股")
    if not preview.requires_cash:
        try:
            validate_complete_snapshot(positions)
        except (TypeError, ValueError) as exc:
            raise PhotoImportError(f"编辑后未形成完整账户快照：{exc}") from exc
    inference_by_symbol = {item.symbol: item for item in preview.inferences}
    updated_inferences: list[FundHoldingInference] = []
    for position in positions:
        previous = inference_by_symbol.get(position.symbol)
        if previous is None:
            continue
        cost_basis = (position.quantity * position.average_cost).quantize(_SUMMARY_QUANTUM)
        holding_profit = (position.market_value - cost_basis).quantize(_SUMMARY_QUANTUM)
        updated_inferences.append(
            replace(
                previous,
                market_value=position.market_value,
                holding_profit=holding_profit,
                cost_basis=cost_basis,
                quantity=position.quantity,
                average_cost=position.average_cost,
            )
        )
    warnings = tuple(
        dict.fromkeys((*preview.warnings, "已应用人工逐行修正；导入前仍需再次核对原图与账户现金。"))
    )
    return replace(
        preview,
        positions=tuple(sorted(positions, key=lambda item: item.symbol)),
        warnings=warnings,
        inferences=tuple(sorted(updated_inferences, key=lambda item: item.symbol)),
    )


def editable_photo_positions(
    preview: PhotoImportPreview,
) -> tuple[dict[str, str], ...]:
    """Return stable Chinese-column rows suitable for ``st.data_editor``."""

    if not isinstance(preview, PhotoImportPreview):
        raise TypeError("preview must be PhotoImportPreview")
    return tuple(
        {
            "代码": item.symbol,
            "资产类型": item.asset_type.value,
            "数量": str(item.quantity),
            "平均成本": str(item.average_cost),
            "市值": str(item.market_value),
        }
        for item in preview.positions
    )


def photo_positions_from_editor(
    preview: PhotoImportPreview,
    edited: object,
) -> tuple[PositionInput, ...]:
    """Convert a data-editor frame back to validated immutable position rows."""

    if not hasattr(edited, "to_dict"):
        raise TypeError("编辑结果必须是表格")
    original = {item.symbol: item for item in preview.positions}
    rows: list[PositionInput] = []
    seen: set[str] = set()
    for raw in edited.to_dict("records"):
        symbol = str(raw.get("代码", "")).strip().upper()
        if not symbol or symbol not in original:
            raise ValueError(f"编辑结果中的标的未知：{symbol or '空'}")
        if symbol in seen:
            raise ValueError(f"编辑结果包含重复标的：{symbol}")
        seen.add(symbol)
        base = original[symbol]
        quantity = _editor_decimal(raw.get("数量"), f"{symbol}数量")
        average_cost = _editor_decimal(raw.get("平均成本"), f"{symbol}平均成本")
        market_value = _editor_decimal(raw.get("市值"), f"{symbol}市值")
        rows.append(
            PositionInput(
                symbol, base.asset_type, quantity, average_cost, market_value, base.as_of_date
            )
        )
    if seen != set(original):
        missing = ",".join(sorted(set(original).difference(seen)))
        raise ValueError(f"编辑结果缺少标的：{missing}")
    return tuple(rows)


def _editor_decimal(value: object, label: str) -> Decimal:
    try:
        parsed = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}必须是十进制数") from exc
    if not parsed.is_finite():
        raise ValueError(f"{label}必须是有限数")
    return parsed


__all__ = [
    "PhotoImportPreview",
    "PhotoAccountImportService",
    "replace_photo_positions",
    "editable_photo_positions",
    "photo_positions_from_editor",
]
