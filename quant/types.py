from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class Security:
    ts_code: str
    name: str
    list_date: date
    is_st: bool = False
    market_id: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class SecurityStatusRecord:
    ts_code: str
    effective_from: date
    effective_to: date | None = None
    is_st: bool = False
    status_name: str | None = None


@dataclass(frozen=True)
class RawRecord:
    """Source-preserving record used for replay, audits, and revised filings."""
    dataset: str
    ts_code: str | None
    payload: dict[str, Any]
    report_period: date | None = None
    ann_date: date | None = None
    first_ann_date: date | None = None
    version: str | None = None


@dataclass(frozen=True)
class PriceBar:
    ts_code: str
    trade_date: date
    close: float
    adj_factor: float = 1.0
    suspended: bool = False
    limit_up: bool = False
    limit_down: bool = False
    volume: float = 1.0
    open: float | None = None
    high: float | None = None
    low: float | None = None

    @property
    def adjusted_close(self) -> float:
        return self.close * self.adj_factor

    @property
    def adjusted_open(self) -> float | None:
        return self.open * self.adj_factor if self.open is not None else None

    @property
    def has_ohlc(self) -> bool:
        return all(value is not None for value in (self.open, self.high, self.low, self.close))


@dataclass(frozen=True)
class BenchmarkBar:
    ts_code: str
    trade_date: date
    close: float
    open: float | None = None


@dataclass(frozen=True)
class FinancialRecord:
    ts_code: str
    report_period: date
    ann_date: date
    revenue: float
    net_profit: float
    roe: float
    gross_margin: float
    operating_cashflow: float
    debt_ratio: float
    roic: float | None = None
    current_ratio: float | None = None
    free_cashflow: float | None = None
    deduct_net_profit: float | None = None
    data_version: str = "legacy-v0"


@dataclass(frozen=True)
class ValuationBar:
    ts_code: str
    trade_date: date
    pe_ttm: float | None
    pb: float | None
    ps_ttm: float | None
    dividend_yield: float | None
    turnover_rate: float | None = None
    data_version: str = "pit_v1.0"


@dataclass(frozen=True)
class PITContext:
    as_of_date: date
    tradable_date: date | None
    universe_id: str = "hs300"
    universe_version: str = "unknown"
    data_snapshot_id: str = "unknown"


@dataclass(frozen=True)
class StrategyTemplate:
    template_id: str
    version: str
    name: str
    weights: dict[str, float]
    top_n: int = 30
    cost_bps: float = 10.0


@dataclass(frozen=True)
class IndustryRecord:
    ts_code: str
    industry: str
    effective_date: date


@dataclass(frozen=True)
class AuditRun:
    dataset: str
    source: str
    status: str
    row_count: int
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None


@dataclass
class PITSnapshot:
    as_of_date: date
    universe: list[Security]
    financials: list[FinancialRecord]
    prices: dict[str, list[PriceBar]]
    industries: dict[str, str]
    exclusions: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FactorRow:
    as_of_date: date
    ts_code: str
    values: dict[str, float]


@dataclass
class FeatureSnapshot:
    rows: list[FactorRow]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PredictionRow:
    as_of_date: date
    ts_code: str
    score: float


@dataclass
class PredictionSnapshot:
    rows: list[PredictionRow]
    metadata: dict[str, Any]


@dataclass(frozen=True)
class MarketPredictionRow:
    trade_date: date
    canonical_instrument_id: str
    score: float
    model_version: str
    feature_snapshot_date: date
    market_id: str
    currency: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ts_code(self) -> str:
        return self.canonical_instrument_id

    @property
    def as_of_date(self) -> date:
        return self.trade_date


@dataclass(frozen=True)
class Position:
    rebalance_date: date
    ts_code: str
    weight: float
    score: float


@dataclass
class BacktestResult:
    positions: list[Position]
    equity_curve: list[tuple[date, float]]
    metrics: dict[str, float]
    trades: list[dict[str, Any]]
    benchmark_curve: list[tuple[date, float]] = field(default_factory=list)
    excess_curve: list[tuple[date, float]] = field(default_factory=list)
    annual_returns: list[dict[str, Any]] = field(default_factory=list)
    status_reason: str | None = None
    skipped_periods: list[dict[str, Any]] = field(default_factory=list)


class PortfolioStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class PortfolioOrderStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    COMPLETED = "COMPLETED"
    SUPERSEDED = "SUPERSEDED"
    FAILED = "FAILED"


class PortfolioValuationStatus(str, Enum):
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


@dataclass(frozen=True)
class ResearchPortfolio:
    portfolio_id: str
    name: str
    initial_capital: float
    benchmark_code: str = "000300.SH"
    transaction_cost_bps: float = 5.0
    status: PortfolioStatus = PortfolioStatus.ACTIVE
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    market_id: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class PortfolioTargetRevision:
    revision_id: str
    portfolio_id: str
    revision_no: int
    signal_date: date
    status: str = "PENDING"
    note: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True)
class PortfolioTargetItem:
    revision_id: str
    ts_code: str
    target_weight: float


@dataclass(frozen=True)
class PortfolioRebalanceOrder:
    order_id: str
    portfolio_id: str
    revision_id: str
    ts_code: str
    side: str
    target_weight: float
    status: PortfolioOrderStatus = PortfolioOrderStatus.PENDING
    target_quantity: float | None = None
    remaining_quantity: float | None = None
    planned_trade_date: date | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PortfolioTrade:
    trade_id: str
    order_id: str
    portfolio_id: str
    revision_id: str
    ts_code: str
    trade_date: date
    side: str
    quantity: float
    price: float
    gross_amount: float
    fee_bps: float
    fee_amount: float


@dataclass(frozen=True)
class PortfolioCashFlow:
    cash_flow_id: str
    portfolio_id: str
    flow_date: date
    flow_type: str
    amount: float
    reference_type: str
    reference_id: str
    note: str | None = None


@dataclass(frozen=True)
class PortfolioHolding:
    ts_code: str
    quantity: float
    average_cost: float
    first_buy_date: date
    realized_pnl: float
    current_price: float | None = None
    market_value: float | None = None
    unrealized_pnl: float | None = None


@dataclass(frozen=True)
class PortfolioDailyValuation:
    portfolio_id: str
    valuation_date: date
    cash: float
    market_value: float | None
    total_value: float | None
    nav: float | None
    benchmark_nav: float | None
    status: PortfolioValuationStatus
    coverage: float
    reason: str | None = None
    calculation_version: str = "portfolio_v1.0"
