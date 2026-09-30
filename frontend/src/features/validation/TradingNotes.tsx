import type { TradingNote } from "../../api/validation";

const reason: Record<string, string> = { suspended: "停牌", limit_up: "涨停限制", limit_down: "跌停限制", untradable: "当日无法交易" };

export function TradingNotes({ notes }: { notes: TradingNote[] }) {
  if (!notes.length) return null;
  const stocks = new Set(notes.map((item) => item.ts_code));
  return <details className="trading-notes"><summary>交易情况说明 · {stocks.size} 只股票涉及停牌或延期</summary>
    <p>这些是历史交易限制，不是程序报错。持仓股票停牌时，组合暂按最后可见收盘价估值；需要买卖时则等待恢复可交易，再按实际成交日价格记账。</p>
    {notes.map((item, index) => <div className="trading-note" key={index}>
      <strong>{item.name ?? item.ts_code} <small>{item.name ? item.ts_code : ""}</small></strong>
      {item.kind === "suspension" ? <p>{item.start_date} 至 {item.end_date}，共 {item.session_count} 个估值日因停牌没有行情，沿用停牌前价格计算持仓市值。这期间没有据此生成成交。</p> : <p>{item.start_date} 至 {item.end_date}，因{reason[item.reason ?? ""] ?? "交易条件限制"}，共 {item.session_count} 次调整尝试未能成交。{item.resolution_date ? `${item.resolution_date} 开始出现该期成交记录。` : "本实验内未记录该期后续成交。"}</p>}
    </div>)}
  </details>;
}
