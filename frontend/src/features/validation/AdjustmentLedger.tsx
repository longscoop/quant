import { Fragment, useState } from "react";
import type { Adjustment } from "../../api/validation";

const actions: Record<string, string> = { OPEN: "建仓", INCREASE: "加仓", REDUCE: "减仓", CLOSE: "清仓", HOLD: "持有不变" };
const finite = (value: number | null | undefined): value is number => typeof value === "number" && Number.isFinite(value);
export const formatPrice = (value: number | null | undefined) => finite(value) ? value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : "--";
export const formatWeight = (value: number | null | undefined) => finite(value) ? `${(Math.abs(value * 100) < .005 ? 0 : value * 100).toFixed(2)}%` : "--";

const actionLabel = (row: Adjustment) => actions[row.action ?? ""] ?? (row.side === "BUY" ? "买入 · 动作未记录" : row.side === "SELL" ? "卖出 · 动作未记录" : "未记录");
const PAGE_SIZE = 15;

function exportRows(rows: Adjustment[]) {
  const cells = (values: unknown[]) => values.map((value) => {
    let text = value == null ? "" : String(value);
    if (/^[=+@-]/.test(text)) text = `'${text}`;
    return `"${text.replaceAll('"', '""')}"`;
  }).join(",");
  const content = [cells(["信号日", "处理日期", "股票名称（当前）", "代码", "动作", "调整前仓位", "调整后仓位", "开盘价（元）", "首次买入日期", "最近买入日期", "最近买入价（元）", "持仓成本价（元）", "模拟持仓（股）", "持仓涨跌幅（不含费用）", "持仓市值（元）", "本次成交股数", "成交金额（元）", "手续费（元）", "延期"]),
    ...rows.map((row) => cells([row.date, row.execution_date, row.name, row.ts_code, actionLabel(row), formatWeight(row.weight_before), formatWeight(row.weight_after), formatPrice(row.market_open), row.first_buy_date, row.last_buy_date, formatPrice(row.last_buy_price), formatPrice(row.average_cost), formatPrice(row.holding_shares), formatWeight(row.holding_return), formatPrice(row.holding_value), formatPrice(row.traded_shares), formatPrice(row.trade_amount), formatPrice(row.fee_amount), row.deferred ? "是" : "否"]))].join("\r\n");
  const url = URL.createObjectURL(new Blob(["\uFEFF", content], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = "历史调仓明细.csv"; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function AdjustmentLedger({ rows, capital }: { rows: Adjustment[]; capital?: number }) {
  const periods = [...new Set(rows.map((row) => row.date).filter((day): day is string => !!day))].sort().reverse();
  const [period, setPeriod] = useState(periods[0] ?? "all");
  const [action, setAction] = useState("all");
  const [query, setQuery] = useState("");
  const [deferredOnly, setDeferredOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [expanded, setExpanded] = useState<number | null>(null);
  const periodRows = rows.filter((row) => period === "all" || row.date === period);
  const needle = query.trim().toLowerCase();
  const filtered = periodRows.filter((row) => (action === "all" || (action === "unknown" ? !row.action : row.action === action))
    && (!deferredOnly || row.deferred) && `${row.name ?? ""} ${row.ts_code ?? ""}`.toLowerCase().includes(needle));
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const resetPage = () => { setPage(1); setExpanded(null); };
  return <div className="adjustment-ledger">
    <div className="validation-section-heading"><div><h3>调仓明细</h3><p className="muted">持仓跨期延续，按实际差额调整。共 {periods.length} 个信号期 · {rows.length} 条记录</p></div>
      <button type="button" className="secondary" disabled={!filtered.length} onClick={() => exportRows(filtered)}>导出筛选结果</button></div>
    <div className="ledger-note">模拟本金：{formatPrice(capital)} 元。股数按该本金折算，允许小数股；价格单位为元。明细中的持仓和涨跌幅均截至该次调仓，不是今天的持仓。</div>
    <div className="ledger-toolbar">
      <label>调仓期<select value={period} onChange={(event) => { setPeriod(event.target.value); resetPage(); }}><option value="all">全部周期</option>{periods.map((day) => <option key={day} value={day}>{day}</option>)}</select></label>
      <label className="ledger-search">搜索股票<input type="search" value={query} placeholder="股票名称或代码" onChange={(event) => { setQuery(event.target.value); resetPage(); }} /></label>
      <label>调仓动作<select value={action} onChange={(event) => { setAction(event.target.value); resetPage(); }}><option value="all">全部动作</option>{Object.entries(actions).map(([key, label]) => <option key={key} value={key}>{label}</option>)}<option value="unknown">旧记录：动作未记录</option></select></label>
      <label className="ledger-checkbox"><input type="checkbox" checked={deferredOnly} onChange={(event) => { setDeferredOnly(event.target.checked); resetPage(); }} />仅看延期</label>
    </div>
    <div className="action-counts" aria-label="所选周期动作统计">{Object.entries(actions).map(([key, label]) => <span key={key} className={`action-badge action-${key.toLowerCase()}`}>{label} <strong>{periodRows.filter((row) => row.action === key).length}</strong></span>)}</div>
    {periodRows.some((row) => !row.action || !finite(row.weight_before)) && <p className="data-note">旧记录未保存完整动作或前后仓位，缺失项显示 --。重新运行历史验证后可查看完整明细。</p>}
    {filtered.some((row) => row.price_status === "mismatch" || row.price_status === "missing") && <p className="data-note">部分成交缺少可核验的当日开盘价，显示 --；不会用复权价或其他日期价格替代。</p>}
    <div className="ledger-table-scroll"><table className="ledger-table" aria-label="历史调整"><thead><tr>
      <th>股票</th><th>动作</th><th>仓位变化</th><th className="numeric">开盘价（元）</th><th className="numeric">模拟持仓（股）</th><th>处理日期</th><th>明细</th>
    </tr></thead><tbody>{visible.map((row, index) => {
      const rowId = (currentPage - 1) * PAGE_SIZE + index;
      return <Fragment key={rowId}><tr>
        <td><a className="stock-name" href={`/stocks/${encodeURIComponent(row.ts_code ?? "")}`}>{row.name || "名称缺失"}</a><small>{row.ts_code ?? "--"}</small></td>
        <td><span className={`action-badge action-${(row.action ?? "unknown").toLowerCase()}`}>{actionLabel(row)}</span></td>
        <td className="weight-change"><span>{formatWeight(row.weight_before)}</span><span aria-label="变为"> → </span><strong>{formatWeight(row.weight_after)}</strong><small>目标 {formatWeight(row.weight)}</small></td>
        <td className="numeric">{row.action === "HOLD" ? "--" : formatPrice(row.market_open)}</td>
        <td className="numeric">{formatPrice(row.holding_shares)}</td>
        <td>{row.execution_date ?? "--"}<small>信号 {row.date ?? "--"}{row.deferred && <span className="deferred-badge"> · 延期</span>}</small></td>
        <td><button className="text-button" type="button" aria-label={`${expanded === rowId ? "收起" : "展开"}${row.name || row.ts_code}持仓详情`} aria-expanded={expanded === rowId} onClick={() => setExpanded(expanded === rowId ? null : rowId)}>{expanded === rowId ? "收起" : "查看"}</button></td>
      </tr>{expanded === rowId && <tr className="calculation-row"><td colSpan={7}><div>
        <strong>{row.name || row.ts_code} · 截至 {row.execution_date ?? "--"} 的持仓</strong>
        <dl>
          <div><dt>首次买入日期</dt><dd>{row.first_buy_date ?? "--"}</dd></div>
          <div><dt>最近买入日期</dt><dd>{row.last_buy_date ?? "--"}</dd></div>
          <div><dt>最近买入价（元）</dt><dd>{formatPrice(row.last_buy_price)}</dd></div>
          <div><dt>持仓成本价（元）</dt><dd>{formatPrice(row.average_cost)}</dd></div>
          <div><dt>模拟持仓（股）</dt><dd>{formatPrice(row.holding_shares)}</dd></div>
          <div><dt>{row.action === "CLOSE" ? "清仓时涨跌幅" : "持仓涨跌幅"}</dt><dd>{formatWeight(row.holding_return)}</dd></div>
          <div><dt>持仓市值（元）</dt><dd>{formatPrice(row.holding_value)}</dd></div>
          <div><dt>本次成交（股）</dt><dd>{formatPrice(row.traded_shares)}</dd></div>
          <div><dt>本次成交金额（元）</dt><dd>{formatPrice(row.trade_amount)}</dd></div>
          <div><dt>本次手续费（元）</dt><dd>{formatPrice(row.fee_amount)}</dd></div>
        </dl><p>成本价按加权平均和除权口径计算，涨跌幅不含手续费。模拟股数含分红再投资的折算，不等同于券商整手成交。清仓后再次买入会重新计算持仓起始日期。</p>
      </div></td></tr>}</Fragment>;
    })}</tbody></table></div>
    {!filtered.length && <div className="ledger-empty"><strong>{rows.length ? "没有符合条件的调仓记录" : "暂无调仓记录"}</strong><p>可切换周期，或清除股票与动作筛选。</p><button type="button" className="secondary" onClick={() => { setPeriod("all"); setAction("all"); setQuery(""); setDeferredOnly(false); resetPage(); }}>清除筛选</button></div>}
    <div className="ledger-pagination"><span>筛选后 {filtered.length} 条 · 每页 {PAGE_SIZE} 条</span><div><button className="secondary" type="button" disabled={currentPage <= 1} onClick={() => { setPage(currentPage - 1); setExpanded(null); }}>上一页</button><span aria-live="polite">第 {currentPage} / {pages} 页</span><button className="secondary" type="button" disabled={currentPage >= pages} onClick={() => { setPage(currentPage + 1); setExpanded(null); }}>下一页</button></div></div>
  </div>;
}
