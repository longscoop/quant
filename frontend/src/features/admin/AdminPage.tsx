import { useEffect, useState, type FormEvent } from "react";
import { getAdminOverview, runFactors, runModel, runSync, type AdminOverview, type AdminRun } from "../../api/admin";

export function AdminPage() {
  const [data, setData] = useState<AdminOverview | null>(null);
  const [error, setError] = useState("");
  const [last, setLast] = useState<AdminRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [syncStart, setSyncStart] = useState("2020-01-01");
  const [syncEnd, setSyncEnd] = useState(new Date().toLocaleDateString("sv-SE"));
  const [token, setToken] = useState("");
  const [cutoff, setCutoff] = useState("");
  const [factorRunId, setFactorRunId] = useState("");
  const [dates, setDates] = useState<Record<string, string>>({ train_start: "", train_end: "", valid_start: "", valid_end: "", test_start: "", test_end: "" });
  const [filterType, setFilterType] = useState("");
  const [filterStatus, setFilterStatus] = useState("");

  async function refresh() {
    const overview = await getAdminOverview();
    setData(overview);
    if (!cutoff && overview.quality.latest_trade_date) setCutoff(overview.quality.latest_trade_date);
    if (!factorRunId && overview.factor_runs[0]) setFactorRunId(overview.factor_runs[0].run_id);
  }
  useEffect(() => { refresh().catch(() => { setData(null); setError("管理员数据暂时无法加载。"); }); }, []);
  async function execute(event: FormEvent, action: () => Promise<AdminRun>) {
    event.preventDefault(); setError(""); setLast(null); setBusy(true);
    try { const run = await action(); setLast(run); await refresh(); }
    catch (reason) { setData(null); setError(reason instanceof Error ? reason.message : "任务未能完成。"); }
    finally { setBusy(false); setToken(""); }
  }
  const syncStep = data?.steps.find((row) => row.id === "sync");
  const factorStep = data?.steps.find((row) => row.id === "factors");
  const modelStep = data?.steps.find((row) => row.id === "model");
  const runTypes = [...new Set(data?.runs.map((run) => run.run_type).filter((value): value is string => Boolean(value)) ?? [])];
  const statuses = [...new Set(data?.runs.map((run) => run.status).filter((value): value is string => Boolean(value)) ?? [])];
  const visibleRuns = data?.runs.filter((run) => (!filterType || run.run_type === filterType) && (!filterStatus || run.status === filterStatus)) ?? [];
  return <main><h1>管理员工作台</h1><p>准备研究数据、执行研究流水线并诊断失败任务。</p>
    {error && <p role="alert">{error}</p>}
    {busy && <p role="status">任务正在执行，请等待真实结果。</p>}
    {last && <p role="status">最近任务：{last.run_type} · {last.status === "completed" ? "已完成" : last.status === "not_trainable" ? "不可训练" : last.status === "partial" ? "部分完成" : last.status === "failed" ? "失败" : last.status}</p>}
    {data === null ? !error && <p>正在加载管理员工作台…</p> : <>
      <h2>数据概览</h2><dl><div><dt>最新交易日</dt><dd>{data.quality.latest_trade_date ?? "--"}</dd></div>
        <div><dt>证券数量</dt><dd>{data.quality.security_count ?? "--"}</dd></div>
        <div><dt>缺最新行情</dt><dd>{data.quality.missing_latest_price_count}</dd></div>
        <div><dt>缺财务</dt><dd>{data.quality.missing_financial_count}</dd></div>
        <div><dt>估值记录</dt><dd>{data.quality.valuation_count ?? "--"}</dd></div></dl>
      <p>完整性：{data.quality.is_complete ? "通过" : "尚未通过"} · 新鲜度：{data.quality.is_stale ? "已过期" : "未过期"}</p>
      <h2>研究流水线</h2>{data.steps.map((step) => <p key={step.id}>{step.title} · {step.state}：{step.summary} 下一步：{step.next_step}</p>)}
      <form onSubmit={(event) => execute(event, () => runSync(syncStart, syncEnd, token))}><h3>数据同步</h3>
        <label>Tushare Token（留空使用服务器配置） <input type="password" value={token} onChange={(event) => setToken(event.target.value)} autoComplete="off" /></label>
        <label>开始日期 <input type="date" value={syncStart} onChange={(event) => setSyncStart(event.target.value)} /></label>
        <label>结束日期 <input type="date" value={syncEnd} onChange={(event) => setSyncEnd(event.target.value)} /></label>
        <button disabled={busy || !syncStep?.can_run || syncStart > syncEnd}>开始同步</button></form>
      <form onSubmit={(event) => execute(event, () => runFactors(cutoff))}><h3>构建研究因子</h3>
        <label>因子截止日期 <input type="date" value={cutoff} onChange={(event) => setCutoff(event.target.value)} /></label>
        <button disabled={busy || !factorStep?.can_run || !cutoff}>构建研究因子</button></form>
      <form onSubmit={(event) => execute(event, () => runModel({ factor_run_id: factorRunId, ...dates }))}><h3>生成研究模型</h3>
        <label>因子运行 <select value={factorRunId} onChange={(event) => setFactorRunId(event.target.value)}>{data.factor_runs.map((run) => <option key={run.run_id} value={run.run_id}>截止 {run.date_end ?? "未知"} · {run.run_id}</option>)}</select></label>
        {([ ["train_start", "训练开始"], ["train_end", "训练结束"], ["valid_start", "验证开始"], ["valid_end", "验证结束"], ["test_start", "测试开始"], ["test_end", "测试结束"] ] as const).map(([key, label]) =>
          <label key={key}>{label} <input type="date" required value={dates[key]} onChange={(event) => setDates({ ...dates, [key]: event.target.value })} /></label>)}
        <button disabled={busy || !modelStep?.can_run || !factorRunId}>生成研究模型</button></form>
      <h2>运行审计</h2><label>任务类型 <select value={filterType} onChange={(event) => setFilterType(event.target.value)}><option value="">全部</option>{runTypes.map((value) => <option key={value}>{value}</option>)}</select></label>
      <label>运行状态 <select value={filterStatus} onChange={(event) => setFilterStatus(event.target.value)}><option value="">全部</option>{statuses.map((value) => <option key={value}>{value}</option>)}</select></label>
      <table><thead><tr><th>任务类型</th><th>状态</th><th>开始时间</th><th>结束时间</th></tr></thead><tbody>{visibleRuns.map((run, index) => <tr key={`${run.run_id}-${index}`}><td>{run.run_type ?? "--"}</td><td>{run.status ?? "--"}</td><td>{run.created_at ?? "--"}</td><td>{run.completed_at ?? "--"}</td></tr>)}</tbody></table>
      {visibleRuns.filter((run) => run.status === "failed").map((run) => <details key={run.run_id}><summary>失败任务技术详情 · {run.run_type} · {run.created_at}</summary>
        <p>运行 ID：{run.run_id}</p><pre>{run.error ?? "未记录技术原因"}</pre>
        {run.retry_parameters && <pre>可安全复用的参数：{JSON.stringify(run.retry_parameters, null, 2)}</pre>}
      </details>)}
    </>}
  </main>;
}
