import { useEffect, useState } from "react";
import { getJson } from "../../api/client";

type DataStatus = {
  status: string;
  latest_trade_date: string | null;
  counts: { securities: number; prices: number; financials: number; valuation_count: number };
  stages: { 阶段: string; 状态: string }[];
};

export function DataStatusPage() {
  const [data, setData] = useState<DataStatus | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { getJson<DataStatus>("/api/v1/data-status").then(setData).catch(() => setError(true)); }, []);
  return <main><h1>数据状态</h1>
    {error ? <p role="alert">服务暂时不可用，请稍后重试。</p> : data === null ? <p>正在加载数据状态…</p> : <>
      <dl><div><dt>最新行情</dt><dd>{data.latest_trade_date ?? "--"}</dd></div>
        <div><dt>证券</dt><dd>{data.counts.securities}</dd></div>
        <div><dt>行情记录</dt><dd>{data.counts.prices}</dd></div>
        <div><dt>财务记录</dt><dd>{data.counts.financials}</dd></div>
        <div><dt>估值记录</dt><dd>{data.counts.valuation_count}</dd></div></dl>
      <h2>研究状态</h2><table><thead><tr><th>阶段</th><th>状态</th></tr></thead><tbody>{data.stages.map((row) => <tr key={row.阶段}><td>{row.阶段}</td><td>{row.状态}</td></tr>)}</tbody></table>
    </>}
    <p>研究内容仅供参考，不构成投资建议。</p>
  </main>;
}
