"""Native Streamlit rendering for the public research homepage."""

from __future__ import annotations

from datetime import date

from .research import (
    filter_research_candidates,
    latest_research_snapshot_date,
    research_candidate_page,
    research_candidate_rows,
    research_data_freshness,
)


SELECTED_SECURITY_KEY = "research_selected_security"
TARGET_PAGE_KEY = "research_target_page"
POOL_PAGE_KEY = "research_pool_page"
_CURRENT_PAGE_FOR_TARGET = {
    "candidate_pool": "股票池",
    "stock_detail": "个股研究",
}


def consume_research_target(session_state, pages: list[str]) -> str | None:
    """Consume one public navigation target and map it to the current shell labels."""
    target = session_state.pop(TARGET_PAGE_KEY, None)
    page = _CURRENT_PAGE_FOR_TARGET.get(target)
    return page if page in pages else None


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


def _rerun(st) -> None:
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun is not None:
        rerun()


def _show_candidate_actions(st, store, candidate: dict, joined_codes: set[str], *, key_prefix: str) -> None:
    code = candidate["代码"]
    candidate_key = f"{code}_{candidate['数据日期']}"
    with st.container(horizontal=True, gap="small"):
        if st.button("查看详情", key=f"{key_prefix}_detail_{candidate_key}", width="stretch"):
            st.session_state[SELECTED_SECURITY_KEY] = code
            st.session_state[TARGET_PAGE_KEY] = "stock_detail"
            _rerun(st)
        if code in joined_codes:
            st.button("已加入", key=f"{key_prefix}_add_{candidate_key}", disabled=True, width="stretch")
        elif st.button("加入研究组合", key=f"{key_prefix}_add_{candidate_key}", width="stretch"):
            try:
                store.upsert_portfolio_position("default", code, 0.0)
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
        joined_codes = _portfolio_codes(store)
        for candidate in prioritized:
            with st.container(border=True):
                st.markdown(f"**{candidate['名称']}（{candidate['代码']}）**")
                st.caption(
                    f"行业：{candidate['行业']} · 数据可信度：{candidate['数据可信度']} · "
                    f"数据日期：{candidate['数据日期']}"
                )
                st.markdown(f"**核心优势**：{candidate['核心优势']}")
                st.markdown(f"**主要风险**：{candidate['主要风险']}")
                _show_candidate_actions(st, store, candidate, joined_codes, key_prefix="research_home")

    if st.button("查看全部候选", key="research_home_all_candidates", width="stretch"):
        st.session_state[TARGET_PAGE_KEY] = "candidate_pool"
        _rerun(st)
    _render_first_use_guide(st)
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
        "min_score": minimum_score or None,
        "min_coverage": minimum_coverage or None,
    }


def render_research_candidate_pool(st, store, *, today: date | None = None) -> None:
    """Render the native public candidate pool without the legacy signal table."""
    del today
    memory = _home_memory(store)
    factor_run = _latest_completed_run(store, "factors")
    model_run = _latest_completed_run(store, "model")
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)

    st.title("股票池")
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

    joined_codes = _portfolio_codes(store)
    for candidate in page["rows"]:
        with st.expander(f"{candidate['名称']}（{candidate['代码']}）", expanded=False):
            st.caption(
                f"行业：{candidate['行业']} · 数据可信度：{candidate['数据可信度']} · "
                f"风险水平：{candidate['风险水平']} · 数据日期：{candidate['数据日期']}"
            )
            st.markdown(f"**核心优势**：{candidate['核心优势']}")
            st.markdown(f"**主要风险**：{candidate['主要风险']}")
            _show_candidate_actions(st, store, candidate, joined_codes, key_prefix="research_pool")

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
