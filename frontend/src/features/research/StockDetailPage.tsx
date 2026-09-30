import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { getStockDetail, type StockDetail } from "../../api/detail";
import { StockSearch } from "../../components/StockSearch";
import { AddToPortfolio } from "../../components/AddToPortfolio";
import { TimeSeriesChart } from "../../components/TimeSeriesChart";

const score = (value: number | null) => value == null ? "--" : value.toFixed(1);
const coverage = (value: number | null) => value == null ? "--" : `${(value * 100).toFixed(0)}%`;

export function StockDetailPage() {
  const { code } = useParams();
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  const [detail, setDetail] = useState<StockDetail | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let current = true;
    setDetail(null); setError(false); setAdding(false);
    if (code) getStockDetail(code).then((data) => { if (current) setDetail(data); }).catch(() => { if (current) setError(true); });
    return () => { current = false; };
  }, [code]);
  return <main><a href="/candidates">← 返回选股</a><h1>个股研究</h1>
    <StockSearch onSelect={(stock) => navigate(`/stocks/${encodeURIComponent(stock.code)}`)} />
    {!code && <p>输入名称或代码，选择搜索结果查看研究证据。</p>}
    {error && <p role="alert">证券数据暂时无法加载，请稍后重试。</p>}
    {code && !error && detail === null && <p>正在加载个股详情…</p>}
    {detail && <>
      <div className="section-heading"><h2>{detail.name}（{detail.code}）</h2><button onClick={() => setAdding(true)}>添加到组合</button></div>
      {adding && <AddToPortfolio codes={[detail.code]} onClose={() => setAdding(false)} />}
      <p>研究日期：{detail.as_of_date} · 模型日期：{detail.model_date} · 因子日期：{detail.factor_date} · PIT 证据：{detail.evidence_status}</p>
      <dl><div><dt>行业</dt><dd>{detail.industry}</dd></div><div><dt>研究结论</dt><dd>{detail.tendency}</dd></div>
        <div><dt>因子综合分</dt><dd>{score(detail.factor_score)}</dd></div><div><dt>模型分数</dt><dd>{score(detail.model_score)}</dd></div>
        <div><dt>覆盖率</dt><dd>{coverage(detail.coverage)}</dd></div><div><dt>数据可信度</dt><dd>{detail.confidence}</dd></div><div><dt>风险水平</dt><dd>{detail.risk}</dd></div></dl>
      {detail.status !== "COMPLETED" && <p role="status">{detail.reason}</p>}
      <h3>研究摘要</h3><p>核心优势：{detail.core_advantage}</p><p>主要风险：{detail.primary_risk}</p><p>{detail.sufficiency}</p>
      <h3>证据组</h3>{detail.evidence_groups.map((group) => <section key={group.类别}><h4>{group.类别} · {group.方向}</h4><p>{group.说明}</p><small>{group.局限}</small></section>)}
      <details><summary>因子证据</summary><p>证据关联：{detail.evidence_status} · {detail.evidence_reason}</p><table><thead><tr><th>研究概念</th><th>分数</th><th>覆盖率</th><th>可用性</th></tr></thead><tbody>{detail.factor_evidence.map((item, index) => <tr key={index}><td>{String(item.研究概念 ?? "--")}</td><td>{String(item.分数 ?? "--")}</td><td>{typeof item.覆盖率 === "number" ? `${(item.覆盖率 * 100).toFixed(0)}%` : "--"}</td><td>{String(item.可用性 ?? "--")}</td></tr>)}</tbody></table>
        <p>模型版本：{detail.model_version} · 因子版本：{detail.factor_version} · 因子模型版本：{detail.factor_model_version} · 因子数据版本：{detail.factor_data_version}</p></details>
      <h3>相对沪深300走势</h3><p>{detail.relative_history.reason}</p>
      {detail.relative_history.rows.length > 0 && <TimeSeriesChart title="个股与沪深300归一化走势" series={[{ label: detail.name, color: "#2563eb", points: detail.relative_history.rows.map((row) => ({ date: row.date, value: row.stock })) }, { label: "沪深300", color: "#d97706", points: detail.relative_history.rows.map((row) => ({ date: row.date, value: row.benchmark })) }]} />}
      {detail.relative_history.rows.length > 0 && <details><summary>共同交易日（{detail.relative_history.rows.length}）</summary><table><thead><tr><th>日期</th><th>个股归一化</th><th>沪深300归一化</th></tr></thead><tbody>{detail.relative_history.rows.map((row) => <tr key={row.date}><td>{row.date}</td><td>{row.stock.toFixed(3)}</td><td>{row.benchmark.toFixed(3)}</td></tr>)}</tbody></table></details>}
      <h3>估值走势</h3>{Object.entries(detail.valuation_trends).map(([label, rows]) => <details key={label}><summary>{label}（{rows.length}）</summary><table><thead><tr><th>日期</th><th>数值</th></tr></thead><tbody>{rows.map((row) => <tr key={row.数据日期}><td>{row.数据日期}</td><td>{row.数值.toFixed(2)}</td></tr>)}</tbody></table></details>)}
      <h3>财务披露</h3>{detail.financial_disclosures.length === 0 ? <p>暂无可用披露记录。</p> : <details><summary>查看全部披露（{detail.financial_disclosures.length}）</summary><table><thead><tr>{Object.keys(detail.financial_disclosures[0]).map((key) => <th key={key}>{key}</th>)}</tr></thead><tbody>{detail.financial_disclosures.map((row, index) => <tr key={index}>{Object.keys(detail.financial_disclosures[0]).map((key) => <td key={key}>{String(row[key] ?? "--")}</td>)}</tr>)}</tbody></table></details>}
      <p>研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。</p>
    </>}
  </main>;
}
