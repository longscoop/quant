import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { archivePortfolio, copyPortfolio, createPortfolio, getPortfolioDashboard, getPortfolioList, recordCashFlow, runPortfolioBacktest, saveTargets, addDraftSecurity, type PortfolioBacktest, type PortfolioDashboard, type PortfolioSummary } from "../../api/portfolios";
import { TimeSeriesChart } from "../../components/TimeSeriesChart";
import { StockSearch } from "../../components/StockSearch";

const percent = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "--" : `${(value * 100).toFixed(2)}%`;
const number = (value: number | null | undefined) => value == null || !Number.isFinite(value) ? "--" : value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const money = (value: number | null | undefined) => value == null ? "--" : `¥${number(value)}`;
const today = () => new Date().toLocaleDateString("sv-SE");
const status = (value: string) => ({ COMPLETED: "已完成", ACTIVE: "进行中", ARCHIVED: "已归档", PENDING: "等待成交", PARTIAL: "部分完成", FAILED: "失败", INSUFFICIENT_DATA: "数据不足" }[value.toUpperCase()] ?? value);
const reasonText = (reason: unknown) => reason instanceof Error ? reason.message : "操作失败，请重试。";

export function PortfolioPage() {
  const { portfolioId = "" } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const panel = params.get("tab") ?? "holdings";
  const [portfolios, setPortfolios] = useState<PortfolioSummary[]>([]);
  const [dashboard, setDashboard] = useState<PortfolioDashboard | null>(null);
  const [draft, setDraft] = useState<Record<string, number>>({});
  const [names, setNames] = useState<Record<string, string>>({});
  const [create, setCreate] = useState(false);
  const [name, setName] = useState("");
  const [capital, setCapital] = useState(1_000_000);
  const [fee, setFee] = useState(5);
  const [flowDate, setFlowDate] = useState(today());
  const [flowAmount, setFlowAmount] = useState(100_000);
  const [flowDirection, setFlowDirection] = useState("in");
  const [flowNote, setFlowNote] = useState("");
  const [revisionId, setRevisionId] = useState("");
  const [backtestStart, setBacktestStart] = useState("2023-01-01");
  const [backtestEnd, setBacktestEnd] = useState(today());
  const [backtestFee, setBacktestFee] = useState(5);
  const [backtest, setBacktest] = useState<PortfolioBacktest | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [readError, setReadError] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [preview, setPreview] = useState(false);
  const [orderPage, setOrderPage] = useState(1);
  const sequence = useRef(0);
  function accept(data: PortfolioDashboard) {
    setOrderPage(1); setDashboard(data); setDraft(Object.fromEntries(data.draft_positions.map((row) => [row.code, row.weight])));
    setNames(Object.fromEntries([...data.draft_positions.map((row) => [row.code, row.name ?? row.code]), ...data.positions.map((row) => [row.ts_code, row.name])]));
    setRevisionId(data.target_revisions.at(-1)?.revision_id ?? ""); setBacktest(data.backtest); setBacktestFee(data.portfolio.transaction_cost_bps);
    if (data.valuation_date) setBacktestEnd(data.valuation_date);
  }
  async function load() {
    const current = ++sequence.current;
    setLoading(true); setDashboard(null); setReadError(""); setError(""); setMessage(""); setPreview(false);
    try {
      if (portfolioId) { const data = await getPortfolioDashboard(portfolioId); if (current === sequence.current) accept(data); }
      else { const data = await getPortfolioList(); if (current === sequence.current) setPortfolios(data.portfolios); }
    } catch (reason) { if (current === sequence.current) setReadError(reasonText(reason)); }
    finally { if (current === sequence.current) setLoading(false); }
  }
  useEffect(() => { load(); return () => { sequence.current++; }; }, [portfolioId]);
  async function mutate(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setMessage("");
    try {
      await action();
      // A successful write must be followed by a fresh read. Never label an old snapshot as updated.
      try { accept(await getPortfolioDashboard(portfolioId)); setMessage(success); setPreview(false); }
      catch (reason) { setDashboard(null); setReadError(`操作已提交，但最新结果读取失败。${reasonText(reason)}`); }
    } catch (reason) { setError(reasonText(reason)); }
    finally { setBusy(false); }
  }
  async function submitCreate(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { const result = await createPortfolio(name.trim(), capital, fee); setCreate(false); navigate(`/portfolios/${result.portfolio_id}?tab=targets`); }
    catch (reason) { setError(reasonText(reason)); } finally { setBusy(false); }
  }
  const total = Object.values(draft).reduce((sum, value) => sum + value, 0);
  const valid = Object.values(draft).every((value) => Number.isFinite(value) && value >= 0 && value <= 1) && total <= 1 + 1e-9;
  const codes = Object.keys(draft);
  const previewCodes = [...new Set([...codes, ...(dashboard?.positions.map((row) => row.ts_code) ?? [])])];
  function changeDraft(next: Record<string, number>) { setDraft(next); setPreview(false); setError(""); setMessage(""); }
  return <main>
    {portfolioId && <a href="/portfolios">← 全部组合</a>}
    <div className="section-heading"><div><p className="eyebrow">独立管理 · 模拟研究</p><h1>{dashboard?.portfolio.name ?? "我的组合"}</h1><p className="muted">收集股票 → 设置目标比例 → 预览变化 → 保存模拟计划</p></div>{!portfolioId && <button onClick={() => setCreate(!create)}>{create ? "取消新建" : "新建组合"}</button>}</div>
    {error && <p role="alert">{error} {dashboard && "本次操作未完成；输入已保留，下方仍为上次读取的已保存记录。"}</p>}
    {message && <p role="status">{message}</p>}
    {readError && <p role="alert">{readError} <button onClick={load}>重新加载</button></p>}
    {create && !portfolioId && <form onSubmit={submitCreate}><h2>新建组合</h2><label>组合名称<input autoFocus required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} /></label><details><summary>本金与费用 · 默认 100 万元 / 每万元 5 元</summary><label>初始模拟资金（元）<input type="number" required min="1" value={capital} onChange={(event) => setCapital(Number(event.target.value))} /></label><label>每万元单边费用（元）<input type="number" required min="0" max="9999" step="0.1" value={fee} onChange={(event) => setFee(Number(event.target.value))} /></label></details><button disabled={busy || !name.trim()}>创建组合</button></form>}
    {loading && <p role="status">正在读取组合…</p>}
    {!portfolioId && !loading && !readError && <div className="portfolio-grid">{portfolios.length === 0 ? <section><h2>创建你的第一个组合</h2><p>每个组合独立保存目标、成交和资金记录。</p><button onClick={() => setCreate(true)}>新建组合</button></section> : portfolios.map((row) => <section key={row.portfolio_id}><span className="result-status">{status(row.status)}</span><h2>{row.name}</h2><p className="muted">持仓、待调整清单与历史计划</p><a className="button-link" href={`/portfolios/${row.portfolio_id}`}>打开组合 →</a></section>)}</div>}
    {dashboard && <>
      <div className="freshness-strip">数据截止 {dashboard.valuation_date ?? "--"} · 估值状态 {status(dashboard.valuation.status ?? "尚无估值")} · T 日收盘形成信号，下一交易日开盘模拟成交</div>
      <dl><div><dt>总资产</dt><dd>{money(dashboard.valuation.total_value)}</dd></div><div><dt>现金</dt><dd>{money(dashboard.valuation.cash)}</dd></div><div><dt>持有股票</dt><dd>{dashboard.positions.length} 只</dd></div><div><dt>累计收益</dt><dd>{percent(dashboard.metrics.total_return)}</dd></div><div><dt>最大回撤</dt><dd>{percent(dashboard.metrics.max_drawdown)}</dd></div></dl>
      <div className="validation-tabs" role="tablist" aria-label="组合内容">{[["holdings", "当前持仓"], ["targets", `待调整清单（${codes.length}）`], ["orders", "计划与成交"], ["cash", "资金管理"], ["analysis", "分析与回放"]].map(([key, label]) => <button key={key} role="tab" aria-selected={panel === key} disabled={busy} onClick={() => { setParams({ tab: key }); setError(""); setMessage(""); }}>{label}</button>)}</div>
      {panel === "holdings" && <section><h2>当前持仓</h2><p className="muted">以下是已模拟成交的持仓；待调整清单中的股票不代表已买入。</p>{dashboard.positions.length === 0 ? <p>尚无实际模拟成交。到待调整清单设置目标比例并保存计划。</p> : <><p className="muted">持仓数量为含分红再投资的模拟股数，可含小数；成本按当前复权因子换算，包含交易费用。</p><table><thead><tr><th>股票</th><th>首次买入日期</th><th>持仓成本价</th><th>收盘价</th><th>持仓数量（股）</th><th>持仓涨跌幅</th><th>当前比例</th><th>浮动盈亏</th></tr></thead><tbody>{dashboard.positions.map((row) => <tr key={row.ts_code}><td><a href={`/stocks/${row.ts_code}`}>{row.name}</a><small>{row.ts_code}</small></td><td>{row.first_buy_date ?? "--"}</td><td>{money(row.buy_cost)}</td><td>{money(row.market_price)}</td><td>{number(row.holding_shares)}</td><td>{percent(row.holding_return)}</td><td>{percent(row.current_weight)}</td><td>{money(row.unrealized_pnl)}</td></tr>)}</tbody></table></>}
        <TimeSeriesChart title="组合净值" series={[{ label: "研究组合", color: "#2563eb", points: dashboard.nav_rows.map((row) => ({ date: row.date, value: row.nav })) }, { label: "沪深300", color: "#d97706", points: dashboard.nav_rows.filter((row) => row.benchmark_nav != null).map((row) => ({ date: row.date, value: row.benchmark_nav! })) }]} />
      </section>}
      {panel === "targets" && <section><h2>待调整清单</h2><p>设置每只股票的目标比例。0% 表示不持有；未分配部分保留现金。保存前可以预览变化。</p>
        <StockSearch disabled={busy} label="搜索并添加股票" onSelect={(stock) => { if (stock.code in draft) return; setBusy(true); setError(""); addDraftSecurity(portfolioId, stock.code).then(() => { changeDraft({ ...draft, [stock.code]: 0 }); setNames({ ...names, [stock.code]: stock.name }); }).catch((reason) => setError(reasonText(reason))).finally(() => setBusy(false)); }} />
        {codes.length > 0 ? <table><thead><tr><th>股票</th><th>当前比例</th><th>目标比例（%）</th><th>操作</th></tr></thead><tbody>{codes.map((code) => <tr key={code}><td><a href={`/stocks/${code}`}>{names[code] ?? code}</a><small>{code}</small></td><td>{percent(dashboard.positions.find((row) => row.ts_code === code)?.current_weight ?? (dashboard.positions.some((row) => row.ts_code === code) ? null : 0))}</td><td><input disabled={busy} type="number" min="0" max="100" step="0.01" aria-label={`${code} 目标比例（%）`} value={Number((draft[code] * 100).toFixed(6))} onChange={(event) => changeDraft({ ...draft, [code]: Number(event.target.value) / 100 })} /> %</td><td><button className="secondary" disabled={busy} onClick={() => { const next = { ...draft }; delete next[code]; changeDraft(next); }}>移出清单</button></td></tr>)}</tbody></table> : <p>搜索添加股票，或从每日推荐、选股页面添加。</p>}
        <div className="selection-bar">目标合计 {percent(total)} · 预留现金比例 {valid ? percent(Math.max(0, 1 - total)) : "--"}（费用另计）</div>
        {!valid && <p role="alert">目标比例应在 0–100% 之间，合计不能超过 100%。</p>}
        <button className="secondary" disabled={busy || !codes.length} onClick={() => changeDraft(Object.fromEntries(codes.map((code) => [code, 1 / codes.length])))}>等权分配</button>
        <button className="secondary" disabled={busy || !codes.some((code) => (dashboard.research_scores[code] ?? 0) > 0)} onClick={() => { const sum = codes.reduce((sum, code) => sum + Math.max(0, dashboard.research_scores[code] ?? 0), 0); changeDraft(Object.fromEntries(codes.map((code) => [code, Math.max(0, dashboard.research_scores[code] ?? 0) / sum]))); }}>按可用研究分分配</button>
        <button className="secondary" disabled={busy || total <= 0} onClick={() => changeDraft(Object.fromEntries(Object.entries(draft).map(([code, weight]) => [code, weight / total])))}>分配至 100%</button>
        <button disabled={busy || !valid || !previewCodes.length} onClick={() => setPreview(true)}>预览调整</button>
        {preview && <section aria-label="调整预览"><h3>调整预览</h3><p className="muted">按当前持仓比例与目标比例比较。成交数量、价格和费用由下一交易日行情确定；仅成交差额，不会先全部清仓。</p><table><thead><tr><th>股票</th><th>计划动作</th><th>当前 → 目标</th></tr></thead><tbody>{previewCodes.map((code) => { const holding = dashboard.positions.find((row) => row.ts_code === code); const before = holding ? holding.current_weight : 0; const after = draft[code] ?? 0; const action = before == null ? "待估值后确定" : Math.abs(after - before) < 1e-9 ? "持有不变" : !holding ? "建仓" : after === 0 ? "清仓" : after > before ? "加仓" : "减仓"; return <tr key={code}><td>{names[code] ?? code}</td><td>{action}</td><td>{percent(before)} → {percent(after)}</td></tr>; })}</tbody></table><button disabled={busy || !valid} onClick={() => mutate(() => saveTargets(portfolioId, draft), "模拟计划已保存。请在计划与成交中查看执行状态。")}>{busy ? "正在保存…" : "保存模拟计划"}</button></section>}
        <p className="muted">编辑比例和移出清单将在保存模拟计划后生效。</p>
      </section>}
      {panel === "orders" && <section><h2>计划与成交</h2>{dashboard.target_revision && <p>最新计划 #{dashboard.target_revision.revision_no} · 信号日 {dashboard.target_revision.signal_date} · {status(dashboard.target_revision.status)}</p>}{dashboard.orders.length === 0 ? <p>尚无模拟计划。</p> : <><table><thead><tr><th>计划</th><th>股票</th><th>方向</th><th>信号日</th><th>成交日</th><th>成交价</th><th>模拟股数</th><th>费用</th><th>状态 / 原因</th></tr></thead><tbody>{dashboard.orders.slice((orderPage - 1) * 15, orderPage * 15).map((row, index) => <tr key={index}><td>#{row.revision_no ?? "--"}</td><td>{row.name ?? row.code}<small>{row.code}</small></td><td>{{ BUY: "买入", SELL: "卖出", HOLD: "持有不变" }[row.side] ?? row.side}</td><td>{row.signal_date ?? "--"}</td><td>{row.actual_trade_date ?? "待成交"}</td><td>{money(row.market_price)}</td><td>{number(row.traded_shares)}</td><td>{money(row.fee)}</td><td>{status(row.status)}<small>{row.reason}</small></td></tr>)}</tbody></table><button disabled={orderPage === 1} onClick={() => setOrderPage(orderPage - 1)}>上一页</button>第 {orderPage} 页 · 共 {dashboard.orders.length} 条<button disabled={orderPage * 15 >= dashboard.orders.length} onClick={() => setOrderPage(orderPage + 1)}>下一页</button></>}</section>}
      {panel === "cash" && <section><h2>资金管理</h2><p>初始模拟本金 {money(dashboard.portfolio.initial_capital)} · 每万元单边费用 {number(dashboard.portfolio.transaction_cost_bps)} 元</p><form onSubmit={(event) => { event.preventDefault(); mutate(() => recordCashFlow(portfolioId, flowDate, flowDirection === "in" ? flowAmount : -flowAmount, flowNote), "资金流水已记录。"); }}><label>操作<select value={flowDirection} onChange={(event) => setFlowDirection(event.target.value)}><option value="in">追加资金</option><option value="out">转出资金</option></select></label><label>生效日期<input required type="date" value={flowDate} onChange={(event) => setFlowDate(event.target.value)} /></label><label>金额（元）<input required type="number" min="0.01" step="0.01" value={flowAmount} onChange={(event) => setFlowAmount(Number(event.target.value))} /></label><label>备注<input value={flowNote} maxLength={200} onChange={(event) => setFlowNote(event.target.value)} /></label><button disabled={busy || flowAmount <= 0}>保存资金调整</button></form></section>}
      {panel === "analysis" && <section><h2>分析与历史回放</h2><details><summary>行业与因子分布</summary><h3>行业分布</h3>{Object.entries(dashboard.industry_exposure).map(([name, value]) => <p key={name}>{name}：{percent(value)}</p>)}<h3>因子分布</h3>{Object.entries(dashboard.factor_exposure).map(([name, row]) => <p key={name}>{name}：{number(row.value)} · 覆盖率 {percent(row.coverage)}</p>)}</details>
        <h3>冻结目标版本历史回放</h3><p>把已保存的某一版目标比例放到历史区间进行模拟；与当前组合的实际模拟成交记录分别展示。</p>{dashboard.target_revisions.length === 0 ? <p>先保存一个目标计划后再回放。</p> : <form onSubmit={async (event) => { event.preventDefault(); setBusy(true); setError(""); setBacktest(null); try { setBacktest(await runPortfolioBacktest(portfolioId, revisionId, backtestStart, backtestEnd, backtestFee)); } catch (reason) { setError(reasonText(reason)); } finally { setBusy(false); } }}><label>目标版本<select value={revisionId} onChange={(event) => setRevisionId(event.target.value)}>{dashboard.target_revisions.map((row) => <option key={row.revision_id} value={row.revision_id}>#{row.revision_no} · {row.signal_date}</option>)}</select></label><label>开始日期<input required type="date" value={backtestStart} onChange={(event) => setBacktestStart(event.target.value)} /></label><label>结束日期<input required type="date" value={backtestEnd} onChange={(event) => setBacktestEnd(event.target.value)} /></label><label>每万元交易成本（元）<input required type="number" min="0" max="9999" step="0.1" value={backtestFee} onChange={(event) => setBacktestFee(Number(event.target.value))} /></label><button disabled={busy || !revisionId || backtestStart > backtestEnd}>{busy ? "正在回放…" : "运行历史回放"}</button></form>}
        {backtest && <section><p>回放状态：{status(backtest.status)}</p>{backtest.status !== "COMPLETED" ? <p>{backtest.reason}</p> : <><dl>{[["累计收益", percent(backtest.metrics?.total_return)], ["年化收益", percent(backtest.metrics?.annualized_return)], ["最大回撤", percent(backtest.metrics?.max_drawdown)], ["夏普比率", number(backtest.metrics?.sharpe)]].map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl><TimeSeriesChart title="冻结目标版本历史回放" series={[{ label: "研究组合", color: "#2563eb", points: backtest.equity_curve ?? [] }, { label: "沪深300", color: "#d97706", points: backtest.benchmark_curve ?? [] }]} /></>}</section>}
        <details><summary>组合管理</summary><button disabled={busy} onClick={async () => { setBusy(true); setError(""); try { const result = await copyPortfolio(portfolioId); navigate(`/portfolios/${result.portfolio_id}?tab=targets`); } catch (reason) { setError(reasonText(reason)); } finally { setBusy(false); } }}>复制为新组合</button><button className="secondary" disabled={busy} onClick={async () => { if (!window.confirm("归档后保留全部目标版本、成交和净值记录。确定归档？")) return; setBusy(true); try { await archivePortfolio(portfolioId); navigate("/portfolios"); } catch (reason) { setError(reasonText(reason)); } finally { setBusy(false); } }}>归档组合</button></details>
      </section>}
    </>}
  </main>;
}
