import { useEffect, useState } from "react";

import { ApiRequestError } from "../../api/client";
import { getResearchHome, type ResearchHome } from "../../api/research";
import { AddToPortfolio } from "../../components/AddToPortfolio";

function valueOrUnavailable(value: number | null): string {
  return value === null ? "--" : value.toFixed(1);
}

export function ResearchHomePage() {
  const [page, setPage] = useState<ResearchHome | null>(null);
  const [error, setError] = useState(false);
  const [adding, setAdding] = useState<string[]>([]);

  useEffect(() => {
    getResearchHome().then(setPage).catch((reason: unknown) => {
      setError(reason instanceof ApiRequestError);
    });
  }, []);

  if (error) return <p role="alert">服务暂时不可用，请稍后重试。</p>;
  if (page === null) return <p>正在加载研究首页…</p>;

  return (
    <main>
      <header className="page-heading"><p className="eyebrow">发现 · 研究 · 模拟</p><h1>工作台</h1><p>从每日选股推荐出发，研究个股、管理组合、验证策略。</p></header>
      <div className="freshness-strip">行情截止 {page.freshness.latest_trade_date ?? "--"} · 研究快照 {page.freshness.research_snapshot_date ?? "--"} · 覆盖 {page.freshness.security_count} 只 <a href="/data-status">查看数据状态 →</a></div>
      <section aria-label="优先研究的候选">
        <div className="section-heading"><div><h2>每日选股推荐</h2><p className="muted">研究日期：{page.freshness.research_snapshot_date ?? "--"} · {page.freshness.research_snapshot_date === new Date().toLocaleDateString("sv-SE") ? "今日研究推荐" : "最新可用推荐 · 今日待更新"}</p></div><a href="/candidates">查看全部候选 →</a></div>
        <p className="muted">{page.freshness.reason}</p>
        {page.status !== "COMPLETED" ? <p>{page.reason ?? "暂时没有可优先研究的候选。"}</p> : (
          <ul className="stock-grid">
            {page.candidates.map((candidate) => (
              <li key={candidate.code}>
                <h3>{candidate.name}（{candidate.code}）</h3>
                <p>行业：{candidate.industry} · 数据可信度：{candidate.confidence} · 数据日期：{candidate.as_of_date ?? "不可用"}</p>
                <p>核心优势：{candidate.core_advantage}</p>
                <p>主要风险：{candidate.primary_risk}</p>
                <p>研究分数：{valueOrUnavailable(candidate.score)} · 数据覆盖率：{candidate.coverage === null ? "--" : `${(candidate.coverage * 100).toFixed(0)}%`}</p>
                <a href={`/stocks/${encodeURIComponent(candidate.code)}`}>查看详情</a>
                <button onClick={() => setAdding([candidate.code])}>添加到组合</button>
              </li>
            ))}
          </ul>
        )}
      </section>
      {adding.length > 0 && <AddToPortfolio codes={adding} onClose={() => setAdding([])} />}
    </main>
  );
}
