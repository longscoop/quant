from __future__ import annotations

from datetime import date, timedelta
import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from quant.admin import admin_mode_enabled, database_unavailable_copy, navigation_pages
from quant.admin_ui import render_admin_workbench
from quant.insights import (
    data_quality_summary,
    filter_candidate_rows,
    financial_detail_rows,
    industry_summary_rows,
    market_snapshot,
    model_signal_rows,
    paginate_candidate_rows,
    portfolio_exposure_summary,
    portfolio_history_rows,
    research_candidate_rows,
    run_status_summary,
)
from quant.presentation import (
    format_metric,
    public_run_feedback,
    public_run_label,
    public_run_rows,
    sanitize_display_rows,
    sanitize_display_value,
)
from quant.public_status_ui import render_public_data_status
from quant.storage import PostgresStore
from quant.ui import empty_state, inject_workspace_css, metric_cards, page_header, research_disclaimer, security_option_label, status_badge
from quant.workflows import build_factor_run, run_backtest_run, run_portfolio_backtest_run, sync_hs300, train_model_run
from quant.templates import TEMPLATES


st.set_page_config(page_title="量化研究工作台", page_icon="◒", layout="wide")


def frame(rows):
    return pd.DataFrame(sanitize_display_rows(rows)) if rows else pd.DataFrame()


def date_value(value):
    if isinstance(value, date):
        return value
    return date.fromisoformat(value) if value else None


def run_payload(run):
    return run.get("payload") or {}


def factor_run_end_date(run):
    metadata = run_payload(run).get("metadata") or {}
    if metadata.get("date_end"):
        return date_value(metadata["date_end"])
    dates = [date_value(row.get("as_of_date")) for row in run_payload(run).get("rows", [])]
    return max((day for day in dates if day is not None), default=None)


def latest_rankings(store):
    run = latest_run(store, "factors", "completed")
    return (run, (run.get("payload") or {}).get("rankings") or []) if run else (None, [])


@st.cache_data(ttl=120, show_spinner=False)
def cached_page_memory(dsn: str, price_days: int, codes: tuple[str, ...] = (), include_financials: bool = False, include_valuations: bool = False):
    return PostgresStore(dsn).load_page_memory(price_days=price_days, codes=list(codes) or None, include_financials=include_financials, include_valuations=include_valuations)


def page_memory(store, price_days=0, codes=(), include_financials=False, include_valuations=False):
    return cached_page_memory(store.dsn, price_days, tuple(codes), include_financials, include_valuations)


def refresh_today_opportunities(store):
    """Create a user-requested latest-date ranking snapshot; never run on page load."""
    latest = store.latest_trade_date()
    if latest is None:
        raise ValueError("研究数据尚未准备，暂时无法刷新候选。")
    cached_page_memory.clear()
    return build_factor_run(store, [latest])


def display_score(value):
    return "不可用" if value is None else f"{float(value):.0f}"


def stars(value):
    if value is None:
        return ""
    return "★" * (5 if value >= 90 else 4 if value >= 70 else 3 if value >= 50 else 2 if value >= 30 else 1)


def render_overview(store):
    memory = store.load_memory()
    benchmark = memory.benchmark_for("000300.SH")
    latest, rankings = latest_rankings(store)
    change = benchmark[-1].close / benchmark[-2].close - 1 if len(benchmark) > 1 else None
    trend = "中性" if len(benchmark) < 60 else ("中性偏多" if benchmark[-1].close > sum(item.close for item in benchmark[-20:]) / 20 else "中性偏空")
    cards = st.columns(4)
    cards[0].metric("市场状态", trend)
    cards[1].metric("沪深300", format_metric(change, percent=True))
    cards[2].metric("全市场估值分位", "不可用")
    cards[3].metric("风险等级", "不可用" if len(benchmark) < 60 else "中")
    st.caption(f"数据日期：{benchmark[-1].trade_date if benchmark else '不可用'} · 市场估值需完成全A股 daily_basic 同步后显示。")
    st.subheader("今日 Top Picks")
    if rankings:
        rows = []
        for rank, row in enumerate(rankings[:20], 1):
            security = memory.securities.get(row["ts_code"])
            factors = row["factors"]
            rows.append({"排名": rank, "股票": security.name if security else row["ts_code"], "总分": display_score(row["score"]["score"]), "覆盖率": format_metric(row["score"]["coverage"], percent=True), "质量": display_score(factors["quality"]["score"]), "成长": display_score(factors["growth"]["score"]), "估值": display_score(factors["valuation"]["score"]), "动量": display_score(factors["momentum"]["score"]), "景气": display_score(factors["industry"]["score"]), "风险": display_score(factors["risk"]["score"])})
        st.dataframe(frame(rows), hide_index=True, use_container_width=True)
        st.caption(f"模型：{(latest.get('payload') or {}).get('metadata', {}).get('factor_model_version', 'pit_v1.0')} · 历史排名比较将在第二份可比运行生成后显示。")
    else:
        st.info("研究数据正在准备，当前暂无可展示的研究候选。请稍后刷新或联系管理员。")
    return

    status = store.status()
    counts = status["counts"]
    memory = store.load_memory()
    quality = data_quality_summary(memory)
    st.subheader("研究工作流")
    workflow = st.columns(4)
    for column, title, detail in zip(
        workflow,
        ("1. 数据同步", "2. PIT 因子", "3. 模型评分", "4. 历史回测"),
        ("同步沪深300成分、行情和财务数据。", "仅使用当日可见信息构建六类因子。", "检查模型状态和研究信号。", "观察历史样本中的策略表现。"),
    ):
        column.markdown(f"**{title}**\n\n{detail}")
    st.subheader("数据质量摘要")
    columns = st.columns(4)
    values = [
        ("证券", quality["security_count"]),
        ("行情记录", quality["price_count"]),
        ("财务记录", quality["financial_count"]),
        ("最新行情日期", quality["latest_trade_date"] or "不可用"),
    ]
    for column, (label, value) in zip(columns, values):
        column.metric(label, sanitize_display_value(value))
    st.caption("数据质量摘要按当前数据库快照计算；缺失、NaN 或无限值不会被赋予经济含义。")
    st.caption(f"当前已记录 {len(status['runs'])} 项研究运行。")


def render_data_management(store):
    st.subheader("沪深300数据同步")
    if hasattr(store, "fail_stale_sync_runs"):
        # A browser refresh must never leave an abandoned process looking
        # permanently active.  Active runs heartbeat on every API sub-step;
        # 15 minutes without a heartbeat is treated as a failed run.
        store.fail_stale_sync_runs()
    sync_runs = [run for run in store.list_runs() if run["run_type"] == "sync"]
    if hasattr(store, "data_quality"):
        quality = store.data_quality("hs300")
        st.caption(
            f"完整性：证券 {quality.get('security_count', 0)} · 最新行情 {quality.get('latest_trade_date') or '不可用'} · "
            f"缺最新行情 {len(quality.get('missing_latest_price_codes', []))} · "
            f"缺财务 {len(quality.get('missing_financial_codes', []))} · "
            f"估值 {quality.get('valuation_count', 0)} 条"
        )
        if not quality.get("is_complete"):
            st.warning("数据尚未完整；请查看同步状态或运行 audit-data 获取缺口代码。")
    if sync_runs:
        latest_sync = sync_runs[0]
        sync_payload = run_payload(latest_sync)
        if latest_sync["status"] == "running":
            progress = sync_payload.get("progress") or {}
            detail = progress.get("detail")
            updated = progress.get("updated_at")
            updated_label = updated or latest_sync.get("created_at") or "不可用"
            st.warning(f"同步仍在进行：{progress.get('phase', '准备同步')} {progress.get('current', 0)}/{progress.get('total', 0)}。最后更新：{updated_label}。")
            if detail:
                st.caption(detail)
        else:
            params = latest_sync.get("parameters") or {}
            counts = sync_payload.get("row_counts") or {}
            price_count = counts.get("price_count", counts.get("prices", "不可用"))
            financial_count = counts.get("financial_count", counts.get("financials", "不可用"))
            valuation_count = counts.get("valuation_count", counts.get("valuations", "不可用"))
            feedback_level, feedback_message = public_run_feedback(
                latest_sync, "最近数据同步已完成。"
            )
            getattr(st, feedback_level)(
                f"{feedback_message} 请求区间 {params.get('start_date', '不可用')} 至 "
                f"{params.get('end_date', '不可用')} · 行情 {price_count} 条 · "
                f"财务 {financial_count} 条 · 估值 {valuation_count} 条"
            )
            if latest_sync["status"] == "completed" and valuation_count == 0:
                st.warning("同步运行完成，但每日估值返回 0 行；请检查 Tushare daily_basic 权限或积分。")
    token = st.text_input("Tushare Token", type="password", value=os.getenv("TUSHARE_TOKEN", ""))
    # The research workspace is PIT-oriented; default to the requested
    # historical baseline instead of silently falling back to the last 400
    # days after a browser refresh.
    start = st.date_input("开始日期", date(2020, 1, 1))
    end = st.date_input("结束日期", date.today())
    if st.button("同步沪深300", type="primary"):
        if not token:
            st.error("请输入 Tushare Token；Token 不会保存到数据库。")
        elif start > end:
            st.error("开始日期不能晚于结束日期。")
        else:
            progress_bar = st.progress(0, text="准备同步…")
            progress_text = st.empty()
            phase_start = {"获取沪深300成分": (0.00, 0.05), "行情与复权": (0.05, 0.40), "财务报表": (0.45, 0.35), "每日估值": (0.80, 0.15), "写入数据库": (0.95, 0.05)}
            def report_progress(phase, current, total):
                start_fraction, span = phase_start.get(phase, (0.0, 0.0))
                fraction = start_fraction + span * (current / total if total else 0)
                progress_bar.progress(min(1.0, fraction), text=f"{phase}：{current}/{total} 只证券")
                progress_text.caption(f"同步区间：{start} 至 {end} · 当前阶段：{phase}")
            run_id = sync_hs300(store, token, start, end, progress=report_progress)
            progress_bar.progress(1.0, text="同步完成")
            run = store.get_run(run_id)
            feedback_level, feedback_message = public_run_feedback(run, "数据同步已完成。")
            getattr(st, feedback_level)(feedback_message)
    st.dataframe(frame(store.status()["audit"]), use_container_width=True)


def normalized_price_frame(memory, code):
    stock_by_date = {row.trade_date: row.adjusted_close for row in memory.prices_for(code)}
    benchmark_by_date = {row.trade_date: row.close for row in memory.benchmark_for("000300.SH")}
    dates = sorted(set(stock_by_date) & set(benchmark_by_date))
    if not dates:
        return pd.DataFrame()
    stock_start, benchmark_start = stock_by_date[dates[0]], benchmark_by_date[dates[0]]
    return frame([
        {
            "日期": day,
            "个股累计涨跌幅": stock_by_date[day] / stock_start - 1,
            "沪深300累计涨跌幅": benchmark_by_date[day] / benchmark_start - 1,
        }
        for day in dates
    ])


def render_stock_data(store):
    memory = page_memory(store)
    page_header(st, "个股研究", "从价格、因子和财务披露建立一页式研究摘要。")
    codes = sorted(memory.securities)
    if not codes:
        st.info("研究数据正在准备，当前暂无可展示的数据。请稍后刷新或联系管理员。")
        return
    code = st.selectbox("证券", codes, format_func=lambda value: security_option_label(value, memory.securities[value].name))
    memory = page_memory(store, price_days=10_000, codes=(code,), include_financials=True, include_valuations=True)
    security = memory.securities[code]
    _, rankings = latest_rankings(store)
    ranking = next((row for row in rankings if row["ts_code"] == code), None)
    st.subheader(f"{security.name} · {code}")
    existing_position = next((row for row in _portfolio_rows(store) if row["ts_code"] == code), None)
    action_col, weight_col = st.columns([1, 1])
    with action_col:
        if existing_position:
            if st.button("移出研究组合", key=f"stock_remove_{code}"):
                store.delete_portfolio_position("default", code)
                _safe_rerun(st)
        elif st.button("加入研究组合", key=f"stock_add_{code}"):
            try:
                _add_to_portfolio(store, code, 0.05)
                st.success("已加入研究组合，默认权重 5%")
            except ValueError as exc:
                st.warning(str(exc))
    with weight_col:
        if existing_position:
            new_weight = st.number_input("组合权重", 0.0, 1.0, float(existing_position["weight"]), 0.05, format="%.2f", key=f"stock_weight_{code}")
            if st.button("保存组合权重", key=f"stock_save_{code}"):
                try:
                    _add_to_portfolio(store, code, new_weight)
                    st.success("组合权重已保存")
                except ValueError as exc:
                    st.warning(str(exc))
    if ranking:
        score = ranking["score"]
        st.metric("综合评分", display_score(score["score"]), f"数据覆盖 {format_metric(score['coverage'], percent=True)}")
        cards = st.columns(6)
        for card, name in zip(cards, ("quality", "growth", "valuation", "momentum", "industry", "risk")):
            factor = ranking["factors"][name]
            card.metric({"quality": "质量", "growth": "成长", "valuation": "估值", "momentum": "动量", "industry": "景气", "risk": "风险"}[name], display_score(factor["score"]), f"覆盖 {format_metric(factor['coverage'], percent=True)} {stars(factor['score'])}")
        st.subheader("为什么入选")
        metrics = ranking.get("metrics") or {}
        positives = sorted(((name, value) for name, value in metrics.items() if value is not None), key=lambda item: item[1], reverse=True)[:4]
        warnings = sorted(((name, value) for name, value in metrics.items() if value is not None), key=lambda item: item[1])[:2]
        for name, value in positives:
            st.write(f"✓ {name} 截面分位 {value:.0f}%")
        for name, value in warnings:
            st.write(f"△ {name} 截面分位 {value:.0f}%")
        st.caption(f"可见截面：{ranking['as_of_date']} · 最早可交易：{ranking['tradable_date'] or '不可用'}")
    else:
        st.info("该股票的研究数据正在准备，请稍后刷新或联系管理员。")
    prices = memory.prices_for(code)
    comparison = normalized_price_frame(memory, code)
    if comparison.empty:
        st.warning("该证券与沪深300没有重叠交易日，暂不能展示归一化对比；这不代表相对表现。")
    else:
        st.subheader("相对沪深300的归一化历史走势")
        st.line_chart(comparison.set_index("日期"))
    if not prices:
        st.info("该证券暂无可展示的行情记录。")
    else:
        ohlc = [row for row in prices if row.has_ohlc]
        if not ohlc:
            st.warning("当前历史行情缺少绘制 K 线所需字段，暂时无法展示。请稍后刷新或联系管理员。")
        else:
            st.subheader("复权 K 线与成交量")
            chart = go.Figure()
            chart.add_trace(go.Candlestick(
                x=[row.trade_date for row in ohlc],
                open=[row.open * row.adj_factor for row in ohlc], high=[row.high * row.adj_factor for row in ohlc],
                low=[row.low * row.adj_factor for row in ohlc], close=[row.adjusted_close for row in ohlc], name="复权价格",
            ))
            chart.add_trace(go.Bar(x=[row.trade_date for row in ohlc], y=[row.volume for row in ohlc], name="成交量", yaxis="y2", opacity=.28))
            chart.update_layout(height=560, xaxis_rangeslider_visible=True, yaxis2={"overlaying": "y", "side": "right", "showgrid": False}, margin={"l": 10, "r": 10, "t": 25, "b": 10})
            st.plotly_chart(chart, width="stretch", config={"scrollZoom": True})
    financial_labels = {
        "report_period": "报告期", "ann_date": "公告日", "revenue": "营业收入", "net_profit": "净利润",
        "roe": "净资产收益率", "gross_margin": "毛利率", "operating_cashflow": "经营现金流",
        "debt_ratio": "资产负债率", "pe": "市盈率", "pb": "市净率", "ps": "市销率",
        "dividend_yield": "股息率", "valuation_date": "估值日期", "deduct_net_profit": "扣非净利润",
        "data_version": "数据版本",
    }
    financials = [{financial_labels.get(key, key): value for key, value in row.items()} for row in financial_detail_rows(memory, code)]
    st.subheader("年度财务趋势（已披露实际值）")
    annual = [row for row in memory.financials_for(code) if row.report_period.month == 12]
    annual = sorted(annual, key=lambda row: row.report_period)[-4:]
    all_reports = sorted(memory.financials_for(code), key=lambda row: (row.report_period, row.ann_date))
    latest_report = all_reports[-1] if all_reports else None
    if latest_report and latest_report.report_period.month != 12:
        st.caption(f"最新已披露报告：{latest_report.report_period}（公告日 {latest_report.ann_date}）。年度趋势表只列年报，因此最新年度可能仍为 {annual[-1].report_period.year if annual else '不可用'}。")
    if annual:
        annual_rows = []
        for label, field in (("营收", "revenue"), ("净利润", "net_profit"), ("ROE", "roe"), ("毛利率", "gross_margin"), ("经营现金流", "operating_cashflow")):
            annual_rows.append({"指标": label, **{str(row.report_period.year): getattr(row, field) for row in annual}})
        st.dataframe(frame(annual_rows), hide_index=True, use_container_width=True)
    else:
        st.info("没有足够的年度已披露财务记录。")
    with st.expander("数据详情"):
        st.dataframe(frame(financials), use_container_width=True)
    st.caption("缺失、NaN 和无限数据均显示为不可用，不构成对证券质量或预期的判断。")


def render_factor_research(store):
    memory = store.load_memory()
    dates = sorted({bar.trade_date for bar in memory.prices.values()})
    st.subheader("pit_v1.0 六类 PIT 因子")
    st.dataframe(frame([
        {"因子类别": "质量 Quality", "权重建议": "20%", "主要内容": "ROE、ROIC、现金流、负债、毛利率"},
        {"因子类别": "成长 Growth", "权重建议": "25%", "主要内容": "营收、利润、扣非、CAGR、加速度"},
        {"因子类别": "估值 Valuation", "权重建议": "15%", "主要内容": "PE/PB/PS历史分位、PEG、股息率"},
        {"因子类别": "动量 Momentum", "权重建议": "15%", "主要内容": "20/60/120日相对收益"},
        {"因子类别": "景气 Industry", "权重建议": "15%", "主要内容": "首版不构造伪景气；数据不足时不可用"},
        {"因子类别": "风险 Risk Quality", "权重建议": "10%", "主要内容": "波动、回撤、流动性、财务异常"},
        {"因子类别": "PIT规则", "权重建议": "", "主要内容": "公告日仅可见，下一交易日才可交易"},
        {"因子类别": "覆盖率", "权重建议": "", "主要内容": "一级<50%不可用；综合权重或覆盖率<70%不可用"},
        {"因子类别": "版本", "权重建议": "", "主要内容": "factor_model_version = pit_v1.0"},
    ]), hide_index=True, use_container_width=True)
    if not dates:
        st.info("需要先同步真实数据。")
        return
    selected = st.date_input("因子计算截止日", dates[-1])
    if st.button("构建六类 PIT 因子"):
        run_id = build_factor_run(store, [day for day in dates if day <= selected])
        run = store.get_run(run_id)
        feedback_level, feedback_message = public_run_feedback(run, "因子构建已完成。")
        getattr(st, feedback_level)(feedback_message)
    runs = [run for run in store.list_runs() if run["run_type"] == "factors"]
    if not runs:
        st.info("尚无因子运行。下一步：选择截止日并构建覆盖预测日期的因子。")
        return
    run = st.selectbox("因子运行", runs, format_func=public_run_label)
    metadata = run_payload(run).get("metadata") or {}
    coverage = st.columns(3)
    coverage[0].metric("开始日期", sanitize_display_value(metadata.get("date_start")))
    coverage[1].metric("结束日期", sanitize_display_value(metadata.get("date_end")))
    coverage[2].metric("覆盖证券数", sanitize_display_value(metadata.get("security_count")))
    rows = run_payload(run).get("rows", [])
    if rows:
        st.dataframe(frame([{**row["values"], "日期": row["as_of_date"], "代码": row["ts_code"]} for row in rows]), use_container_width=True)
    else:
        st.info("该因子运行没有可用行。请检查行情、财务披露和截止日期覆盖范围。")


def render_model_training(store):
    factor_runs = [run for run in store.list_runs() if run["run_type"] == "factors" and run["status"] == "completed"]
    if not factor_runs:
        st.info("请先完成一项因子运行，并确保其覆盖所选预测日期。")
        return
    factor_run = st.selectbox("因子运行", factor_runs, format_func=public_run_label)
    default_date = factor_run_end_date(factor_run)
    if default_date is None:
        st.warning("所选因子运行没有可用日期，无法训练模型。")
        return
    prediction_date = st.date_input("预测日期", default_date)
    if st.button("训练模型并生成研究信号"):
        run_id = train_model_run(store, factor_run["run_id"], [prediction_date])
        run = store.get_run(run_id)
        feedback_level, feedback_message = public_run_feedback(run, "模型研究已完成。")
        getattr(st, feedback_level)(feedback_message)
    models = [run for run in store.list_runs() if run["run_type"] == "model"]
    if not models:
        st.info("尚无模型运行。下一步：使用覆盖预测日期和更早日期的因子运行。")
        return
    model = st.selectbox("模型运行", models, format_func=public_run_label)
    summary = run_status_summary(model)
    metadata = run_payload(model).get("metadata") or {}
    prediction_dates = metadata.get("prediction_dates") or (model.get("parameters") or {}).get("prediction_dates") or []
    if not isinstance(prediction_dates, list):
        prediction_dates = [prediction_dates]
    model_details = st.columns(4)
    model_details[0].metric("训练截止日", sanitize_display_value(metadata.get("train_end")))
    model_details[1].metric("预测日期", "、".join(str(sanitize_display_value(day)) for day in prediction_dates) or "不可用")
    model_details[2].metric("训练样本行数", sanitize_display_value(metadata.get("training_row_count")))
    model_details[3].metric("预测覆盖行数", sanitize_display_value(metadata.get("prediction_row_count")))
    st.caption("有效范围：模型结果仅适用于训练截止日之后的指定预测日期截面；数据覆盖、因子版本或样本变化时需重新研究，不代表未来结果。")
    if model["status"] == "completed":
        st.success("模型已完成，可查看下方研究信号或继续进行历史回测。")
        memory = store.load_memory()
        signals = model_signal_rows(model, memory.securities)
        if signals:
            st.dataframe(frame(signals), use_container_width=True, hide_index=True)
        else:
            st.info("模型已完成但没有可展示的研究信号。")
    elif model["status"] == "not_trainable":
        st.warning(summary["next_step"])
        if summary["reason"]:
            st.caption(f"不可训练原因：{summary['reason']}")
    else:
        feedback_level, feedback_message = public_run_feedback(model, "模型研究已完成。")
        getattr(st, feedback_level)(feedback_message)


def render_strategy_configuration():
    st.subheader("策略模板")
    selected = st.selectbox("研究模板", list(TEMPLATES), format_func=lambda key: TEMPLATES[key].name)
    strategy = TEMPLATES[selected]
    st.write("月度调仓 · Top-N 历史研究组合 · 沪深300基准 · 默认过滤 ST、新股、停牌及涨跌停。")
    st.dataframe(frame([{"一级因子": {"quality": "质量", "growth": "成长", "valuation": "估值", "momentum": "动量", "industry": "景气", "risk": "Risk Quality"}[name], "权重": f"{weight:.0%}"} for name, weight in strategy.weights.items()]), hide_index=True, use_container_width=True)
    st.caption(f"模板版本：{strategy.version} · 默认持仓 {strategy.top_n} · 单边成本 {strategy.cost_bps:.0f}bps")
    with st.expander("高级设置（仅用于复制为自定义研究参数）"):
        st.number_input("持仓数", min_value=1, max_value=300, value=strategy.top_n)
        st.number_input("单边交易成本（bps）", min_value=0.0, value=strategy.cost_bps)
    st.caption("内置模板不可被页面直接覆盖；因子、策略和数据版本均随研究运行记录。")


def render_backtest_results(store):
    st.subheader("历史回测")
    st.caption("默认研究排名使用透明 pit_v1.0 综合分；本页当前保留高级模型实验回测入口。")
    models = [run for run in store.list_runs() if run["run_type"] == "model" and run["status"] == "completed"]
    if not models:
        st.info("研究数据正在准备，当前暂无可用于历史验证的结果。请稍后刷新或联系管理员。")
        return
    model = st.selectbox("模型运行", models, format_func=public_run_label)
    top_n = st.number_input("持仓数", 1, 300, 30)
    cost = st.number_input("单边成本（bps）", 0.0, value=10.0)
    if st.button("运行历史回测"):
        run_id = run_backtest_run(store, model["run_id"], int(top_n), float(cost))
        run = store.get_run(run_id)
        feedback_level, feedback_message = public_run_feedback(run, "历史回测已完成。")
        getattr(st, feedback_level)(feedback_message)
    results = [run for run in store.list_runs() if run["run_type"] == "backtest"]
    if not results:
        st.info("历史验证结果正在准备，请稍后刷新或联系管理员。")
        return
    result = results[0]
    if result["status"] != "completed":
        feedback_level, feedback_message = public_run_feedback(result, "历史回测已完成。")
        getattr(st, feedback_level)(feedback_message)
        return
    metrics = run_payload(result).get("metrics") or {}
    payload = run_payload(result)
    parameters = result.get("parameters") or {}
    page_header(st, "回测", f"历史规则验证 · {parameters.get('experiment_name') or '最近一次实验'} · 月度调仓")
    metric_cards(st, [
        ("策略累计收益", format_metric(metrics.get("total_return"), percent=True), "历史样本期"),
        ("相对沪深300", format_metric(metrics.get("excess_return"), percent=True), "策略净值减基准"),
        ("最大回撤", format_metric(metrics.get("max_drawdown"), percent=True), "从峰值回撤"),
        ("Sharpe", format_metric(metrics.get("sharpe")), "月度收益口径"),
        ("年化换手", format_metric(metrics.get("annualized_turnover"), percent=True), "规则估算"),
        ("交易成本", f"{parameters.get('cost_bps', '不可用')}bps", "回测参数"),
    ])
    curves = []
    for key, label in (("equity_curve", "策略净值"), ("benchmark_curve", "沪深300"), ("excess_curve", "超额净值")):
        curves.extend({"date": row["date"], "曲线": label, "value": row["value"]} for row in payload.get(key, []))
    if curves:
        chart = frame(curves).pivot(index="date", columns="曲线", values="value")
        st.line_chart(chart)
    table = [
        {"指标": "累计收益", "策略": format_metric(metrics.get("total_return"), percent=True), "沪深300": format_metric(metrics.get("benchmark_return"), percent=True), "超额": format_metric(metrics.get("excess_return"), percent=True)},
        {"指标": "年化收益", "策略": format_metric(metrics.get("annualized_return"), percent=True), "沪深300": format_metric(metrics.get("benchmark_annualized_return"), percent=True), "超额": format_metric((metrics.get("annualized_return") or 0) - (metrics.get("benchmark_annualized_return") or 0), percent=True)},
        {"指标": "最大回撤", "策略": format_metric(metrics.get("max_drawdown"), percent=True), "沪深300": format_metric(metrics.get("benchmark_max_drawdown"), percent=True), "超额": "—"},
        {"指标": "Sharpe", "策略": format_metric(metrics.get("sharpe")), "沪深300": format_metric(metrics.get("benchmark_sharpe")), "超额": "—"},
        {"指标": "Calmar", "策略": format_metric(metrics.get("calmar")), "沪深300": format_metric(metrics.get("benchmark_calmar")), "超额": "—"},
        {"指标": "年化换手", "策略": format_metric(metrics.get("annualized_turnover"), percent=True), "沪深300": "—", "超额": "—"},
    ]
    st.dataframe(frame(table), hide_index=True, use_container_width=True)
    annual = payload.get("annual_returns") or []
    if annual:
        st.subheader("逐年收益")
        st.dataframe(frame([{"年份": row["year"], "策略": format_metric(row["strategy"], percent=True), "沪深300": format_metric(row["benchmark"], percent=True), "超额": format_metric(row["strategy"] - row["benchmark"], percent=True)} for row in annual]), hide_index=True, use_container_width=True)
    st.dataframe(frame(run_payload(result).get("positions", [])), use_container_width=True)
    st.caption("回测指标仅描述历史样本期表现，受数据覆盖、交易成本和模型设定影响；不代表未来结果或收益保证。")


def render_run_records(store):
    runs = store.list_runs()
    if runs:
        st.dataframe(frame(public_run_rows(runs)), use_container_width=True)
    else:
        st.info("暂无运行记录。请从数据同步开始研究流程。")


def latest_run(store, run_type: str, status: str | None = None):
    if hasattr(store, "latest_run_summary"):
        return store.latest_run_summary(run_type, status)
    runs = [run for run in store.list_runs() if run["run_type"] == run_type and (status is None or run["status"] == status)]
    return runs[0] if runs else None


def _safe_rerun(st):
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if rerun:
        rerun()


def _portfolio_rows(store, portfolio_id="default"):
    return store.get_portfolio_positions(portfolio_id) if hasattr(store, "get_portfolio_positions") else []


def _add_to_portfolio(store, code, weight=0.0, portfolio_id="default"):
    current = _portfolio_rows(store, portfolio_id)
    existing = next((row for row in current if row["ts_code"] == code), None)
    total = sum(float(row["weight"]) for row in current) - (float(existing["weight"]) if existing else 0.0) + float(weight)
    if total > 1.000001:
        raise ValueError("组合权重合计不能超过 100%")
    store.upsert_portfolio_position(portfolio_id, code, float(weight))


def _render_add_to_portfolio(store, codes, key_prefix):
    if not codes:
        return
    securities = page_memory(store).securities
    code = st.selectbox("搜索并加入研究组合", codes, format_func=lambda value: security_option_label(value, securities[value].name if value in securities else "未知证券"), key=f"{key_prefix}_code")
    weight = st.number_input("目标权重", min_value=0.0, max_value=1.0, value=0.05, step=0.05, format="%.2f", key=f"{key_prefix}_weight")
    if st.button("加入组合", key=f"{key_prefix}_add"):
        try:
            _add_to_portfolio(store, code, weight)
            st.success("已加入研究组合")
        except ValueError as exc:
            st.warning(str(exc))


def render_today_opportunities(store):
    memory = page_memory(store, price_days=2)
    page_header(st, "今日机会", "先看值得研究的标的，再沿证据链进入个股与策略。")
    snapshot = market_snapshot(memory)
    factor_run = latest_run(store, "factors", "completed")
    model_run = latest_run(store, "model", "completed")
    factor_rankings = (factor_run.get("payload") or {}).get("rankings") or [] if factor_run else []
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
    candidate_count = sum(1 for row in candidates if row.get("研究信号") == "研究候选")
    top_score = next((row.get("综合分") for row in candidates if row.get("综合分") is not None), None)
    metric_cards(st, [
        ("研究候选", str(candidate_count), f"候选池 {len(candidates)} 只" if candidates else "暂无可用排名"),
        ("市场状态", snapshot["market_label"], format_metric(snapshot["benchmark_change"], percent=True) + " · 沪深300" if snapshot["benchmark_change"] is not None else "基准数据不可用"),
        ("最高综合分", f"{top_score:.0f}" if top_score is not None else "不可用", "来自最新研究运行" if top_score is not None else "请先完成因子或模型运行"),
        ("排名快照", str(len(candidates)), "点击刷新后保存最新研究快照"),
        ("数据状态", snapshot["data_health"], str(snapshot["latest_trade_date"] or "尚无行情")),
    ])
    st.markdown("### 值得先看")
    if st.button("刷新今日机会", type="primary"):
        try:
            run_id = refresh_today_opportunities(store)
            st.success("已更新最新研究快照。")
            _safe_rerun(st)
        except ValueError as exc:
            st.warning(str(exc))
    if not candidates:
        empty_state(st, "还没有今日机会", "研究候选数据正在准备。", "请稍后刷新或联系管理员。")
    else:
        table = [{key: value for key, value in row.items() if key != "as_of_date"} for row in candidates[:20]]
        st.dataframe(frame(table), hide_index=True, use_container_width=True)
        with st.expander("加入研究组合"):
            _render_add_to_portfolio(store, [row["代码"] for row in candidates], "today")
    st.markdown("### 研究进度")
    flow = []
    for run_type, label in (("sync", "数据同步"), ("factors", "因子构建"), ("model", "模型预测"), ("backtest", "回测验证")):
        run = latest_run(store, run_type)
        status = run_status_summary(run)["label"] if run and run_type == "model" else ("已完成" if run and run.get("status") == "completed" else "待执行")
        flow.append({"阶段": label, "状态": status, "最近运行": run.get("created_at", "") if run else "—"})
    st.dataframe(frame(flow), hide_index=True, use_container_width=True)
    research_disclaimer(st)


def render_stock_pool(store):
    memory = page_memory(store)
    page_header(st, "股票池", "研究候选池 · 通过信号、行业和覆盖率缩小研究范围。")
    factor_run = latest_run(store, "factors", "completed")
    model_run = latest_run(store, "model", "completed")
    candidates = research_candidate_rows(memory, factor_run=factor_run, model_run=model_run)
    if not candidates:
        empty_state(st, "候选池暂不可用", "研究候选数据正在准备。", "请稍后刷新或联系管理员。")
        research_disclaimer(st)
        return
    industries = ["全部"] + sorted({row.get("行业", "未分类") for row in candidates})
    signals = ["全部", "研究候选", "中性", "低优先级", "数据不可用"]
    controls = st.columns(5)
    signal = controls[0].selectbox("研究信号", signals)
    industry = controls[1].selectbox("行业", industries)
    min_score = controls[2].number_input("最低综合分", min_value=0.0, max_value=100.0, value=0.0, step=5.0)
    coverage_label = controls[3].selectbox("最低覆盖率", ["不限", "≥50%", "≥70%"])
    query = controls[4].text_input("搜索代码或名称")
    coverage = {"不限": None, "≥50%": 0.5, "≥70%": 0.7}[coverage_label]
    filtered = filter_candidate_rows(candidates, signal=None if signal == "全部" else signal, industry=None if industry == "全部" else industry, min_score=min_score or None, min_coverage=coverage)
    page_count = max(1, (len(filtered) + 19) // 20)
    page = st.number_input("页码", min_value=1, max_value=page_count, value=1, step=1)
    page_rows, searched_total = paginate_candidate_rows(filtered, query=query, page=int(page), page_size=20)
    metric_cards(st, [("候选池", str(len(candidates)), "最新研究运行"), ("筛选结果", str(searched_total), "当前筛选条件与搜索"), ("覆盖证券", str(len(memory.securities)), "沪深300研究范围")])
    st.caption(f"第 {int(page)} / {max(1, (searched_total + 19) // 20)} 页，每页 20 条")
    st.dataframe(frame([{key: value for key, value in row.items() if key != "as_of_date"} for row in page_rows]), hide_index=True, use_container_width=True)
    if page_rows:
        with st.expander("加入研究组合"):
            _render_add_to_portfolio(store, [row["代码"] for row in page_rows], "pool")
    research_disclaimer(st)


def render_industry_heat(store):
    memory = page_memory(store, price_days=61)
    page_header(st, "行业景气", "用行业归属、研究分数和相对表现观察景气线索。")
    factor_run = latest_run(store, "factors", "completed")
    rankings = (factor_run.get("payload") or {}).get("rankings") or [] if factor_run else []
    rows = industry_summary_rows(memory, rankings=rankings, as_of=max((bar.trade_date for bar in memory.prices.values()), default=None))
    if not rows:
        empty_state(st, "行业数据不足", "行业研究数据正在准备。", "请稍后刷新或联系管理员。")
        research_disclaimer(st)
        return
    st.dataframe(frame(rows), hide_index=True, use_container_width=True)
    selected = st.selectbox("查看行业内股票", [row["行业"] for row in rows])
    industry_map = {}
    for (code, effective), record in memory.industries.items():
        if record.industry == selected and (code not in industry_map or effective > industry_map[code][0]):
            industry_map[code] = (effective, record.industry)
    codes = sorted(industry_map)
    st.dataframe(frame([{"名称": memory.securities[code].name if code in memory.securities else "未知证券", "代码": code, "行业": selected} for code in codes]), hide_index=True, use_container_width=True)
    research_disclaimer(st)


def render_strategy_lab(store):
    page_header(st, "策略实验室", "调整研究参数，保存一项可追溯的历史实验。")
    models = [run for run in store.list_runs() if run["run_type"] == "model" and run["status"] == "completed"]
    if not models:
        empty_state(st, "还没有可实验的模型", "研究模型数据正在准备。", "请稍后刷新或联系管理员。")
        research_disclaimer(st)
        return
    with st.form("strategy_experiment"):
        model = st.selectbox("模型运行", models, format_func=public_run_label)
        template_id = st.selectbox("策略模板", list(TEMPLATES), format_func=lambda key: TEMPLATES[key].name)
        experiment_name = st.text_input("实验名称", value=f"{TEMPLATES[template_id].name} · {date.today().isoformat()}")
        controls = st.columns(2)
        top_n = controls[0].number_input("Top-N 持仓数", min_value=1, max_value=300, value=30, step=1)
        cost = controls[1].number_input("单边交易成本（bps）", min_value=0.0, max_value=500.0, value=10.0, step=1.0)
        submitted = st.form_submit_button("运行实验", type="primary")
    if submitted:
        run_id = run_backtest_run(store, model["run_id"], int(top_n), float(cost), template_id=template_id, experiment_name=experiment_name.strip() or "未命名实验")
        run = store.get_run(run_id)
        feedback_level, feedback_message = public_run_feedback(run, "历史实验已保存。")
        getattr(st, feedback_level)(feedback_message)
    st.info("调仓频率固定为月度；模板会记录研究假设，回测只使用真实已有模型分数。")
    experiments = [run for run in store.list_runs() if run["run_type"] == "backtest"]
    if experiments:
        st.markdown("### 最近实验")
        st.dataframe(frame(public_run_rows(experiments[:10])), hide_index=True, use_container_width=True)
    research_disclaimer(st)


def render_portfolio(store):
    positions = _portfolio_rows(store)
    memory = page_memory(store, price_days=10_000, codes=tuple(row["ts_code"] for row in positions))
    page_header(st, "组合", "我的研究组合 · 持久化权重草案与行业暴露。")
    summary = portfolio_exposure_summary(memory, positions)
    factor_run = latest_run(store, "factors", "completed")
    rankings = {(row.get("ts_code")): row for row in ((factor_run.get("payload") or {}).get("rankings") or [])} if factor_run else {}
    weighted_factor_scores = {}
    for name in ("quality", "growth", "valuation", "momentum", "industry", "risk"):
        values = []
        for position in positions:
            factor = ((rankings.get(position["ts_code"]) or {}).get("factors") or {}).get(name) or {}
            if factor.get("score") is not None:
                values.append((float(position["weight"]), float(factor["score"])))
        denominator = sum(weight for weight, _ in values)
        weighted_factor_scores[name] = sum(weight * score for weight, score in values) / denominator if denominator else None
    quality_score = weighted_factor_scores.get("quality")
    metric_cards(st, [("持仓数量", str(summary["position_count"]), "研究组合"), ("已分配权重", format_metric(summary["allocated_weight"], percent=True), "保存后的权重"), ("未分配权重", format_metric(summary["unallocated_weight"], percent=True), "可继续加入候选"), ("组合质量因子", f"{quality_score:.0f}" if quality_score is not None else "不可用", "按持仓权重加权")])
    if not memory.securities:
        empty_state(st, "证券池为空", "研究数据正在准备，暂时无法创建组合成员。", "请稍后刷新或联系管理员。")
        return
    with st.expander("加入组合", expanded=not positions):
        _render_add_to_portfolio(store, sorted(memory.securities), "portfolio")
    if positions:
        st.markdown("### 权重草案")
        with st.form("portfolio_weights"):
            updated = []
            for row in positions:
                security = memory.securities.get(row["ts_code"])
                weight = st.number_input(security_option_label(row["ts_code"], security.name if security else "未知证券"), min_value=0.0, max_value=1.0, value=float(row["weight"]), step=0.05, format="%.2f", key=f"portfolio_weight_{row['ts_code']}")
                updated.append((row["ts_code"], weight))
            if st.form_submit_button("保存权重"):
                if sum(weight for _, weight in updated) > 1.000001:
                    st.error("组合权重合计不能超过 100%")
                else:
                    for code, weight in updated:
                        store.upsert_portfolio_position("default", code, weight)
                    st.success("组合权重已保存")
        remove = st.selectbox("移除成员", [row["ts_code"] for row in positions], format_func=lambda value: security_option_label(value, memory.securities[value].name))
        if st.button("移出组合"):
            store.delete_portfolio_position("default", remove)
            _safe_rerun(st)
        st.markdown("### 行业暴露")
        st.dataframe(frame([{"行业": industry, "权重": weight} for industry, weight in sorted(summary["industry_weights"].items(), key=lambda item: item[1], reverse=True)]), hide_index=True, use_container_width=True)
        history = portfolio_history_rows(memory, positions)
        if history:
            st.markdown("### 组合历史净值")
            st.line_chart(frame(history).set_index("日期"))
        else:
            st.caption("持仓与沪深300没有足够的共同交易日，暂不展示历史净值。")
    else:
        empty_state(st, "组合还没有成员", "从股票池或个股研究加入标的后，这里会显示权重与行业暴露。")
    if positions and st.button("运行我的组合历史回测", type="primary"):
        run_id = run_portfolio_backtest_run(store)
        run = store.get_run(run_id)
        feedback_level, feedback_message = public_run_feedback(run, "组合历史回测已完成。")
        getattr(st, feedback_level)(feedback_message)
    personal_runs = [run for run in store.list_runs() if run["run_type"] == "portfolio_backtest"]
    if personal_runs:
        st.markdown("### 我的组合回测")
        personal = personal_runs[0]
        payload = run_payload(personal)
        if personal["status"] == "completed":
            metric_cards(st, [("组合累计收益", format_metric((payload.get("metrics") or {}).get("total_return"), percent=True), "按保存权重模拟持有"), ("相对沪深300", format_metric((payload.get("metrics") or {}).get("excess_return"), percent=True), "共同交易日"), ("最大回撤", format_metric((payload.get("metrics") or {}).get("max_drawdown"), percent=True), "历史样本")])
        else:
            feedback_level, feedback_message = public_run_feedback(personal, "组合历史回测已完成。")
            getattr(st, feedback_level)(feedback_message)
    research_disclaimer(st)


def render_data_status(store):
    memory = page_memory(store, price_days=2)
    quality = data_quality_summary(memory)
    counts = store.fast_counts()
    render_public_data_status(st, quality, counts, store.list_runs())


@st.cache_resource(show_spinner=False)
def repository(dsn: str) -> PostgresStore:
    return PostgresStore(dsn)


inject_workspace_css(st)
st.sidebar.markdown("### 量化研究工作台")
st.sidebar.caption("沪深300 · 研究信号")
admin_mode = admin_mode_enabled(os.getenv("QUANT_ADMIN_MODE"))
pages = navigation_pages(admin_mode)
page = st.sidebar.radio("研究模块", pages, index=0)
dsn = os.getenv("DATABASE_URL", "")
if not dsn:
    title, detail, next_step = database_unavailable_copy(admin_mode)
    page_header(st, "量化研究工作台", "研究数据与历史结果仅用于研究参考。")
    empty_state(st, title, detail, next_step)
    research_disclaimer(st)
    st.stop()
try:
    store = repository(dsn)
    store.initialize()
except Exception:
    title, detail, next_step = database_unavailable_copy(admin_mode)
    empty_state(st, title, detail, next_step)
    research_disclaimer(st)
    st.stop()

if page == "今日机会":
    render_today_opportunities(store)
elif page == "股票池":
    render_stock_pool(store)
elif page == "个股研究":
    render_stock_data(store)
elif page == "行业景气":
    render_industry_heat(store)
elif page == "策略实验室":
    render_strategy_lab(store)
elif page == "组合":
    render_portfolio(store)
elif page == "回测":
    render_backtest_results(store)
elif page == "管理员":
    render_admin_workbench(st, store)
else:
    render_data_status(store)
