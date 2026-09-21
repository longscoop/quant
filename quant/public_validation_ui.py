"""Reader-facing historical validation workbench."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pandas as pd

from .presentation import format_metric
from .templates import TEMPLATES
from .ui import metric_cards, research_disclaimer
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
        output.append({"年度": label, "策略": format_metric(row.get("strategy"), percent=True), "沪深300": format_metric(row.get("benchmark"), percent=True), "超额": format_metric((row.get("strategy") or 0) - (row.get("benchmark") or 0), percent=True), "最大回撤": format_metric(min((item["回撤"] for item in _drawdown_rows(curve)), default=None), percent=True)})
    return output


def _render_parameters(st) -> tuple[bool, dict]:
    compact = st.session_state.get("public_validation_compact", False)
    label = "验证参数" if not compact else "参数摘要 · 修改参数"
    with st.expander(label, expanded=not compact, icon=":material/tune:"):
        with st.form("public_historical_validation"):
            template_id = st.selectbox("研究模板", list(TEMPLATES), format_func=lambda item: TEMPLATES[item].name, key="public_validation_template")
            experiment_name = st.text_input("实验名称", value=f"{TEMPLATES[template_id].name} · 历史验证", key="public_validation_name")
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
                st.caption("信号生成日：月末最后交易日 · 交易执行日：下一交易日 · 权重方式：等权 · 单股上限：10%")
                st.caption("ST 股票排除；停牌与涨跌停按无法成交处理；上市不足 180 天的股票排除。")
            submitted = st.form_submit_button("开始历史验证", type="primary", width="stretch")
    return submitted, {"template_id": template_id, "experiment_name": experiment_name, "start_date": start_date, "end_date": end_date, "holding_count": holding_count, "cost_per_10k": cost_per_10k}


def _render_data_check(st, store, start_date: date, end_date: date) -> None:
    memory = store.load_memory() if hasattr(store, "load_memory") else store
    dates = _monthly_rebalance_dates(memory, start_date, end_date)
    existing = [day for day in dates if store.get_factor_snapshot(day, "pit_v1.0", "pit_v1.0", _historical_universe_version(memory, day))]
    st.subheader("历史数据检查")
    st.caption(f"请求区间：{start_date} 至 {end_date} · 月度调仓期：{len(dates)} 个")
    st.dataframe(pd.DataFrame([{"检查项": "已缓存 PIT 因子快照", "状态": "部分可用" if existing and len(existing) < len(dates) else "可用" if dates and len(existing) == len(dates) else "待补算", "覆盖": f"{len(existing)} / {len(dates)} 个调仓期"}, {"检查项": "可检查调仓期数", "状态": "可用" if len(dates) >= 2 else "样本不足", "覆盖": f"{len(dates)} 个"}]), hide_index=True, width="stretch")
    if len(dates) < 2:
        st.warning("当前不足 2 个可检查调仓期；运行后只会展示数据不足状态。", icon=":material/warning:")


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
            progress_bar = st.progress(0, text="准备检查 PIT 因子快照…")
            with st.status("正在检查 PIT 因子快照…", expanded=True, width="stretch") as status_box:
                def report(event):
                    copy = _progress_copy(event)
                    if copy:
                        status_box.write(copy)
                        status_box.update(label=copy)
                        if event.get("current") and event.get("total"):
                            phase_start = 80 if event.get("event", "").startswith("period_") else 0
                            phase_span = 20 if phase_start else 80
                            value = phase_start + int(event["current"] / event["total"] * phase_span)
                            progress_bar.progress(min(value, 99), text=copy)
                run_id = run_factor_backtest_run(store, start_date=values["start_date"], end_date=values["end_date"], top_n=int(values["holding_count"]), cost_bps=float(values["cost_per_10k"]), template_id=values["template_id"], experiment_name=values["experiment_name"].strip() or "未命名历史验证", progress=report)
                active_result = store.get_run(run_id)
                summary = _payload(active_result).get("coverage_summary") or {}
                final_copy = f"执行结束：有效 {summary.get('valid_periods', 0)} 期，跳过 {summary.get('skipped_periods', 0)} 期"
                final_state = "complete" if active_result.get("status") == "completed" else "error"
                status_box.update(label=final_copy, state=final_state, expanded=True)
                progress_bar.progress(100, text=final_copy)
            st.session_state["public_validation_compact"] = True
            st.session_state["public_validation_scroll"] = True
    result = active_result or _newest_validation(store)
    if result and result.get("status") == "completed":
        render_validation_results(st, store, result=result)
    elif result:
        _render_noncompleted_result(st, result)
    st.caption("模拟规则：按所选研究模板等权持仓并每月调整，以沪深300作为历史比较基准。交易成本按每万元交易金额估算；历史验证不代表未来结果。")
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
    dates = _curve_dates(payload)
    sample_insufficient = len(dates) < 2 or len({str(item.get("date")) for item in trades}) < 2
    if sample_insufficient:
        st.warning(f"样本不足：当前仅包含 {len({str(item.get('date')) for item in trades})} 个调仓周期，本结果不具有统计意义。", icon=":material/warning:")
    with st.expander("核心指标", expanded=True, icon=":material/analytics:"):
        metric_cards(st, [("策略收益", format_metric(metrics.get("total_return"), percent=True), "历史样本期"), ("基准收益", format_metric(metrics.get("benchmark_return"), percent=True), "沪深300"), ("超额收益", format_metric(metrics.get("excess_return"), percent=True), "相对表现"), ("最大回撤", format_metric(metrics.get("max_drawdown"), percent=True), "从峰值回落")])
        metric_cards(st, [("年化收益", "不可用" if sample_insufficient else format_metric(metrics.get("annualized_return"), percent=True), "至少需要两个调仓期"), ("年化波动率", format_metric(metrics.get("annualized_volatility"), percent=True), "月度收益年化"), ("夏普比率", format_metric(metrics.get("sharpe")), "风险调整收益"), ("调仓次数", str(len(trades)), "按月调整")])
    curves = []
    for rows, label in ((payload.get("equity_curve"), "研究组合"), (payload.get("benchmark_curve"), "沪深300"), (payload.get("excess_curve"), "超额表现")):
        curves.extend({"日期": row.get("date"), "曲线": label, "净值": row.get("value")} for row in rows or [] if isinstance(row, Mapping) and row.get("value") is not None)
    with st.expander("净值走势", expanded=True, icon=":material/show_chart:"):
        if curves:
            st.line_chart(pd.DataFrame(curves).pivot(index="日期", columns="曲线", values="净值"))
            st.caption("曲线均以 1.0000 为起点；悬停可查看各期净值，避免将小数净值误读为收益率。")
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
    st.caption(f"每万元交易成本：{format_metric(cost) if cost is not None else '不可用'} 元 · 模拟规则：按所选研究模板等权持仓并月度调整。")
    annual = _annual_rows(payload)
    if annual:
        with st.expander("年度表现", expanded=True, icon=":material/table_chart:"):
            st.dataframe(pd.DataFrame(annual), hide_index=True, width="stretch")
    if trades:
        with st.expander("历史调整", expanded=False, icon=":material/history:"):
            st.dataframe(pd.DataFrame([{"调整日期": row.get("date"), "证券代码": row.get("ts_code"), "目标权重": format_metric(row.get("weight"), percent=True)} for row in trades]), hide_index=True, width="stretch")
    with st.expander("历史实验记录", icon=":material/history:"):
        records = [run for run in store.list_runs() if run.get("run_type") == "backtest"]
        st.dataframe(pd.DataFrame([{"实验名称": (run.get("parameters") or {}).get("experiment_name", "未命名历史验证"), "状态": "已完成" if run.get("status") == "completed" else "需要处理", "持仓数量": (run.get("parameters") or {}).get("top_n", "不可用")} for run in records]), hide_index=True, width="stretch")
    if st.session_state.pop("public_validation_scroll", False):
        st.html("<script>document.getElementById('validation-results')?.scrollIntoView({behavior: 'smooth', block: 'start'});</script>", unsafe_allow_javascript=True)
