import { useEffect, useRef, useState } from "react";
import { addDraftSecurity, createPortfolio, getPortfolioList, type PortfolioSummary } from "../api/portfolios";

export function AddToPortfolio({ codes, onClose }: { codes: string[]; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [rows, setRows] = useState<PortfolioSummary[]>([]);
  const [target, setTarget] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<string[]>([]);
  useEffect(() => { dialog.current?.showModal(); getPortfolioList().then((data) => setRows(data.portfolios.filter((row) => row.status !== "ARCHIVED" && row.status !== "archived"))).catch((reason) => setError(reason.message)); }, []);
  async function add() {
    setBusy(true); setError("");
    const completed = [...done];
    try { for (const code of codes.filter((code) => !completed.includes(code))) { await addDraftSecurity(target, code); completed.push(code); setDone([...completed]); } }
    catch (reason) { setError(`${reason instanceof Error ? reason.message : "添加失败"} 已添加 ${completed.length} 只，重试会继续添加剩余股票。`); }
    finally { setBusy(false); }
  }
  return <dialog ref={dialog} aria-labelledby="portfolio-picker-title" onCancel={(event) => { if (busy) event.preventDefault(); else onClose(); }}><div className="dialog-heading"><h2 id="portfolio-picker-title">添加到组合</h2><button className="secondary" disabled={busy} onClick={onClose}>关闭</button></div><p>已选 {codes.length} 只股票。加入后在该组合的「待调整清单」中设置比例，保存计划后才会模拟调仓。</p>
    {error && <p role="alert">{error}</p>}
    {done.length === codes.length ? <p role="status">已添加到「{rows.find((row) => row.portfolio_id === target)?.name}」。<a href={`/portfolios/${encodeURIComponent(target)}?tab=targets`}>查看待调整清单 →</a></p> : <>
    <div className="picker-list">{rows.map((row) => <label key={row.portfolio_id}><input type="radio" name="target-portfolio" disabled={busy || done.length > 0} checked={target === row.portfolio_id} onChange={() => setTarget(row.portfolio_id)} />{row.name}</label>)}</div>
    <details><summary>新建组合并添加</summary><form onSubmit={async (event) => { event.preventDefault(); setBusy(true); setError(""); try { const result = await createPortfolio(name.trim(), 1000000, 5); setRows([...rows, { portfolio_id: result.portfolio_id, name: name.trim(), status: "ACTIVE" }]); setTarget(result.portfolio_id); setName(""); } catch (reason) { setError(reason instanceof Error ? reason.message : "创建失败"); } finally { setBusy(false); } }}><label>组合名称<input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} /></label><p>默认模拟本金 100 万元，每万元单边费用 5 元。</p><button disabled={busy || !name.trim() || done.length > 0}>创建组合</button></form></details>
    <button disabled={!target || busy} onClick={add}>{busy ? "正在保存…" : done.length ? "继续添加剩余股票" : "添加到待调整清单"}</button></>}
  </dialog>;
}
