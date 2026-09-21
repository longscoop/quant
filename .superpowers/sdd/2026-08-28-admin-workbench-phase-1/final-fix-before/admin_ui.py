"""Native Streamlit renderer for the deployment-gated administrator workbench."""

from __future__ import annotations

from datetime import date
import os

import pandas as pd

from .admin import data_is_stale, filter_admin_runs, pipeline_steps, redact_sensitive_text, retry_parameters
from .workflows import build_factor_run, sync_hs300, train_model_run


def render_admin_workbench(st, store, *, today: date | None = None) -> None:
    """Render operational controls without treating the page as authentication."""
    st.title("管理员工作台")
    st.caption("准备研究数据、执行研究流水线并诊断失败任务。")
    _apply_retry_defaults(st)
    if st.session_state.pop("admin_retry_ready", False):
        st.success("已预填安全参数；请确认后手动提交任务。")

    if hasattr(store, "fail_stale_sync_runs"):
        store.fail_stale_sync_runs()
    quality = dict(store.data_quality("hs300"))
    quality["is_stale"] = data_is_stale(
        quality.get("latest_trade_date"),
        today=today or date.today(),
        is_trade_day=store.is_trade_day,
    )
    counts = store.fast_counts()
    runs = store.list_runs(200)

    _render_overview(st, quality, counts)
    _render_pipeline(st, store, quality, runs)
    _render_audit(st, runs)


def _render_overview(st, quality: dict, counts: dict) -> None:
    st.subheader("数据概览")
    security_count = int(quality.get("security_count") or counts.get("securities") or 0)
    price_covered = max(0, security_count - len(quality.get("missing_latest_price_codes") or []))
    financial_covered = max(0, security_count - len(quality.get("missing_financial_codes") or []))
    metrics = st.container(horizontal=True, width="stretch")
    metrics.metric("最新交易日", _display_date(quality.get("latest_trade_date")))
    metrics.metric("证券数量", security_count)
    metrics.metric("最新行情覆盖", _coverage(price_covered, security_count))
    metrics.metric("财务覆盖", _coverage(financial_covered, security_count))
    metrics.metric("估值记录", quality.get("valuation_count", counts.get("valuation_count", 0)))


def _render_pipeline(st, store, quality: dict, runs: list[dict]) -> None:
    st.subheader("研究流水线")
    steps = pipeline_steps(runs, quality)
    for step in steps:
        with st.container(border=True, width="stretch"):
            st.subheader(f"{step['title']} · {step['state']}")
            st.write(step["summary"])
            st.caption(f"下一步：{step['next_step']}")
            if step["id"] == "sync":
                _render_sync_form(st, store, step["can_run"])
            elif step["id"] == "quality":
                _render_quality_details(st, quality)
            elif step["id"] == "factors":
                _render_factor_form(st, store, step["can_run"], quality)
            elif step["id"] == "model":
                _render_model_form(st, store, step["can_run"], runs)


def _render_quality_details(st, quality: dict) -> None:
    if quality.get("is_stale"):
        st.warning("数据日期已过期；请先重新同步并完成完整性检查。")
    elif quality.get("is_complete"):
        st.success("数据完整性检查已通过。")
    else:
        st.warning("数据尚未完整；请先同步并处理缺口。")
    st.caption(
        "缺最新行情："
        f"{len(quality.get('missing_latest_price_codes') or [])} · "
        "缺财务："
        f"{len(quality.get('missing_financial_codes') or [])}"
    )


def _render_sync_form(st, store, can_run: bool) -> None:
    start_default = _session_date(st, "admin_sync_retry_start", date(2020, 1, 1))
    end_default = _session_date(st, "admin_sync_retry_end", date.today())
    with st.form("admin_sync"):
        entered_token = st.text_input(
            "Tushare Token",
            type="password",
            value="",
            key="admin_sync_token",
        )
        start = _date_input(st, "开始日期", start_default, "admin_sync_start")
        end = _date_input(st, "结束日期", end_default, "admin_sync_end")
        submitted = st.form_submit_button(
            "开始同步",
            key="admin_sync_submit",
            type="primary",
            disabled=not can_run,
            width="content",
        )
    if not submitted:
        return
    token = entered_token or os.getenv("TUSHARE_TOKEN", "")
    if not token:
        st.error("请输入 Tushare Token；Token 不会被保存。")
    elif start > end:
        st.error("开始日期不能晚于结束日期。")
    else:
        try:
            with st.status("正在同步研究数据…", expanded=True, width="stretch") as status:
                run_id = sync_hs300(
                    store,
                    token,
                    start,
                    end,
                    progress=lambda phase, current, total, detail=None: st.write(
                        f"{phase}：{current}/{total}"
                    ),
                )
                run = store.get_run(run_id)
                _update_status(st, status, run, "同步")
        except Exception as exc:
            st.error(f"同步未能启动：{redact_sensitive_text(str(exc))}")


def _render_factor_form(st, store, can_run: bool, quality: dict) -> None:
    latest = _coerce_date(quality.get("latest_trade_date"))
    default_cutoff = latest
    if default_cutoff is None:
        default_cutoff = date.today()
    with st.form("admin_factors"):
        cutoff = _date_input(st, "因子截止日期", default_cutoff, "admin_factor_cutoff")
        submitted = st.form_submit_button(
            "构建研究因子",
            key="admin_factors_submit",
            type="primary",
            disabled=not can_run or latest is None,
            width="content",
        )
    if not submitted:
        return
    if latest is None or cutoff > latest:
        st.error("因子截止日期必须是最新可用交易日或更早日期。")
        return
    dates_up_to_cutoff = _trading_dates_through(store, cutoff)
    if not dates_up_to_cutoff:
        st.error("所选截止日期前没有可用交易日；请先同步行情数据。")
        return
    with st.status("正在构建研究因子…", expanded=True, width="stretch") as status:
        run_id = build_factor_run(store, dates_up_to_cutoff)
        _update_status(st, status, store.get_run(run_id), "因子运行")


def _render_model_form(st, store, can_run: bool, runs: list[dict]) -> None:
    factor_runs = [
        run for run in runs if run.get("run_type") == "factors" and run.get("status") == "completed"
    ]
    retry_factor_run_id = st.session_state.pop("admin_model_retry_factor_run_id", None)
    if retry_factor_run_id:
        retry_index = next(
            (index for index, run in enumerate(factor_runs) if run.get("run_id") == retry_factor_run_id),
            None,
        )
        if retry_index is not None:
            st.session_state["admin_model_factor_run"] = retry_index
    factor_index = None
    if factor_runs:
        factor_index = st.selectbox(
            "因子运行",
            range(len(factor_runs)),
            format_func=lambda index: f"已完成因子运行（截止 {_display_date(_factor_run_end_date(factor_runs[index]))}）",
            key="admin_model_factor_run",
        )
    factor_run = factor_runs[factor_index] if factor_index is not None else None
    default_prediction = _factor_run_end_date(factor_run)
    if default_prediction is None:
        default_prediction = date.today()
    with st.form("admin_model"):
        st.caption("使用最近完成的因子运行。")
        prediction_date = _date_input(
            st, "预测日期", default_prediction, "admin_model_prediction_date"
        )
        submitted = st.form_submit_button(
            "生成研究模型",
            key="admin_model_submit",
            type="primary",
            disabled=not can_run or factor_run is None or _factor_run_end_date(factor_run) is None,
            width="content",
        )
    if not submitted or factor_run is None:
        return
    with st.status("正在生成研究模型…", expanded=True, width="stretch") as status:
        run_id = train_model_run(store, factor_run["run_id"], [prediction_date])
        _update_status(st, status, store.get_run(run_id), "模型运行")


def _update_status(st, status, run: dict, activity: str) -> None:
    state = run.get("status")
    if state == "completed":
        status.update(label=f"{activity}已完成", state="complete")
        st.success(f"{activity}已完成。")
    elif state == "not_trainable":
        status.update(label=f"{activity}需要处理", state="error")
        st.warning("运行未产生可训练结果；请检查数据完整性和日期范围。")
    else:
        status.update(label=f"{activity}失败", state="error")
        st.error("运行失败；请在审计记录中查看已脱敏的技术原因。")


def _render_audit(st, runs: list[dict]) -> None:
    st.subheader("运行审计")
    run_types = sorted({str(run.get("run_type")) for run in runs if run.get("run_type")})
    statuses = sorted({str(run.get("status")) for run in runs if run.get("status")})
    controls = st.container(horizontal=True, width="stretch")
    type_label = controls.selectbox("任务类型", ["全部", *run_types], key="admin_audit_type")
    status_label = controls.selectbox("运行状态", ["全部", *statuses], key="admin_audit_status")
    selected_runs = filter_admin_runs(
        runs,
        None if type_label == "全部" else type_label,
        None if status_label == "全部" else status_label,
    )
    rows = [
        {
            "任务类型": run.get("run_type") or "未记录",
            "状态": run.get("status") or "未记录",
            "开始时间": _display_datetime(run.get("created_at")),
            "结束时间": _display_datetime(run.get("completed_at")),
        }
        for run in selected_runs
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    failed = [run for run in selected_runs if run.get("status") == "failed"]
    if not failed:
        return
    selected_index = st.selectbox(
        "选择失败任务",
        range(len(failed)),
        format_func=lambda index: (
            f"{failed[index].get('run_type', '未记录')} · "
            f"{_display_datetime(failed[index].get('created_at'))}"
        ),
        key="admin_failed_run",
    )
    selected = failed[selected_index]
    safe_parameters = retry_parameters(selected)
    with st.expander("失败任务技术详情"):
        st.caption(f"运行 ID：{selected.get('run_id', '未记录')}")
        safe_error = redact_sensitive_text(selected.get("error") or "未记录技术原因")
        st.code(safe_error, language=None)
        if safe_parameters is None:
            st.info("该失败任务没有可安全复用的参数。")
        else:
            st.write("可安全复用的参数：")
            st.json(_display_retry_parameters(safe_parameters))
    if safe_parameters and st.button("准备重试参数", key="admin_prepare_retry", width="content"):
        _prepare_retry(st, selected.get("run_type"), safe_parameters)
        st.session_state["admin_retry_ready"] = True
        st.rerun()


def _prepare_retry(st, run_type: str | None, parameters: dict) -> None:
    if run_type == "sync":
        if parameters.get("start_date"):
            st.session_state["admin_sync_retry_start"] = parameters["start_date"]
        if parameters.get("end_date"):
            st.session_state["admin_sync_retry_end"] = parameters["end_date"]
    elif run_type == "factors":
        dates = list(parameters.get("as_of_dates") or [])
        if dates:
            st.session_state["admin_factor_retry_cutoff"] = dates[-1]
    elif run_type == "model":
        factor_run_id = parameters.get("factor_run_id")
        if factor_run_id:
            st.session_state["admin_model_retry_factor_run_id"] = factor_run_id
        dates = list(parameters.get("prediction_dates") or [])
        if dates:
            st.session_state["admin_model_retry_prediction_date"] = dates[-1]


def _apply_retry_defaults(st) -> None:
    pending = {
        "admin_sync_retry_start": "admin_sync_start",
        "admin_sync_retry_end": "admin_sync_end",
        "admin_factor_retry_cutoff": "admin_factor_cutoff",
        "admin_model_retry_prediction_date": "admin_model_prediction_date",
    }
    for pending_key, widget_key in pending.items():
        value = st.session_state.pop(pending_key, None)
        if value is not None:
            st.session_state[widget_key] = value


def _factor_run_end_date(run: dict | None) -> date | None:
    if not run:
        return None
    payload = run.get("payload") or {}
    metadata = payload.get("metadata") or {}
    value = _coerce_date(metadata.get("date_end"))
    if value is not None:
        return value
    return max(
        (_coerce_date(row.get("as_of_date")) for row in payload.get("rows") or []),
        default=None,
    )


def _trading_dates_through(store, cutoff: date) -> list[date]:
    memory = store.load_memory()
    return sorted(
        {
            trade_date
            for price in memory.prices.values()
            if (trade_date := _coerce_date(price.trade_date)) is not None and trade_date <= cutoff
        }
    )


def _session_date(st, key: str, fallback: date) -> date:
    return _coerce_date(st.session_state.get(key)) or fallback


def _date_input(st, label: str, default: date, key: str) -> date:
    if key in st.session_state:
        return st.date_input(label, key=key)
    return st.date_input(label, default, key=key)


def _coerce_date(value) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _display_date(value) -> str:
    parsed = _coerce_date(value)
    return parsed.isoformat() if parsed else "不可用"


def _display_datetime(value) -> str:
    return str(value) if value else "未记录"


def _coverage(covered: int, total: int) -> str:
    return "不可用" if total <= 0 else f"{covered}/{total}"


def _display_retry_parameters(parameters: dict) -> dict:
    return {
        key: [item.isoformat() if isinstance(item, date) else item for item in value]
        if isinstance(value, list)
        else value.isoformat() if isinstance(value, date) else value
        for key, value in parameters.items()
    }
