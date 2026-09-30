import { useState, type FormEvent } from "react";
import { simulateValidationTarget, type TargetSimulation, type ValidationResult } from "../../api/validation";
import { formatPrice, formatWeight } from "./AdjustmentLedger";

const actionLabel: Record<string, string> = { OPEN: "计划建仓", INCREASE: "计划加仓", REDUCE: "计划减仓", CLOSE: "计划清仓", HOLD: "持有不变" };

export function TargetSimulationPanel({ result, templates, latestDate }: { result: ValidationResult; templates: { id: string; name: string }[]; latestDate?: string | null }) {
  const holdingDate = result.equity_curve?.at(-1)?.date;
  const [request, setRequest] = useState({ as_of_date: latestDate ?? holdingDate ?? "", template_id: result.template_id ?? "quality_growth", top_n: result.top_n ?? 30 });
  const [saved, setSaved] = useState(result.target_simulations ?? []);
  const [preview, setPreview] = useState<TargetSimulation | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const change = (next: typeof request) => { setRequest(next); setPreview(null); setError(null); };
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!result.experiment_id) return;
    setRunning(true); setPreview(null); setError(null);
    try {
      const next = await simulateValidationTarget(result.experiment_id, request);
      setPreview(next); setSaved((items) => [next, ...items].slice(0, 20));
    } catch (error) { setError(error instanceof Error ? error.message : "目标模拟暂时不可用。"); }
    finally { setRunning(false); }
  }
  return <div className="target-simulation">
    <h3>下期目标模拟</h3>
    <p className="muted">以「{result.experiment_name}」在 {holdingDate ?? "--"} 的期末持仓和现金为起点，估算下一次该如何调整。</p>
    <div className="ledger-note">信号日可以晚于实验结束日；期间假设原持仓继续持有。目标按信号日收盘价估算，并预留手续费；尚未执行交易，实际开盘价与可成交数量需到下一交易日确认。</div>
    <form onSubmit={submit}>
      <label>目标信号日<input type="date" required min={holdingDate} max={latestDate ?? undefined} value={request.as_of_date} disabled={running} onChange={(event) => change({ ...request, as_of_date: event.target.value })} /></label>
      <label>下期研究模板<select value={request.template_id} disabled={running} onChange={(event) => change({ ...request, template_id: event.target.value })}>{templates.map((item) => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label>下期目标股票数<input type="number" min="1" max="300" required value={request.top_n} disabled={running} onChange={(event) => change({ ...request, top_n: Number(event.target.value) })} /></label>
      <button disabled={running || !result.experiment_id || !request.as_of_date} type="submit">{running ? "正在生成目标…" : "模拟并保存下期目标"}</button>
    </form>
    {running && <p role="status">正在读取所选日期可见的因子数据，并核对原持仓与目标差额。</p>}
    {error && <p role="alert">{error}</p>}
    {saved.length > 0 && <label>已保存的目标模拟<select value={preview ? String(saved.indexOf(preview)) : ""} disabled={running} onChange={(event) => { const item = saved[Number(event.target.value)]; if (item) { setPreview(item); setError(null); setRequest({ as_of_date: item.as_of_date, template_id: item.template_id, top_n: item.top_n }); } }}><option value="" disabled>选择查看（最近 20 次）</option>{saved.map((item, index) => <option key={index} value={index}>{item.as_of_date} · {templates.find((template) => template.id === item.template_id)?.name ?? item.template_id} · {item.top_n} 只 · {item.saved_at ? new Date(item.saved_at).toLocaleString("zh-CN") : "已保存"}</option>)}</select></label>}
    {preview && <section aria-label="目标模拟结果">
      <h3>{preview.as_of_date} 下期目标 · 已保存</h3><p>{preview.execution_rule}</p>
      <dl><div><dt>信号日组合资产（元）</dt><dd>{formatPrice(preview.portfolio_value)}</dd></div><div><dt>现有现金（元）</dt><dd>{formatPrice(preview.cash_before)}</dd></div><div><dt>预计手续费（元）</dt><dd>{formatPrice(preview.estimated_cost)}</dd></div><div><dt>预计调整后现金（元）</dt><dd>{formatPrice(Math.abs(preview.estimated_cash_after) < .005 ? 0 : preview.estimated_cash_after)}</dd></div></dl>
      <p className="muted">模拟本金 {formatPrice(preview.initial_capital)} 元。以下为折算股数，允许小数股；不是已经成交的订单。</p>
      <div className="ledger-table-scroll"><table className="ledger-table"><thead><tr><th>股票</th><th>计划动作</th><th className="numeric">参考收盘价</th><th className="numeric">现有股数</th><th className="numeric">目标股数</th><th className="numeric">增减股数</th><th className="numeric">目标仓位</th><th className="numeric">预计金额（元）</th></tr></thead><tbody>{preview.rows.map((row) => <tr key={row.ts_code}><td><strong>{row.name ?? "名称缺失"}</strong><small>{row.ts_code}</small>{row.signal_day_note && <small>{row.signal_day_note}</small>}</td><td>{actionLabel[row.action] ?? "--"}</td><td className="numeric">{formatPrice(row.reference_price)}</td><td className="numeric">{formatPrice(row.holding_shares)}</td><td className="numeric">{formatPrice(row.target_shares)}</td><td className="numeric">{row.change_shares > 0 ? "+" : ""}{formatPrice(row.change_shares)}</td><td className="numeric">{formatWeight(row.target_weight)}</td><td className="numeric">{formatPrice(row.estimated_amount)}</td></tr>)}</tbody></table></div>
    </section>}
  </div>;
}
