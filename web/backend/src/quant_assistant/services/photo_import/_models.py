"""Shared immutable models and parser constants for photo import."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from typing import Protocol

from quant_assistant.common.time import SHANGHAI_TZ
from quant_assistant.models.entities import Action, AssetType
from quant_assistant.services.portfolio_commands import (
    InstrumentInput,
    PositionInput,
    TransactionInput,
)

_CODE_RE = re.compile(r"(?<!\d)(\d{6}(?:[AC]|\.(?:SH|SZ|BJ|OF))?)(?!\d)", re.IGNORECASE)
_NUMBER_RE = re.compile(r"[-+]?(?:\d[\d,]*)(?:\.\d+)?%?")
_SUMMARY_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<sign>[+-]?)(?P<number>\d[\d,]*(?:\.\d+)?)(?P<percent>%?)(?![A-Za-z0-9])"
)
_DATE_RE = re.compile(r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日?)")
_CASH_KEYWORDS = ("可用资金", "资金余额", "现金余额", "银行存款", "现金")
_CASH_VALUE_KEYWORDS = (*_CASH_KEYWORDS, "可用余额")
_FUND_KEYWORDS = ("基金", "ETF", "LOF", "联接", "债券", "混合", "指数", "货币")
_DELIMITERS = " \t|,:：;；()（）[]【】"
_HEADER_MARKERS = (
    "证券代码",
    "股票代码",
    "基金代码",
    "证券名称",
    "股票名称",
    "基金名称",
    "持仓数量",
    "持仓份额",
    "股票余额",
    "可用余额",
    "可用数量",
    "平均成本",
    "成本价",
    "单位净值",
    "市价",
    "当前价",
    "现价",
    "盈亏",
    "市值",
)
_QUANTITY_LABELS = (
    "持仓数量",
    "持有数量",
    "股票余额",
    "持仓份额",
    "持仓",
    "数量",
    "份额",
)
_AVAILABLE_QUANTITY_LABELS = ("可用数量", "可用余额", "可用份额")
_COST_LABELS = ("平均成本", "成本价", "单位成本", "买入均价", "成本", "单位净值")
_MARKET_VALUE_LABELS = ("参考市值", "当前市值", "总市值", "市值")
_SUMMARY_IGNORED_LINES = frozenset(
    {
        "基金",
        "我的持有",
        "持有收益率排序",
        "名称",
        "金额/昨日收益",
        "持有收益/率",
        "全部",
        "偏股",
        "偏债",
        "指数",
        "黄金",
        "全球",
        "金选指数基金",
        "定投",
        "机器人",
        "产品周报",
    }
)
_SUMMARY_IGNORE_MARKERS = (
    "我的持有",
    "持有收益",
    "金额/昨日收益",
    "持有收益/率",
    "产品周报",
    "好基金",
    "支付宝",
    "投资盈利",
)
_SUMMARY_SUFFIX_RE = re.compile(r"\((?:LOF|QDII)\)[A-Z]?$", re.IGNORECASE)
_SUMMARY_QUANTUM = Decimal("0.00000001")
_SUMMARY_NAME_HINTS = ("中证", "主题", "沪深", "A500", "银行", "医疗", "有色", "科技")
_TRANSACTION_ACTIONS = {
    "买入": Action.BUY,
    "申购": Action.BUY,
    "认购": Action.BUY,
    "转入": Action.ADD,
    "加仓": Action.ADD,
    "卖出": Action.SELL,
    "赎回": Action.SELL,
    "转出": Action.REDUCE,
    "减仓": Action.REDUCE,
}
_TRANSACTION_IGNORE_MARKERS = (
    "历史交易",
    "交易记录",
    "成交记录",
    "委托记录",
    "申请日期",
    "成交日期",
    "确认日期",
    "交易类型",
    "确认状态",
)
_TRANSACTION_QUANTITY_LABELS = (*_QUANTITY_LABELS, "成交数量", "成交份额", "确认份额", "确认数量")
_TRANSACTION_PRICE_LABELS = (
    "成交价",
    "成交价格",
    "成交均价",
    "确认净值",
    "确认价格",
    "单位净值",
    "净值",
    "价格",
)
_TRANSACTION_FEE_LABELS = ("手续费", "交易费用", "申购费", "赎回费", "费用")
_TRANSACTION_AMOUNT_LABELS = ("成交金额", "成交额", "确认金额", "交易金额", "金额")
_TRANSACTION_ID_LABELS = ("成交编号", "交易编号", "申请编号", "确认号", "流水号", "订单号")
_SHANGHAI_NOON = time(15, 0)
_SHANGHAI_TZ = SHANGHAI_TZ


class PhotoImportError(RuntimeError):
    """The photo cannot be converted into a safe complete account snapshot."""


class MarketInstrumentProvider(Protocol):
    def fetch_stock_universe(self): ...

    def fetch_fund_daily(self): ...

    def fetch_fund_nav(self, symbol: str): ...


@dataclass(frozen=True, slots=True)
class FundHoldingHint:
    """Values visible in a fund-holdings summary screenshot."""

    name: str
    market_value: Decimal
    holding_profit: Decimal
    holding_return: Decimal | None

    @property
    def cost_basis(self) -> Decimal:
        return self.market_value - self.holding_profit


@dataclass(frozen=True, slots=True)
class FundHoldingInference:
    """A fund holding reconstructed from a summary row and a dated NAV."""

    name: str
    symbol: str
    market_value: Decimal
    holding_profit: Decimal
    cost_basis: Decimal
    quantity: Decimal
    average_cost: Decimal
    unit_nav: Decimal
    nav_date: date


@dataclass(frozen=True, slots=True)
class FundHoldingsSummary:
    """Parsed rows from a fund-holdings summary screenshot."""

    holdings: tuple[FundHoldingHint, ...]
    warnings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class FundNameMatch:
    """A fund name resolved to an externally verified code."""

    requested_name: str
    symbol: str
    matched_name: str
    share_class: str | None
    score: float


@dataclass(frozen=True, slots=True)
class FundNameLookupPreview:
    """Non-persistent result for a name-only screenshot or crop."""

    provider: str
    image_sha256: str
    matches: tuple[FundNameMatch, ...]
    ocr_lines: tuple[str, ...]
    detected_dates: tuple[date, ...]
    warnings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class PhotoTransactionHint:
    """One transaction row reconstructed from a transaction screenshot."""

    name: str
    symbol: str | None
    asset_type: AssetType
    action: Action
    quantity: Decimal
    price: Decimal
    fees: Decimal | None
    traded_at: datetime
    external_id: str | None = None
    note: str | None = None


@dataclass(frozen=True, slots=True)
class PhotoTransactionPreview:
    """Reviewable transaction rows; nothing is written until the user confirms."""

    as_of_date: date
    provider: str
    image_sha256: str
    transactions: tuple[TransactionInput, ...]
    instrument_metadata: tuple[InstrumentInput, ...]
    ocr_lines: tuple[str, ...]
    detected_dates: tuple[date, ...]
    warnings: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class _ParsedAccount:
    positions: tuple[PositionInput, ...]
    warnings: tuple[str, ...]
    confidence: float


__all__ = [
    "PhotoImportError",
    "MarketInstrumentProvider",
    "FundHoldingHint",
    "FundHoldingInference",
    "FundHoldingsSummary",
    "FundNameMatch",
    "FundNameLookupPreview",
    "PhotoTransactionHint",
    "PhotoTransactionPreview",
    "_ParsedAccount",
]
