import { useEffect, useState, type FormEvent } from "react";
import { getCandidates, type CandidatePage, type CandidateQuery } from "../../api/research";
import { AddToPortfolio } from "../../components/AddToPortfolio";

export function CandidatePoolPage() {
  const [page, setPage] = useState<CandidatePage | null>(null);
  const [filters, setFilters] = useState<CandidateQuery>({});
  const [applied, setApplied] = useState<CandidateQuery>({});
  const [pageNumber, setPageNumber] = useState(1);
  const [error, setError] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [adding, setAdding] = useState<string[]>([]);
  useEffect(() => {
    let current = true;
    setPage(null); setError(false);
    getCandidates({ ...applied, page: pageNumber, page_size: 10 }).then((data) => { if (current) setPage(data); }).catch(() => { if (current) setError(true); });
    return () => { current = false; };
  }, [applied, pageNumber]);

  function apply(event: FormEvent) { event.preventDefault(); setPageNumber(1); setApplied(filters); }
  const update = (key: string, value: string) => setFilters({ ...filters, [key]: value || undefined });
  return <main>
    <div className="section-heading"><div><h1>选股</h1><p className="muted">筛选研究候选，勾选后批量添加到指定组合。</p></div><a href="/industries">行业观察 →</a></div>
    <form onSubmit={apply}>
      <label>搜索名称或代码 <input value={filters.query ?? ""} onChange={(event) => update("query", event.target.value)} /></label>
      <details className="filter-details"><summary>更多筛选条件</summary><div className="filter-fields">
      <label>研究倾向 <select value={filters.tendency ?? ""} onChange={(event) => update("tendency", event.target.value)}><option value="">全部</option>{["优先研究", "持续观察", "暂缓研究", "证据不足"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>数据可信度 <select value={filters.confidence ?? ""} onChange={(event) => update("confidence", event.target.value)}><option value="">全部</option>{["可信", "部分可用", "不足"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>风险水平 <select value={filters.risk ?? ""} onChange={(event) => update("risk", event.target.value)}><option value="">全部</option>{["较低", "中等", "较高", "未知"].map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>行业 <input value={filters.industry ?? ""} onChange={(event) => update("industry", event.target.value)} /></label>

      <label>最低原始分数 <input type="number" min="0" max="100" step="1" value={filters.min_score ?? ""} onChange={(event) => update("min_score", event.target.value)} /></label>
      <label>最低数据覆盖率 <input type="number" min="0" max="1" step="0.05" value={filters.min_coverage ?? ""} onChange={(event) => update("min_coverage", event.target.value)} /></label>
      </div></details><button>应用筛选</button><button type="button" className="secondary" onClick={() => { setFilters({}); setApplied({}); setPageNumber(1); }}>重置</button>
    </form>
    {selected.length > 0 && <div className="selection-bar">已选 {selected.length} 只 <button onClick={() => setAdding(selected)}>添加到组合</button><button className="secondary" onClick={() => setSelected([])}>清空选择</button></div>}
    {error ? <p role="alert">服务暂时不可用，请稍后重试。</p> : page === null ? <p>正在加载候选池…</p> :
      page.status !== "COMPLETED" ? <p>{page.reason ?? "暂时没有可研究的候选。"}</p> : <>
        <p>筛选结果：{page.total} 只 · 第 {page.current_page} / {page.page_count} 页</p>
        <ul>{page.rows.map((candidate) => <li key={candidate.code}>
          <label className="check-label"><input type="checkbox" aria-label={`选择 ${candidate.name}`} checked={selected.includes(candidate.code)} onChange={(event) => setSelected(event.target.checked ? [...selected, candidate.code] : selected.filter((code) => code !== candidate.code))} />选择</label><h2>{candidate.name}（{candidate.code}）</h2>
          <p>行业：{candidate.industry} · 研究倾向：{candidate.tendency} · 数据可信度：{candidate.confidence} · 风险水平：{candidate.risk} · 数据日期：{candidate.as_of_date ?? "--"}</p>
          <p>核心优势：{candidate.core_advantage}</p><p>主要风险：{candidate.primary_risk}</p>
          <a href={`/stocks/${encodeURIComponent(candidate.code)}`}>查看详情</a>
          <button onClick={() => setAdding([candidate.code])}>添加到组合</button>
        </li>)}</ul>
        <button disabled={page.current_page <= 1} onClick={() => setPageNumber(page.current_page - 1)}>上一页</button>
        <button disabled={page.current_page >= page.page_count} onClick={() => setPageNumber(page.current_page + 1)}>下一页</button>
      </>}
    {adding.length > 0 && <AddToPortfolio codes={adding} onClose={() => setAdding([])} />}
    <p>研究内容仅供研究参考，不构成投资建议、交易指令或收益保证。</p>
  </main>;
}
