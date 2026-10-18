"""Closed, bounded evidence schema for uploaded offline fund order replay."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Amount = Annotated[str, Field(min_length=1, max_length=64)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_max_length=4096)


class Evidence(Closed):
    kind: str
    confirmed: bool
    reference: str
    known_at: str


class Nav(Closed):
    nav_date: str
    unit_nav: Amount
    accumulated_nav: Amount
    published_at: str


class Status(Closed):
    status_date: str
    latest_nav_date: str
    known_at: str
    purchase_open: bool
    redemption_open: bool
    purchase_limit: Amount | None
    redemption_limit: Amount | None


class Settlement(Closed):
    application_cutoff: str
    subscription_confirm_days: int = Field(ge=1, le=30)
    redemption_confirm_days: int = Field(ge=1, le=30)
    redemption_cash_days: int = Field(ge=1, le=30)


class Redemption(Closed):
    min_days: int = Field(ge=0, le=100000)
    max_days: int | None = Field(ge=0, le=100000)
    rate: Amount


class Purchase(Closed):
    min_amount: Amount
    max_amount: Amount | None
    rate: Amount | None = None
    fixed_amount: Amount | None = None


class Fee(Closed):
    channel: str
    share_class: str
    purchase_rate: Amount
    annual_service_rate: Amount
    verified_on: str
    effective_from: str
    effective_to: str | None = None
    evidence_reference: str
    redemption_tiers: list[Redemption] = Field(min_length=1, max_length=100)
    purchase_tiers: list[Purchase] = Field(default_factory=list, max_length=100)
    service_refund_rule: dict | None = None


class Order(Closed):
    submitted_at: str
    signal_nav_date: str
    action: Literal["buy", "add", "sell", "reduce"]
    amount: Amount
    quantity: Amount | None


class Bundle(Closed):
    version: Literal["fund-execution-evidence-v1"]
    symbol: str
    capital: Amount
    calendar: list[str] = Field(min_length=2, max_length=10000)
    corporate_actions_review: str
    evidence: list[Evidence] = Field(min_length=6, max_length=6)
    navs: list[Nav] = Field(min_length=2, max_length=10001)
    statuses: list[Status] = Field(min_length=2, max_length=10000)
    settlement: Settlement
    fees: list[Fee] = Field(min_length=1, max_length=100)
    orders: list[Order] = Field(min_length=1, max_length=1000)
