"""Pure projections for the administrator workbench."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from collections.abc import Callable

from .storage import sanitize_sensitive_text


TRUTHY = {"1", "true", "yes", "on"}
TASK_ORDER = ("sync", "quality", "factors", "model")
FRESHNESS_LOOKBACK_DAYS = 32
PUBLIC_NAVIGATION_PAGES = (
    "研究首页",
    "候选池",
    "个股详情",
    "行业观察",
    "我的组合",
    "历史验证",
    "数据状态",
)

_STATE_LABELS = {
    "running": "进行中",
    "completed": "已完成",
    "not_trainable": "需要处理",
    "partial": "需要处理",
    "failed": "失败",
}


def admin_mode_enabled(value: str | None) -> bool:
    """Return whether the deployment explicitly enables administrator mode."""
    return str(value or "").strip().lower() in TRUTHY


def navigation_pages(admin_mode: bool) -> list[str]:
    """Return the deployment-appropriate research navigation labels."""
    pages = list(PUBLIC_NAVIGATION_PAGES)
    if admin_mode:
        pages.append("管理员")
    return pages


def database_unavailable_copy(admin_mode: bool) -> tuple[str, str, str]:
    """Return audience-safe copy for an unavailable research database."""
    if admin_mode:
        return (
            "尚未连接研究数据库",
            "管理员部署尚未提供研究数据库。",
            "请在部署环境配置 DATABASE_URL。",
        )
    return ("研究数据正在准备", "当前暂时无法加载研究数据。", "请稍后刷新或联系管理员。")


def data_is_stale(
    latest_trade_date: date | None,
    *,
    today: date,
    is_trade_day: Callable[[date], bool],
) -> bool:
    """Return whether an expected completed trade day is missing from the latest data.

    The check excludes ``today`` because the current session may not have
    completed.  It calls the persisted-calendar predicate for at most 31
    calendar dates; gaps longer than 32 days are immediately stale.
    """
    if latest_trade_date is None or today <= latest_trade_date + timedelta(days=1):
        return False
    if today - latest_trade_date > timedelta(days=FRESHNESS_LOOKBACK_DAYS):
        return True
    candidate = latest_trade_date + timedelta(days=1)
    while candidate < today:
        if is_trade_day(candidate):
            return True
        candidate += timedelta(days=1)
    return False


def pipeline_steps(runs: list[dict], quality: dict) -> list[dict]:
    """Project stored runs and data quality into the ordered admin pipeline."""
    latest = {
        run_type: next((run for run in runs if run.get("run_type") == run_type), None)
        for run_type in ("sync", "factors", "model")
    }
    data_ready = bool(quality.get("is_complete")) and not bool(quality.get("is_stale"))
    factor_ready = bool(latest["factors"] and latest["factors"].get("status") == "completed")
    running = {
        run_type
        for run_type, run in latest.items()
        if run and run.get("status") == "running"
    }
    return [
        _step(
            "sync",
            "数据同步",
            latest["sync"],
            can_run="sync" not in running,
            next_step="同步沪深300行情、财务和估值数据。",
        ),
        _quality_step(quality),
        _step(
            "factors",
            "构建研究因子",
            latest["factors"],
            can_run=data_ready and "factors" not in running,
            next_step="请先完成数据完整性检查。"
            if not data_ready
            else "使用最新完整数据构建研究因子。",
        ),
        _step(
            "model",
            "生成研究模型",
            latest["model"],
            can_run=factor_ready and "model" not in running,
            next_step="请先完成一项研究因子运行。"
            if not factor_ready
            else "使用最近完成的因子生成研究结果。",
            prerequisite_run_id=latest["factors"].get("run_id") if factor_ready else None,
        ),
    ]


def _step(
    step_id: str,
    title: str,
    run: dict | None,
    *,
    can_run: bool,
    next_step: str,
    prerequisite_run_id: str | None = None,
) -> dict:
    status = run.get("status") if run else None
    state = _STATE_LABELS.get(status, "待执行" if run is None else "需要处理")
    payload = (run or {}).get("payload") or {}
    progress = payload.get("progress") or {}
    if run is None:
        summary = "尚未运行。"
    elif status == "failed":
        summary = "最近一次运行失败。"
    elif status in {"not_trainable", "partial"}:
        summary = "最近一次运行需要处理。"
    elif status == "completed":
        summary = "最近一次运行已完成。"
    elif status == "running":
        summary = "最近一次运行正在进行。"
    else:
        summary = "最近一次运行状态需要处理。"
    return {
        "id": step_id,
        "title": title,
        "state": state,
        "summary": summary,
        "next_step": next_step,
        "can_run": bool(can_run),
        "run_id": run.get("run_id") if run else None,
        "prerequisite_run_id": prerequisite_run_id,
        "progress": progress,
    }


def _quality_step(quality: dict) -> dict:
    complete = bool(quality.get("is_complete"))
    stale = bool(quality.get("is_stale"))
    missing_prices = quality.get("missing_latest_price_codes") or []
    missing_financials = quality.get("missing_financial_codes") or []
    if stale:
        summary = "最新交易日已过期，需要重新同步。"
        next_step = "请重新同步数据后再执行完整性检查。"
        state = "需要处理"
    elif complete:
        summary = "行情、财务和估值数据完整。"
        next_step = "可以构建研究因子。"
        state = "已完成"
    else:
        summary = (
            f"仍需处理 {len(missing_prices)} 个行情缺口、"
            f"{len(missing_financials)} 个财务缺口。"
        )
        next_step = "请先完成数据完整性检查。"
        state = "需要处理"
    return {
        "id": "quality",
        "title": "数据完整性检查",
        "state": state,
        "summary": summary,
        "next_step": next_step,
        "can_run": False,
        "run_id": None,
        "prerequisite_run_id": None,
        "progress": {
            "security_count": quality.get("security_count", 0),
            "latest_trade_date": quality.get("latest_trade_date"),
            "missing_latest_price_count": len(missing_prices),
            "missing_financial_count": len(missing_financials),
            "valuation_count": quality.get("valuation_count", 0),
        },
    }


def filter_admin_runs(
    runs: list[dict], run_type: str | None = None, status: str | None = None
) -> list[dict]:
    """Filter audit rows without changing their input ordering."""
    return [
        run
        for run in runs
        if (run_type is None or run.get("run_type") == run_type)
        and (status is None or run.get("status") == status)
    ]


def retry_parameters(run: dict) -> dict | None:
    """Extract only safe date and run-reference parameters for a failed run."""
    if not isinstance(run, dict) or run.get("status") != "failed":
        return None
    run_type = run.get("run_type")
    parameters = run.get("parameters") or {}
    if not isinstance(parameters, dict) or run_type not in {"sync", "factors", "model"}:
        return None

    if run_type == "sync":
        result = {}
        for key in ("start_date", "end_date"):
            parsed = _as_date(parameters.get(key))
            if parsed is not None:
                result[key] = parsed
        return result

    result = {}
    date_key = "as_of_dates" if run_type == "factors" else "prediction_dates"
    dates = parameters.get(date_key)
    if isinstance(dates, (list, tuple)):
        parsed_dates = [_as_date(item) for item in dates]
        result[date_key] = [item for item in parsed_dates if item is not None]
    elif dates is not None:
        parsed = _as_date(dates)
        if parsed is not None:
            result[date_key] = [parsed]
    if run_type == "model":
        factor_run_id = parameters.get("factor_run_id")
        if factor_run_id is not None and str(factor_run_id).strip():
            result["factor_run_id"] = factor_run_id
        # Keep the public order aligned with workflow parameters.
        if "factor_run_id" in result and date_key in result:
            result = {"factor_run_id": result["factor_run_id"], date_key: result[date_key]}
    return result


def _as_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def redact_sensitive_text(value: str) -> str:
    """Remove credentials and values of common secret-bearing fields."""
    return sanitize_sensitive_text(value)
