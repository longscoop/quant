"""Native Streamlit rendering for the public research homepage."""

from __future__ import annotations

from datetime import date

from .research import research_candidate_rows, research_data_freshness


SELECTED_SECURITY_KEY = "research_selected_security"
TARGET_PAGE_KEY = "research_target_page"


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


def _show_candidate_actions(st, store, candidate: dict, joined_codes: set[str]) -> None:
    code = candidate["代码"]
    with st.container(horizontal=True, gap="small"):
        if st.button("查看详情", key=f"research_home_detail_{code}", width="stretch"):
            st.session_state[SELECTED_SECURITY_KEY] = code
            st.session_state[TARGET_PAGE_KEY] = "stock_detail"
            _rerun(st)
        if code in joined_codes:
            st.button("已加入", key=f"research_home_add_{code}", disabled=True, width="stretch")
        elif st.button("加入研究组合", key=f"research_home_add_{code}", width="stretch"):
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
    freshness = research_data_freshness(
        quality["latest_trade_date"],
        today=today,
        is_trade_day=is_trade_day,
        is_complete=quality["is_complete"],
    )
    factor_run = _latest_completed_run(store, "factors")
    model_run = _latest_completed_run(store, "model")
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
    prioritized = [row for row in candidates if row.get("研究倾向") == "优先研究"][:10]

    st.title("研究首页")
    st.subheader("数据状态")
    with st.container(horizontal=True, gap="small"):
        st.metric("最新数据日期", freshness["数据日期"], border=True)
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
                _show_candidate_actions(st, store, candidate, joined_codes)

    if st.button("查看全部候选", key="research_home_all_candidates", width="stretch"):
        st.session_state[TARGET_PAGE_KEY] = "candidate_pool"
        _rerun(st)
    _render_first_use_guide(st)
    st.caption("研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。")
