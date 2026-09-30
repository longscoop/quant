import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getIndustryPage, type IndustryPage as IndustryData } from "../../api/detail";

const percent = (value: number | null) => value == null ? "--" : `${(value * 100).toFixed(2)}%`;

export function IndustryPage() {
  const [data, setData] = useState<IndustryData | null>(null);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState(false);
  useEffect(() => { getIndustryPage().then((page) => { setData(page); setSelected(page.rows[0]?.name ?? ""); }).catch(() => setError(true)); }, []);
  const industry = data?.rows.find((row) => row.name === selected);
  return <main><h1>行业观察</h1><p>从行业覆盖、研究关注度和相对表现了解市场线索。</p>
    {error ? <p role="alert">服务暂时不可用，请稍后重试。</p> : data === null ? <p>正在加载行业观察…</p> :
      data.status !== "COMPLETED" ? <p>{data.reason}</p> : <>
        <p>行情日期：{data.as_of_date ?? "--"}</p>
        <table><thead><tr><th>行业</th><th>覆盖证券</th><th>优先研究标的</th><th>研究关注度</th><th>20 日相对表现</th><th>60 日相对表现</th></tr></thead><tbody>{data.rows.map((row) =>
          <tr key={row.name}><td>{row.name}</td><td>{row.security_count}</td><td>{row.candidate_count}</td><td>{row.label}</td><td>{percent(row.relative_20d)}</td><td>{percent(row.relative_60d)}</td></tr>)}</tbody></table>
        <label>查看行业内股票 <select value={selected} onChange={(event) => setSelected(event.target.value)}>{data.rows.map((row) => <option key={row.name}>{row.name}</option>)}</select></label>
        <ul>{industry?.members.map((item) => <li key={item.code}><Link to={`/stocks/${encodeURIComponent(item.code)}`}>{item.name}（{item.code}）</Link></li>)}</ul>
      </>}
  </main>;
}
