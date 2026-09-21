import { useEffect, useState } from "react";

import { ApiRequestError } from "../../api/client";
import { getResearchHome, type ResearchHome } from "../../api/research";

function valueOrUnavailable(value: number | null): string {
  return value === null ? "--" : value.toFixed(1);
}

export function ResearchHomePage() {
  const [page, setPage] = useState<ResearchHome | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getResearchHome().then(setPage).catch((reason: unknown) => {
      setError(reason instanceof ApiRequestError);
    });
  }, []);

  if (error) return <p role="alert">服务暂时不可用，请稍后重试。</p>;
  if (page === null) return <p>正在加载研究首页…</p>;

  return (
    <main>
      <h1>研究首页</h1>
      <section aria-label="数据状态">
        <h2>数据状态</h2>
        <dl>
          <div><dt>最新数据日期</dt><dd>{page.freshness.latest_trade_date}</dd></div>
          <div><dt>研究快照日期</dt><dd>{page.freshness.research_snapshot_date}</dd></div>
          <div><dt>覆盖证券</dt><dd>{page.freshness.security_count}</dd></div>
          <div><dt>数据状态</dt><dd>{page.freshness.state}</dd></div>
        </dl>
        <p>{page.freshness.reason}</p>
      </section>
      <section aria-label="优先研究的候选">
        <h2>优先研究的候选</h2>
        {page.status !== "COMPLETED" ? <p>{page.reason ?? "暂时没有可优先研究的候选。"}</p> : (
          <ul>
            {page.candidates.map((candidate) => (
              <li key={candidate.code}>
                <h3>{candidate.name}（{candidate.code}）</h3>
                <p>行业：{candidate.industry} · 数据可信度：{candidate.confidence} · 数据日期：{candidate.as_of_date ?? "不可用"}</p>
                <p>核心优势：{candidate.core_advantage}</p>
                <p>主要风险：{candidate.primary_risk}</p>
                <p>研究分数：{valueOrUnavailable(candidate.score)} · 数据覆盖率：{candidate.coverage === null ? "--" : `${(candidate.coverage * 100).toFixed(0)}%`}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
