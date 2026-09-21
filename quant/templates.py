"""Versioned, transparent strategy templates."""

from __future__ import annotations

from .scoring import FACTOR_MODEL_VERSION\nfrom .types import StrategyTemplate


_VERSION = FACTOR_MODEL_VERSION


TEMPLATES = {
    "quality_growth": StrategyTemplate("quality_growth", _VERSION, "质量成长", {"quality": .20, "growth": .25, "valuation": .15, "momentum": .15, "industry": .15, "low_volatility": .07, "liquidity": .03}),
    "value_growth": StrategyTemplate("value_growth", _VERSION, "价值成长", {"quality": .20, "growth": .25, "valuation": .30, "momentum": .10, "industry": .05, "low_volatility": .07, "liquidity": .03}),
    "industry_trend": StrategyTemplate("industry_trend", _VERSION, "景气趋势", {"quality": .15, "growth": .15, "valuation": .05, "momentum": .25, "industry": .30, "low_volatility": .07, "liquidity": .03}),
    "low_valuation": StrategyTemplate("low_valuation", _VERSION, "低估值", {"quality": .15, "growth": .10, "valuation": .45, "momentum": .10, "industry": .05, "low_volatility": .10, "liquidity": .05}),
    "high_dividend": StrategyTemplate("high_dividend", _VERSION, "高股息", {"quality": .25, "growth": .05, "valuation": .30, "momentum": .05, "industry": .05, "low_volatility": .20, "liquidity": .10}),
}


def template(template_id: str = "quality_growth") -> StrategyTemplate:
    return TEMPLATES[template_id]
