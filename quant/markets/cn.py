from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import pandas as pd

from .base import (
    CanonicalInstrument,
    InstrumentMapper,
    LotSizeProvider,
    MarketCalendar,
    MarketConfig,
    Order,
    PITFeatureProvider,
    PortfolioState,
    ResearchContext,
    TradabilityProvider,
    TradabilityStatus,
    TransactionCost,
    TransactionCostModel,
    UniverseProvider,
    UnsupportedMarketError,
)


CN_DEFAULT_CONTEXT = ResearchContext(
    market_id="CN",
    currency="CNY",
    calendar_id="CN_A_SHARE",
    universe_id="000300.SH",
    benchmark_id="000300.SH",
)


class CnInstrumentMapper(InstrumentMapper):
    def normalize(self, value: str, source: str) -> str:
        source_name = source.strip().lower()
        normalized = str(value).strip().upper()
        if source_name in {"canonical", "tushare"}:
            instrument = CanonicalInstrument.parse(normalized)
        elif source_name == "qlib":
            if len(normalized) < 3 or normalized[:2] not in {"SH", "SZ"}:
                raise UnsupportedMarketError(f"CN Qlib instrument is not implemented: {value}")
            instrument = CanonicalInstrument.parse(f"{normalized[2:]}.{normalized[:2]}")
        else:
            raise ValueError(f"unsupported instrument source: {source}")
        self._ensure_cn(instrument)
        return str(instrument)

    def to_tushare(self, canonical_id: str) -> str:
        instrument = CanonicalInstrument.parse(canonical_id)
        self._ensure_cn(instrument)
        return str(instrument)

    def to_qlib(self, canonical_id: str) -> str:
        instrument = CanonicalInstrument.parse(canonical_id)
        self._ensure_cn(instrument)
        return f"{instrument.exchange}{instrument.code}"

    @staticmethod
    def _ensure_cn(instrument: CanonicalInstrument) -> None:
        if instrument.exchange not in {"SH", "SZ"}:
            raise UnsupportedMarketError(f"CN instrument mapper does not support {instrument}")


class CnLotSizeProvider(LotSizeProvider):
    def lot_size(self, instrument: str, day: date) -> int:
        CnInstrumentMapper().to_tushare(instrument)
        return 100


class CnMarketCalendar(MarketCalendar):
    calendar_id = "CN_A_SHARE"

    def __init__(self, store):
        self.store = store

    def sessions(self, start: date, end: date) -> list[date]:
        return [
            row["trade_date"]
            for row in self.store.market_calendar_sessions(self.calendar_id, start=start, end=end)
            if row["is_open"]
        ]

    def is_session(self, day: date) -> bool:
        return bool(self.sessions(day, day))

    def shift(self, day: date, sessions: int) -> date:
        rows = [
            row["trade_date"]
            for row in self.store.market_calendar_sessions(self.calendar_id)
            if row["is_open"]
        ]
        offsets = {value: offset for offset, value in enumerate(rows)}
        if day not in offsets:
            raise ValueError(f"{day} is not a session of {self.calendar_id}")
        target = offsets[day] + sessions
        if target < 0 or target >= len(rows):
            raise ValueError(f"calendar {self.calendar_id} does not cover shift({day}, {sessions})")
        return rows[target]


class CnUniverseProvider(UniverseProvider):
    def __init__(self, store):
        self.store = store

    def members(self, universe_id: str, as_of_date: date) -> list[str]:
        return list(self.store.members_for(universe_id, as_of_date))


class CnPITFeatureProvider(PITFeatureProvider):
    def __init__(self, store, universe_provider: CnUniverseProvider):
        self.store = store
        self.universe_provider = universe_provider

    def build_features(self, context: ResearchContext, dates: Iterable[date]) -> pd.DataFrame:
        if context.market_id != "CN":
            raise UnsupportedMarketError(f"CN PIT feature provider does not support {context.market_id}")
        memory = self.store.load_memory() if hasattr(self.store, "load_memory") else self.store
        from ..factors_v1 import build_rankings
        rows = []
        for day in sorted(set(dates)):
            rankings = build_rankings(
                memory,
                day,
                context=context,
                universe_provider=self.universe_provider,
            )
            for ranking in rankings:
                rows.append({
                    "feature_date": day,
                    "canonical_instrument_id": ranking["ts_code"],
                    "market_id": context.market_id,
                    "currency": context.currency,
                    **{
                        name: value
                        for name, value in ranking.get("metrics", {}).items()
                        if name.startswith(("q_", "g_", "v_", "m_", "r_"))
                    },
                })
        return pd.DataFrame(rows)


def persist_cn_calendar_intersection(store, records) -> int:
    """Persist only sessions Tushare reports open for both SSE and SZSE."""
    by_exchange: dict[str, dict[date, bool]] = {"SSE": {}, "SZSE": {}}
    for record in records:
        exchange = str(record.ts_code or "").upper()
        if record.dataset != "trade_cal" or exchange not in by_exchange or record.report_period is None:
            continue
        is_open = str(record.payload.get("is_open", "0")).lower() in {"1", "true"}
        by_exchange[exchange][record.report_period] = is_open
    if not by_exchange["SSE"] or not by_exchange["SZSE"]:
        raise ValueError("CN calendar requires persisted SSE and SZSE sources")
    all_days = sorted(set(by_exchange["SSE"]) | set(by_exchange["SZSE"]))
    for day in all_days:
        is_open = by_exchange["SSE"].get(day) is True and by_exchange["SZSE"].get(day) is True
        store.upsert_market_calendar_session(
            "CN_A_SHARE",
            day,
            is_open,
            source="TUSHARE:SSE&SZSE",
        )
    return len(all_days)


class CnTradabilityProvider(TradabilityProvider):
    def __init__(self, store, *, min_listing_days: int = 180):
        self.store = store
        self.min_listing_days = min_listing_days

    def status(self, instrument: str, day: date, side: str) -> TradabilityStatus:
        CnInstrumentMapper().to_tushare(instrument)
        normalized_side = side.upper()
        if normalized_side not in {"BUY", "SELL"}:
            raise ValueError(f"unsupported order side: {side}")
        security = self.store.securities.get(instrument)
        if security is None:
            return TradabilityStatus(False, "unknown_security")
        status = self.store.security_status_for(instrument, day) if hasattr(self.store, "security_status_for") else None
        if status.is_st if status is not None else security.is_st:
            return TradabilityStatus(False, "st")
        if (day - security.list_date).days < self.min_listing_days:
            return TradabilityStatus(False, "new_listing")
        bar = self.store.prices.get((instrument, day))
        if bar is None:
            return TradabilityStatus(False, "missing_price")
        if bar.open is None:
            return TradabilityStatus(False, "missing_open")
        if bar.suspended:
            return TradabilityStatus(False, "suspended")
        if normalized_side == "BUY" and bar.limit_up:
            return TradabilityStatus(False, "limit_up")
        if normalized_side == "SELL" and bar.limit_down:
            return TradabilityStatus(False, "limit_down")
        return TradabilityStatus(True)


class CnTransactionCostModel(TransactionCostModel):
    def __init__(self, cost_bps: float):
        if cost_bps < 0:
            raise ValueError("cost_bps must be non-negative")
        self.cost_bps = float(cost_bps)

    def calculate(self, order: Order, portfolio_state: PortfolioState) -> TransactionCost:
        del portfolio_state
        gross_amount = abs(float(order.quantity) * float(order.price))
        amount = gross_amount * self.cost_bps / 10_000
        return TransactionCost(
            amount=amount,
            audit={
                "market_id": "CN",
                "side": order.side.upper(),
                "cost_bps": self.cost_bps,
                "sell_tax_bps": 0.0,
                "minimum_fee": 0.0,
                "gross_amount": gross_amount,
            },
        )


def build_cn_market_config(store, *, cost_bps: float = 5.0) -> MarketConfig:
    """Build the CN application adapter without registering a process-global fallback."""
    universe_provider = CnUniverseProvider(store) if store is not None else None
    return MarketConfig(
        market_id="CN",
        default_context=CN_DEFAULT_CONTEXT,
        instrument_mapper=CnInstrumentMapper(),
        qlib_region="cn",
        calendar=CnMarketCalendar(store) if store is not None else None,
        universe_provider=universe_provider,
        pit_feature_provider=CnPITFeatureProvider(store, universe_provider) if store is not None else None,
        transaction_cost_model=CnTransactionCostModel(cost_bps),
        tradability_provider=CnTradabilityProvider(store) if store is not None else None,
        lot_size_provider=CnLotSizeProvider(),
    )
