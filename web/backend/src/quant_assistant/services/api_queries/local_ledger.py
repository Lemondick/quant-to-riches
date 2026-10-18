"""Mixin providing ledger reads, profit attribution, corporate actions,
corrections, and the trade / dividend write paths.

Holds the largest concentration of mutable state access in the api
surface.  The ``profit_attribution`` method is preserved verbatim from the
original monolithic ``api_queries.py`` so reverting to a single class, if
needed, is a trivial inlining.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from quant_assistant.models.entities import Action, AssetType
from quant_assistant.providers.fund_valuation import normalize_fund_symbol
from quant_assistant.services.portfolio_commands import (
    DividendInput,
    InstrumentInput,
    TransactionInput,
)

from ._common import (
    _boolean,
    _datetime,
    _decimal,
    _enum_value,
    _field,
    _nonempty_text,
    _optional_decimal,
    _optional_text_value,
    _records,
)
from .mappers import (
    _corporate_action_view,
    _ledger_correction_view,
    _ledger_entry_view,
    _positions,
)
from .views import (
    CorporateActionInputView,
    CorporateActionView,
    DividendLedgerInputView,
    LedgerCorrectionInputView,
    LedgerCorrectionView,
    LedgerEntryView,
    LedgerView,
    ProfitAttributionItemView,
    ProfitAttributionView,
    TradeLedgerInputView,
)


class _LedgerMixin:
    """Ledger reads, attribution, corporate actions, corrections, writes."""

    def _ledger_entries(self, limit: int | None = None) -> tuple[LedgerEntryView, ...]:
        """Read ledger rows; omit ``limit`` when reconstructing full cost history."""

        unified_loader = getattr(self._queries, "ledger_history", None)
        if callable(unified_loader):
            rows = unified_loader() if limit is None else unified_loader(limit=limit)
            return tuple(_ledger_entry_view(row) for row in _records(rows))

        entries: list[LedgerEntryView] = []
        transaction_loader = self._queries.transaction_history
        transaction_rows = (
            transaction_loader() if limit is None else transaction_loader(limit=limit)
        )
        for row in _records(transaction_rows):
            action = _enum_value(Action, _field(row, "action"), "ledger action")
            source = str(_field(row, "source", "") or "").strip().lower()
            entry_type = (
                "dca"
                if source == "dca"
                else "buy"
                if action in {Action.BUY, Action.ADD}
                else "sell"
            )
            gross = _decimal(_field(row, "gross_amount"), "ledger gross_amount")
            entries.append(
                LedgerEntryView(
                    id=int(_field(row, "id")),
                    entry_type=entry_type,
                    symbol=_nonempty_text(_field(row, "symbol"), "ledger symbol"),
                    name=_nonempty_text(_field(row, "name", _field(row, "symbol")), "ledger name"),
                    asset_type=_nonempty_text(_field(row, "asset_type"), "ledger asset_type"),
                    quantity=_decimal(_field(row, "quantity"), "ledger quantity"),
                    price=_decimal(_field(row, "price"), "ledger price"),
                    gross_amount=gross,
                    fees=_decimal(_field(row, "fees"), "ledger fees"),
                    net_amount=_decimal(_field(row, "cash_delta"), "ledger cash_delta"),
                    occurred_at=_datetime(_field(row, "traded_at"), "traded_at"),
                    external_id=_nonempty_text(_field(row, "external_id"), "ledger external_id"),
                    note=_optional_text_value(_field(row, "note", None)),
                    reversed=_boolean(
                        _field(row, "reversed_by_correction_id", None) is not None,
                        "ledger reversed",
                    ),
                )
            )
        dividend_history = getattr(self._queries, "dividend_history", None)
        if callable(dividend_history):
            dividend_rows = dividend_history() if limit is None else dividend_history(limit=limit)
            for row in _records(dividend_rows):
                entries.append(
                    LedgerEntryView(
                        id=int(_field(row, "id")),
                        entry_type="dividend",
                        symbol=_nonempty_text(_field(row, "symbol"), "dividend symbol"),
                        name=_nonempty_text(
                            _field(row, "name", _field(row, "symbol")), "dividend name"
                        ),
                        asset_type=_nonempty_text(_field(row, "asset_type"), "dividend asset_type"),
                        quantity=None,
                        price=None,
                        gross_amount=_decimal(_field(row, "gross_amount"), "dividend gross_amount"),
                        fees=_decimal(_field(row, "fees"), "dividend fees"),
                        net_amount=_decimal(_field(row, "net_amount"), "dividend net_amount"),
                        occurred_at=_datetime(_field(row, "paid_at"), "paid_at"),
                        external_id=_nonempty_text(
                            _field(row, "external_id"), "dividend external_id"
                        ),
                        note=_optional_text_value(_field(row, "note", None)),
                        reversed=_boolean(
                            _field(row, "reversed_by_correction_id", None) is not None,
                            "dividend reversed",
                        ),
                    )
                )
        entries.sort(key=lambda item: (item.occurred_at, item.entry_type, item.id), reverse=True)
        return tuple(entries)

    def _all_ledger_entries(self) -> tuple[LedgerEntryView, ...]:
        """Replay the ledger in bounded SQL pages for attribution.

        The fallback adapter path remains available for older integrations, but
        the built-in DuckDB query never imposes an arbitrary 5,000-row cap.
        """

        loader = getattr(self._queries, "ledger_history", None)
        if not callable(loader):
            return self._ledger_entries()
        entries: list[LedgerEntryView] = []
        offset = 0
        page_size = 500
        while True:
            try:
                rows = loader(limit=page_size, offset=offset, descending=False)
            except TypeError:
                return self._ledger_entries()
            page = tuple(_ledger_entry_view(row) for row in _records(rows))
            entries.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return tuple(entries)

    def _all_corporate_actions(self) -> tuple[CorporateActionView, ...]:
        """Read every company action in bounded SQL pages for attribution."""

        loader = getattr(self._queries, "corporate_action_history", None)
        if not callable(loader):
            return ()
        page_size = 500
        offset = 0
        actions: list[CorporateActionView] = []
        while True:
            try:
                rows = loader(
                    limit=page_size,
                    offset=offset,
                    descending=False,
                )
            except TypeError:
                try:
                    rows = loader()
                except TypeError:
                    rows = loader(limit=page_size)
                return tuple(_corporate_action_view(row) for row in _records(rows))
            page = tuple(_corporate_action_view(row) for row in _records(rows))
            actions.extend(page)
            if len(page) < page_size:
                break
            offset += page_size
        return tuple(actions)

    def ledger(self, limit: int = 200) -> LedgerView:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("ledger limit must be between 1 and 500")
        entries = self._ledger_entries(limit)
        totals_loader = getattr(self._queries, "ledger_totals", None)
        if callable(totals_loader):
            total_fees, total_dividends = totals_loader()
        else:
            total_fees = sum((item.fees for item in entries), Decimal("0"))
            total_dividends = sum(
                (item.net_amount for item in entries if item.entry_type == "dividend"),
                Decimal("0"),
            )
        return LedgerView(
            entries[:limit],
            _decimal(total_fees, "total_fees"),
            _decimal(total_dividends, "total_dividends"),
        )

    def profit_attribution(self) -> ProfitAttributionView:
        # A correction keeps the original row for audit purposes, but the
        # reversed row must not participate in economic attribution.
        entries = tuple(item for item in self._all_ledger_entries() if not item.reversed)
        positions = {
            item.symbol: item
            for item in _positions(self._queries.current_positions())
            if item.asset_type is not AssetType.CASH
        }
        actions = self._all_corporate_actions()
        names = {item.symbol: item.name for item in entries}
        names.update({symbol: item.name for symbol, item in positions.items()})
        for action in actions:
            names.setdefault(action.symbol, action.symbol)
            if action.target_symbol:
                names.setdefault(action.target_symbol, action.target_symbol)
        asset_types = {item.symbol: item.asset_type for item in entries}
        asset_types.update({symbol: item.asset_type.value for symbol, item in positions.items()})
        for action in actions:
            asset_types.setdefault(action.symbol, action.asset_type)
            if action.target_symbol:
                asset_types.setdefault(action.target_symbol, action.asset_type)
        symbols = sorted(set(names) | set(positions))
        entry_symbols = {item.symbol for item in entries}
        states: dict[str, dict[str, object]] = {
            symbol: {
                "quantity": (position.quantity if symbol not in entry_symbols else Decimal("0")),
                "cost": (
                    position.quantity * position.average_cost
                    if symbol not in entry_symbols
                    else Decimal("0")
                ),
                "realized": Decimal("0"),
                "known": True,
                "dividends": Decimal("0"),
                "fees": Decimal("0"),
            }
            for symbol, position in positions.items()
        }
        for symbol in symbols:
            states.setdefault(
                symbol,
                {
                    "quantity": Decimal("0"),
                    "cost": Decimal("0"),
                    "realized": Decimal("0"),
                    "known": True,
                    "dividends": Decimal("0"),
                    "fees": Decimal("0"),
                },
            )

        events: list[tuple[datetime, int, str, object]] = [
            (entry.occurred_at, entry.id, "entry", entry) for entry in entries
        ]
        events.extend((action.effective_at, action.id, "action", action) for action in actions)
        for _, _, event_type, event in sorted(events, key=lambda item: (item[0], item[2], item[1])):
            if event_type == "action":
                action = event
                assert isinstance(action, CorporateActionView)
                state = states.setdefault(
                    action.symbol,
                    {
                        "quantity": Decimal("0"),
                        "cost": Decimal("0"),
                        "realized": Decimal("0"),
                        "known": True,
                        "dividends": Decimal("0"),
                        "fees": Decimal("0"),
                    },
                )
                state["fees"] = state["fees"] + action.fees
                if action.action_type == "dividend_reinvestment":
                    quantity = action.quantity
                    if quantity is None and action.price and action.price > 0:
                        quantity = action.gross_amount / action.price
                    # With no trade history, current positions already contain
                    # the post-reinvestment basis.  Keep that basis and add
                    # only the income leg to avoid replaying the action twice.
                    if action.symbol not in entry_symbols:
                        state["dividends"] = state["dividends"] + max(
                            Decimal("0"), action.gross_amount - action.fees
                        )
                    elif quantity is not None:
                        state["quantity"] = state["quantity"] + quantity
                        state["cost"] = state["cost"] + action.gross_amount + action.fees
                        state["dividends"] = state["dividends"] + max(
                            Decimal("0"), action.gross_amount - action.fees
                        )
                elif action.action_type == "fund_conversion":
                    if action.symbol not in entry_symbols:
                        continue
                    quantity = action.quantity or Decimal("0")
                    target_quantity = action.target_quantity or Decimal("0")
                    held = state["quantity"]
                    if quantity <= 0:
                        quantity = held
                    if quantity > held or held <= 0:
                        state["known"] = False
                        continue
                    removed_cost = state["cost"] / held * quantity
                    state["quantity"] = held - quantity
                    state["cost"] = state["cost"] - removed_cost
                    if action.target_symbol and target_quantity > 0:
                        target = states.setdefault(
                            action.target_symbol,
                            {
                                "quantity": Decimal("0"),
                                "cost": Decimal("0"),
                                "realized": Decimal("0"),
                                "known": True,
                                "dividends": Decimal("0"),
                                "fees": Decimal("0"),
                            },
                        )
                        target["quantity"] = target["quantity"] + target_quantity
                        target["cost"] = target["cost"] + removed_cost
                elif action.action_type == "split":
                    if action.symbol not in entry_symbols:
                        continue
                    quantity = action.quantity
                    ratio = action.ratio
                    held = state["quantity"]
                    if quantity is None:
                        quantity = held
                    if ratio is None or quantity > held or held <= 0:
                        state["known"] = False
                        continue
                    state["quantity"] = held - quantity + quantity * ratio
                # Custody transfers do not change economic basis.
                continue

            entry = event
            assert isinstance(entry, LedgerEntryView)
            state = states.setdefault(
                entry.symbol,
                {
                    "quantity": Decimal("0"),
                    "cost": Decimal("0"),
                    "realized": Decimal("0"),
                    "known": True,
                    "dividends": Decimal("0"),
                    "fees": Decimal("0"),
                },
            )
            state["fees"] = state["fees"] + entry.fees
            if entry.entry_type == "dividend":
                state["dividends"] = state["dividends"] + entry.net_amount
                continue
            quantity = entry.quantity or Decimal("0")
            if entry.entry_type in {"buy", "dca"}:
                state["quantity"] = state["quantity"] + quantity
                state["cost"] = state["cost"] + entry.gross_amount + entry.fees
                continue
            held = state["quantity"]
            if quantity > held or held <= 0:
                state["known"] = False
                continue
            removed_cost = state["cost"] / held * quantity
            state["quantity"] = held - quantity
            state["cost"] = state["cost"] - removed_cost
            state["realized"] = state["realized"] + entry.gross_amount - entry.fees - removed_cost

        items: list[ProfitAttributionItemView] = []
        for symbol in sorted(states):
            state = states[symbol]
            position = positions.get(symbol)
            unrealized = (
                Decimal("0")
                if position is None
                else position.market_value - position.quantity * position.average_cost
            )
            realized = state["realized"] if state["known"] else None
            dividends = state["dividends"]
            total = None if realized is None else realized + dividends + unrealized
            items.append(
                ProfitAttributionItemView(
                    symbol=symbol,
                    name=names.get(symbol, symbol),
                    asset_type=asset_types.get(symbol, "fund"),
                    realized_trading_profit=realized,
                    dividend_income=dividends,
                    unrealized_profit=unrealized,
                    fees=state["fees"],
                    total_profit=total,
                )
            )
        realized_values = [item.realized_trading_profit for item in items]
        realized_total = (
            None
            if any(value is None for value in realized_values)
            else sum((value for value in realized_values if value is not None), Decimal("0"))
        )
        dividend_total = sum((item.dividend_income for item in items), Decimal("0"))
        unrealized_total = sum(
            (item.unrealized_profit or Decimal("0") for item in items), Decimal("0")
        )
        return ProfitAttributionView(
            tuple(items),
            realized_total,
            dividend_total,
            unrealized_total,
            sum((item.fees for item in items), Decimal("0")),
            None if realized_total is None else realized_total + dividend_total + unrealized_total,
        )

    def record_ledger_trade(self, item: TradeLedgerInputView) -> LedgerEntryView:
        if self._portfolio_commands is None:
            raise RuntimeError("portfolio commands are unavailable")
        if not isinstance(item, TradeLedgerInputView):
            raise TypeError("ledger trade must use TradeLedgerInputView")
        entry_type = item.entry_type.strip().lower()
        actions = {"buy": Action.BUY, "sell": Action.SELL, "dca": Action.ADD}
        if entry_type not in actions:
            raise ValueError("ledger entry type must be buy, sell or dca")
        asset_type = _enum_value(AssetType, item.asset_type, "ledger asset_type")
        if asset_type not in {AssetType.STOCK, AssetType.FUND}:
            raise ValueError("ledger trade only accepts stocks or funds")
        occurred_at = _datetime(item.occurred_at, "ledger occurred_at")
        source = "dca" if entry_type == "dca" else "manual-api"
        transaction = TransactionInput(
            symbol=normalize_fund_symbol(item.symbol)
            if asset_type is AssetType.FUND
            else _nonempty_text(item.symbol, "ledger symbol"),
            asset_type=asset_type,
            action=actions[entry_type],
            quantity=_decimal(item.quantity, "ledger quantity"),
            price=_decimal(item.price, "ledger price"),
            fees=_decimal(item.fees, "ledger fees"),
            traded_at=occurred_at,
            external_id=_nonempty_text(item.external_id, "ledger external_id"),
            source=source,
            note=_optional_text_value(item.note),
        )
        metadata = InstrumentInput(
            transaction.symbol,
            asset_type,
            _nonempty_text(item.name, "ledger name"),
            None,
            occurred_at.date(),
        )
        ids = self._portfolio_commands.record_transaction_batch(
            (transaction,), instrument_metadata=(metadata,)
        )
        return self._ledger_entry_by_id(ids[0], entry_type)

    def record_ledger_dividend(self, item: DividendLedgerInputView) -> LedgerEntryView:
        if self._portfolio_commands is None:
            raise RuntimeError("portfolio commands are unavailable")
        if not isinstance(item, DividendLedgerInputView):
            raise TypeError("ledger dividend must use DividendLedgerInputView")
        asset_type = _enum_value(AssetType, item.asset_type, "dividend asset_type")
        dividend_id = self._portfolio_commands.record_dividend(
            DividendInput(
                symbol=normalize_fund_symbol(item.symbol)
                if asset_type is AssetType.FUND
                else _nonempty_text(item.symbol, "dividend symbol"),
                asset_type=asset_type,
                gross_amount=_decimal(item.gross_amount, "dividend gross_amount"),
                fees=_decimal(item.fees, "dividend fees"),
                paid_at=_datetime(item.occurred_at, "dividend occurred_at"),
                external_id=_nonempty_text(item.external_id, "dividend external_id"),
                source="manual-api",
                note=_optional_text_value(item.note),
            )
        )
        return self._ledger_entry_by_id(dividend_id, "dividend")

    def corporate_actions(self, limit: int = 100) -> tuple[CorporateActionView, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("corporate action limit must be between 1 and 500")
        loader = getattr(self._queries, "corporate_action_history", None)
        if not callable(loader):
            return ()
        try:
            rows = loader(limit=limit, descending=True)
        except TypeError:
            try:
                rows = loader(limit=limit)
            except TypeError:
                rows = loader()
        views = tuple(_corporate_action_view(row) for row in _records(rows))
        if len(views) <= limit:
            return views
        return tuple(
            sorted(views, key=lambda item: (item.effective_at, item.id), reverse=True)[:limit]
        )

    def record_corporate_action(self, item: CorporateActionInputView) -> CorporateActionView:
        from quant_assistant.services.portfolio_commands import CorporateActionInput

        if self._portfolio_commands is None or not callable(
            getattr(self._portfolio_commands, "record_corporate_action", None)
        ):
            raise RuntimeError("corporate action commands are unavailable")
        if not isinstance(item, CorporateActionInputView):
            raise TypeError("corporate action must use CorporateActionInputView")
        asset_type = _enum_value(AssetType, item.asset_type, "corporate action asset_type")
        symbol = (
            normalize_fund_symbol(item.symbol)
            if asset_type is AssetType.FUND
            else _nonempty_text(item.symbol, "corporate action symbol")
        )
        target_symbol = (
            None
            if item.target_symbol is None
            else (
                normalize_fund_symbol(item.target_symbol)
                if asset_type is AssetType.FUND
                else _nonempty_text(item.target_symbol, "corporate action target_symbol")
            )
        )
        action = CorporateActionInput(
            action_type=_nonempty_text(item.action_type, "corporate action type"),
            symbol=symbol,
            asset_type=asset_type,
            effective_at=_datetime(item.effective_at, "corporate action effective_at"),
            external_id=_nonempty_text(item.external_id, "corporate action external_id"),
            source="manual-api",
            target_symbol=target_symbol,
            quantity=_optional_decimal(item.quantity, "corporate action quantity"),
            target_quantity=_optional_decimal(
                item.target_quantity, "corporate action target_quantity"
            ),
            price=_optional_decimal(item.price, "corporate action price"),
            target_price=_optional_decimal(item.target_price, "corporate action target_price"),
            gross_amount=_decimal(item.gross_amount, "corporate action gross_amount"),
            fees=_decimal(item.fees, "corporate action fees"),
            ratio=_optional_decimal(item.ratio, "corporate action ratio"),
            cash_symbol=(
                None
                if item.cash_symbol is None
                else _nonempty_text(item.cash_symbol, "cash_symbol")
            ),
            from_account=item.from_account,
            to_account=item.to_account,
            note=_optional_text_value(item.note),
        )
        action_id = self._portfolio_commands.record_corporate_action(action)
        for view in self.corporate_actions():
            if view.id == action_id:
                return view
        raise RuntimeError("recorded corporate action was not found")

    def ledger_corrections(self, limit: int = 100) -> tuple[LedgerCorrectionView, ...]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("ledger correction limit must be between 1 and 500")
        loader = getattr(self._queries, "ledger_correction_history", None)
        if not callable(loader):
            return ()
        try:
            rows = loader(limit=limit, descending=True)
        except TypeError:
            try:
                rows = loader(limit=limit)
            except TypeError:
                rows = loader()
        views = tuple(_ledger_correction_view(row) for row in _records(rows))
        if len(views) <= limit:
            return views
        return tuple(
            sorted(views, key=lambda item: (item.effective_at, item.id), reverse=True)[:limit]
        )

    def correct_ledger_entry(self, item: LedgerCorrectionInputView) -> LedgerCorrectionView:
        from quant_assistant.services.portfolio_commands import LedgerCorrectionInput

        if self._portfolio_commands is None or not callable(
            getattr(self._portfolio_commands, "correct_ledger_entry", None)
        ):
            raise RuntimeError("ledger correction commands are unavailable")
        if not isinstance(item, LedgerCorrectionInputView):
            raise TypeError("ledger correction must use LedgerCorrectionInputView")
        correction_id = self._portfolio_commands.correct_ledger_entry(
            LedgerCorrectionInput(
                target_type=_nonempty_text(item.target_type, "correction target_type"),
                target_id=item.target_id,
                reason=_nonempty_text(item.reason, "correction reason"),
                effective_at=_datetime(item.effective_at, "correction effective_at"),
                external_id=_nonempty_text(item.external_id, "correction external_id"),
                source="manual-api",
                note=_optional_text_value(item.note),
            )
        )
        for view in self.ledger_corrections():
            if view.id == correction_id:
                return view
        raise RuntimeError("recorded ledger correction was not found")

    def _ledger_entry_by_id(self, entry_id: int, entry_type: str) -> LedgerEntryView:
        for entry in self._ledger_entries():
            if entry.id == entry_id and (
                (entry.entry_type == "dividend") == (entry_type == "dividend")
            ):
                return entry
        raise RuntimeError("recorded ledger entry was not found")
