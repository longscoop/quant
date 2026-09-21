"""Pure calculations for auditable research-simulation portfolios."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import isfinite, sqrt
from statistics import fmean, pstdev
from typing import Mapping, Sequence


DEFAULT_FACTOR_DIMENSIONS = (
    "valuation",
    "quality",
    "growth",
    "momentum",
    "low_volatility",
    "liquidity",
)


def _finite(value) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def validate_target_weights(targets: Mapping[str, float]) -> dict[str, float]:
    """Validate target weights while preserving an intentional cash allocation."""
    normalized = {str(code): float(weight) for code, weight in targets.items()}
    if any(not isfinite(weight) or weight < 0 or weight > 1 for weight in normalized.values()):
        raise ValueError("组合权重必须在 0 到 100% 之间")
    if sum(normalized.values()) > 1.000001:
        raise ValueError("组合权重合计不能超过 100%")
    return normalized


def project_holdings(trades: Sequence[Mapping], prices: Mapping[str, float] | None = None) -> list[dict]:
    """Replay trades into current holdings using moving-average book cost."""
    states: dict[str, dict] = {}
    for trade in sorted(trades, key=lambda row: (row.get("trade_date") or date.min, str(row.get("trade_id") or ""))):
        code = str(trade["ts_code"])
        side = str(trade["side"]).upper()
        quantity = float(trade["quantity"])
        price = float(trade["price"])
        fee = float(trade.get("fee_amount") or 0.0)
        if not all(isfinite(value) for value in (quantity, price, fee)) or quantity <= 0 or price <= 0 or fee < 0:
            raise ValueError("组合成交包含无效数量、价格或费用")
        state = states.setdefault(code, {
            "ts_code": code,
            "quantity": 0.0,
            "average_cost": 0.0,
            "realized_pnl": 0.0,
            "first_buy_date": None,
        })
        if side == "BUY":
            old_cost = state["quantity"] * state["average_cost"]
            new_quantity = state["quantity"] + quantity
            state["average_cost"] = (old_cost + quantity * price + fee) / new_quantity
            state["quantity"] = new_quantity
            if state["first_buy_date"] is None:
                state["first_buy_date"] = trade.get("trade_date")
        elif side == "SELL":
            if quantity > state["quantity"] + 1e-9:
                raise ValueError(f"{code} 卖出数量超过可用模拟持仓")
            state["realized_pnl"] += quantity * price - fee - quantity * state["average_cost"]
            state["quantity"] = max(0.0, state["quantity"] - quantity)
        else:
            raise ValueError(f"未知组合成交方向：{side}")

    prices = prices or {}
    result = []
    for code, state in sorted(states.items()):
        if state["quantity"] <= 1e-9:
            continue
        current_price = float(prices[code]) if code in prices and _finite(prices[code]) else None
        market_value = state["quantity"] * current_price if current_price is not None else None
        unrealized = market_value - state["quantity"] * state["average_cost"] if market_value is not None else None
        result.append({
            **state,
            "current_price": current_price,
            "market_value": market_value,
            "unrealized_pnl": unrealized,
        })
    total_market_value = sum(row["market_value"] for row in result if row["market_value"] is not None)
    for row in result:
        row["current_weight"] = row["market_value"] / total_market_value if row["market_value"] is not None and total_market_value > 0 else None
    return result


def portfolio_metrics(nav_rows: Sequence[Mapping]) -> dict[str, float | None]:
    """Calculate risk metrics from complete observed NAV rows only."""
    rows = [
        row for row in nav_rows
        if str(row.get("status") or "COMPLETED").upper() == "COMPLETED" and _finite(row.get("nav"))
    ]
    rows.sort(key=lambda row: row["valuation_date"])
    result: dict[str, float | None] = {
        "total_return": None,
        "one_year_return": None,
        "annualized_return": None,
        "annualized_volatility": None,
        "max_drawdown": None,
        "sharpe": None,
        "benchmark_return": None,
        "excess_return": None,
        "monthly_win_rate": None,
        "annualized_turnover": None,
    }
    if not rows:
        return result
    values = [float(row["nav"]) for row in rows]
    if values[0] <= 0:
        return result
    result["total_return"] = values[-1] - 1.0
    peak = 1.0
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        max_drawdown = min(max_drawdown, value / peak - 1)
    result["max_drawdown"] = max_drawdown
    returns = [values[0] - 1.0]
    returns.extend(values[index] / values[index - 1] - 1 for index in range(1, len(values)) if values[index - 1] > 0)
    if len(returns) >= 2:
        volatility = pstdev(returns)
        result["annualized_volatility"] = volatility * sqrt(252)
        result["sharpe"] = fmean(returns) / volatility * sqrt(252) if volatility > 0 else None
    span_days = (rows[-1]["valuation_date"] - rows[0]["valuation_date"]).days
    if span_days >= 365 and values[-1] > 0:
        result["annualized_return"] = values[-1] ** (365.0 / span_days) - 1
        cutoff = rows[-1]["valuation_date"].replace(year=rows[-1]["valuation_date"].year - 1)
        base = next((row for row in rows if row["valuation_date"] >= cutoff), None)
        if base and float(base["nav"]) > 0:
            result["one_year_return"] = values[-1] / float(base["nav"]) - 1
    benchmark_rows = [row for row in rows if _finite(row.get("benchmark_nav"))]
    if benchmark_rows and float(benchmark_rows[-1]["benchmark_nav"]) > 0:
        result["benchmark_return"] = float(benchmark_rows[-1]["benchmark_nav"]) - 1.0
        if result["total_return"] is not None:
            result["excess_return"] = result["total_return"] - result["benchmark_return"]
    month_ends = {}
    for row in rows:
        month_ends[(row["valuation_date"].year, row["valuation_date"].month)] = float(row["nav"])
    monthly_values = [month_ends[key] for key in sorted(month_ends)]
    if len(monthly_values) >= 2:
        monthly_returns = [monthly_values[index] / monthly_values[index - 1] - 1 for index in range(1, len(monthly_values))]
        result["monthly_win_rate"] = sum(value > 0 for value in monthly_returns) / len(monthly_returns)
    return result


def industry_exposure(holdings: Sequence[Mapping], industry_map: Mapping[str, str]) -> dict[str, float]:
    """Aggregate current market values using an already PIT-filtered industry map."""
    valid = [row for row in holdings if _finite(row.get("market_value")) and float(row["market_value"]) >= 0]
    total = sum(float(row["market_value"]) for row in valid)
    if total <= 0:
        return {}
    result = defaultdict(float)
    for row in valid:
        result[industry_map.get(str(row["ts_code"]), "未分类")] += float(row["market_value"]) / total
    return dict(sorted(result.items(), key=lambda item: item[1], reverse=True))


def _factor_value(item: Mapping, name: str) -> float | None:
    factor = (item.get("factors") or {}).get(name)
    if isinstance(factor, Mapping):
        factor = factor.get("score")
    available = (item.get("availability") or {}).get(name, factor is not None)
    return float(factor) if available and _finite(factor) else None


def factor_exposure(holdings: Sequence[Mapping], snapshot_items: Sequence[Mapping]) -> dict:
    """Return market-value-weighted PIT factor Z-scores with truthful coverage."""
    names = set(DEFAULT_FACTOR_DIMENSIONS)
    for item in snapshot_items:
        names.update((item.get("factors") or {}).keys())
    zscores: dict[str, dict[str, float]] = {}
    for name in sorted(names):
        values = {
            str(item["ts_code"]): value
            for item in snapshot_items
            if (value := _factor_value(item, name)) is not None
        }
        if len(values) < 2:
            zscores[name] = {}
            continue
        mean = fmean(values.values())
        deviation = pstdev(values.values())
        zscores[name] = {code: (value - mean) / deviation for code, value in values.items()} if deviation > 0 else {}
    held = [row for row in holdings if _finite(row.get("market_value")) and float(row["market_value"]) > 0]
    total = sum(float(row["market_value"]) for row in held)
    factors = {}
    for name in sorted(names):
        covered = [row for row in held if str(row["ts_code"]) in zscores[name]]
        covered_value = sum(float(row["market_value"]) for row in covered)
        factors[name] = {
            "value": (
                sum(float(row["market_value"]) * zscores[name][str(row["ts_code"])] for row in covered) / covered_value
                if covered_value > 0 else None
            ),
            "coverage": covered_value / total if total > 0 else 0.0,
        }
    return {"factors": factors}
