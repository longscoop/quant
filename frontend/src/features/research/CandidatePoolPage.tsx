import { useEffect, useState } from "react";

import { ApiRequestError } from "../../api/client";
import { getCandidates, type CandidatePage } from "../../api/research";

export function CandidatePoolPage() {
  const [page, setPage] = useState<CandidatePage | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    getCandidates().then(setPage).catch((reason: unknown) => {
      setError(reason instanceof ApiRequestError);
    });
  }, []);

  if (error) return <p role="alert">服务暂时不可用，请稍后重试。</p>;
  if (page === null) return <p>正在加载候选池…</p>;

  return (
    <main>
      <h1>候选池</h1>
      {page.status !== "COMPLETED" ? <p>{page.reason ?? "暂时没有可研究的候选。"}</p> : (
        <>
          <p>筛选结果：{page.total} 只 · 第 {page.current_page} / {page.page_count} 页</p>
          <ul>
            {page.rows.map((candidate) => (
              <li key={candidate.code}>
                <h2>{candidate.name}（{candidate.code}）</h2>
                <p>行业：{candidate.industry} · 研究倾向：{candidate.tendency} · 数据可信度：{candidate.confidence}</p>
                <p>核心优势：{candidate.core_advantage}</p>
                <p>主要风险：{candidate.primary_risk}</p>
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
