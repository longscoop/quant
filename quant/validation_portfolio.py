"""Readable, capital-scaled views of the normalized historical research account."""
from __future__ import annotations

from collections.abc import Mapping
from math import isclose, isfinite
from typing import Any

DEFAULT_SIMULATION_CAPITAL = 1_000_000.0


def validate_capital(value: float) -> float:
    value = float(value)
    if not isfinite(value) or value <= 0:
        raise ValueError("模拟本金必须大于零")
    return value


def replay_account(payload: Mapping) -> tuple[dict[str, float], float]:
    """Replay saved fills only; unavailable fill evidence makes preview unavailable."""
    holdings: dict[str, float] = {}
    cash = 1.0
    if "trades" not in payload or not isinstance(payload["trades"], list):
        raise ValueError("此实验缺少完整成交记录，无法还原期末持仓，请重新运行实验。")
    for row in sorted(payload.get("trades") or [], key=lambda item: str(item.get("execution_date") or "")):
        code, side = row.get("ts_code"), row.get("side")
        quantity, price = row.get("quantity"), row.get("price")
        if not code or side not in {"BUY", "SELL"} or not row.get("execution_date") or quantity is None or price is None:
            raise ValueError("此实验缺少完整成交记录，无法还原期末持仓，请重新运行实验。")
        quantity, price = float(quantity), float(price)
        if not isfinite(quantity) or not isfinite(price) or quantity <= 0 or price <= 0:
            raise ValueError("成交数量或价格无效，无法还原期末持仓。")
        gross = quantity * price
        cost = row.get("cost")
        if cost is None and row.get("cost_bps") is not None:
            cost = gross * float(row["cost_bps"]) / 10_000
        if cost is None or not isfinite(float(cost)) or cost < 0:
            raise ValueError("此实验缺少成交费用，无法还原可用资金。")
        current = holdings.get(code, 0.0)
        after = current + (quantity if side == "BUY" else -quantity)
        if after < -1e-10:
            raise ValueError("历史成交与持仓不一致，无法模拟下期目标。")
        cash += gross - cost if side == "SELL" else -gross - cost
        if after <= 1e-12:
            holdings.pop(code, None)
        else:
            holdings[code] = after
    if cash < -1e-8:
        raise ValueError("历史成交导致资金不足，无法模拟下期目标。")
    return holdings, max(0.0, cash)


def add_holding_details(rows: list[dict[str, Any]], capital: float) -> None:
    """Weighted average cost in adjusted units, displayed at each row's own date.

    Scaling to shares uses that date's factor. It describes a fractional-share,
    dividend-reinvested research account, not a broker's integer-lot account.
    """
    capital = validate_capital(capital)
    states: dict[str, dict] = {}
    for row in sorted(rows, key=lambda item: str(item.get("execution_date") or "")):
        code = row.get("ts_code")
        state = states.setdefault(code, {"units": 0.0, "cost": 0.0, "first_buy_date": None, "last_buy_date": None, "last_buy_price": None, "valid": True})
        before = float(state["units"])
        if row.get("quantity_before") is not None and not isclose(before, float(row["quantity_before"]), abs_tol=1e-10):
            state["valid"] = False
        quantity, adjusted_price = row.get("quantity"), row.get("price")
        side = row.get("side")
        cost_before = state["cost"]
        if side in {"BUY", "SELL"} and quantity is not None and adjusted_price is not None:
            quantity, adjusted_price = float(quantity), float(adjusted_price)
            if side == "BUY":
                if before <= 1e-12:
                    state.update(first_buy_date=row.get("execution_date"), cost=0.0)
                state["cost"] += quantity * adjusted_price
                state["units"] += quantity
                state.update(last_buy_date=row.get("execution_date"), last_buy_price=row.get("market_open"))
            else:
                if quantity > before + 1e-10:
                    state["valid"] = False
                state["units"] = max(0.0, before - quantity)
                state["cost"] *= state["units"] / before if before > 0 else 0.0
        elif row.get("action") != "HOLD":
            state["valid"] = False
        after = state["units"]
        factor = row.get("adj_factor")
        reference = row.get("reference_price") or row.get("market_open")
        mark = row.get("adjusted_reference_price") or adjusted_price
        if factor is None and mark is not None and reference is not None and reference > 0:
            factor = mark / reference
        valid_mark = state["valid"] and factor is not None and isfinite(factor) and factor > 0 and reference is not None
        average = state["cost"] / after if after > 1e-12 else cost_before / before if before > 0 else None
        row.update({
            "first_buy_date": state["first_buy_date"] if state["valid"] else None,
            "last_buy_date": state["last_buy_date"] if state["valid"] else None,
            "last_buy_price": state["last_buy_price"] if state["valid"] else None,
            "holding_shares": after * factor * capital if valid_mark else None,
            "traded_shares": quantity * factor * capital if valid_mark and quantity is not None else None,
            "average_cost": average / factor if valid_mark and average is not None else None,
            "holding_return": mark / average - 1 if valid_mark and mark is not None and average and average > 0 else None,
            "holding_value": after * mark * capital if valid_mark and mark is not None else None,
            "trade_amount": quantity * adjusted_price * capital if quantity is not None and adjusted_price is not None else 0.0 if row.get("action") == "HOLD" else None,
            "fee_amount": row["cost"] * capital if row.get("cost") is not None else None,
        })
        if after <= 1e-12:
            states.pop(code, None)
