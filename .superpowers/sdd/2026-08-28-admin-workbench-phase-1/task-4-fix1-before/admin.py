"""Pure projections for the administrator workbench."""

from __future__ import annotations

from datetime import date, datetime
import re


TRUTHY = {"1", "true", "yes", "on"}
TASK_ORDER = ("sync", "quality", "factors", "model")
PUBLIC_NAVIGATION_PAGES = (
    "今日机会",
    "股票池",
    "个股研究",
    "行业景气",
    "策略实验室",
    "组合",
    "回测",
    "数据状态",
)

_STATE_LABELS = {
    "running": "进行中",
    "completed": "已完成",
    "not_trainable": "需要处理",
    "partial": "需要处理",
    "failed": "失败",
}
_SENSITIVE_VALUE_RE = re.compile(
    r"(?<![A-Za-z0-9])(token|password|secret|dsn|database_dsn|database_url)\b"
    r"(\s*(?:(?:=|:)\s*)|[\t ]+)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)",
    re.IGNORECASE,
)
_URL_CREDENTIAL_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)([^\s/@]*)@",
    re.IGNORECASE,
)


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
    return ("研究数据正在准备", "当前暂时无法加载研究数据。", "请稍后刷新页面。")


def pipeline_steps(runs: list[dict], quality: dict) -> list[dict]:
    """Project stored runs and data quality into the ordered admin pipeline."""
    latest = {
        run_type: next((run for run in runs if run.get("run_type") == run_type), None)
        for run_type in ("sync", "factors", "model")
    }
    data_ready = bool(quality.get("is_complete"))
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
    missing_prices = quality.get("missing_latest_price_codes") or []
    missing_financials = quality.get("missing_financial_codes") or []
    if complete:
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
    text = str(value or "")
    text = _SENSITIVE_VALUE_RE.sub(r"\1\2[已隐藏]", text)
    return _URL_CREDENTIAL_RE.sub(r"\1[已隐藏]@", text)
