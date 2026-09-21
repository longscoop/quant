"""Pure helpers for presenting research-run summaries."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from math import ceil, isfinite
from statistics import median

from .research import research_candidate_rows as _public_research_candidate_rows


def data_quality_summary(memory):
    """Summarize the in-memory data snapshot without changing it."""
    dates = [bar.trade_date for bar in memory.prices.values()]
    return {
        "latest_trade_date": max(dates) if dates else None,
        "security_count": len(memory.securities),
        "price_count": len(memory.prices),
        "financial_count": len(memory.financials),
    }


def financial_detail_rows(memory, code: str) -> list[dict]:
    """Pair each disclosed financial report with the last valuation known on its announcement date."""
    valuations = memory.valuations_for(code)
    rows = []
    for financial in sorted(memory.financials_for(code), key=lambda row: (row.report_period, row.ann_date)):
        valuation = next((bar for bar in reversed(valuations) if bar.trade_date <= financial.ann_date), None)
        row = dict(financial.__dict__)
        row["valuation_date"] = valuation.trade_date if valuation else None
        if valuation:
            row.update({
                "pe": valuation.pe_ttm,
                "pb": valuation.pb,
                "ps": valuation.ps_ttm,
                "dividend_yield": valuation.dividend_yield,
            })
        rows.append(row)
    return rows


def run_status_summary(run):
    """Translate a stored run status into a concise research-workflow message."""
    payload = run.get("payload") or {}
    reason = (payload.get("metadata") or {}).get("status_reason")
    if run.get("status") == "not_trainable":
        return {
            "label": "不可训练",
            "next_step": "请构建覆盖预测日期及更早日期的因子运行。",
            "reason": reason,
        }
    return {
        "label": "已完成" if run.get("status") == "completed" else "失败",
        "next_step": "前往下一研究步骤。",
        "reason": run.get("error"),
    }


def model_signal_rows(run, securities):
    """Rank model rows and label them as research signals, never trade actions."""
    source_rows = (run.get("payload") or {}).get("rows") or []
    valid_rows = []
    invalid_rows = []
    for row in source_rows:
        score = row.get("score")
        try:
            valid = score is not None and isfinite(float(score))
        except (TypeError, ValueError):
            valid = False
        (valid_rows if valid else invalid_rows).append(row)
    rows = sorted(valid_rows, key=lambda row: row["score"], reverse=True)
    count = len(rows)
    upper_end = ceil(count / 3)
    middle_end = ceil(count * 2 / 3)
    result = []
    for index, row in enumerate(rows):
        code = row.get("ts_code")
        security = securities.get(code)
        if index < upper_end:
            signal = "研究候选"
        elif index < middle_end:
            signal = "中性"
        else:
            signal = "低优先级"
        result.append({
            "名称": security.name if security is not None else "未知证券",
            "代码": code,
            "分数": row.get("score"),
            "研究信号": signal,
        })
    for row in invalid_rows:
        code = row.get("ts_code")
        security = securities.get(code)
        result.append({
            "名称": security.name if security is not None else "未知证券",
            "代码": code,
            "分数": None,
            "研究信号": "数据不可用",
        })
    return result


def _finite(value):
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _display_score(value):
    """Normalize model scores and factor scores to a 0-100 research scale."""
    if not _finite(value):
        return None
    value = float(value)
    return value * 100 if -1 <= value <= 1 else value


def _latest_industry_map(memory, as_of=None):
    latest = {}
    for (code, effective), record in getattr(memory, "industries", {}).items():
        if as_of is not None and effective > as_of:
            continue
        if code not in latest or effective > latest[code][0]:
            latest[code] = (effective, record.industry or "未分类")
    return {code: industry for code, (_, industry) in latest.items()}


def market_snapshot(memory):
    """Return a truthful, compact market/data snapshot for the homepage."""
    benchmark = memory.benchmark_for("000300.SH")
    latest_trade_date = max((bar.trade_date for bar in memory.prices.values()), default=None)
    benchmark_change = None
    if len(benchmark) >= 2 and _finite(benchmark[-2].close) and _finite(benchmark[-1].close) and benchmark[-2].close:
        benchmark_change = benchmark[-1].close / benchmark[-2].close - 1
    security_count = len(memory.securities)
    price_count = len(memory.prices)
    industry_count = len(set(_latest_industry_map(memory).values()))
    data_health = "数据完整" if security_count and price_count and benchmark else "数据不足"
    if benchmark_change is None:
        market_label = "不可用"
    elif benchmark_change >= 0.005:
        market_label = "偏多"
    elif benchmark_change <= -0.005:
        market_label = "偏空"
    else:
        market_label = "中性"
    return {
        "latest_trade_date": latest_trade_date,
        "benchmark_change": benchmark_change,
        "market_label": market_label,
        "security_count": security_count,
        "price_count": price_count,
        "industry_count": industry_count,
        "data_health": data_health,
    }


def _model_candidate_rows(memory, run):
    source = (run.get("payload") or {}).get("rows") or []
    valid = [row for row in source if _finite(row.get("score"))]
    invalid = [row for row in source if not _finite(row.get("score"))]
    valid.sort(key=lambda row: float(row["score"]), reverse=True)
    first_end, second_end = ceil(len(valid) / 3), ceil(len(valid) * 2 / 3)
    result = []
    industries = _latest_industry_map(memory)
    for index, row in enumerate(valid):
        code = row.get("ts_code")
        security = memory.securities.get(code)
        result.append({
            "名称": security.name if security else "未知证券",
            "代码": code,
            "研究信号": "研究候选" if index < first_end else "中性" if index < second_end else "低优先级",
            "综合分": _display_score(row.get("score")),
            "行业": industries.get(code, "未分类"),
            "数据覆盖率": None,
            "关键因子": "模型预测",
            "as_of_date": row.get("as_of_date"),
        })
    for row in invalid:
        code = row.get("ts_code")
        security = memory.securities.get(code)
        result.append({"名称": security.name if security else "未知证券", "代码": code, "研究信号": "数据不可用", "综合分": None, "行业": industries.get(code, "未分类"), "数据覆盖率": None, "关键因子": "数据不可用", "as_of_date": row.get("as_of_date")})
    return result


def _factor_candidate_rows(memory, run):
    source = (run.get("payload") or {}).get("rankings") or []
    industries = _latest_industry_map(memory)
    valid = [row for row in source if _finite(((row.get("score") or {}).get("score")))]
    valid.sort(key=lambda row: float(row["score"]["score"]), reverse=True)
    first_end, second_end = ceil(len(valid) / 3), ceil(len(valid) * 2 / 3)
    result = []
    for index, row in enumerate(valid):
        code = row.get("ts_code")
        security = memory.securities.get(code)
        factors = row.get("factors") or {}
        factor_names = ("quality", "growth", "valuation", "momentum", "industry")
        factor_values = [f"{name}:{_display_score((factors.get(name) or {}).get('score')):.0f}" for name in factor_names if _finite((factors.get(name) or {}).get("score"))]
        result.append({
            "名称": security.name if security else "未知证券",
            "代码": code,
            "研究信号": "研究候选" if index < first_end else "中性" if index < second_end else "低优先级",
            "综合分": _display_score((row.get("score") or {}).get("score")),
            "行业": industries.get(code, "未分类"),
            "数据覆盖率": (row.get("score") or {}).get("coverage"),
            "关键因子": " · ".join(factor_values) if factor_values else "因子证据不可用",
            "as_of_date": row.get("as_of_date"),
        })
    return result


def research_candidate_rows(memory, factor_run=None, model_run=None):
    """Build the legacy candidate rows from the public projection.

    Existing callers retain their established field names while the public
    research layer owns candidate ordering and evidence interpretation.
    """
    legacy_signals = {
        "优先研究": "研究候选",
        "持续观察": "中性",
        "暂缓研究": "低优先级",
        "证据不足": "数据不可用",
    }
    result = []
    for row in _public_research_candidate_rows(memory, factor_run=factor_run, model_run=model_run):
        details = row["专业详情"]
        result.append({
            "名称": row["名称"],
            "代码": row["代码"],
            "研究信号": legacy_signals[row["研究倾向"]],
            "综合分": details["模型分数"] if details["模型分数"] is not None else details["因子综合分"],
            "行业": row["行业"],
            "数据覆盖率": details["数据覆盖率"],
            "关键因子": row["核心优势"],
            "as_of_date": None if row["数据日期"] == "未知" else row["数据日期"],
        })
    return result


def filter_candidate_rows(rows, signal=None, industry=None, min_score=None, min_coverage=None):
    """Apply user-facing pool filters without changing the source rows."""
    result = []
    for row in rows:
        if signal and row.get("研究信号") != signal:
            continue
        if industry and row.get("行业") != industry:
            continue
        score = row.get("综合分")
        if min_score is not None and (not _finite(score) or float(score) < min_score):
            continue
        coverage = row.get("数据覆盖率")
        if min_coverage is not None and (not _finite(coverage) or float(coverage) < min_coverage):
            continue
        result.append(row)
    return result


def paginate_candidate_rows(rows, query="", page=1, page_size=20):
    """Search candidate identity fields, then return one bounded page."""
    needle = str(query or "").strip().lower()
    matched = [
        row for row in rows
        if not needle or needle in str(row.get("代码", "")).lower() or needle in str(row.get("名称", "")).lower()
    ]
    size = max(1, int(page_size))
    current = max(1, int(page))
    start = (current - 1) * size
    return matched[start:start + size], len(matched)


def _relative_return(memory, code, periods):
    prices = memory.prices_for(code)
    benchmark = memory.benchmark_for("000300.SH")
    if len(prices) <= periods or len(benchmark) <= periods:
        return None
    stock = {bar.trade_date: bar.adjusted_close for bar in prices}
    index = {bar.trade_date: bar.close for bar in benchmark}
    dates = sorted(set(stock) & set(index))
    if len(dates) <= periods:
        return None
    start, end = dates[-periods - 1], dates[-1]
    if not stock[start] or not index[start] or not index[end]:
        return None
    return stock[end] / stock[start] - index[end] / index[start]


def industry_summary_rows(memory, rankings=None, as_of=None):
    """Aggregate only available candidate/price evidence by latest industry."""
    industries = _latest_industry_map(memory, as_of)
    ranking_map = {row.get("ts_code"): row for row in (rankings or [])}
    groups = defaultdict(list)
    for code in memory.securities:
        groups[industries.get(code, "未分类")].append(code)
    valid_scores = sorted((_display_score((row.get("score") or {}).get("score")) for row in ranking_map.values() if _finite((row.get("score") or {}).get("score"))), reverse=True)
    candidate_cutoff = valid_scores[ceil(len(valid_scores) / 3) - 1] if valid_scores else None
    result = []
    for industry, codes in sorted(groups.items()):
        scores = [_display_score((ranking_map[code].get("score") or {}).get("score")) for code in codes if code in ranking_map and _finite((ranking_map[code].get("score") or {}).get("score"))]
        growth = [_display_score(((ranking_map[code].get("factors") or {}).get("growth") or {}).get("score")) for code in codes if code in ranking_map and _finite(((ranking_map[code].get("factors") or {}).get("growth") or {}).get("score"))]
        momentum = [_relative_return(memory, code, 20) for code in codes]
        momentum = [value for value in momentum if _finite(value)]
        momentum_60 = [_relative_return(memory, code, 60) for code in codes]
        momentum_60 = [value for value in momentum_60 if _finite(value)]
        candidate_count = sum(1 for score in scores if candidate_cutoff is not None and score >= candidate_cutoff)
        median_score = median(scores) if scores else None
        median_growth = median(growth) if growth else None
        median_momentum = median(momentum) if momentum else None
        median_momentum_60 = median(momentum_60) if momentum_60 else None
        if median_score is None:
            label = "证据不足"
        elif median_score >= 75 and (median_growth is None or median_growth >= 65 or (median_momentum is not None and median_momentum > 0)):
            label = "高景气"
        elif median_score >= 50:
            label = "中性"
        else:
            label = "低景气"
        result.append({"行业": industry, "股票数量": len(codes), "研究候选数量": candidate_count, "综合分中位数": median_score, "成长因子中位数": median_growth, "20日相对表现": median_momentum, "60日相对表现": median_momentum_60, "景气标签": label})
    return result


def portfolio_exposure_summary(memory, positions):
    """Summarize weights and industry exposure for a research-only portfolio."""
    industries = _latest_industry_map(memory)
    industry_weights = defaultdict(float)
    allocated = 0.0
    for position in positions or []:
        weight = float(position.get("weight") or 0)
        allocated += weight
        industry_weights[industries.get(position.get("ts_code"), "未分类")] += weight
    return {"position_count": len(positions or []), "allocated_weight": allocated, "unallocated_weight": max(0.0, 1.0 - allocated), "industry_weights": dict(industry_weights)}


def portfolio_history_rows(memory, positions):
    """Build a transparent, price-only history for a research portfolio."""
    positions = [position for position in (positions or []) if position.get("ts_code") in memory.securities and _finite(position.get("weight"))]
    benchmark = {bar.trade_date: bar.close for bar in memory.benchmark_for("000300.SH") if _finite(bar.close)}
    if not positions or not benchmark:
        return []
    series = {}
    common_dates = set(benchmark)
    for position in positions:
        prices = {bar.trade_date: bar.adjusted_close for bar in memory.prices_for(position["ts_code"]) if _finite(bar.adjusted_close)}
        if not prices:
            return []
        series[position["ts_code"]] = prices
        common_dates &= set(prices)
    dates = sorted(common_dates)
    if not dates:
        return []
    start = dates[0]
    stock_bases = {code: prices[start] for code, prices in series.items()}
    benchmark_base = benchmark[start]
    allocated = sum(float(position["weight"]) for position in positions)
    rows = []
    for day in dates:
        portfolio_value = max(0.0, 1.0 - allocated)
        for position in positions:
            code = position["ts_code"]
            portfolio_value += float(position["weight"]) * series[code][day] / stock_bases[code]
        rows.append({"日期": day, "研究组合": portfolio_value, "沪深300": benchmark[day] / benchmark_base})
    return rows
