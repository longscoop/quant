"""Pure, audience-safe projections for public research candidates."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime
from math import ceil, isfinite

from .admin import data_is_stale


_FACTOR_CONCEPTS = (
    ("quality", "盈利质量"),
    ("growth", "成长性"),
    ("valuation", "估值"),
    ("momentum", "趋势表现"),
    ("industry", "行业比较"),
    ("risk", "风险维度"),
)
_PRIMARY_FACTOR_CONCEPTS = _FACTOR_CONCEPTS[:-1]
_DETAIL_EVIDENCE_GROUPS = (
    ("quality", "盈利质量"),
    ("growth", "成长性"),
    ("valuation", "估值"),
    ("momentum", "趋势表现"),
    ("risk", "风险"),
)
_MAX_PAGE_SIZE = 100
_UNSPECIFIED_SNAPSHOT = object()


def _mapping(value) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _finite(value) -> bool:
    try:
        return value is not None and isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _display_score(value):
    """Normalize the persisted score formats to a public 0–100 scale."""
    if not _finite(value):
        return None
    number = float(value)
    return number * 100 if -1 <= number <= 1 else number


def _parse_date(value) -> date | None:
    """Return a supported persisted date value without raising on malformed data."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            return None


def _display_date(value) -> str:
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed is not None else "未知"


def _run_rows(run, key: str) -> list[Mapping]:
    payload = _mapping(_mapping(run).get("payload"))
    rows = payload.get(key)
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def _industry_for(memory, target_code, as_of_date: date | None) -> str:
    selected = None
    for (code, effective), record in getattr(memory, "industries", {}).items():
        if code != target_code:
            continue
        effective_date = _parse_date(effective)
        if effective_date is None or (as_of_date is not None and effective_date > as_of_date):
            continue
        if selected is None or effective_date > selected[0]:
            selected = (effective_date, record.industry or "未分类")
    return selected[1] if selected is not None else "未分类"


def _ordered_rows(rows: list[Mapping], score_getter: Callable[[Mapping], object]) -> tuple[list[Mapping], set[int]]:
    valid = [(index, row) for index, row in enumerate(rows) if _finite(score_getter(row))]
    invalid = [(index, row) for index, row in enumerate(rows) if not _finite(score_getter(row))]
    valid.sort(key=lambda item: float(score_getter(item[1])), reverse=True)
    return [row for _, row in valid + invalid], {id(row) for _, row in valid}


def _newest_snapshot_rows(rows: list[Mapping]) -> list[Mapping]:
    dated_rows = [(row, _parse_date(row.get("as_of_date"))) for row in rows]
    snapshot_date = max((as_of_date for _, as_of_date in dated_rows if as_of_date is not None), default=None)
    if snapshot_date is None:
        return list(rows)
    return [row for row, as_of_date in dated_rows if as_of_date == snapshot_date]


def _deduplicate_security_rows(rows: list[Mapping]) -> list[Mapping]:
    """Keep the highest-ranked row for each security in an already ordered snapshot."""
    seen_codes = set()
    result = []
    for row in rows:
        code = row.get("ts_code")
        if code not in (None, ""):
            if code in seen_codes:
                continue
            seen_codes.add(code)
        result.append(row)
    return result


def _factor_row_by_key(factor_run, code, as_of_date: date | None) -> Mapping | None:
    if _mapping(factor_run).get("status") != "completed" or as_of_date is None:
        return None
    return next(
        (
            row
            for row in _run_rows(factor_run, "rankings")
            if row.get("ts_code") == code and _parse_date(row.get("as_of_date")) == as_of_date
        ),
        None,
    )


def _factor_evidence(factor_row: Mapping | None) -> list[dict]:
    factors = _mapping(_mapping(factor_row).get("factors"))
    evidence = []
    for key, concept in _FACTOR_CONCEPTS:
        factor = _mapping(factors.get(key))
        if factor:
            evidence.append({
                "研究概念": concept,
                "分数": _display_score(factor.get("score")),
                "覆盖率": factor.get("coverage") if _finite(factor.get("coverage")) else None,
                "可用性": "可用" if _finite(factor.get("score")) else "不足",
            })
    return evidence


def _factor_value(factor_row: Mapping | None, key: str):
    factors = _mapping(_mapping(factor_row).get("factors"))
    return _display_score(_mapping(factors.get(key)).get("score"))


def _factor_score(factor_row: Mapping | None):
    return _display_score(_mapping(_mapping(factor_row).get("score")).get("score"))


def _factor_coverage(factor_row: Mapping | None):
    coverage = _mapping(_mapping(factor_row).get("score")).get("coverage")
    return float(coverage) if _finite(coverage) else None


def _data_confidence(factor_row: Mapping | None) -> str:
    coverage = _factor_coverage(factor_row)
    if coverage is None or _factor_score(factor_row) is None:
        return "不足"
    if coverage >= 0.9:
        return "可信"
    if coverage >= 0.7:
        return "部分可用"
    return "不足"


def _core_advantage(factor_row: Mapping | None) -> str:
    strongest = max(
        ((score, concept) for key, concept in _PRIMARY_FACTOR_CONCEPTS if (score := _factor_value(factor_row, key)) is not None),
        default=None,
    )
    if strongest is None:
        return "因子证据不足，暂无法说明核心优势。"
    score, concept = strongest
    if score >= 70:
        return f"{concept}相对较强。"
    if score >= 40:
        return f"{concept}有一定支撑，仍需进一步核对。"
    return f"{concept}证据偏弱，暂未形成明确优势。"


def _risk_assessment(factor_row: Mapping | None) -> tuple[str, str]:
    score = _factor_value(factor_row, "risk")
    if score is None:
        return "未知", "因子证据不足，暂无法评估主要风险。"
    if score >= 70:
        return "较低", "风险维度相对稳健，仍需结合其他信息核对。"
    if score >= 40:
        return "中等", "风险维度处于中等水平，仍需结合其他信息核对。"
    return "较高", "风险维度相对偏弱，波动、回撤或财务风险需进一步核对。"


def _tendency(index: int | None, valid_count: int) -> str:
    if index is None or not valid_count:
        return "证据不足"
    if index < ceil(valid_count / 3):
        return "优先研究"
    if index < ceil(valid_count * 2 / 3):
        return "持续观察"
    return "暂缓研究"


def _candidate_date(model_row: Mapping | None, factor_row: Mapping | None) -> str:
    for row in (model_row, factor_row):
        parsed = _parse_date(_mapping(row).get("as_of_date"))
        if parsed is not None:
            return parsed.isoformat()
    return "未知"


def _candidate_as_of_date(model_row: Mapping | None, factor_row: Mapping | None) -> date | None:
    for row in (model_row, factor_row):
        parsed = _parse_date(_mapping(row).get("as_of_date"))
        if parsed is not None:
            return parsed
    return None


def _candidate_details(model_row: Mapping | None, factor_row: Mapping | None, *, ordering_source: str) -> dict:
    return {
        "排序依据": ordering_source,
        "模型分数": _display_score(_mapping(model_row).get("score")),
        "因子综合分": _factor_score(factor_row),
        "数据覆盖率": _factor_coverage(factor_row),
        "模型数据日期": _display_date(_mapping(model_row).get("as_of_date")),
        "因子数据日期": _display_date(_mapping(factor_row).get("as_of_date")),
        "因子证据": _factor_evidence(factor_row),
    }


def research_candidate_rows(memory, factor_run=None, model_run=None) -> list[dict]:
    """Project completed research runs into a stable, public candidate shape.

    A completed model run controls candidate membership and order.  A matching
    completed factor ranking enriches those candidates with human-readable
    evidence.  When no completed model run is available, factor rankings are
    used as the ordering source.
    """
    model_completed = _mapping(model_run).get("status") == "completed"
    factor_completed = _mapping(factor_run).get("status") == "completed"
    if model_completed:
        source_rows = _run_rows(model_run, "rows")
        score_getter = lambda row: row.get("score")
        ordering_source = "模型结果"
    elif factor_completed:
        source_rows = _run_rows(factor_run, "rankings")
        score_getter = lambda row: _mapping(row.get("score")).get("score")
        ordering_source = "因子结果"
    else:
        return []

    ordered, valid_ids = _ordered_rows(_newest_snapshot_rows(source_rows), score_getter)
    ordered = _deduplicate_security_rows(ordered)
    valid_count = sum(1 for row in ordered if id(row) in valid_ids)
    candidates = []
    for index, source_row in enumerate(ordered):
        code = source_row.get("ts_code")
        security = getattr(memory, "securities", {}).get(code)
        model_row = source_row if model_completed else None
        factor_row = _factor_row_by_key(factor_run, code, _parse_date(source_row.get("as_of_date"))) if model_completed else source_row
        candidate_as_of_date = _candidate_as_of_date(model_row, factor_row)
        valid_index = sum(1 for prior in ordered[:index] if id(prior) in valid_ids) if id(source_row) in valid_ids else None
        risk_level, risk_copy = _risk_assessment(factor_row)
        candidates.append({
            "名称": security.name if security is not None else "未知证券",
            "代码": code or "未知代码",
            "行业": _industry_for(memory, code, candidate_as_of_date),
            "研究倾向": _tendency(valid_index, valid_count),
            "核心优势": _core_advantage(factor_row),
            "主要风险": risk_copy,
            "数据可信度": _data_confidence(factor_row),
            "风险水平": risk_level,
            "数据日期": _candidate_date(model_row, factor_row),
            "专业详情": _candidate_details(model_row, factor_row, ordering_source=ordering_source),
        })
    return candidates


def _detail_sufficiency(confidence: str) -> str:
    if confidence == "可信":
        return "研究快照的有效证据覆盖较充分，但仍需结合后续公开信息核对。"
    if confidence == "部分可用":
        return "部分证据可用，缺失或覆盖不足会限制当前研究结论。"
    return "证据不足，当前研究结论不完整，不能据此形成明确判断。"


def _evidence_direction(score, *, is_risk: bool) -> tuple[str, str]:
    if score is None:
        return "证据不足", "暂缺有效研究证据，不能据此判断优势或风险。"
    if is_risk:
        if score >= 70:
            return "较低", "风险维度相对稳健，仍需结合其他公开信息核对。"
        if score >= 40:
            return "中等", "风险维度处于中等水平，仍需结合其他公开信息核对。"
        return "较高", "风险维度相对偏弱，波动、回撤或财务风险需要进一步核对。"
    if score >= 70:
        return "相对较强", "该维度在研究快照中相对较好。"
    if score >= 40:
        return "中等", "该维度处于中等水平，仍需进一步核对。"
    return "相对偏弱", "该维度在研究快照中相对偏弱，需要进一步核对。"


def _reader_evidence_groups(details: Mapping) -> list[dict]:
    evidence_by_concept = {
        item.get("研究概念"): item
        for item in details.get("因子证据", [])
        if isinstance(item, Mapping)
    }
    groups = []
    for key, concept in _DETAIL_EVIDENCE_GROUPS:
        factor_concept = "风险维度" if key == "risk" else concept
        factor = _mapping(evidence_by_concept.get(factor_concept))
        score = factor.get("分数") if _finite(factor.get("分数")) else None
        direction, explanation = _evidence_direction(score, is_risk=key == "risk")
        groups.append({
            "类别": concept,
            "方向": direction,
            "说明": explanation,
            "局限": "基于同一研究日期的相对比较，不代表未来表现或交易建议。",
        })
    return groups


def _display_value(value):
    return value if _finite(value) else "不可用"


def _financial_disclosures(memory, code: str) -> list[dict]:
    rows = []
    for record in sorted(
        getattr(memory, "financials_for", lambda _code: [])(code),
        key=lambda item: (item.report_period, item.ann_date),
    ):
        rows.append({
            "报告期": _display_date(record.report_period),
            "披露日期": _display_date(record.ann_date),
            "营业收入": _display_value(record.revenue),
            "净利润": _display_value(record.net_profit),
            "净资产收益率": _display_value(record.roe),
            "毛利率": _display_value(record.gross_margin),
            "经营现金流": _display_value(record.operating_cashflow),
            "资产负债率": _display_value(record.debt_ratio),
            "市盈率": _display_value(record.pe),
            "市净率": _display_value(record.pb),
            "市销率": _display_value(record.ps),
            "股息率": _display_value(record.dividend_yield),
            "数据版本": record.data_version or "未提供",
        })
    return rows


def _valuation_records(memory, code: str) -> list[dict]:
    rows = []
    for record in getattr(memory, "valuations_for", lambda _code: [])(code):
        rows.append({
            "数据日期": _display_date(record.trade_date),
            "市盈率": _display_value(record.pe_ttm),
            "市净率": _display_value(record.pb),
            "市销率": _display_value(record.ps_ttm),
            "股息率": _display_value(record.dividend_yield),
            "换手率": _display_value(record.turnover_rate),
            "数据版本": record.data_version or "未提供",
        })
    return rows


def _run_version(run) -> str:
    run_data = _mapping(run)
    payload = _mapping(run_data.get("payload"))
    parameters = _mapping(run_data.get("parameters"))
    for key in ("model_version", "data_version", "version"):
        value = payload.get(key, parameters.get(key))
        if value not in (None, ""):
            return str(value)
    return "未提供"


def research_stock_detail(memory, code: str, *, factor_run=None, model_run=None) -> dict:
    """Project one security into reader-facing detail without inventing absent evidence."""
    candidate = next(
        (
            row
            for row in research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
            if row.get("代码") == code
        ),
        None,
    )
    security = getattr(memory, "securities", {}).get(code)
    details = _mapping(candidate.get("专业详情")) if candidate is not None else {}
    confidence = candidate.get("数据可信度", "不足") if candidate is not None else "不足"
    as_of_date = _parse_date(candidate.get("数据日期")) if candidate is not None else None
    return {
        "名称": candidate.get("名称") if candidate is not None else (security.name if security is not None else "未知证券"),
        "代码": code,
        "行业": candidate.get("行业") if candidate is not None else _industry_for(memory, code, as_of_date),
        "数据日期": candidate.get("数据日期") if candidate is not None else "未知",
        "研究结论": candidate.get("研究倾向") if candidate is not None else "证据不足",
        "值得关注的原因": candidate.get("核心优势") if candidate is not None else "因子证据不足，暂无法说明值得关注的原因。",
        "主要风险": candidate.get("主要风险") if candidate is not None else "因子证据不足，暂无法评估主要风险。",
        "数据充分程度": _detail_sufficiency(confidence),
        "证据组": _reader_evidence_groups(details),
        "专业详情": {
            "排序依据": details.get("排序依据", "未提供"),
            "模型分数": details.get("模型分数"),
            "因子综合分": details.get("因子综合分"),
            "数据覆盖率": details.get("数据覆盖率"),
            "因子证据": details.get("因子证据", []),
            "模型版本": _run_version(model_run),
            "因子数据版本": _run_version(factor_run),
            "财务披露": _financial_disclosures(memory, code),
            "估值记录": _valuation_records(memory, code),
        },
    }


def normalized_stock_benchmark_history(memory, code: str, *, minimum_common_dates: int = 2) -> dict:
    """Normalize a stock and HS300 only when enough finite common dates exist."""
    stock = {
        bar.trade_date: bar.adjusted_close
        for bar in getattr(memory, "prices_for", lambda _code: [])(code)
        if _finite(bar.adjusted_close)
    }
    benchmark = {
        bar.trade_date: bar.close
        for bar in getattr(memory, "benchmark_for", lambda _code: [])("000300.SH")
        if _finite(bar.close)
    }
    dates = sorted(set(stock) & set(benchmark))
    required = max(2, int(minimum_common_dates))
    if len(dates) < required:
        return {"状态": "不足", "数据": [], "说明": "个股与沪深300的共同交易日不足，无法形成可靠的归一化比较。"}
    stock_base, benchmark_base = stock[dates[0]], benchmark[dates[0]]
    if not _finite(stock_base) or not _finite(benchmark_base) or float(stock_base) == 0 or float(benchmark_base) == 0:
        return {"状态": "不足", "数据": [], "说明": "归一化基期数据不可用，无法形成可靠的比较。"}
    return {
        "状态": "可用",
        "数据": [
            {
                "日期": day,
                "个股（归一化）": float(stock[day]) / float(stock_base),
                "沪深300（归一化）": float(benchmark[day]) / float(benchmark_base),
            }
            for day in dates
        ],
        "说明": "仅使用个股与沪深300同时有有效收盘价的交易日，并以首个共同交易日归一化。",
    }


def latest_research_snapshot_date(rows: Iterable[Mapping]) -> date | None:
    """Return the newest public candidate date without exposing run metadata."""
    return max(
        (_parse_date(row.get("数据日期")) for row in rows or [] if isinstance(row, Mapping)),
        default=None,
    )


def _selected(value) -> bool:
    return value not in (None, "", "全部", "不限")


def filter_research_candidates(
    rows: Iterable[dict],
    *,
    tendency=None,
    confidence=None,
    risk=None,
    industry=None,
    query=None,
    min_score=None,
    min_coverage=None,
) -> list[dict]:
    """Filter public candidates without reordering their source research order."""
    score_threshold = float(min_score) if _finite(min_score) else None
    coverage_threshold = float(min_coverage) if _finite(min_coverage) else None
    needle = str(query or "").strip().lower()
    result = []
    for row in rows or []:
        if _selected(tendency) and row.get("研究倾向") != tendency:
            continue
        if _selected(confidence) and row.get("数据可信度") != confidence:
            continue
        if _selected(risk) and row.get("风险水平") != risk:
            continue
        if _selected(industry) and row.get("行业") != industry:
            continue
        if needle and needle not in str(row.get("代码", "")).lower() and needle not in str(row.get("名称", "")).lower():
            continue
        details = _mapping(row.get("专业详情"))
        raw_score = details.get("模型分数")
        if raw_score is None:
            raw_score = details.get("因子综合分")
        if score_threshold is not None and (not _finite(raw_score) or float(raw_score) < score_threshold):
            continue
        coverage = details.get("数据覆盖率")
        if coverage_threshold is not None and (not _finite(coverage) or float(coverage) < coverage_threshold):
            continue
        result.append(row)
    return result


def _page_size(value, default: int = 20) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        size = default
    return min(_MAX_PAGE_SIZE, max(1, size))


def _requested_page(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1


def research_candidate_page(rows: Iterable[dict], *, page=1, page_size=20) -> dict:
    """Return a bounded, source-ordered candidate page and its reader-facing position."""
    candidates = list(rows or [])
    size = _page_size(page_size)
    total = len(candidates)
    page_count = max(1, ceil(total / size))
    current = min(page_count, max(1, _requested_page(page)))
    start = (current - 1) * size
    return {
        "rows": candidates[start:start + size],
        "total": total,
        "current_page": current,
        "page_count": page_count,
        "page_size": size,
    }


def paginate_research_candidates(rows: Iterable[dict], *, page=1, page_size=20) -> tuple[list[dict], int]:
    """Return a source-ordered public page with the established two-value contract."""
    result = research_candidate_page(rows, page=page, page_size=page_size)
    return result["rows"], result["total"]


def research_data_freshness(
    latest_trade_date: date | None,
    *,
    research_snapshot_date: date | None | object = _UNSPECIFIED_SNAPSHOT,
    today: date,
    is_trade_day: Callable[[date], bool],
    is_complete: bool,
) -> dict:
    """Project data freshness with the shared completed-trade-day contract."""
    snapshot_date = (
        latest_trade_date
        if research_snapshot_date is _UNSPECIFIED_SNAPSHOT
        else _parse_date(research_snapshot_date)
    )
    snapshot_label = _display_date(snapshot_date)
    if latest_trade_date is None:
        return {"状态": "部分可用", "数据日期": "未知", "研究快照日期": snapshot_label, "说明": "尚无可用交易日数据。"}
    if data_is_stale(latest_trade_date, today=today, is_trade_day=is_trade_day):
        return {"状态": "数据已过期", "数据日期": latest_trade_date.isoformat(), "研究快照日期": snapshot_label, "说明": "存在已完成交易日尚未覆盖，研究结果仅供回顾。"}
    if not is_complete:
        return {"状态": "部分可用", "数据日期": latest_trade_date.isoformat(), "研究快照日期": snapshot_label, "说明": "数据尚未完整覆盖，研究结论需要谨慎核对。"}
    if snapshot_date is None:
        return {"状态": "部分可用", "数据日期": latest_trade_date.isoformat(), "研究快照日期": snapshot_label, "说明": "尚无可用研究快照，需要更新研究结果。"}
    if snapshot_date < latest_trade_date:
        return {"状态": "部分可用", "数据日期": latest_trade_date.isoformat(), "研究快照日期": snapshot_label, "说明": "研究快照早于最新数据，需要更新研究结果。"}
    return {"状态": "可信", "数据日期": latest_trade_date.isoformat(), "研究快照日期": snapshot_label, "说明": "已覆盖最近可确认的交易日，研究快照已更新。"}
