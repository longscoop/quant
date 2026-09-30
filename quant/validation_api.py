"""Streamlit-free public projections for factor historical validation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from math import isclose, isfinite
from typing import Any

from .templates import TEMPLATES
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION
from .workflows import _historical_universe_version, _monthly_rebalance_dates
from .validation_portfolio import DEFAULT_SIMULATION_CAPITAL, add_holding_details


def experiment_id(run: Mapping) -> str | None:
    return sha256(f"factor-experiment-v1:{run['run_id']}".encode()).hexdigest()[:24] if run.get("run_id") else None


def factor_experiments(store) -> list[dict]:
    if hasattr(store, "list_factor_experiments"):
        return store.list_factor_experiments()
    return [run for run in store.list_runs() if run.get("run_type") == "backtest" and (run.get("parameters") or {}).get("strategy_type") == "FACTOR"]


def get_factor_experiment(store, public_id: str) -> dict:
    header = next((run for run in factor_experiments(store) if experiment_id(run) == public_id), None)
    if header is None:
        raise KeyError("历史实验不存在")
    return store.get_run(header["run_id"])


def validation_templates() -> list[dict[str, Any]]:
    descriptions = {
        "quality_growth": "兼顾盈利质量与成长，综合估值、趋势及行业表现。",
        "value_growth": "在成长与盈利质量基础上，更重视估值合理性。",
        "industry_trend": "侧重行业景气与价格动量，关注趋势延续。",
        "low_valuation": "主要按估值评分排序，同时考虑质量与风险。",
        "high_dividend": "偏重估值、质量与低波动；没有独立的高股息率筛选门槛。",
    }
    return [{"id": key, "name": "稳健价值（原高股息）" if key == "high_dividend" else value.name,
             "description": descriptions[key], "weights": dict(value.weights), "version": value.version}
            for key, value in TEMPLATES.items()]


def latest_factor_validation(store) -> dict | None:
    """Select only FACTOR experiments; MODEL runs have a separate contract."""
    records = factor_experiments(store)
    if not records:
        return None
    return store.get_run(records[0]["run_id"]) if hasattr(store, "list_factor_experiments") else records[0]


ADJUSTMENT_LABELS = {"OPEN": "建仓", "INCREASE": "加仓", "REDUCE": "减仓", "CLOSE": "清仓", "HOLD": "持有不变"}


def validation_adjustments(payload: Mapping, store=None, *, capital: float = DEFAULT_SIMULATION_CAPITAL) -> list[dict[str, Any]]:
    """Expose actual fills and unchanged holdings without inferring legacy actions."""
    fields = ("date", "execution_date", "ts_code", "name", "action", "side", "quantity_before", "quantity_after", "quantity", "price", "market_open", "weight", "weight_before", "weight_after", "trade_weight", "cost", "deferred", "adj_factor", "reference_price", "adjusted_reference_price")
    rows = [{key: row.get(key) for key in fields} for row in payload.get("trades") or [] if isinstance(row, Mapping)]
    for row in payload.get("order_audit") or []:
        if not isinstance(row, Mapping) or row.get("status") != "unchanged":
            continue
        rows.append({key: {"date": row.get("signal_date"), "execution_date": row.get("attempt_date"), "weight": row.get("target_weight")}.get(key, row.get(key)) for key in fields})
    evidence = None
    if store is not None and hasattr(store, "load_validation_evidence") and rows:
        codes = sorted({row["ts_code"] for row in rows if row.get("ts_code")})
        days = sorted({date.fromisoformat(str(row["execution_date"])) for row in rows if row.get("execution_date")})
        evidence = store.load_validation_evidence(codes, days)
    for row in rows:
        if evidence is not None:
            security = evidence.securities.get(row["ts_code"])
            if security:
                row["name"] = security.name
        row["price_status"] = "not_traded" if row.get("action") == "HOLD" else "recorded" if row.get("market_open") is not None else "missing"
        if row["price_status"] == "missing" and evidence is not None and row.get("execution_date"):
            bar = evidence.prices.get((row["ts_code"], date.fromisoformat(str(row["execution_date"]))))
            # Older fills stored adjusted prices. Only map to the raw open when
            # that exact day's adjusted open still agrees with the saved fill.
            if bar is not None and bar.open is not None and row.get("price") is not None:
                if isfinite(bar.open) and bar.open > 0 and isclose(bar.adjusted_open, float(row["price"]), rel_tol=1e-9, abs_tol=1e-9):
                    row["market_open"] = bar.open
                    row["price_status"] = "verified"
                else:
                    row["price_status"] = "mismatch"
        if row.get("action") == "HOLD" and evidence is not None and row.get("execution_date"):
            bar = evidence.prices.get((row["ts_code"], date.fromisoformat(str(row["execution_date"]))))
            if bar and bar.open is not None:
                row.update(adj_factor=bar.adj_factor, reference_price=bar.open, adjusted_reference_price=bar.adjusted_open)
    add_holding_details(rows, capital)
    return sorted(rows, key=lambda row: (str(row.get("date") or ""), str(row.get("execution_date") or ""), str(row.get("ts_code") or "")))


def trading_notes(payload: Mapping, store=None) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for kind, items in (("suspension", payload.get("valuation_audit") or []), ("deferred", payload.get("order_audit") or [])):
        for row in items:
            if kind == "deferred" and row.get("status") != "deferred":
                continue
            key = (kind, row.get("ts_code"), str(row.get("suspend_date") if kind == "suspension" else row.get("signal_date")), row.get("reason"))
            day = str(row.get("date") if kind == "suspension" else row.get("attempt_date"))
            item = groups.setdefault(key, {"kind": kind, "ts_code": row.get("ts_code"), "name": None, "start_date": day, "end_date": day, "days": set(), "reason": row.get("reason"), "resume_date": row.get("resume_date"), "resolution_date": None})
            item["start_date"], item["end_date"] = min(item["start_date"], day), max(item["end_date"], day)
            item["days"].add(day)
            if kind == "deferred":
                fills = [str(trade["execution_date"]) for trade in payload.get("trades") or [] if trade.get("ts_code") == row.get("ts_code") and str(trade.get("date")) == str(row.get("signal_date")) and trade.get("execution_date") and str(trade["execution_date"]) >= day]
                item["resolution_date"] = min(fills) if fills else None
    names = store.load_validation_evidence(sorted({item["ts_code"] for item in groups.values() if item["ts_code"]}), []).securities if groups and store is not None and hasattr(store, "load_validation_evidence") else {}
    for item in groups.values():
        item["session_count"] = len(item.pop("days"))
        security = names.get(item["ts_code"])
        item["name"] = security.name if security else None
    return list(groups.values())


def validation_page_model(run: Mapping | None, store=None) -> dict[str, Any]:
    if run is None:
        return {"status": "INSUFFICIENT_DATA", "reason": "尚无历史验证记录。", "result": None}

    status = str(run.get("status") or "failed").upper()
    payload = run.get("payload") or {}
    parameters = run.get("parameters") or {}
    coverage = payload.get("coverage_summary") or {}
    base = {
        "experiment_id": experiment_id(run),
        "initial_capital": parameters.get("initial_capital", DEFAULT_SIMULATION_CAPITAL),
        "capital_is_display_assumption": "initial_capital" not in parameters,
        "created_at": str(run.get("created_at")) if run.get("created_at") else None,
        "experiment_name": parameters.get("experiment_name") or "未命名历史验证",
        "template_id": parameters.get("template_id"),
        "start_date": str(parameters.get("start_date")) if parameters.get("start_date") else None,
        "end_date": str(parameters.get("end_date")) if parameters.get("end_date") else None,
        "top_n": parameters.get("top_n"),
        "cost_bps": parameters.get("cost_bps"),
        "coverage": {
            "requested_periods": coverage.get("requested_periods"),
            "valid_periods": coverage.get("valid_periods"),
            "skipped_periods": coverage.get("skipped_periods"),
        },
        "skipped_periods": [
            {"date": str(row.get("date")) if row.get("date") else None,
             "exit_date": str(row.get("exit_date")) if row.get("exit_date") else None,
             "reason": row.get("reason")}
            for row in payload.get("skipped_periods") or [] if isinstance(row, Mapping)
        ],
    }
    if status != "COMPLETED":
        reason = payload.get("status_reason") if status in {"PARTIAL", "INSUFFICIENT_DATA", "NOT_TRAINABLE"} else None
        return {"status": status, "reason": reason or "历史验证暂时无法显示。", "result": base}

    def curve(name: str) -> list[dict[str, Any]]:
        return [
            {"date": str(row["date"]), "value": row["value"]}
            for row in payload.get(name) or []
            if isinstance(row, Mapping) and row.get("date") and row.get("value") is not None
        ]

    def public_rows(name: str, fields: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {key: row.get(key) for key in fields}
            for row in payload.get(name) or [] if isinstance(row, Mapping)
        ]

    metrics = payload.get("metrics") or {}
    metric_names = (
        "total_return", "benchmark_return", "excess_return", "max_drawdown",
        "annualized_return", "annualized_volatility", "sharpe",
    )
    base.update({
        "metrics": {name: metrics.get(name) for name in metric_names},
        "equity_curve": curve("equity_curve"),
        "benchmark_curve": curve("benchmark_curve"),
        "excess_curve": curve("excess_curve"),
        "annual_returns": public_rows("annual_returns", ("year", "strategy", "benchmark")),
        "trades": public_rows("trades", ("date", "execution_date", "ts_code", "side", "action", "quantity_before", "quantity_after", "price", "quantity", "weight", "deferred", "gross_amount", "cost_bps", "cost", "transaction_cost_fraction")),
        "adjustments": validation_adjustments(payload, store, capital=base["initial_capital"]),
        "trading_notes": trading_notes(payload, store),
        "target_simulations": store.list_factor_target_simulations(experiment_id(run)) if store is not None and hasattr(store, "list_factor_target_simulations") and experiment_id(run) else [],
        "valuation_audit": public_rows("valuation_audit", ("date", "ts_code", "mark_price", "method", "suspend_date", "resume_date")),
        "order_audit": public_rows("order_audit", ("signal_date", "attempt_date", "ts_code", "side", "status", "reason", "deferred", "price", "quantity", "cost", "action", "quantity_before", "quantity_after")),
    })
    return {"status": status, "reason": None, "result": base}


def validation_history(store) -> list[dict[str, Any]]:
    return [
        {
            "experiment_id": experiment_id(run),
            "metrics": {key: ((run.get("payload") or {}).get("metrics") or {}).get(key)
                        if str(run.get("status")).upper() == "COMPLETED" else None
                        for key in ("total_return", "annualized_return", "max_drawdown", "sharpe")},
            "actual_start_date": run.get("actual_start_date") or next((str(row["date"]) for row in (run.get("payload") or {}).get("equity_curve") or [] if row.get("date")), None),
            "actual_end_date": run.get("actual_end_date") or next((str(row["date"]) for row in reversed((run.get("payload") or {}).get("equity_curve") or []) if row.get("date")), None),
            "start_date": (run.get("parameters") or {}).get("start_date"),
            "end_date": (run.get("parameters") or {}).get("end_date"),
            "initial_capital": (run.get("parameters") or {}).get("initial_capital", DEFAULT_SIMULATION_CAPITAL),
            "experiment_name": (run.get("parameters") or {}).get("experiment_name") or "未命名历史验证",
            "template_id": (run.get("parameters") or {}).get("template_id"),
            "status": str(run.get("status") or "failed").upper(),
            "top_n": (run.get("parameters") or {}).get("top_n"),
            "created_at": str(run.get("created_at")) if run.get("created_at") else None,
        }
        for run in factor_experiments(store)
    ]


def validation_date_range_is_valid(start_date: date, end_date: date) -> bool:
    return start_date <= end_date


def validation_data_check(store, start_date: date, end_date: date) -> dict[str, Any]:
    memory = store.load_memory() if hasattr(store, "load_memory") else store
    dates = _monthly_rebalance_dates(memory, start_date, end_date)[:-1]
    cached = sum(bool(store.get_factor_snapshot(
        day, FACTOR_MODEL_VERSION, PIT_DATA_VERSION,
        _historical_universe_version(memory, day),
    )) for day in dates)
    return {
        "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "complete_periods": len(dates), "cached_snapshots": cached,
        "status": "INSUFFICIENT_DATA" if len(dates) < 2 else "READY_TO_RUN",
    }


def validation_day_holdings(run: Mapping, day: date, store) -> dict:
    """Project saved fills through a selected close; never include subsequent trades."""
    from .validation_portfolio import replay_account

    payload = run.get("payload") or {}
    if str(run.get("status")).upper() != "COMPLETED":
        raise ValueError("此实验尚未完成，无法展示完整的每日持仓。")
    point = next((row for row in payload.get("equity_curve") or [] if str(row.get("date")) == day.isoformat()), None)
    if point is None:
        raise ValueError("所选日期不在实验的已保存估值区间。")
    if not isinstance(payload.get("trades"), list):
        raise ValueError("此实验缺少完整成交记录，无法还原每日持仓。")
    if any(not row.get("execution_date") for row in payload["trades"]):
        raise ValueError("成交日期缺失，无法还原每日持仓。")
    trades = [dict(row) for row in payload["trades"] if str(row["execution_date"]) <= day.isoformat()]
    holdings, cash = replay_account({"trades": trades})
    capital = (run.get("parameters") or {}).get("initial_capital", DEFAULT_SIMULATION_CAPITAL)
    evidence = store.load_validation_evidence(sorted(holdings), [day])
    marks = []
    for code, quantity in holdings.items():
        bar = evidence.prices.get((code, day))
        marks.append({"ts_code": code, "name": evidence.securities[code].name if code in evidence.securities else None,
                      "execution_date": day.isoformat(), "action": "HOLD", "quantity_before": quantity,
                      "quantity_after": quantity, "quantity": 0.0,
                      "reference_price": bar.close if bar else None,
                      "adjusted_reference_price": bar.adjusted_close if bar else None,
                      "adj_factor": bar.adj_factor if bar else None})
    add_holding_details(trades + marks, capital)
    fields = ("ts_code", "name", "first_buy_date", "last_buy_date", "last_buy_price", "holding_shares", "average_cost", "holding_return", "holding_value", "reference_price")
    return {"date": day.isoformat(), "cash": cash * capital, "total_value": point["value"] * capital,
            "status": "PARTIAL" if any(row["holding_value"] is None for row in marks) else "COMPLETED",
            "rows": [{key: row.get(key) for key in fields} for row in marks]}
