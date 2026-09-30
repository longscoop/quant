import { useEffect, useState } from "react";
import { getDayHoldings, type DayHoldings, type ValidationResult } from "../../api/validation";
import { formatPrice, formatWeight } from "./AdjustmentLedger";
export function SelectedDay({ day, result }: { day: string; result: ValidationResult }) {
  const [data, setData] = useState<DayHoldings | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let current = true; setData(null); setError(false);
    if (result.experiment_id) getDayHoldings(result.experiment_id, day).then((value) => { if (current) setData(value); }).catch(() => { if (current) setError(true); });
    return () => { current = false; };
  }, [day, result.experiment_id]);
  const trades = (result.adjustments ?? []).filter((row) => row.execution_date === day && row.action !== "HOLD");
  return <section aria-label="所选日期持仓"><h3>{day} · 收盘持仓</h3>{error ? <p role="alert">该日持仓读取失败，请重新选择日期重试。</p> : data ? <><p>总资产 ¥{formatPrice(data.total_value)} · 现金 ¥{formatPrice(data.cash)}</p>{data.status !== "COMPLETED" && <p className="data-note">部分股票缺少该日行情，相关数据显示 --。停牌等估值说明可在下方查看。</p>}<p className="muted">模拟股数含分红再投资，可含小数；成本按所选日复权因子换算，不含费用。</p><table><thead><tr><th>股票</th><th>首次买入</th><th>持仓成本价</th><th>当日收盘价</th><th>持仓数量（股）</th><th>持仓涨跌幅</th><th>市值（元）</th></tr></thead><tbody>{data.rows.map((row) => <tr key={row.ts_code}><td><a href={`/stocks/${row.ts_code}`}>{row.name ?? row.ts_code}</a><small>{row.ts_code}</small></td><td>{row.first_buy_date ?? "--"}</td><td>{formatPrice(row.average_cost)}</td><td>{formatPrice(row.reference_price)}</td><td>{formatPrice(row.holding_shares)}</td><td>{formatWeight(row.holding_return)}</td><td>{formatPrice(row.holding_value)}</td></tr>)}</tbody></table>{data.rows.length === 0 && <p>该日尚无持仓。</p>}</> : <p>正在读取所选日持仓…</p>}
    <details><summary>当日成交（{trades.length} 笔）</summary>{trades.length === 0 ? <p>当日未发生调仓成交。</p> : <table><thead><tr><th>股票</th><th>动作</th><th>成交价</th><th>模拟股数</th></tr></thead><tbody>{trades.map((row, index) => <tr key={index}><td>{row.name ?? row.ts_code}</td><td>{{ OPEN: "建仓", INCREASE: "加仓", REDUCE: "减仓", CLOSE: "清仓" }[row.action ?? ""] ?? row.side}</td><td>{formatPrice(row.market_open)}</td><td>{formatPrice(row.traded_shares)}</td></tr>)}</tbody></table>}</details>
  </section>;
}
