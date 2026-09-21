"""Shared target-portfolio execution primitives.

All strategy families should converge on target weights before this layer.  The
kernel then applies the market's lot-size, tradability and transaction-cost
contracts and emits auditable quantity-level execution records.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import floor
from typing import Mapping

from .markets import (
    LotSizeProvider,
    Order,
    PortfolioState,
    TradabilityProvider,
    TransactionCostModel,
)


@dataclass(frozen=True)
class ExecutionBatch:
    cash: float
    holdings: dict[str, float]
    target_quantities: dict[str, float]
    records: list[dict]
    gross_traded: float
    transaction_cost: float
    turnover: float


def target_weight_turnover(
    current_weights: Mapping[str, float],
    target_weights: Mapping[str, float],
) -> float:
    """Return one-way portfolio turnover under the project's existing convention.

    The project historically reports the sum of absolute target-weight changes,
    so a full A->B replacement is 2.0 and an initial fully invested rebalance is
    1.0.  Keeping this convention makes portfolio and backtest metrics directly
    comparable while removing the former fixed placeholders.
    """
    codes = set(current_weights) | set(target_weights)
    return sum(
        abs(float(target_weights.get(code, 0.0)) - float(current_weights.get(code, 0.0)))
        for code in codes
    )


def _affordable_quantity(
    *,
    instrument: str,
    requested: float,
    lot: int,
    price: float,
    day: date,
    cash: float,
    holdings: Mapping[str, float],
    cost_model: TransactionCostModel,
) -> tuple[float, object]:
    max_lots = max(0, min(int(requested // lot), int(cash // (price * lot))))
    low, high = 0, max_lots
    best_lots = 0
    best_cost = cost_model.calculate(
        Order(instrument, "BUY", 0.0, price, day),
        PortfolioState(cash, dict(holdings)),
    )
    while low <= high:
        mid = (low + high) // 2
        quantity = float(mid * lot)
        cost = cost_model.calculate(
            Order(instrument, "BUY", quantity, price, day),
            PortfolioState(cash, dict(holdings)),
        )
        required = quantity * price + float(cost.amount)
        if required <= cash + 1e-9:
            best_lots = mid
            best_cost = cost
            low = mid + 1
        else:
            high = mid - 1
    quantity = float(best_lots * lot)
    if quantity == 0:
        best_cost = cost_model.calculate(
            Order(instrument, "BUY", 0.0, price, day),
            PortfolioState(cash, dict(holdings)),
        )
    return quantity, best_cost


def execute_target_weights(
    *,
    memory,
    signal_date: date,
    execution_date: date,
    signal_value: float,
    target_weights: Mapping[str, float],
    holdings: Mapping[str, float],
    cash: float,
    tradability_provider: TradabilityProvider,
    cost_model: TransactionCostModel,
    lot_size_provider: LotSizeProvider,
    execution_codes: set[str] | None = None,
) -> ExecutionBatch:
    """Execute one target portfolio at one market session.

    Sells are processed before buys so released cash can be reused.  Rejected
    orders remain in the returned audit records and therefore can be replayed or
    compared by another engine without silently changing the market decision.
    """
    if signal_value <= 0:
        raise ValueError("signal_value must be positive")
    normalized_targets = {str(code): float(weight) for code, weight in target_weights.items()}
    if any(weight < 0 for weight in normalized_targets.values()):
        raise ValueError("target weights must be non-negative")
    if sum(normalized_targets.values()) > 1.000001:
        raise ValueError("target weights must sum to <= 1")

    working_holdings = {
        str(code): float(quantity)
        for code, quantity in holdings.items()
        if float(quantity) > 1e-12
    }
    target_quantities: dict[str, float] = {}
    preparation: dict[str, dict] = {}

    for code in sorted(set(working_holdings) | set(normalized_targets)):
        current = float(working_holdings.get(code, 0.0))
        weight = float(normalized_targets.get(code, 0.0))
        bar = memory.prices.get((code, execution_date))
        price = float(bar.adjusted_open) if bar is not None and bar.adjusted_open is not None else None
        if price is None:
            target_quantities[code] = current if weight > 0 else 0.0
            preparation[code] = {
                "current": current,
                "target": target_quantities[code],
                "price": None,
                "side": "SELL" if weight == 0 and current > 0 else "BUY",
                "requested": abs(target_quantities[code] - current),
                "reason": "missing_open",
                "target_weight": weight,
            }
            continue
        lot = int(lot_size_provider.lot_size(code, execution_date))
        if lot <= 0:
            raise ValueError(f"invalid lot size for {code}: {lot}")
        target = floor((signal_value * weight / price) / lot) * lot if weight > 0 else 0.0
        target = float(max(0, target))
        target_quantities[code] = target
        delta = target - current
        side = "SELL" if delta < 0 else "BUY"
        preparation[code] = {
            "current": current,
            "target": target,
            "price": price,
            "side": side,
            "requested": abs(delta),
            "reason": None,
            "target_weight": weight,
            "lot": lot,
        }

    records: list[dict] = []
    gross_traded = 0.0
    total_cost = 0.0
    executable = set(preparation) if execution_codes is None else set(preparation) & {str(code) for code in execution_codes}
    ordered_codes = sorted(
        executable,
        key=lambda code: (
            0 if preparation[code]["target"] < preparation[code]["current"] else 1,
            code,
        ),
    )

    for code in ordered_codes:
        info = preparation[code]
        current = float(working_holdings.get(code, 0.0))
        target = float(info["target"])
        requested = abs(target - current)
        if requested <= 1e-12:
            continue

        side = "SELL" if target < current else "BUY"
        price = info["price"]
        base_record = {
            "signal_date": signal_date,
            "execution_date": execution_date,
            "instrument": code,
            "side": side,
            "target_weight": float(info["target_weight"]),
            "target_quantity": target,
            "requested_quantity": requested,
        }
        if price is None:
            records.append(
                {
                    **base_record,
                    "executed_quantity": 0.0,
                    "actual_weight": 0.0,
                    "reason": "missing_open",
                    "remaining_quantity": requested,
                }
            )
            continue

        status = tradability_provider.status(code, execution_date, side)
        if not status.tradable:
            records.append(
                {
                    **base_record,
                    "executed_quantity": 0.0,
                    "actual_weight": current * price / signal_value,
                    "reason": status.reason,
                    "remaining_quantity": requested,
                }
            )
            continue

        lot = int(info["lot"])
        if side == "BUY":
            quantity, cost = _affordable_quantity(
                instrument=code,
                requested=requested,
                lot=lot,
                price=price,
                day=execution_date,
                cash=cash,
                holdings=working_holdings,
                cost_model=cost_model,
            )
        else:
            quantity = min(requested, current)
            order = Order(code, side, quantity, price, execution_date)
            cost = cost_model.calculate(order, PortfolioState(cash, dict(working_holdings)))

        if quantity <= 1e-12:
            records.append(
                {
                    **base_record,
                    "executed_quantity": 0.0,
                    "actual_weight": current * price / signal_value,
                    "reason": "insufficient_cash_or_position",
                    "remaining_quantity": requested,
                }
            )
            continue

        gross = float(quantity) * price
        cost_amount = float(cost.amount)
        if side == "SELL":
            cash += gross - cost_amount
            new_quantity = max(0.0, current - quantity)
        else:
            cash -= gross + cost_amount
            new_quantity = current + quantity
        if new_quantity > 1e-12:
            working_holdings[code] = new_quantity
        else:
            working_holdings.pop(code, None)

        gross_traded += gross
        total_cost += cost_amount
        records.append(
            {
                **base_record,
                "executed_quantity": float(quantity),
                "actual_weight": new_quantity * price / signal_value,
                "reason": None,
                "gross_amount": gross,
                "fee_amount": cost_amount,
                "cost_audit": dict(cost.audit),
                "remaining_quantity": max(0.0, requested - float(quantity)),
            }
        )

    return ExecutionBatch(
        cash=float(cash),
        holdings={code: quantity for code, quantity in sorted(working_holdings.items()) if quantity > 1e-12},
        target_quantities=target_quantities,
        records=records,
        gross_traded=gross_traded,
        transaction_cost=total_cost,
        turnover=gross_traded / signal_value,
    )
