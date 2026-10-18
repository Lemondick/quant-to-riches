"""Exact-money fee estimates for public-fund A/C share classes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
MAX_VERIFICATION_AGE_DAYS = 30


class FeeScheduleError(ValueError):
    """Base error for an unusable fund fee schedule."""


class UnverifiedFeeSchedule(FeeScheduleError):
    """Raised when a schedule was not valid and recently verified on the decision date."""


class RedemptionTierNotFound(FeeScheduleError):
    """Raised when no redemption tier covers the expected holding period."""


def _require_decimal(value: object, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be Decimal")
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")
    return value


def _validate_rate(value: object, field_name: str) -> Decimal:
    rate = _require_decimal(value, field_name)
    if rate < 0 or rate > 1:
        raise ValueError(f"{field_name} must be between 0 and 1")
    return rate


def _validate_holding_days(holding_days: int) -> None:
    if isinstance(holding_days, bool) or not isinstance(holding_days, int):
        raise TypeError("holding_days must be an integer")
    if holding_days < 0:
        raise ValueError("holding_days must be non-negative")


@dataclass(frozen=True, slots=True)
class RedemptionTier:
    """Inclusive holding-day band and its redemption fee rate."""

    min_days: int
    max_days: int | None
    rate: Decimal

    def __post_init__(self) -> None:
        _validate_holding_days(self.min_days)
        if self.max_days is not None:
            _validate_holding_days(self.max_days)
            if self.max_days < self.min_days:
                raise ValueError("max_days must be greater than or equal to min_days")
        _validate_rate(self.rate, "rate")

    def includes(self, holding_days: int) -> bool:
        """Return whether this inclusive tier covers ``holding_days``."""

        return self.min_days <= holding_days and (
            self.max_days is None or holding_days <= self.max_days
        )


@dataclass(frozen=True, slots=True)
class PurchaseTier:
    """CNY amount band: inclusive lower bound, exclusive upper bound."""

    min_amount: Decimal
    max_amount: Decimal | None
    rate: Decimal | None = None
    fixed_amount: Decimal | None = None

    def __post_init__(self) -> None:
        if _require_decimal(self.min_amount, "min_amount") < 0:
            raise ValueError("min_amount must be non-negative")
        if self.max_amount is not None and (
            _require_decimal(self.max_amount, "max_amount") <= self.min_amount
        ):
            raise ValueError("max_amount must exceed min_amount")
        if (self.rate is None) == (self.fixed_amount is None):
            raise ValueError("provide either a rate or a fixed CNY fee")
        if self.rate is not None:
            _validate_rate(self.rate, "rate")
        if self.fixed_amount is not None and (
            _require_decimal(self.fixed_amount, "fixed_amount") < 0
        ):
            raise ValueError("fixed_amount must be non-negative")


@dataclass(frozen=True, slots=True)
class ServiceFeeRefundRule:
    """An explicitly evidenced refund rule; never installed for all C shares."""

    effective_from: date
    after_holding_days: int
    evidence_reference: str

    def __post_init__(self) -> None:
        if not isinstance(self.effective_from, date):
            raise TypeError("refund effective_from must be a date")
        _validate_holding_days(self.after_holding_days)
        if not self.evidence_reference.strip():
            raise ValueError("refund rule requires its own announcement evidence")


@dataclass(frozen=True, slots=True)
class FundCostEstimate:
    purchase_fee: Decimal
    redemption_fee: Decimal
    service_fee_estimate: Decimal
    expected_service_refund: Decimal
    net_cost_estimate: Decimal
    cash_credit: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class FundRedemptionLot:
    confirmed_on: date
    gross_amount: Decimal

    def __post_init__(self) -> None:
        if type(self.confirmed_on) is not date:
            raise TypeError("lot confirmation must be a plain date")
        if _require_decimal(self.gross_amount, "gross_amount") <= 0:
            raise ValueError("lot amount must be positive")


@dataclass(frozen=True, slots=True)
class FundFeeSchedule:
    """A verified product-specific fee schedule for one fund share class."""

    symbol: str
    share_class: str
    purchase_rate: Decimal
    annual_service_rate: Decimal
    redemption_tiers: tuple[RedemptionTier, ...]
    verified_on: date
    effective_from: date | None = None
    effective_to: date | None = None
    purchase_tiers: tuple[PurchaseTier, ...] = ()
    channel: str | None = None
    evidence_reference: str | None = None
    service_refund_rule: ServiceFeeRefundRule | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if not isinstance(self.share_class, str):
            raise TypeError("share_class must be a string")
        normalized_class = self.share_class.strip().upper()
        if normalized_class not in {"A", "C"}:
            raise ValueError("share_class must be A or C")
        object.__setattr__(self, "symbol", self.symbol.strip())
        object.__setattr__(self, "share_class", normalized_class)

        _validate_rate(self.purchase_rate, "purchase_rate")
        _validate_rate(self.annual_service_rate, "annual_service_rate")
        if not isinstance(self.verified_on, date):
            raise TypeError("verified_on must be a date")
        if self.effective_from is not None and not isinstance(self.effective_from, date):
            raise TypeError("effective_from must be a date or None")
        if self.effective_to is not None and not isinstance(self.effective_to, date):
            raise TypeError("effective_to must be a date or None")
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not precede effective_from")

        tiers = tuple(self.redemption_tiers)
        if not tiers:
            raise ValueError("redemption_tiers must not be empty")
        if not all(isinstance(tier, RedemptionTier) for tier in tiers):
            raise TypeError("redemption_tiers must contain RedemptionTier values")
        ordered = tuple(sorted(tiers, key=lambda tier: tier.min_days))
        for previous, current in zip(ordered, ordered[1:], strict=False):
            if previous.max_days is None or current.min_days <= previous.max_days:
                raise ValueError("redemption tiers must not overlap")
        object.__setattr__(self, "redemption_tiers", ordered)
        purchases = tuple(sorted(self.purchase_tiers, key=lambda tier: tier.min_amount))
        for previous, current in zip(purchases, purchases[1:], strict=False):
            if previous.max_amount is None or current.min_amount < previous.max_amount:
                raise ValueError("purchase tiers must not overlap")
        object.__setattr__(self, "purchase_tiers", purchases)
        if self.service_refund_rule is not None and not isinstance(
            self.service_refund_rule, ServiceFeeRefundRule
        ):
            raise TypeError("service_refund_rule must be ServiceFeeRefundRule")


@dataclass(frozen=True, slots=True)
class ShareClassDecision:
    """Deterministic A/C cost comparison for a stated amount and holding period."""

    a_symbol: str
    c_symbol: str
    a_cost: Decimal
    c_cost: Decimal
    selected_symbol: str
    savings: Decimal
    holding_days: int
    reasons: tuple[str, ...]

    @property
    def symbol(self) -> str:
        """Compatibility alias for the selected fund symbol."""

        return self.selected_symbol

    @property
    def chosen_symbol(self) -> str:
        """Compatibility alias for callers using 'chosen' terminology."""

        return self.selected_symbol

    @property
    def cheaper_symbol(self) -> str:
        """The selected symbol (including the deterministic tie-break result)."""

        return self.selected_symbol

    @property
    def selected_share_class(self) -> str:
        """Return ``A`` or ``C`` for the selected symbol."""

        return "A" if self.selected_symbol == self.a_symbol else "C"


def _matching_redemption_tier(schedule: FundFeeSchedule, holding_days: int) -> RedemptionTier:
    matches = [tier for tier in schedule.redemption_tiers if tier.includes(holding_days)]
    if len(matches) != 1:
        raise RedemptionTierNotFound(
            f"{schedule.symbol} has no unique redemption tier for {holding_days} holding days"
        )
    return matches[0]


def subscription_fee(schedule: FundFeeSchedule, amount: Decimal) -> Decimal:
    """Front-end external-rate fee for an amount including its purchase fee."""

    investment = _require_decimal(amount, "amount")
    if investment < 0:
        raise ValueError("amount must be non-negative")
    rate = schedule.purchase_rate
    fixed = None
    if schedule.purchase_tiers:
        matches = [
            tier
            for tier in schedule.purchase_tiers
            if tier.min_amount <= investment
            and (tier.max_amount is None or investment < tier.max_amount)
        ]
        if len(matches) != 1:
            raise FeeScheduleError("no unique purchase tier covers the subscription amount")
        rate, fixed = matches[0].rate, matches[0].fixed_amount
    fee = fixed if fixed is not None else investment - investment / (Decimal("1") + rate)
    if fee > investment:
        raise FeeScheduleError("purchase fee exceeds the subscription amount")
    return fee.quantize(CENT, rounding=ROUND_HALF_UP)


def estimate_cost_breakdown(
    schedule: FundFeeSchedule,
    amount: Decimal,
    holding_days: int,
    *,
    confirmed_on: date | None = None,
    nav_includes_service_fee: bool = False,
) -> FundCostEstimate:
    """Constant-capital planning estimate. A future refund is never cash."""

    if not isinstance(schedule, FundFeeSchedule):
        raise TypeError("schedule must be a FundFeeSchedule")
    investment = _require_decimal(amount, "amount")
    if investment < 0:
        raise ValueError("amount must be non-negative")
    _validate_holding_days(holding_days)

    tier = _matching_redemption_tier(schedule, holding_days)
    purchase_cost = subscription_fee(schedule, investment)
    service_cost = investment * schedule.annual_service_rate * Decimal(holding_days) / Decimal(365)
    redemption_cost = investment * tier.rate
    refund = Decimal("0")
    if schedule.service_refund_rule is not None and schedule.annual_service_rate > 0:
        if confirmed_on is None:
            raise UnverifiedFeeSchedule("refund estimate requires the lot confirmation date")
        rule = schedule.service_refund_rule
        end = confirmed_on + timedelta(days=holding_days)
        eligible_from = max(
            rule.effective_from, confirmed_on + timedelta(days=rule.after_holding_days)
        )
        refund_days = max((end - eligible_from).days, 0)
        refund = investment * schedule.annual_service_rate * Decimal(refund_days) / Decimal(365)
    # Actual share-class NAV already includes accrued service expenses. A NAV
    # replay must not subtract them a second time or credit an unreceived refund.
    effective_service = Decimal("0") if nav_includes_service_fee else service_cost - refund
    return FundCostEstimate(
        purchase_cost,
        redemption_cost.quantize(CENT, rounding=ROUND_HALF_UP),
        service_cost.quantize(CENT, rounding=ROUND_HALF_UP),
        refund.quantize(CENT, rounding=ROUND_HALF_UP),
        (purchase_cost + redemption_cost + effective_service).quantize(
            CENT, rounding=ROUND_HALF_UP
        ),
    )


def estimate_share_cost(
    schedule: FundFeeSchedule,
    amount: Decimal,
    holding_days: int,
    *,
    confirmed_on: date | None = None,
) -> Decimal:
    return estimate_cost_breakdown(
        schedule, amount, holding_days, confirmed_on=confirmed_on
    ).net_cost_estimate


def estimate_redemption_lots(
    schedule: FundFeeSchedule, lots: tuple[FundRedemptionLot, ...], confirmed_on: date
) -> tuple[dict, ...]:
    """Evaluate selected redemption lots, excluding the redemption confirmation day.

    Caller supplies the actual selected lots and confirmation dates; this does
    not infer FIFO or a holding period from an aggregate portfolio position.
    """
    if not lots or type(confirmed_on) is not date:
        raise ValueError("redemption requires selected lots and its confirmation date")
    results = []
    for lot in lots:
        days = (confirmed_on - lot.confirmed_on).days
        if days < 0:
            raise ValueError("redemption confirmation precedes a purchase confirmation")
        tier = _matching_redemption_tier(schedule, days)
        fee = (lot.gross_amount * tier.rate).quantize(CENT, rounding=ROUND_HALF_UP)
        results.append(
            {
                "confirmed_on": lot.confirmed_on,
                "holding_days": days,
                "gross_amount": lot.gross_amount,
                "rate": tier.rate,
                "fee": fee,
            }
        )
    return tuple(results)


def _verify_schedule(schedule: FundFeeSchedule, as_of_date: date) -> None:
    if schedule.verified_on > as_of_date:
        raise UnverifiedFeeSchedule(f"{schedule.symbol} was verified after the decision date")
    if (as_of_date - schedule.verified_on).days > MAX_VERIFICATION_AGE_DAYS:
        raise UnverifiedFeeSchedule(
            f"{schedule.symbol} fee schedule is older than {MAX_VERIFICATION_AGE_DAYS} days"
        )
    if schedule.effective_from is not None and as_of_date < schedule.effective_from:
        raise UnverifiedFeeSchedule(f"{schedule.symbol} fee schedule is not effective yet")
    if schedule.effective_to is not None and as_of_date > schedule.effective_to:
        raise UnverifiedFeeSchedule(f"{schedule.symbol} fee schedule has expired")


def choose_share_class(
    a_schedule: FundFeeSchedule,
    c_schedule: FundFeeSchedule,
    amount: Decimal,
    holding_days: int,
    as_of_date: date | None = None,
) -> ShareClassDecision:
    """Compare verified A/C schedules and select the lower-cost share class.

    Ties select A for holding periods of at least 365 days and C for shorter
    periods. ``as_of_date`` should always be supplied by historical strategy
    runs; the current date is only a convenience for direct interactive use.
    """

    if not isinstance(a_schedule, FundFeeSchedule) or not isinstance(c_schedule, FundFeeSchedule):
        raise TypeError("a_schedule and c_schedule must be FundFeeSchedule values")
    if a_schedule.share_class != "A" or c_schedule.share_class != "C":
        raise ValueError("a_schedule must be class A and c_schedule must be class C")
    investment = _require_decimal(amount, "amount")
    if investment <= 0:
        raise ValueError("amount must be positive")
    _validate_holding_days(holding_days)
    decision_date = as_of_date or date.today()
    if not isinstance(decision_date, date):
        raise TypeError("as_of_date must be a date or None")

    _verify_schedule(a_schedule, decision_date)
    _verify_schedule(c_schedule, decision_date)
    a_cost = estimate_share_cost(a_schedule, investment, holding_days, confirmed_on=decision_date)
    c_cost = estimate_share_cost(c_schedule, investment, holding_days, confirmed_on=decision_date)

    if a_cost < c_cost:
        selected = a_schedule.symbol
        tie_reason = "A类预计费用较低"
    elif c_cost < a_cost:
        selected = c_schedule.symbol
        tie_reason = "C类预计费用较低"
    elif holding_days >= 365:
        selected = a_schedule.symbol
        tie_reason = "预计费用相同，持有期不少于365日，按规则选择A类"
    else:
        selected = c_schedule.symbol
        tie_reason = "预计费用相同，持有期少于365日，按规则选择C类"

    savings = abs(a_cost - c_cost).quantize(CENT, rounding=ROUND_HALF_UP)
    reasons = (
        f"{holding_days}日预计费用：A类{a_cost:.2f}元，C类{c_cost:.2f}元",
        f"{tie_reason}，预计节省{savings:.2f}元",
    )
    return ShareClassDecision(
        a_symbol=a_schedule.symbol,
        c_symbol=c_schedule.symbol,
        a_cost=a_cost,
        c_cost=c_cost,
        selected_symbol=selected,
        savings=savings,
        holding_days=holding_days,
        reasons=reasons,
    )
