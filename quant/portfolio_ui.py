"""Reader-facing Streamlit workbench for research-simulation portfolios."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from .presentation import format_metric, public_run_feedback
from .ui import metric_cards, research_disclaimer, security_option_label
from .workflows import portfolio_dashboard, record_portfolio_cash_flow, run_portfolio_backtest_run, save_portfolio_targets


_ORDER_LABELS = {
    "PENDING": "待成交",
    "PARTIAL": "部分成交",
    "COMPLETED": "已成交",
    "SUPERSEDED": "已被新版本取代",
    "FAILED": "失败",
}

_FACTOR_LABELS = {
    "valuation": "价值",
    "quality": "质量",
    "growth": "成长",
    "momentum": "动量",
    "low_volatility": "低波动",
    "liquidity": "流动性",
    "industry": "行业景气",
    "risk": "风险",
}


def _percent(value) -> str:
    return "--" if value is None else f"{float(value):.2%}"


def _amount(value) -> str:
    return "--" if value is None else f"¥{float(value):,.2f}"


def _portfolio_memory(store):
    if hasattr(store, "load_page_memory"):
        return store.load_page_memory(price_days=0)
    return store


def _rerun(st) -> None:
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun:
        rerun()


def _render_portfolio_actions(st, store, portfolio: dict) -> None:
    with st.container(horizontal=True, horizontal_alignment="right"):
        with st.popover("新建组合", icon=":material/add:"):
            with st.form("portfolio_create_form"):
                name = st.text_input("组合名称", value="新研究组合")
                capital = st.number_input("初始模拟资金", min_value=10_000.0, value=1_000_000.0, step=100_000.0)
                fee = st.number_input("单边费率（bps）", min_value=0.0, value=5.0, step=1.0)
                if st.form_submit_button("创建", icon=":material/add:"):
                    from .markets.cn import CN_DEFAULT_CONTEXT
                    created = store.create_portfolio(
                        name,
                        capital,
                        fee,
                        CN_DEFAULT_CONTEXT.benchmark_id,
                        market_id=CN_DEFAULT_CONTEXT.market_id,
                        currency=CN_DEFAULT_CONTEXT.currency,
                    )
                    st.session_state["portfolio_selected_id"] = created
                    st.success("组合已创建")
                    _rerun(st)
        if st.button("复制", key="portfolio_copy", icon=":material/content_copy:"):
            copied = store.copy_portfolio(portfolio["portfolio_id"])
            st.session_state["portfolio_selected_id"] = copied
            st.success("已复制组合配置和目标权重草案")
            _rerun(st)
        with st.popover("资金调整", icon=":material/account_balance_wallet:"):
            with st.form("portfolio_cash_flow_form"):
                direction = st.segmented_control("方向", ["资金流入", "资金流出"], default="资金流入", required=True)
                amount = st.number_input("金额", min_value=0.01, value=100_000.0, step=10_000.0)
                flow_date = st.date_input("生效日期", value=store.latest_trade_date() or date.today())
                note = st.text_input("备注", value="研究资金调整")
                if st.form_submit_button("记录资金流水", icon=":material/save:"):
                    signed = float(amount) if direction == "资金流入" else -float(amount)
                    try:
                        record_portfolio_cash_flow(store, portfolio["portfolio_id"], flow_date, signed, note)
                    except ValueError as exc:
                        st.warning(str(exc))
                    else:
                        st.success("资金流水已记录")
                        _rerun(st)
        with st.popover("归档", icon=":material/archive:"):
            st.caption("归档会保留全部目标版本、成交和净值记录。")
            if st.button("确认归档", key="portfolio_archive_confirm"):
                store.archive_portfolio(portfolio["portfolio_id"])
                st.session_state.pop("portfolio_selected_id", None)
                _rerun(st)


def _draft_rows(store, dashboard: dict) -> list[dict]:
    memory = _portfolio_memory(store)
    target_items = dashboard.get("target_items") or []
    if target_items:
        target_map = {row["ts_code"]: float(row["target_weight"]) for row in target_items}
    else:
        target_map = {row["ts_code"]: float(row["weight"]) for row in store.get_portfolio_positions(dashboard["portfolio"]["portfolio_id"])}
    override = dashboard.get("draft_override")
    if override is not None:
        target_map = dict(override)
    position_map = {row["ts_code"]: row for row in dashboard.get("positions") or []}
    rows = []
    for code in sorted(set(target_map) | set(position_map)):
        position = position_map.get(code) or {}
        security = memory.securities.get(code)
        rows.append({
            "代码": code,
            "股票": security.name if security else position.get("name", "未知证券"),
            "行业": position.get("industry", "未分类"),
            "目标权重": target_map.get(code, 0.0),
            "当前权重": position.get("current_weight"),
            "模拟股数": position.get("quantity"),
            "首次买入日": position.get("first_buy_date"),
            "成本价": position.get("average_cost"),
            "当前价": position.get("current_price"),
            "未实现收益": position.get("unrealized_pnl"),
            "已实现收益": position.get("realized_pnl"),
            "研究分": dashboard.get("research_scores", {}).get(code),
            "证据覆盖率": dashboard.get("research_coverage", {}).get(code),
        })
    return rows


def _render_weights(st, store, dashboard: dict) -> None:
    st.subheader("持仓与权重")
    portfolio_id = dashboard["portfolio"]["portfolio_id"]
    draft_key = f"portfolio_draft_{portfolio_id}"
    version_key = f"portfolio_draft_version_{portfolio_id}"
    st.session_state.setdefault(version_key, 0)
    dashboard["draft_override"] = st.session_state.get(draft_key)
    rows = _draft_rows(store, dashboard)
    if rows:
        editor = st.data_editor(
            pd.DataFrame(rows),
            key=f"portfolio_weight_editor_{portfolio_id}_{st.session_state[version_key]}",
            hide_index=True,
            num_rows="fixed",
            disabled=[column for column in rows[0] if column != "目标权重"],
            column_config={
                "代码": st.column_config.TextColumn(pinned=True),
                "股票": st.column_config.TextColumn(pinned=True),
                "目标权重": st.column_config.NumberColumn(format="percent", min_value=0.0, max_value=1.0, step=0.01),
                "当前权重": st.column_config.NumberColumn(format="percent"),
                "模拟股数": st.column_config.NumberColumn(format="%.0f"),
                "成本价": st.column_config.NumberColumn(format="¥%.3f"),
                "当前价": st.column_config.NumberColumn(format="¥%.3f"),
                "未实现收益": st.column_config.NumberColumn(format="¥%.2f"),
                "已实现收益": st.column_config.NumberColumn(format="¥%.2f"),
                "研究分": st.column_config.NumberColumn(format="%.1f"),
                "证据覆盖率": st.column_config.NumberColumn(format="percent"),
            },
        )
        current_targets = {str(row["代码"]): float(row["目标权重"] or 0.0) for _, row in editor.iterrows()}
        with st.container(horizontal=True):
            if st.button("等权分配", key="portfolio_equal_weights"):
                positive_codes = list(current_targets)
                st.session_state[draft_key] = {code: 1.0 / len(positive_codes) for code in positive_codes} if positive_codes else {}
                st.session_state[version_key] += 1
                _rerun(st)
            if st.button("按研究分分配", key="portfolio_score_weights", disabled=not dashboard.get("research_scores")):
                scores = dashboard["research_scores"]
                total = sum(max(0.0, float(scores.get(code, 0.0))) for code in current_targets)
                st.session_state[draft_key] = {code: max(0.0, float(scores.get(code, 0.0))) / total for code in current_targets} if total else current_targets
                st.session_state[version_key] += 1
                _rerun(st)
            if st.button("归一化", key="portfolio_normalize_weights"):
                total = sum(current_targets.values())
                st.session_state[draft_key] = {code: weight / total for code, weight in current_targets.items()} if total else current_targets
                st.session_state[version_key] += 1
                _rerun(st)
            if st.button("保存权重", key="portfolio_save_weights", type="primary", icon=":material/save:"):
                try:
                    revision_id = save_portfolio_targets(store, portfolio_id, current_targets)
                    st.session_state.pop(draft_key, None)
                    st.session_state[version_key] += 1
                    st.success(f"目标权重已保存：{revision_id[:8]}")
                    _rerun(st)
                except ValueError as exc:
                    st.warning(str(exc), icon=":material/warning:")
    else:
        st.info("组合还没有目标证券。请先添加证券并保存权重。", icon=":material/info:")
    memory = _portfolio_memory(store)
    available = [code for code in sorted(memory.securities) if code not in {row["代码"] for row in rows}]
    if available:
        with st.expander("添加证券", icon=":material/add:"):
            code = st.selectbox("证券", available, format_func=lambda value: security_option_label(value, memory.securities[value].name), key=f"portfolio_add_code_{portfolio_id}")
            weight = st.number_input("目标权重", min_value=0.0, max_value=1.0, value=0.0, step=0.05, format="%.2f", key=f"portfolio_add_weight_{portfolio_id}")
            if st.button("加入权重草案", key="portfolio_add_security"):
                draft = {row["代码"]: float(row["目标权重"] or 0.0) for row in rows}
                draft[code] = float(weight)
                st.session_state[draft_key] = draft
                st.session_state[version_key] += 1
                _rerun(st)


def _render_rebalances(st, dashboard: dict) -> None:
    st.subheader("调仓记录")
    revisions = {row["revision_id"]: row for row in dashboard.get("target_revisions") or []}
    trades = {row["order_id"]: row for row in dashboard.get("trades") or []}
    rows = []
    for order in reversed(dashboard.get("orders") or []):
        revision = revisions.get(order["revision_id"]) or {}
        trade = trades.get(order["order_id"]) or {}
        rows.append({
            "目标版本": revision.get("revision_no"),
            "信号日": revision.get("signal_date"),
            "计划成交日": order.get("planned_trade_date"),
            "实际成交日": trade.get("trade_date"),
            "代码": order["ts_code"],
            "方向": "买入" if order.get("side") == "BUY" else "卖出",
            "目标权重": order.get("target_weight"),
            "成交数量": trade.get("quantity"),
            "成交价": trade.get("price"),
            "费用": trade.get("fee_amount"),
            "状态": _ORDER_LABELS.get(order.get("status"), order.get("status")),
            "原因": order.get("reason"),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={"目标权重": st.column_config.NumberColumn(format="percent"), "成交价": st.column_config.NumberColumn(format="¥%.3f"), "费用": st.column_config.NumberColumn(format="¥%.2f")})
    else:
        st.caption("尚无调仓记录；保存目标权重后会自动生成待成交计划。")


def _render_exposure(st, dashboard: dict) -> None:
    left, right = st.columns(2)
    with left.container(border=True):
        st.subheader("行业暴露")
        rows = [{"行业": name, "权重": weight} for name, weight in dashboard.get("industry_exposure", {}).items()]
        if rows:
            st.bar_chart(pd.DataFrame(rows), x="行业", y="权重")
            st.dataframe(pd.DataFrame(rows), hide_index=True, column_config={"权重": st.column_config.NumberColumn(format="percent")})
        else:
            st.caption("实际成交后按持仓市值与 PIT 行业记录计算。")
    with right.container(border=True):
        st.subheader("因子暴露")
        factor_rows = []
        for name, row in dashboard.get("factor_exposure", {}).get("factors", {}).items():
            factor_rows.append({"因子": _FACTOR_LABELS.get(name, name), "Z-Score": row.get("value"), "覆盖率": row.get("coverage")})
        if any(row["Z-Score"] is not None for row in factor_rows):
            st.dataframe(pd.DataFrame(factor_rows), hide_index=True, column_config={"Z-Score": st.column_config.NumberColumn(format="%.2f"), "覆盖率": st.column_config.NumberColumn(format="percent")})
        else:
            st.caption("估值日前没有可用的完整 PIT 因子截面，当前不展示伪造暴露。")
        snapshot = dashboard.get("factor_snapshot")
        if snapshot:
            st.caption(f"快照：{snapshot['as_of_date']} · 因子版本：{snapshot['factor_version']} · PIT：{snapshot['pit_version']}")


def _render_actual_history(st, dashboard: dict) -> None:
    st.subheader("组合模拟实绩")
    rows = [row for row in dashboard.get("nav_rows") or [] if row.get("status") == "COMPLETED" and row.get("nav") is not None]
    if rows:
        chart = pd.DataFrame([{"日期": row["valuation_date"], "研究组合": row["nav"], "沪深300": row.get("benchmark_nav")} for row in rows]).set_index("日期")
        st.line_chart(chart)
        st.caption("只展示自组合创建后由真实模拟成交形成的完整收盘净值。")
    else:
        st.info("尚无完整模拟实绩。待 T+1 成交并同步收盘行情后生成。", icon=":material/schedule:")


def _render_backtest(st, store, dashboard: dict) -> None:
    st.subheader("组合历史回放")
    revisions = dashboard.get("target_revisions") or []
    if not revisions:
        st.caption("保存至少一个目标权重版本后，才能冻结该版本进行历史回放。")
        return
    latest_date = store.latest_trade_date()
    default_start = latest_date - timedelta(days=365 * 3) if latest_date else date.today() - timedelta(days=365 * 3)
    default_end = latest_date or date.today()
    with st.form("portfolio_backtest_form"):
        revision_id = st.selectbox("目标权重版本", [row["revision_id"] for row in revisions], format_func=lambda value: f"版本 {next(row['revision_no'] for row in revisions if row['revision_id'] == value)}")
        date_range = st.date_input("历史区间", value=(default_start, default_end))
        fee_bps = st.number_input("单边费率（bps）", min_value=0.0, value=float(dashboard["portfolio"]["transaction_cost_bps"]), step=1.0)
        submitted = st.form_submit_button("重新计算", type="primary", icon=":material/calculate:")
    if submitted:
        if not isinstance(date_range, (tuple, list)) or len(date_range) != 2:
            st.warning("请选择完整的起止日期。")
        else:
            run_id = run_portfolio_backtest_run(store, dashboard["portfolio"]["portfolio_id"], revision_id=revision_id, start_date=date_range[0], end_date=date_range[1], cost_bps=fee_bps)
            run = store.get_run(run_id)
            level, message = public_run_feedback(run, "组合历史回放已完成。")
            getattr(st, level)(message)
    runs = [run for run in store.list_runs() if run.get("run_type") == "portfolio_backtest"] if hasattr(store, "list_runs") else []
    if runs:
        run = runs[0]
        payload = run.get("payload") or {}
        metrics = payload.get("metrics") or {}
        metric_cards(st, [("累计收益", _percent(metrics.get("total_return")), "冻结目标版本"), ("年化收益", _percent(metrics.get("annualized_return")), "样本不足显示 --"), ("最大回撤", _percent(metrics.get("max_drawdown")), "完整调仓期"), ("Sharpe", format_metric(metrics.get("sharpe")), "月度收益年化")])
        curves = {}
        for row in payload.get("equity_curve") or []:
            curves.setdefault(row["date"], {})["研究组合"] = row["value"]
        for row in payload.get("benchmark_curve") or []:
            curves.setdefault(row["date"], {})["沪深300"] = row["value"]
        if curves:
            st.line_chart(pd.DataFrame([{"日期": day, **values} for day, values in sorted(curves.items())]).set_index("日期"))


def _render_diagnostics(st, dashboard: dict) -> None:
    st.subheader("组合诊断")
    orders = dashboard.get("orders") or []
    pending = [row for row in orders if row.get("status") in {"PENDING", "PARTIAL"}]
    snapshot = dashboard.get("factor_snapshot")
    with st.container(horizontal=True):
        st.metric("估值状态", (dashboard.get("valuation") or {}).get("status", "尚无估值"), border=True)
        st.metric("待处理计划", str(len(pending)), border=True)
        st.metric("个股集中度", _percent(max((row.get("current_weight") or 0.0 for row in dashboard.get("positions") or []), default=None)), border=True)
        st.metric("因子快照", str(snapshot.get("as_of_date")) if snapshot else "--", border=True)
    if pending:
        st.warning("存在待成交或部分成交计划；系统会在下一交易日行情同步后继续处理。", icon=":material/schedule:")
    st.caption(f"数据截止：{dashboard.get('valuation_date') or '--'} · PIT 证据覆盖率：{_percent(dashboard.get('evidence_coverage'))}")


def render_portfolio_workbench(st, store) -> None:
    """Render a real-data portfolio page without embedding business calculations in UI."""
    st.title("我的组合")
    st.caption("按保存权重模拟持有 · T 日收盘形成信号，T+1 下一交易日开盘模拟成交，每日收盘估值。")
    portfolios = store.list_portfolios()
    if not portfolios:
        from .markets.cn import CN_DEFAULT_CONTEXT
        store.create_portfolio(
            "我的研究组合",
            benchmark_code=CN_DEFAULT_CONTEXT.benchmark_id,
            portfolio_id="default",
            market_id=CN_DEFAULT_CONTEXT.market_id,
            currency=CN_DEFAULT_CONTEXT.currency,
        )
        portfolios = store.list_portfolios()
    ids = [row["portfolio_id"] for row in portfolios]
    if st.session_state.get("portfolio_selected_id") not in ids:
        st.session_state["portfolio_selected_id"] = ids[0]
    selector = st.segmented_control if len(ids) <= 5 else st.selectbox
    selector_kwargs = {"required": True} if len(ids) <= 5 else {}
    selected_id = selector(
        "选择组合",
        ids,
        format_func=lambda value: next(row["name"] for row in portfolios if row["portfolio_id"] == value),
        key="portfolio_selected_id",
        **selector_kwargs,
    )
    portfolio = store.get_portfolio(selected_id)
    _render_portfolio_actions(st, store, portfolio)
    dashboard = portfolio_dashboard(store, selected_id)
    revision = dashboard.get("target_revision")
    if revision:
        st.caption(f"目标版本 {revision['revision_no']} · 信号日 {revision['signal_date']} · 状态：{_ORDER_LABELS.get(revision['status'], revision['status'])}")
    target_weight = sum(float(row["target_weight"]) for row in dashboard.get("target_items") or [])
    valuation = dashboard.get("valuation") or {}
    cash_ratio = float(valuation["cash"]) / float(valuation["total_value"]) if valuation.get("total_value") else 1.0
    metrics = dashboard.get("metrics") or {}
    metric_cards(st, [
        ("组合数量", str(len(portfolios)), "独立模拟账本"),
        ("当前持仓数", str(len(dashboard.get("positions") or [])), "已实际模拟成交"),
        ("已分配目标权重", _percent(target_weight), "未分配部分保留现金"),
        ("现金比例", _percent(cash_ratio), "最近完整估值"),
        ("组合累计收益", _percent(metrics.get("total_return")), "自首次模拟成交"),
        ("最大回撤", _percent(metrics.get("max_drawdown")), "完整净值样本"),
        ("研究证据覆盖率", _percent(dashboard.get("evidence_coverage")), "PIT 因子快照"),
    ])
    _render_weights(st, store, dashboard)
    _render_rebalances(st, dashboard)
    _render_exposure(st, dashboard)
    _render_actual_history(st, dashboard)
    _render_backtest(st, store, dashboard)
    _render_diagnostics(st, dashboard)
    research_disclaimer(st)
