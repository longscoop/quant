from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import date
import re
from typing import Any, Iterable


class UnsupportedMarketError(RuntimeError):
    """Raised when a market or market capability has no registered implementation."""


@dataclass(frozen=True)
class ResearchContext:
    market_id: str
    currency: str
    calendar_id: str
    universe_id: str
    benchmark_id: str

    def __post_init__(self) -> None:
        for field_name, value in asdict(self).items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"ResearchContext.{field_name} must be non-empty")
        object.__setattr__(self, "market_id", self.market_id.upper())
        object.__setattr__(self, "currency", self.currency.upper())

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_CANONICAL_INSTRUMENT_RE = re.compile(r"^(?P<code>[0-9A-Z]+)\.(?P<exchange>SH|SZ|HK)$")


@dataclass(frozen=True)
class CanonicalInstrument:
    code: str
    exchange: str

    @classmethod
    def parse(cls, value: str) -> "CanonicalInstrument":
        normalized = str(value).strip().upper()
        match = _CANONICAL_INSTRUMENT_RE.fullmatch(normalized)
        if match is None:
            raise ValueError(f"invalid canonical instrument id: {value!r}")
        return cls(match.group("code"), match.group("exchange"))

    def __str__(self) -> str:
        return f"{self.code}.{self.exchange}"


class MarketCalendar(ABC):
    calendar_id: str

    @abstractmethod
    def sessions(self, start: date, end: date) -> list[date]:
        raise NotImplementedError

    @abstractmethod
    def is_session(self, day: date) -> bool:
        raise NotImplementedError

    @abstractmethod
    def shift(self, day: date, sessions: int) -> date:
        raise NotImplementedError

    def next_session(self, day: date) -> date:
        return self.shift(day, 1)


class StaticMarketCalendar(MarketCalendar):
    """Deterministic calendar used by tests and immutable research snapshots."""

    def __init__(self, calendar_id: str, sessions: Iterable[date]):
        self.calendar_id = calendar_id
        self._sessions = tuple(sorted(set(sessions)))
        self._offsets = {day: offset for offset, day in enumerate(self._sessions)}

    def sessions(self, start: date, end: date) -> list[date]:
        if start > end:
            raise ValueError("calendar range start must be <= end")
        return [day for day in self._sessions if start <= day <= end]

    def is_session(self, day: date) -> bool:
        return day in self._offsets

    def shift(self, day: date, sessions: int) -> date:
        if day not in self._offsets:
            raise ValueError(f"{day} is not a session of {self.calendar_id}")
        target = self._offsets[day] + sessions
        if target < 0 or target >= len(self._sessions):
            raise ValueError(f"calendar {self.calendar_id} does not cover shift({day}, {sessions})")
        return self._sessions[target]


class InstrumentMapper(ABC):
    @abstractmethod
    def normalize(self, value: str, source: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def to_tushare(self, canonical_id: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def to_qlib(self, canonical_id: str) -> str:
        raise NotImplementedError


class UniverseProvider(ABC):
    @abstractmethod
    def members(self, universe_id: str, as_of_date: date) -> list[Any]:
        raise NotImplementedError


class PITFeatureProvider(ABC):
    @abstractmethod
    def build_features(self, context: ResearchContext, dates: Iterable[date]):
        raise NotImplementedError


@dataclass(frozen=True)
class Order:
    instrument: str
    side: str
    quantity: float
    price: float
    trade_date: date


@dataclass(frozen=True)
class PortfolioState:
    cash: float
    holdings: dict[str, float]


@dataclass(frozen=True)
class TransactionCost:
    amount: float
    audit: dict[str, Any]


class TransactionCostModel(ABC):
    @abstractmethod
    def calculate(self, order: Order, portfolio_state: PortfolioState) -> TransactionCost:
        raise NotImplementedError


@dataclass(frozen=True)
class TradabilityStatus:
    tradable: bool
    reason: str | None = None


class TradabilityProvider(ABC):
    @abstractmethod
    def status(self, instrument: str, day: date, side: str) -> TradabilityStatus:
        raise NotImplementedError


class LotSizeProvider(ABC):
    @abstractmethod
    def lot_size(self, instrument: str, day: date) -> int:
        raise NotImplementedError


@dataclass(frozen=True)
class MarketConfig:
    market_id: str
    default_context: ResearchContext
    instrument_mapper: InstrumentMapper
    qlib_region: str | None = None
    calendar: MarketCalendar | None = None
    universe_provider: UniverseProvider | None = None
    pit_feature_provider: PITFeatureProvider | None = None
    transaction_cost_model: TransactionCostModel | None = None
    tradability_provider: TradabilityProvider | None = None
    lot_size_provider: LotSizeProvider | None = None

    def require(self, capability: str):
        value = getattr(self, capability, None)
        if value is None:
            raise UnsupportedMarketError(f"{self.market_id} capability is not implemented: {capability}")
        return value


_MARKETS: dict[str, MarketConfig] = {}


def register_market(config: MarketConfig, *, replace: bool = False) -> None:
    key = config.market_id.upper()
    if key in _MARKETS and not replace:
        raise ValueError(f"market already registered: {key}")
    _MARKETS[key] = config


def get_market(market_id: str) -> MarketConfig:
    key = market_id.upper()
    try:
        return _MARKETS[key]
    except KeyError as exc:
        raise UnsupportedMarketError(f"market is not registered: {key}") from exc


def clear_market_registry() -> None:
    _MARKETS.clear()
