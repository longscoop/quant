from __future__ import annotations

from datetime import date
from math import isfinite
from statistics import mean, pstdev
from .pit import PITRepository
from .types import FactorRow, FeatureSnapshot


def _zscore(values: dict[str, float]) -> dict[str, float]:
    finite = {code: float(value) for code, value in values.items() if isfinite(float(value))}
    if len(finite) < 2:
        return {code: 0.0 for code in values}
    m, sd = mean(finite.values()), pstdev(finite.values())
    return {
        code: 0.0 if code not in finite or sd == 0 else max(-3.0, min(3.0, (value - m) / sd))
        for code, value in finite.items()
    } | {code: 0.0 for code in values if code not in finite}


class FactorEngine:
    def __init__(self, pit: PITRepository): self.pit = pit

    def build_features(self, as_of_dates: list[date], universe: list[str] | None = None, factor_set: list[str] | None = None) -> FeatureSnapshot:
        rows = []
        for as_of in as_of_dates:
            snapshot = self.pit.snapshot(as_of, universe)
            raw = {}
            financials = {x.ts_code: x for x in snapshot.financials}
            for security in snapshot.universe:
                f, history = financials[security.ts_code], snapshot.prices[security.ts_code]
                valuations = [
                    bar
                    for bar in self.pit.store.valuations_for(security.ts_code)
                    if bar.trade_date <= as_of
                ]
                valuation = max(valuations, key=lambda bar: bar.trade_date) if valuations else None
                closes = [p.adjusted_close for p in history]
                momentum = closes[-1] / closes[max(0, len(closes) - 2)] - 1
                volatility = pstdev(closes) / mean(closes) if len(closes) > 1 else 0.0
                raw[security.ts_code] = {"growth_revenue": f.revenue / 1000, "growth_profit": f.net_profit / 100, "quality_roe": f.roe, "quality_margin": f.gross_margin, "quality_cashflow": f.operating_cashflow / max(f.net_profit, 1), "quality_debt": -f.debt_ratio, "value_pe": -valuation.pe_ttm if valuation and valuation.pe_ttm is not None else 0.0, "value_pb": -valuation.pb if valuation and valuation.pb is not None else 0.0, "value_ps": -valuation.ps_ttm if valuation and valuation.ps_ttm is not None else 0.0, "value_dividend": valuation.dividend_yield if valuation and valuation.dividend_yield is not None else 0.0, "momentum_1m": momentum, "low_volatility": -volatility, "liquidity_volume": history[-1].volume}
            all_names = sorted({name for values in raw.values() for name in values})
            normalized = {code: {} for code in raw}
            for name in all_names:
                scores = _zscore({code: values[name] for code, values in raw.items()})
                for code, score in scores.items(): normalized[code][name] = score
            for code, values in normalized.items():
                values[f"industry_{snapshot.industries[code]}"] = 1.0
                rows.append(FactorRow(as_of, code, values))
        return FeatureSnapshot(rows, {"pit_safe": True, "factor_version": "v2-six-style", "row_count": len(rows), "factor_set": factor_set or ["value", "quality", "growth", "momentum", "low_volatility", "liquidity"]})
