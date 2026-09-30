import { useEffect, useRef, useState, type FormEvent } from "react";
import { checkValidationData, getValidationExperiment, getValidationPage, runValidation, type ValidationPage, type ValidationRequest, type ValidationResponse } from "../../api/validation";
import { AdjustmentLedger, formatPrice } from "./AdjustmentLedger";
import { TargetSimulationPanel } from "./TargetSimulationPanel";
import { TradingNotes } from "./TradingNotes";
import { StrategyDescription } from "./StrategyDescription";
import { SelectedDay } from "./SelectedDay";
import { TimeSeriesChart } from "../../components/TimeSeriesChart";

const percent = (value: number | null | undefined) => value == null ? "--" : `${(value * 100).toFixed(2)}%`;
const number = (value: number | null | undefined) => value == null ? "--" : value.toFixed(2);
const today = () => new Date().toLocaleDateString("sv-SE");
const statusLabel = (status: string) => ({ COMPLETED: "已完成", PARTIAL: "部分完成", INSUFFICIENT_DATA: "数据不足", NOT_TRAINABLE: "不可训练", FAILED: "失败" }[status] ?? status);

function Result({ response, templates, latestDate }: { response: ValidationResponse; templates: ValidationPage["templates"]; latestDate?: string | null }) {
  const [panel, setPanel] = useState("overview");
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const result = response.result;
  if (!result) return <p>{response.reason}</p>;
  const coverage = result.coverage;
  const adjustments = result.adjustments ?? [];
  let peak = 1;
  const drawdown = (result.equity_curve ?? []).map((point) => { peak = Math.max(peak, point.value); return { date: point.date, value: point.value / peak - 1 }; });
  return <section className="validation-result" aria-label="验证结果">
    <div className="validation-section-heading"><div><p className="eyebrow">历史研究结果</p><h2>{result.experiment_name}</h2></div><span className={`result-status status-${response.status.toLowerCase()}`}>{statusLabel(response.status)}</span></div>
    <details><summary>本实验的策略与规则</summary><StrategyDescription template={templates.find((item) => item.id === result.template_id)} /></details>
    <p>模拟本金：{formatPrice(result.initial_capital)} 元{result.capital_is_display_assumption ? "（旧实验未设本金，按 100 万元折算展示）" : ""} · 状态：{statusLabel(response.status)} · 有效 {coverage.valid_periods ?? "--"} 期 · 跳过 {coverage.skipped_periods ?? "--"} 期</p>
    {response.status !== "COMPLETED" ? <>
      <p role="status">{response.reason} 未达到完整历史验证条件，不展示绩效指标。</p>
      {result.skipped_periods.length > 0 && <details><summary>跳过周期明细</summary><table><thead><tr><th>信号日</th><th>期末调仓日</th><th>原因</th></tr></thead><tbody>{result.skipped_periods.map((row, index) =>
        <tr key={index}><td>{row.date ?? "--"}</td><td>{row.exit_date ?? "--"}</td><td>{row.reason ?? "数据不足"}</td></tr>)}</tbody></table></details>}
    </> : <>
      <div className="validation-tabs" role="tablist" aria-label="结果视图">{[["overview", "收益概览"], ["adjustments", "调仓明细"], ["target", "下期目标模拟"]].map(([key, label]) => <button key={key} id={`tab-${key}`} role="tab" aria-controls={`panel-${key}`} aria-selected={panel === key} type="button" onClick={() => setPanel(key)}>{label}</button>)}</div>
      <div id="panel-overview" role="tabpanel" aria-labelledby="tab-overview" hidden={panel !== "overview"}>
      <h3>核心指标</h3>
      <dl>
        <div><dt>策略收益</dt><dd>{percent(result.metrics?.total_return)}</dd></div>
        <div><dt>沪深300收益</dt><dd>{percent(result.metrics?.benchmark_return)}</dd></div>
        <div><dt>超额收益</dt><dd>{percent(result.metrics?.excess_return)}</dd></div>
        <div><dt>最大回撤</dt><dd>{percent(result.metrics?.max_drawdown)}</dd></div>
        <div><dt>年化收益</dt><dd>{(coverage.valid_periods ?? 0) < 2 ? "--" : percent(result.metrics?.annualized_return)}</dd></div>
        <div><dt>年化波动率</dt><dd>{percent(result.metrics?.annualized_volatility)}</dd></div>
        <div><dt>夏普比率</dt><dd>{number(result.metrics?.sharpe)}</dd></div>
      </dl>
      <p>设定区间：{result.start_date ?? "--"} 至 {result.end_date ?? "--"} · 持仓数量：{result.top_n ?? "--"} · 每万元交易成本：{result.cost_bps == null ? "--" : number(result.cost_bps)} 元</p>
      <p className="muted">实际收益曲线：{result.equity_curve?.[0]?.date ?? "--"} 至 {result.equity_curve?.at(-1)?.date ?? "--"} · 按完整月度区间计算</p>
      <TimeSeriesChart title="组合与沪深300累计收益" formatValue={percent} onSelectDate={setSelectedDay} series={[{ label: "研究组合", color: "#2563eb", points: (result.equity_curve ?? []).map((p) => ({ ...p, value: p.value - 1 })) }, { label: "沪深300", color: "#d97706", points: (result.benchmark_curve ?? []).map((p) => ({ ...p, value: p.value - 1 })) }]} detail={(day) => {
        const nav = result.equity_curve?.find((row) => row.date === day)?.value;
        const benchmark = result.benchmark_curve?.find((row) => row.date === day)?.value;
        return <><span>策略收益 {nav == null ? "--" : percent(nav - 1)}</span><span>沪深300 {benchmark == null ? "--" : percent(benchmark - 1)}</span><span>超额 {nav == null || benchmark == null ? "--" : `${((nav - benchmark) * 100).toFixed(2)} 个百分点`}</span><span>当日回撤 {percent(drawdown.find((row) => row.date === day)?.value)}</span><span>净值 {nav?.toFixed(4) ?? "--"}</span></>;
      }} />
      {selectedDay && result.experiment_id && <SelectedDay key={selectedDay} day={selectedDay} result={result} />}
      {!selectedDay && <p className="muted">点击曲线或调整日期滑块，查看当日持仓和成交。</p>}
      <TimeSeriesChart formatValue={percent} title="研究组合回撤" series={[{ label: "回撤", color: "#b44437", points: drawdown }]} />
      <details><summary>年度表现</summary><table><thead><tr><th>年度</th><th>策略</th><th>沪深300</th><th>超额</th></tr></thead><tbody>{(result.annual_returns ?? []).map((row) =>
        <tr key={row.year}><td>{row.year}</td><td>{percent(row.strategy)}</td><td>{percent(row.benchmark)}</td><td>{row.strategy == null || row.benchmark == null ? "--" : percent(row.strategy - row.benchmark)}</td></tr>)}</tbody></table></details>
      <TradingNotes notes={result.trading_notes ?? []} />
      </div>
      <div id="panel-adjustments" role="tabpanel" aria-labelledby="tab-adjustments" hidden={panel !== "adjustments"}><AdjustmentLedger rows={adjustments} capital={result.initial_capital} /></div>
      <div id="panel-target" role="tabpanel" aria-labelledby="tab-target" hidden={panel !== "target"}><TargetSimulationPanel result={result} templates={templates} latestDate={latestDate} /></div>
    </>}
  </section>;
}

export function HistoricalValidationPage() {
  const [page, setPage] = useState<ValidationPage | null>(null);
  const [response, setResponse] = useState<ValidationResponse | null>(null);
  const [error, setError] = useState("");
  const [running, setRunning] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [loadingExperiment, setLoadingExperiment] = useState(false);
  const loadSequence = useRef(0);
  const [view, setView] = useState("list");
  const [step, setStep] = useState(1);
  const [query, setQuery] = useState("");
  const [filterStatus, setFilterStatus] = useState("");
  const [sort, setSort] = useState("created_at");
  const [listPage, setListPage] = useState(1);
  const [checking, setChecking] = useState(false);
  const [check, setCheck] = useState<{ complete_periods: number; cached_snapshots: number; status: string } | null>(null);
  const [request, setRequest] = useState<ValidationRequest>({ template_id: "quality_growth", experiment_name: "质量成长 · 历史回测", start_date: "2023-01-01", end_date: today(), top_n: 30, cost_bps: 10, initial_capital: 1000000 });
  const reasonText = (reason: unknown) => reason instanceof Error ? reason.message : "读取失败，请重试。";
  useEffect(() => { let current = true; getValidationPage().then((data) => { if (current) setPage(data); }).catch((reason) => { if (current) setError(reasonText(reason)); }); return () => { current = false; loadSequence.current++; }; }, []);
  async function selectExperiment(id: string) {
    const sequence = ++loadSequence.current;
    setSelectedId(id); setResponse(null); setError(""); setLoadingExperiment(true); setCheck(null); setView("detail");
    try {
      const next = await getValidationExperiment(id);
      if (sequence !== loadSequence.current) return;
      setResponse(next);
      const result = next.result;
      if (result) setRequest((current) => ({ ...current, template_id: result.template_id ?? current.template_id,
        experiment_name: result.experiment_name, start_date: result.start_date ?? current.start_date,
        end_date: result.end_date ?? current.end_date, top_n: result.top_n ?? current.top_n,
        cost_bps: result.cost_bps ?? current.cost_bps, initial_capital: result.initial_capital ?? current.initial_capital }));
    } catch (reason) { if (sequence === loadSequence.current) setError(reasonText(reason)); }
    finally { if (sequence === loadSequence.current) setLoadingExperiment(false); }
  }
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setRunning(true); setResponse(null);
    try {
      const result = await runValidation(request);
      setResponse(result); setSelectedId(result.result?.experiment_id ?? ""); setView("detail");
      try { setPage(await getValidationPage()); } catch { setError("实验已返回结果，但实验列表刷新失败，请稍后重新进入页面。"); }
    } catch (reason) { setError(reasonText(reason)); }
    finally { setRunning(false); }
  }
  function back() { loadSequence.current++; setView("list"); setLoadingExperiment(false); setResponse(null); setError(""); }
  const template = page?.templates.find((row) => row.id === request.template_id);
  const filtered = (page?.history ?? []).filter((row) => row.experiment_name.toLowerCase().includes(query.toLowerCase()) && (!filterStatus || row.status === filterStatus));
  if (sort !== "created_at") filtered.sort((a, b) => (b.status === "COMPLETED" ? b.metrics?.[sort] ?? -Infinity : -Infinity) - (a.status === "COMPLETED" ? a.metrics?.[sort] ?? -Infinity : -Infinity));
  const pages = Math.max(1, Math.ceil(filtered.length / 12));
  const validDates = !!request.start_date && !!request.end_date && request.start_date <= request.end_date;
  return <main className="validation-page">
    {view !== "list" && <button className="text-button" disabled={running} onClick={back}>← 返回实验列表</button>}
    <header className="section-heading"><div><p className="eyebrow">策略研究 / 沪深300</p><h1>历史回测实验</h1><p className="muted">保存每次实验，比较结果，查看每笔持仓变化。</p></div>{view !== "create" && <button disabled={running || loadingExperiment || !page} onClick={() => { setView("create"); setStep(1); setCheck(null); setError(""); setRequest((current) => ({ ...current, experiment_name: `${page?.templates.find((item) => item.id === current.template_id)?.name ?? "研究"} · 历史回测` })); }}>{view === "detail" ? "以此配置新建实验" : "新建回测实验"}</button>}</header>
    {error && <p role="alert">{error}</p>}
    {!page ? <p>{error ? "历史回测实验加载失败，请刷新重试。" : "正在读取实验列表…"}</p> : <>
      {view === "list" && <section aria-label="已保存实验"><div className="section-heading"><h2>已保存实验 <small>{page.history.length} 次</small></h2><span className="muted">每次运行独立保存，查看不会重新计算</span></div>
        <div className="ledger-toolbar"><label className="ledger-search">搜索实验<input type="search" placeholder="按实验名称搜索" value={query} onChange={(event) => { setQuery(event.target.value); setListPage(1); }} /></label><label>实验状态<select value={filterStatus} onChange={(event) => { setFilterStatus(event.target.value); setListPage(1); }}><option value="">全部状态</option>{["COMPLETED", "PARTIAL", "INSUFFICIENT_DATA", "FAILED"].map((key) => <option key={key} value={key}>{statusLabel(key)}</option>)}</select></label><label>排序<select value={sort} onChange={(event) => { setSort(event.target.value); setListPage(1); }}><option value="created_at">最近创建</option><option value="annualized_return">年化收益从高到低</option><option value="max_drawdown">最大回撤从小到大</option><option value="sharpe">夏普比率从高到低</option></select></label></div>
        <div className="ledger-table-scroll"><table className="ledger-table" aria-label="回测实验列表"><thead><tr><th>实验 / 策略</th><th>回测区间</th><th>年化收益</th><th>累计收益</th><th>最大回撤</th><th>夏普比率</th><th>状态</th><th>操作</th></tr></thead><tbody>{filtered.slice((listPage - 1) * 12, listPage * 12).map((row, index) => <tr key={row.experiment_id ?? index}><td><strong>{row.experiment_name}</strong><small>{page.templates.find((item) => item.id === row.template_id)?.name ?? "--"} · {row.top_n ?? "--"} 只</small><small>创建于 {row.created_at ? new Date(row.created_at).toLocaleString("zh-CN", { hour12: false }) : "--"}</small></td><td>{row.actual_start_date ?? "--"}<small>至 {row.actual_end_date ?? "--"}</small><small title="实验设定的开始与结束日期">设定 {row.start_date ?? "--"} ～ {row.end_date ?? "--"}</small></td>{["annualized_return", "total_return", "max_drawdown", "sharpe"].map((key) => <td className="numeric" key={key}>{row.status !== "COMPLETED" ? "--" : key === "sharpe" ? number(row.metrics?.[key]) : percent(row.metrics?.[key])}</td>)}<td><span className={`result-status status-${row.status.toLowerCase()}`}>{statusLabel(row.status)}</span></td><td><button className="secondary" disabled={!row.experiment_id} aria-label={`查看详情：${row.experiment_name}`} onClick={() => selectExperiment(row.experiment_id!)}>查看详情</button></td></tr>)}</tbody></table></div>
        {!filtered.length && <p className="ledger-empty">{page.history.length ? "没有匹配实验，请调整筛选。" : "还没有历史回测实验。新建实验后，配置与结果都会自动保存。"}</p>}
        <div className="ledger-pagination"><span>第 {listPage} / {pages} 页 · 共 {filtered.length} 次实验</span><div><button className="secondary" disabled={listPage <= 1} onClick={() => setListPage(listPage - 1)}>上一页</button><button className="secondary" disabled={listPage >= pages} onClick={() => setListPage(listPage + 1)}>下一页</button></div></div>
      </section>}
      {view === "create" && <section className="experiment-wizard"><div className="wizard-steps">{["选择策略", "设置实验", "核对并运行"].map((label, index) => <span className={step === index + 1 ? "active" : ""} key={label}>{index + 1}. {label}</span>)}</div>
        {step === 1 && <><h2>选择你想验证的选股策略</h2><p>不同策略使用相同的历史股票池，通过不同因子权重改变排名。</p><div className="strategy-grid">{page.templates.map((item) => <button key={item.id} className={`strategy-card ${request.template_id === item.id ? "selected" : ""}`} aria-pressed={request.template_id === item.id} onClick={() => setRequest({ ...request, template_id: item.id, experiment_name: `${item.name} · 历史回测` })}><strong>{item.name}</strong><span>{item.description ?? "查看下方策略规则"}</span>{request.template_id === item.id && <small>已选择</small>}</button>)}</div><StrategyDescription template={template} /><button disabled={!template} onClick={() => setStep(2)}>下一步：设置实验</button></>}
        {step === 2 && <form onSubmit={(event) => { event.preventDefault(); setStep(3); }}><h2>设置实验</h2><label>实验名称<input required value={request.experiment_name} maxLength={100} onChange={(event) => setRequest({ ...request, experiment_name: event.target.value })} /></label><label>开始日期<input required type="date" value={request.start_date} onChange={(event) => { setRequest({ ...request, start_date: event.target.value }); setCheck(null); }} /></label><label>结束日期<input required type="date" value={request.end_date} onChange={(event) => { setRequest({ ...request, end_date: event.target.value }); setCheck(null); }} /></label><label>模拟本金（元）<input type="number" min="1" max="1000000000" required value={request.initial_capital} onChange={(event) => setRequest({ ...request, initial_capital: Number(event.target.value) })} /></label><label>目标持仓数量<input required type="number" min="1" max="300" value={request.top_n} onChange={(event) => setRequest({ ...request, top_n: Number(event.target.value) })} /></label><label>每万元交易成本（元）<input required type="number" min="0" max="500" step="0.1" value={request.cost_bps} onChange={(event) => setRequest({ ...request, cost_bps: Number(event.target.value) })} /></label><p>每月调仓 · 等权目标 · 沪深300历史成分股 · 下一交易日开盘模拟成交</p>{!validDates && <p role="alert">请填写正确的起止日期。</p>}<button type="button" className="secondary" onClick={() => setStep(1)}>上一步</button><button disabled={!validDates}>下一步：核对配置</button></form>}
        {step === 3 && <><h2>核对并运行</h2><dl>{[["策略", template?.name ?? "--"], ["区间", `${request.start_date} 至 ${request.end_date}`], ["模拟本金", `${formatPrice(request.initial_capital)} 元`], ["股票数量", `${request.top_n} 只`], ["每万元交易成本", `${request.cost_bps} 元`]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><p>按完整月度区间运行，实际曲线截止日期可能早于所选结束日期。已有实验不会被覆盖。</p><button className="secondary" disabled={running || checking || !validDates} onClick={() => { setError(""); setCheck(null); setChecking(true); checkValidationData(request.start_date, request.end_date).then(setCheck).catch((reason) => setError(reasonText(reason))).finally(() => setChecking(false)); }}>{checking ? "正在检查历史数据…" : "检查所选区间"}</button>{check && <p>完整月度区间：{check.complete_periods} 个 · 已缓存 PIT 因子快照：{check.cached_snapshots} / {check.complete_periods} 个 · {check.status === "INSUFFICIENT_DATA" ? "样本不足" : "可运行，缺失快照将在运行时补算"}</p>}<form onSubmit={submit}><button type="button" className="secondary" disabled={running || checking} onClick={() => setStep(2)}>上一步</button><button disabled={running || checking || !validDates}>{running ? "正在运行回测…" : "运行并保存实验"}</button></form></>}
      </section>}
      {running && <p role="status">正在核对历史数据、计算因子并执行回测，请等待真实结果。</p>}
      {view === "detail" && loadingExperiment && <p role="status">正在读取所选实验…</p>}
      {view === "detail" && !running && !loadingExperiment && response && <Result key={selectedId} response={response} templates={page.templates} latestDate={page.latest_market_date} />}
    </>}
    <p className="muted">历史回测结果仅供研究参考，不代表未来收益。</p>
  </main>;
}
