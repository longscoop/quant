"""Native Streamlit rendering for the public research homepage."""

from __future__ import annotations

from datetime import date
from math import isfinite

import altair as alt
import pandas as pd

from .research import (
    filter_research_candidates,
    latest_research_snapshot_date,
    normalized_stock_benchmark_history,
    research_candidate_page,
    research_candidate_rows,
    research_data_freshness,
    research_stock_detail,
)


SELECTED_SECURITY_KEY = "research_selected_security"
TARGET_PAGE_KEY = "research_target_page"
POOL_PAGE_KEY = "research_pool_page"
DETAIL_SECURITY_KEY = "research_detail_security"
NAVIGATION_PAGE_KEY = "research_navigation_page"
_CURRENT_PAGE_FOR_TARGET = {
    "candidate_pool": "候选池",
    "stock_detail": "个股详情",
    "portfolio": "我的组合",
}


def consume_research_target(session_state, pages: list[str]) -> str | None:
    """Consume one public navigation target and map it to the current shell labels."""
    target = session_state.pop(TARGET_PAGE_KEY, None)
    page = _CURRENT_PAGE_FOR_TARGET.get(target)
    return page if page in pages else None


def ensure_research_navigation_page(session_state, pages: list[str]) -> str:
    """Replace a retired page label before the sidebar widget reads session state."""
    current = session_state.get(NAVIGATION_PAGE_KEY)
    if current not in pages:
        current = pages[0]
        session_state[NAVIGATION_PAGE_KEY] = current
    return current


def research_security_index(codes: list[str], selected_security: str | None) -> int:
    """Return a safe initial selectbox index for a homepage detail action."""
    return codes.index(selected_security) if selected_security in codes else 0


def _home_memory(store):
    load_page_memory = getattr(store, "load_page_memory", None)
    if load_page_memory is not None:
        return load_page_memory()
    return store.load_memory()


def _latest_completed_run(store, run_type: str):
    latest = getattr(store, "latest_run_summary", None)
    if latest is not None:
        return latest(run_type, "completed")
    return next(
        (
            run
            for run in store.list_runs()
            if run.get("run_type") == run_type and run.get("status") == "completed"
        ),
        None,
    )


def _home_quality(store, memory) -> dict:
    quality = store.data_quality("hs300") if hasattr(store, "data_quality") else {}
    latest = quality.get("latest_trade_date") if quality else None
    if latest is None and hasattr(store, "latest_trade_date"):
        latest = store.latest_trade_date()
    return {
        "security_count": quality.get("security_count", len(getattr(memory, "securities", {}))),
        "latest_trade_date": latest,
        "is_complete": bool(quality.get("is_complete")),
    }


def _portfolio_codes(store, portfolio_id: str = "default") -> set[str]:
    if not hasattr(store, "get_portfolio_positions"):
        return set()
    return {
        row.get("ts_code")
        for row in store.get_portfolio_positions(portfolio_id)
        if row.get("ts_code")
    }


def _active_portfolio_id(st, store) -> str:
    selected = st.session_state.get("portfolio_selected_id")
    if selected and hasattr(store, "list_portfolios"):
        available = {row["portfolio_id"] for row in store.list_portfolios()}
        if selected in available:
            return selected
    if hasattr(store, "list_portfolios"):
        portfolios = store.list_portfolios()
        if portfolios:
            return portfolios[0]["portfolio_id"]
    return "default"


def _rerun(st) -> None:
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun is not None:
        rerun()


def _show_candidate_actions(st, store, candidate: dict, joined_codes: set[str], *, key_prefix: str, portfolio_id: str = "default") -> None:
    code = candidate["代码"]
    candidate_key = f"{code}_{candidate['数据日期']}"
    with st.container(horizontal=True, gap="small"):
        if st.button("查看详情", key=f"{key_prefix}_detail_{candidate_key}", width="stretch"):
            st.session_state[SELECTED_SECURITY_KEY] = code
            st.session_state[DETAIL_SECURITY_KEY] = code
            st.session_state[TARGET_PAGE_KEY] = "stock_detail"
            _rerun(st)
        if code in joined_codes:
            st.button("已加入", key=f"{key_prefix}_add_{candidate_key}", disabled=True, width="stretch")
        elif st.button("加入研究组合", key=f"{key_prefix}_add_{candidate_key}", width="stretch"):
            try:
                store.upsert_portfolio_position(portfolio_id, code, 0.0)
            except ValueError:
                st.warning("暂时无法加入研究组合，请稍后再试。")
            else:
                _rerun(st)


def _render_first_use_guide(st) -> None:
    with st.container(border=True):
        st.subheader("第一次使用")
        st.markdown("1. 选择候选：从优先研究的标的开始。")
        st.markdown("2. 核对证据：查看核心优势、主要风险和数据日期。")
        st.markdown("3. 加入组合：保存研究对象后，再检查自己的组合。")


def render_research_home(st, store, *, today: date | None = None) -> None:
    """Render a reader-facing home without exposing administrator workflows."""
    today = today or date.today()
    memory = _home_memory(store)
    quality = _home_quality(store, memory)
    is_trade_day = getattr(store, "is_trade_day", lambda day: day.weekday() < 5)
    factor_run = _latest_completed_run(store, "factors")
    model_run = _latest_completed_run(store, "model")
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
    snapshot_date = latest_research_snapshot_date(candidates)
    freshness = research_data_freshness(
        quality["latest_trade_date"],
        research_snapshot_date=snapshot_date,
        today=today,
        is_trade_day=is_trade_day,
        is_complete=quality["is_complete"],
    )
    prioritized = [row for row in candidates if row.get("研究倾向") == "优先研究"][:10]

    st.title("研究首页")
    st.subheader("数据状态")
    with st.container(horizontal=True, gap="small"):
        st.metric("最新数据日期", freshness["数据日期"], border=True)
        st.metric("研究快照日期", freshness["研究快照日期"], border=True)
        st.metric("覆盖证券", str(quality["security_count"]), border=True)
        st.metric("数据状态", freshness["状态"], border=True)
    st.caption(freshness["说明"])

    st.subheader("优先研究的候选")
    if not prioritized:
        st.info("暂时没有可优先研究的候选。")
    else:
        portfolio_id = _active_portfolio_id(st, store)
        joined_codes = _portfolio_codes(store, portfolio_id)
        for candidate in prioritized:
            with st.container(border=True):
                st.markdown(f"**{candidate['名称']}（{candidate['代码']}）**")
                st.caption(
                    f"行业：{candidate['行业']} · 数据可信度：{candidate['数据可信度']} · "
                    f"数据日期：{candidate['数据日期']}"
                )
                st.markdown(f"**核心优势**：{candidate['核心优势']}")
                st.markdown(f"**主要风险**：{candidate['主要风险']}")
                _show_candidate_actions(st, store, candidate, joined_codes, key_prefix="research_home", portfolio_id=portfolio_id)

    if st.button("查看全部候选", key="research_home_all_candidates", width="stretch"):
        st.session_state[TARGET_PAGE_KEY] = "candidate_pool"
        _rerun(st)
    _render_first_use_guide(st)
    st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")


def render_research_stock_summary(st, memory, code: str, *, factor_run=None, model_run=None) -> None:
    """Render the public candidate explanation used at the top of a stock detail."""
    candidate = next(
        (
            row
            for row in research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
            if row.get("代码") == code
        ),
        None,
    )
    if candidate is None:
        st.info("该股票暂时没有可用的研究摘要。请稍后查看。")
        return
    st.subheader("研究摘要")
    st.markdown(f"核心优势：{candidate['核心优势']}")
    st.markdown(f"主要风险：{candidate['主要风险']}")
    st.caption(
        f"数据可信度：{candidate['数据可信度']} · 风险水平：{candidate['风险水平']} · "
        f"数据日期：{candidate['数据日期']}"
    )


def _detail_memory(store, code: str):
    load_page_memory = getattr(store, "load_page_memory", None)
    if load_page_memory is not None:
        return load_page_memory(
            price_days=10_000,
            codes=[code],
            include_financials=True,
            include_valuations=True,
        )
    return store.load_memory()


def _sync_detail_security(st) -> None:
    st.session_state[SELECTED_SECURITY_KEY] = st.session_state[DETAIL_SECURITY_KEY]


def _display_advanced_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "不可用"
    return f"{number:g}" if isfinite(number) else "不可用"


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _display_score(value) -> str:
    number = _finite_number(value)
    if number is None or not 0 <= number <= 100:
        return "--"
    return f"{number:.1f}".rstrip("0").rstrip(".") + " / 100"


def _display_coverage(value) -> str:
    number = _finite_number(value)
    return f"{number:.0%}" if number is not None and 0 <= number <= 1 else "--"


def _render_stock_factor_scores(st, advanced: dict) -> None:
    st.subheader("因子评分（0-100）")
    factor_rows = advanced["因子证据"]
    if not factor_rows:
        st.info("暂无可展示的因子评分。")
        return
    for row in factor_rows:
        label = row.get("研究概念", "未提供")
        score = _finite_number(row.get("分数"))
        coverage = _display_coverage(row.get("覆盖率"))
        availability = row.get("可用性", "不足")
        if score is not None and 0 <= score <= 100:
            score_label = f"{score:.1f}".rstrip("0").rstrip(".")
            st.progress(score / 100, text=f"{label} · {score_label}")
        else:
            st.markdown(f"**{label} · --**")
        st.caption(f"覆盖率：{coverage} · {availability}")


def _render_stock_valuation_trend(st, advanced: dict) -> None:
    st.subheader("估值走势")
    valuation_units = {
        "市盈率": "倍",
        "市净率": "倍",
        "市销率": "倍",
        "股息率": "%",
    }
    metric = st.segmented_control(
        "选择指标",
        options=list(valuation_units),
        default="市盈率",
        key="research_detail_valuation_metric",
        width="stretch",
    )
    trend_rows = advanced["估值走势"].get(metric, [])
    if trend_rows:
        st.line_chart(trend_rows, x="数据日期", y="数值", width="stretch")
        st.caption(f"当前指标：{metric}（{valuation_units[metric]}）")
    else:
        st.info(f"暂无可展示的{metric}走势。")


def _normalized_stock_benchmark_chart(history_rows: list[dict]) -> alt.Chart:
    chart_rows = [
        {
            "日期": row["日期"],
            "系列": series,
            "归一化数值": row[series],
        }
        for row in history_rows
        for series in ("个股（归一化）", "沪深300（归一化）")
    ]
    chart_data = pd.DataFrame(chart_rows)
    chart_data["日期"] = pd.to_datetime(chart_data["日期"])
    available_dates = chart_data["日期"].drop_duplicates().sort_values().tolist()
    maximum_date_ticks = 8
    if len(available_dates) <= maximum_date_ticks:
        date_ticks = available_dates
    else:
        date_ticks = [
            available_dates[round(index * (len(available_dates) - 1) / (maximum_date_ticks - 1))]
            for index in range(maximum_date_ticks)
        ]
    return (
        alt.Chart(chart_data)
        .mark_line(strokeWidth=2)
        .encode(
            x=alt.X(
                "日期:T",
                title="日期",
                axis=alt.Axis(format="%Y-%m-%d", labelAngle=-30, values=date_ticks),
            ),
            y=alt.Y(
                "归一化数值:Q",
                title="归一化数值",
                axis=alt.Axis(format=".2f"),
                scale=alt.Scale(zero=False),
            ),
            color=alt.Color(
                "系列:N",
                title=None,
                legend=alt.Legend(orient="bottom"),
                scale=alt.Scale(
                    domain=["个股（归一化）", "沪深300（归一化）"],
                    range=["#0068C9", "#83C9FF"],
                ),
            ),
            tooltip=[
                alt.Tooltip("日期:T", title="日期", format="%Y-%m-%d"),
                alt.Tooltip("系列:N", title="系列"),
                alt.Tooltip("归一化数值:Q", title="数值", format=".3f"),
            ],
        )
        .properties(height=420)
    )


def _render_stock_financials(st, advanced: dict) -> None:
    st.subheader("财务披露")
    if advanced["财务披露"]:
        st.dataframe(advanced["财务披露"], hide_index=True, width="stretch")
        st.caption("仅展示在研究时点已公告且可获得的财务与估值记录。")
    else:
        st.info("暂无可展示的财务披露记录。")


def _render_stock_evidence_groups(st, detail: dict) -> None:
    st.subheader("研究证据与诊断")
    groups = detail["证据组"]
    if not groups:
        st.info("暂无可展示的研究证据。")
        return
    for start in range(0, len(groups), 2):
        columns = st.columns(2, gap="medium")
        for column, group in zip(columns, groups[start:start + 2]):
            with column.container(border=True):
                st.markdown(f"**{group['类别']} · {group['方向']}**")
                st.markdown(group["说明"])
                st.caption(f"局限：{group['局限']}")


def _render_stock_advanced_details(st, detail: dict) -> None:
    advanced = detail["专业详情"]
    with st.expander("专业详情", expanded=False):
        st.markdown(f"**研究来源**：{advanced['研究来源']}")
        st.markdown(f"**模型版本**：{advanced['模型版本']}")
        st.markdown(f"**因子版本**：{advanced['因子版本']}")
        st.markdown(f"**因子模型版本**：{advanced['因子模型版本']}")
        st.markdown(f"**模型数据日期**：{advanced['模型数据日期']}")
        st.markdown(f"**因子数据日期**：{advanced['因子数据日期']}")
        st.markdown(f"**证据关联状态**：{advanced['证据关联状态']}")
        if advanced["证据不可用原因"] != "未提供":
            st.caption(f"证据说明：{advanced['证据不可用原因']}")
        st.dataframe(
            [
                {"专业字段": "模型分数", "数值": _display_advanced_value(advanced["模型分数"])},
                {"专业字段": "因子综合分", "数值": _display_advanced_value(advanced["因子综合分"])},
                {"专业字段": "数据覆盖率", "数值": _display_advanced_value(advanced["数据覆盖率"])},
            ],
            hide_index=True,
            width="stretch",
        )


def render_research_stock_detail(st, store) -> None:
    """Render the active public stock-detail page from the shared research journey state."""
    selector_memory = _home_memory(store)
    codes = sorted(getattr(selector_memory, "securities", {}))
    st.title("个股详情")
    if not codes:
        st.info("研究数据正在准备，当前暂无可展示的数据。请稍后查看。")
        st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")
        return
    selected = st.session_state.get(SELECTED_SECURITY_KEY)
    if st.session_state.get(DETAIL_SECURITY_KEY) not in codes:
        st.session_state[DETAIL_SECURITY_KEY] = selected if selected in codes else codes[0]
    code = st.selectbox(
        "选择证券",
        codes,
        key=DETAIL_SECURITY_KEY,
        format_func=lambda value: f"{selector_memory.securities[value].name}（{value}）",
        on_change=lambda: _sync_detail_security(st),
    )
    st.session_state[SELECTED_SECURITY_KEY] = code
    memory = _detail_memory(store, code)
    factor_run = _latest_completed_run(store, "factors")
    model_run = _latest_completed_run(store, "model")
    detail = research_stock_detail(memory, code, factor_run=factor_run, model_run=model_run)
    advanced = detail["专业详情"]

    st.markdown(f"### {detail['名称']}（{detail['代码']}）")
    st.caption(
        f"研究日期：{detail['数据日期']} · 模型日期：{advanced['模型数据日期']} · "
        f"因子日期：{advanced['因子数据日期']} · PIT 证据：{advanced['证据关联状态']}"
    )
    with st.container(horizontal=True, gap="small"):
        st.metric("行业", detail["行业"], border=True)
        st.metric("研究结论", detail["研究结论"], border=True)
        st.metric("因子综合分", _display_score(advanced["因子综合分"]), border=True)
        st.metric("数据覆盖率", _display_coverage(advanced["数据覆盖率"]), border=True)
        st.metric("数据可信度", detail["数据可信度"], border=True)
        st.metric("风险水平", detail["风险水平"], border=True)

    summary_column, factor_column = st.columns([1, 1], gap="medium")
    with summary_column.container(border=True):
        st.subheader("研究摘要")
        st.markdown(f"**核心优势**  \n{detail['值得关注的原因']}")
        st.markdown(f"**主要风险**  \n{detail['主要风险']}")
        st.caption(f"数据充分程度：{detail['数据充分程度']}")
    with factor_column.container(border=True):
        _render_stock_factor_scores(st, advanced)

    history = normalized_stock_benchmark_history(memory, code)
    with st.container(border=True):
        st.subheader("相对沪深300走势")
        if history["状态"] == "不足":
            st.info("个股与沪深300的共同交易日不足，暂不能展示归一化对比；这不代表相对表现。")
        else:
            st.altair_chart(_normalized_stock_benchmark_chart(history["数据"]), width="stretch")
            st.caption(history["说明"])

    with st.container(border=True):
        _render_stock_valuation_trend(st, advanced)

    with st.container(border=True):
        _render_stock_financials(st, advanced)

    _render_stock_evidence_groups(st, detail)
    _render_stock_advanced_details(st, detail)
    st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")


def _reset_candidate_pool_page(st) -> None:
    st.session_state[POOL_PAGE_KEY] = 1


def _candidate_pool_filters(st, candidates: list[dict]) -> dict:
    reset_page = lambda: _reset_candidate_pool_page(st)
    controls = st.columns(4)
    tendency = controls[0].selectbox(
        "研究倾向",
        ["全部", "优先研究", "持续观察", "暂缓研究", "证据不足"],
        key="research_pool_tendency",
        on_change=reset_page,
    )
    confidence = controls[1].selectbox(
        "数据可信度",
        ["全部", "可信", "部分可用", "不足"],
        key="research_pool_confidence",
        on_change=reset_page,
    )
    risk = controls[2].selectbox(
        "风险水平",
        ["全部", "较低", "中等", "较高", "未知"],
        key="research_pool_risk",
        on_change=reset_page,
    )
    industry = controls[3].selectbox(
        "行业",
        ["全部", *sorted({str(row.get("行业") or "未分类") for row in candidates})],
        key="research_pool_industry",
        on_change=reset_page,
    )
    query = st.text_input("搜索名称或代码", key="research_pool_query", on_change=reset_page)
    with st.expander("高级筛选", expanded=False):
        advanced = st.columns(2)
        score_enabled = advanced[0].checkbox(
            "启用最低原始分数",
            key="research_pool_score_enabled",
            on_change=reset_page,
        )
        minimum_score = advanced[0].number_input(
            "最低原始分数",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=5.0,
            key="research_pool_min_score",
            on_change=reset_page,
        )
        minimum_coverage = advanced[1].number_input(
            "最低数据覆盖率",
            min_value=0.0,
            max_value=1.0,
            value=0.0,
            step=0.05,
            key="research_pool_min_coverage",
            on_change=reset_page,
        )
    return {
        "tendency": tendency,
        "confidence": confidence,
        "risk": risk,
        "industry": industry,
        "query": query,
        "min_score": minimum_score if score_enabled else None,
        "min_coverage": minimum_coverage or None,
    }


def render_research_candidate_pool(st, store, *, today: date | None = None) -> None:
    """Render the native public candidate pool without the legacy signal table."""
    del today
    memory = _home_memory(store)
    factor_run = _latest_completed_run(store, "factors")
    model_run = _latest_completed_run(store, "model")
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)

    st.title("候选池")
    if not candidates:
        st.info("暂时没有可研究的候选。请稍后查看。")
        st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")
        return

    filters = _candidate_pool_filters(st, candidates)
    filtered = filter_research_candidates(candidates, **filters)
    page = research_candidate_page(
        filtered,
        page=st.session_state.get(POOL_PAGE_KEY, 1),
        page_size=10,
    )
    st.session_state[POOL_PAGE_KEY] = page["current_page"]
    st.caption(f"筛选结果：{page['total']} 只 · 第 {page['current_page']} / {page['page_count']} 页")

    if not filtered:
        st.info("没有符合当前条件的候选。调整筛选条件后再试。")
        st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")
        return

    portfolio_id = _active_portfolio_id(st, store)
    joined_codes = _portfolio_codes(store, portfolio_id)
    for candidate in page["rows"]:
        with st.expander(f"{candidate['名称']}（{candidate['代码']}）", expanded=False):
            st.caption(
                f"行业：{candidate['行业']} · 数据可信度：{candidate['数据可信度']} · "
                f"风险水平：{candidate['风险水平']} · 数据日期：{candidate['数据日期']}"
            )
            st.markdown(f"**核心优势**：{candidate['核心优势']}")
            st.markdown(f"**主要风险**：{candidate['主要风险']}")
            _show_candidate_actions(st, store, candidate, joined_codes, key_prefix="research_pool", portfolio_id=portfolio_id)

    controls = st.columns(2)
    if controls[0].button(
        "上一页",
        key="research_pool_previous",
        disabled=page["current_page"] <= 1,
        width="stretch",
    ):
        st.session_state[POOL_PAGE_KEY] = page["current_page"] - 1
        _rerun(st)
    if controls[1].button(
        "下一页",
        key="research_pool_next",
        disabled=page["current_page"] >= page["page_count"],
        width="stretch",
    ):
        st.session_state[POOL_PAGE_KEY] = page["current_page"] + 1
        _rerun(st)
    st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")
