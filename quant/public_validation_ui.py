"""Reader-facing historical validation workbench."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pandas as pd

from .presentation import format_metric
from .scoring import FACTOR_MODEL_VERSION, PIT_DATA_VERSION
from .templates import TEMPLATES
from .ui import metric_cards, research_disclaimer
from .validation_api import ADJUSTMENT_LABELS, validation_adjustments
from .workflows import _historical_universe_version, _monthly_rebalance_dates, run_factor_backtest_run


def newest_completed_research_result(store) -> dict | None:
    latest = getattr(store, "latest_run_summary", None)
    if latest is not None:
        return latest("model", "completed")
    return next((run for run in store.list_runs() if run.get("run_type") == "model" and run.get("status") == "completed"), None)


def _newest_validation(store) -> dict | None:
    return next(
        (
            run for run in store.list_runs()
            if run.get("run_type") == "backtest"
            and (run.get("parameters") or {}).get("strategy_type") == "FACTOR"
        ),
        None,
    )


def _payload(run: Mapping | None) -> Mapping:
    payload = (run or {}).get("payload")
    return payload if isinstance(payload, Mapping) else {}


def _curve_dates(payload: Mapping) -> list[str]:
    return [str(row["date"]) for row in payload.get("equity_curve") or [] if isinstance(row, Mapping) and row.get("date")]


def _model_coverage(model: Mapping) -> tuple[str, str, int]:
    dates = sorted({str(row.get("trade_date") or row.get("as_of_date")) for row in _payload(model).get("rows") or [] if isinstance(row, Mapping) and (row.get("trade_date") or row.get("as_of_date"))})
    return (dates[0], dates[-1], len(dates)) if dates else ("不可用", "不可用", 0)


def _drawdown_rows(rows: object) -> list[dict]:
    peak, output = 1.0, []
    for row in rows or []:
        if not isinstance(row, Mapping) or row.get("value") is None or not row.get("date"):
            continue
        value = float(row["value"])
        peak = max(peak, value)
        output.append({"日期": row["date"], "回撤": value / peak - 1})
    return output


def _annual_rows(payload: Mapping) -> list[dict]:
    points: dict[int, int] = {}
    for value in _curve_dates(payload):
        try:
            year = date.fromisoformat(value).year
        except ValueError:
            continue
        points[year] = points.get(year, 0) + 1
    output = []
    for row in payload.get("annual_returns") or []:
        if not isinstance(row, Mapping) or row.get("year") is None:
            continue
        year = int(row["year"])
        label = f"{year} YTD" if year == date.today().year else str(year)
        if points.get(year, 0) < 2:
            output.append({"年度": label, "策略": "样本不足", "沪深300": "样本不足", "超额": "样本不足", "最大回撤": "样本不足"})
            continue
        curve = [item for item in payload.get("equity_curve") or [] if str(item.get("date", "")).startswith(str(year))]
        strategy_return, benchmark_return = row.get("strategy"), row.get("benchmark")
        excess_return = strategy_return - benchmark_return if strategy_return is not None and benchmark_return is not None else None
        output.append({"年度": label, "策略": format_metric(strategy_return, percent=True), "沪深300": format_metric(benchmark_return, percent=True), "超额": format_metric(excess_return, percent=True), "最大回撤": format_metric(min((item["回撤"] for item in _drawdown_rows(curve)), default=None), percent=True)})
    return output


def _render_parameters(st) -> tuple[bool, dict]:
    compact = st.session_state.get("public_validation_compact", False)
    label = "验证参数" if not compact else "参数摘要 · 修改参数"
    with st.expander(label, expanded=not compact, icon=":material/tune:"):
        with st.form("public_historical_validation"):
            template_id = st.selectbox("研究模板", list(TEMPLATES), format_func=lambda item: TEMPLATES[item].name, key="public_validation_template")
            default_name = f"{TEMPLATES[template_id].name} · 历史验证"
            previous_default = st.session_state.get("public_validation_default_name")
            if st.session_state.get("public_validation_name") in (None, previous_default):
                st.session_state["public_validation_name"] = default_name
            st.session_state["public_validation_default_name"] = default_name
            experiment_name = st.text_input("实验名称", key="public_validation_name")
            dates = st.columns(2)
            start_date = dates[0].date_input("开始日期", date(2023, 1, 1), key="public_validation_start_date")
            end_date = dates[1].date_input("结束日期", date.today(), key="public_validation_end_date")
            controls = st.columns(4)
            controls[0].selectbox("股票池", ["沪深300"], disabled=True, key="public_validation_universe")
            controls[1].selectbox("基准指数", ["沪深300"], disabled=True, key="public_validation_benchmark")
            holding_count = controls[2].number_input("持仓数量", min_value=1, max_value=300, value=30, step=1, key="public_validation_holding_count")
            controls[3].selectbox("调仓频率", ["每月"], disabled=True, key="public_validation_frequency")
            cost_per_10k = st.number_input("每万元交易成本（元）", min_value=0.0, max_value=500.0, value=10.0, step=1.0, key="public_validation_cost_per_10k")
            with st.expander("高级设置", icon=":material/settings:"):
                st.caption("信号生成日：月末最后交易日 · 交易执行日：下一交易日开盘 · 权重方式：等权目标")
                st.caption("ST 股票排除；停牌与涨跌停时订单受阻并持续跟踪，恢复可交易后按实际开盘价成交；上市不足 180 天的股票排除。")
            submitted = st.form_submit_button("开始历史验证", type="primary", width="stretch")
    return submitted, {"template_id": template_id, "experiment_name": experiment_name, "start_date": start_date, "end_date": end_date, "holding_count": holding_count, "cost_per_10k": cost_per_10k}


def _render_data_check(st, store, start_date: date, end_date: date) -> None:
    memory = store.load_memory() if hasattr(store, "load_memory") else store
    dates = _monthly_rebalance_dates(memory, start_date, end_date)[:-1]
    existing = [day for day in dates if store.get_factor_snapshot(day, FACTOR_MODEL_VERSION, PIT_DATA_VERSION, _historical_universe_version(memory, day))]
    st.subheader("历史数据检查")
    st.caption(f"请求区间：{start_date} 至 {end_date} · 完整月度区间：{len(dates)} 个")
    st.dataframe(pd.DataFrame([{"检查项": "已缓存 PIT 因子快照", "状态": "部分可用" if existing and len(existing) < len(dates) else "可用" if dates and len(existing) == len(dates) else "待补算", "覆盖": f"{len(existing)} / {len(dates)} 个完整区间"}, {"检查项": "可检查月度区间数", "状态": "可用" if len(dates) >= 2 else "样本不足", "覆盖": f"{len(dates)} 个"}]), hide_index=True, width="stretch")
    if len(dates) < 2:
        st.warning("当前不足 2 个完整月度区间；运行后只会展示数据不足状态。", icon=":material/warning:")


def _progress_copy(event: Mapping) -> str | None:
    kind, day = event.get("event"), event.get("date")
    prefix = f"{event.get('current')}/{event.get('total')}  " if event.get("current") and event.get("total") else ""
    if kind == "snapshot_check":
        return f"{prefix}检查 {day} 快照：{'复用已有快照' if event.get('status') == 'reused' else '缺失，开始 PIT 计算'}"
    if kind == "snapshot_built":
        return f"{prefix}{day} 快照计算完成"
    if kind == "snapshot_reused":
        return f"{prefix}复用 {day} 快照"
    if kind in {"snapshot_failed", "period_skipped"}:
        return f"{prefix}{day} 跳过：{event.get('reason', '数据不足')}"
    if kind == "period_completed":
        return f"回测 {day}：有效"
    if kind == "completed":
        return f"执行结束：有效 {event.get('valid_periods', 0)} 期，跳过 {event.get('skipped_periods', 0)} 期"
    if kind == "failed":
        return "历史验证执行失败"
    return None


def _render_noncompleted_result(st, result: Mapping) -> None:
    status = result.get("status")
    summary = _payload(result).get("coverage_summary") or {}
    valid = int(summary.get("valid_periods") or 0)
    skipped = int(summary.get("skipped_periods") or 0)
    if status in {"partial", "insufficient_data", "not_trainable"}:
        label = "部分完成" if status == "partial" else "数据不足"
        st.warning(f"{label}：有效 {valid} 期，跳过 {skipped} 期。未达到完整历史验证条件，不展示绩效指标。", icon=":material/warning:")
        reason = _payload(result).get("status_reason")
        if reason:
            st.caption(f"原因：{reason}")
        skipped_rows = _payload(result).get("skipped_periods") or [
            event for event in _payload(result).get("backtest_events") or []
            if isinstance(event, Mapping) and event.get("event") == "period_skipped"
        ]
        if skipped_rows:
            with st.expander("跳过周期明细", icon=":material/event_busy:"):
                st.dataframe(pd.DataFrame([
                    {"信号日": row.get("date"), "期末调仓日": row.get("exit_date") or "--", "原因": row.get("reason") or "数据不足"}
                    for row in skipped_rows if isinstance(row, Mapping)
                ]), hide_index=True, width="stretch")
    else:
        st.error("历史验证暂时无法显示。请稍后重试或联系管理员。")


def render_historical_validation(st, store) -> None:
    """Keep configuration and its newest result in one workflow."""
    st.title("历史验证")
    submitted, values = _render_parameters(st)
    _render_data_check(st, store, values["start_date"], values["end_date"])
    active_result = None
    if submitted:
        if values["start_date"] > values["end_date"]:
            st.error("开始日期不能晚于结束日期。")
        else:
            with st.status("正在检查 PIT 因子快照…", expanded=True, width="stretch") as status_box:
                def report(event):
                    copy = _progress_copy(event)
                    if copy:
                        status_box.write(copy)
                        status_box.update(label=copy)
                run_id = run_factor_backtest_run(store, start_date=values["start_date"], end_date=values["end_date"], top_n=int(values["holding_count"]), cost_bps=float(values["cost_per_10k"]), template_id=values["template_id"], experiment_name=values["experiment_name"].strip() or "未命名历史验证", progress=report)
                active_result = store.get_run(run_id)
                summary = _payload(active_result).get("coverage_summary") or {}
                final_copy = f"执行结束：有效 {summary.get('valid_periods', 0)} 期，跳过 {summary.get('skipped_periods', 0)} 期"
                final_state = "complete" if active_result.get("status") == "completed" else "error"
                status_box.update(label=final_copy, state=final_state, expanded=True)
            st.session_state["public_validation_compact"] = True
            st.session_state["public_validation_scroll"] = True
    result = active_result or _newest_validation(store)
    if result and result.get("status") == "completed":
        render_validation_results(st, store, result=result)
    elif result:
        _render_noncompleted_result(st, result)
    st.caption("模拟规则：按所选研究模板生成等权目标，每月收盘发出信号、下一交易日开盘调仓；受阻订单延期执行，每日收盘估值。沪深300采用同期口径；历史验证不代表未来结果。")
    research_disclaimer(st)


def render_validation_results(st, store, *, result: Mapping | None = None) -> None:
    """Render saved results as sections within historical validation."""
    st.html('<div id="validation-results"></div>')
    st.header("验证结果")
    result = result or _newest_validation(store)
    if result is None:
        st.info("暂时没有可展示的验证结果。请先完成一次历史验证。")
        return
    if result.get("status") != "completed":
        st.warning("历史验证暂时无法显示。请稍后刷新或联系管理员。")
        return
    payload = _payload(result)
    metrics = _payload({"payload": payload.get("metrics")})
    parameters = result.get("parameters") if isinstance(result.get("parameters"), Mapping) else {}
    trades = [item for item in payload.get("trades") or [] if isinstance(item, Mapping)]
    valuation_audit = [item for item in payload.get("valuation_audit") or [] if isinstance(item, Mapping)]
    order_audit = [item for item in payload.get("order_audit") or [] if isinstance(item, Mapping)]
    dates = _curve_dates(payload)
    coverage = payload.get("coverage_summary") or {}
    period_count = coverage.get("valid_periods")
    if period_count is None:
        period_count = len({str(item.get("date")) for item in trades})
    sample_insufficient = len(dates) < 2 or int(period_count) < 2
    if sample_insufficient:
        st.warning(f"样本不足：当前仅包含 {period_count} 个调仓周期，本结果不具有统计意义。", icon=":material/warning:")
    with st.expander("核心指标", expanded=True, icon=":material/analytics:"):
        metric_cards(st, [("策略收益", format_metric(metrics.get("total_return"), percent=True), "历史样本期"), ("基准收益", format_metric(metrics.get("benchmark_return"), percent=True), "沪深300"), ("超额收益", format_metric(metrics.get("excess_return"), percent=True), "相对表现"), ("最大回撤", format_metric(metrics.get("max_drawdown"), percent=True), "从峰值回落")])
        metric_cards(st, [("年化收益", "不可用" if sample_insufficient else format_metric(metrics.get("annualized_return"), percent=True), "至少需要两个调仓期"), ("年化波动率", format_metric(metrics.get("annualized_volatility"), percent=True), "日收益年化"), ("夏普比率", format_metric(metrics.get("sharpe")), "风险调整收益"), ("调仓次数", str(len({str(item.get("date")) for item in trades})), "实际发生交易的信号日")])
    curves = []
    for rows, label in ((payload.get("equity_curve"), "研究组合"), (payload.get("benchmark_curve"), "沪深300"), (payload.get("excess_curve"), "超额表现")):
        curves.extend({"日期": row.get("date"), "曲线": label, "净值": row.get("value")} for row in rows or [] if isinstance(row, Mapping) and row.get("value") is not None)
    with st.expander("净值走势", expanded=True, icon=":material/show_chart:"):
        if curves:
            st.line_chart(pd.DataFrame(curves).pivot(index="日期", columns="曲线", values="净值"))
            st.caption("组合按首个成交日开盘前价值、基准按同期指数开盘价归一化为 1.0000；图中首个点是当日收盘估值。")
        else:
            st.caption("尚无足够的净值数据。")
    with st.expander("回撤", expanded=False, icon=":material/trending_down:"):
        drawdowns = _drawdown_rows(payload.get("equity_curve"))
        if len(drawdowns) >= 2:
            st.line_chart(pd.DataFrame(drawdowns).set_index("日期"))
        else:
            st.caption("样本不足，暂不展示回撤曲线。")
    sample_start, sample_end = (dates[0], dates[-1]) if dates else ("不可用", "不可用")
    cost = parameters.get("cost_bps")
    st.caption(f"样本区间：{sample_start} 至 {sample_end} · 调仓频率：每月")
    st.caption(f"每万元交易成本：{format_metric(cost) if cost is not None else '不可用'} 元 · 模拟规则：按模板生成等权目标，实际持仓受成交约束。")
    annual = _annual_rows(payload)
    if annual:
        with st.expander("年度表现", expanded=True, icon=":material/table_chart:"):
            st.dataframe(pd.DataFrame(annual), hide_index=True, width="stretch")
    capital = parameters.get("initial_capital", 1_000_000.0)
    adjustments = validation_adjustments(payload, store, capital=capital)
    if adjustments:
        with st.expander("历史调整", expanded=False, icon=":material/history:"):
            st.caption(f"模拟本金 {capital:,.2f} 元。股数按本金折算，允许小数股；明细截至该次调仓，不是今天的持仓。")
            periods = sorted({str(row["date"]) for row in adjustments if row.get("date")}, reverse=True)
            period = st.selectbox("查看调仓期", ["全部周期", *periods], index=1 if periods else 0, key="validation_ledger_period")
            query = st.text_input("搜索股票名称或代码", key="validation_ledger_query").strip().lower()
            action = st.selectbox("筛选调仓动作", ["全部动作", *ADJUSTMENT_LABELS.values(), "未记录"], key="validation_ledger_action")
            selected = [row for row in adjustments if (period == "全部周期" or str(row.get("date")) == period)
                        and (not query or query in f"{row.get('name') or ''} {row.get('ts_code') or ''}".lower())
                        and (action == "全部动作" or ADJUSTMENT_LABELS.get(row.get("action"), "未记录") == action)]
            if any(not row.get("action") or row.get("weight_before") is None for row in selected):
                st.caption("旧记录缺少动作或前后仓位，显示为未记录或 --；重新运行可生成完整明细。")
            page_count = max(1, (len(selected) + 14) // 15)
            page = st.selectbox("明细页", list(range(1, page_count + 1)), format_func=lambda value: f"第 {value} / {page_count} 页")
            st.caption(f"筛选后 {len(selected)} 条，每页最多 15 条。")
            visible = selected[(page - 1) * 15:page * 15]
            st.dataframe(pd.DataFrame([{
                "股票名称": row.get("name") or "名称缺失", "证券代码": row.get("ts_code"),
                "调仓动作": ADJUSTMENT_LABELS.get(row.get("action"), "未记录"),
                "调整前仓位": format_metric(row.get("weight_before"), percent=True),
                "调整后仓位": format_metric(row.get("weight_after"), percent=True),
                "开盘价（元）": format_metric(row.get("market_open")),
                "成交占组合比例": format_metric(row.get("trade_weight"), percent=True),
                "目标仓位": format_metric(row.get("weight"), percent=True),
                "信号日": row.get("date"), "处理日期": row.get("execution_date"),
                "延期处理": "是" if row.get("deferred") else "否",
            } for row in visible]), hide_index=True, width="stretch")
            if not selected:
                st.caption("没有符合条件的调仓记录，请调整筛选条件。")
            if st.checkbox("显示持仓详情"):
                st.caption("成本价按加权平均和除权口径计算，涨跌幅不含手续费；模拟股数不等同于券商整手成交。")
                st.dataframe(pd.DataFrame([{
                    "股票名称": row.get("name") or "名称缺失", "证券代码": row.get("ts_code"),
                    "首次买入日期": row.get("first_buy_date"), "最近买入日期": row.get("last_buy_date"),
                    "最近买入价（元）": format_metric(row.get("last_buy_price")),
                    "持仓成本价（元）": format_metric(row.get("average_cost")),
                    "模拟持仓（股）": format_metric(row.get("holding_shares")),
                    "持仓涨跌幅": format_metric(row.get("holding_return"), percent=True),
                    "持仓市值（元）": format_metric(row.get("holding_value")),
                    "本次手续费（元）": format_metric(row.get("fee_amount")),
                } for row in visible]), hide_index=True, width="stretch")
    if valuation_audit or order_audit:
        deferred = [row for row in order_audit if row.get("status") == "deferred"]
        st.info(f"停牌估值 {len(valuation_audit)} 条，受阻成交尝试 {len(deferred)} 次。停牌无行情时按停牌前最后可见收盘价估值；订单在恢复可交易后按实际开盘价成交，费用计入成交日。")
        with st.expander("停牌估值与订单审计", expanded=False, icon=":material/fact_check:"):
            if valuation_audit:
                st.dataframe(pd.DataFrame([{"估值日": row.get("date"), "证券代码": row.get("ts_code"), "结转收盘价": row.get("mark_price"), "停牌起始": row.get("suspend_date"), "复牌日期": row.get("resume_date")} for row in valuation_audit]), hide_index=True, width="stretch")
            if deferred:
                st.dataframe(pd.DataFrame([{"信号日": row.get("signal_date"), "尝试日期": row.get("attempt_date"), "证券代码": row.get("ts_code"), "原因": row.get("reason")} for row in deferred]), hide_index=True, width="stretch")
    with st.expander("历史实验记录", icon=":material/history:"):
        records = [run for run in store.list_runs() if run.get("run_type") == "backtest"]
        st.dataframe(pd.DataFrame([{"实验名称": (run.get("parameters") or {}).get("experiment_name", "未命名历史验证"), "状态": "已完成" if run.get("status") == "completed" else "需要处理", "持仓数量": (run.get("parameters") or {}).get("top_n", "不可用")} for run in records]), hide_index=True, width="stretch")
    if st.session_state.pop("public_validation_scroll", False):
        st.html("<script>document.getElementById('validation-results')?.scrollIntoView({behavior: 'smooth', block: 'start'});</script>", unsafe_allow_javascript=True)
