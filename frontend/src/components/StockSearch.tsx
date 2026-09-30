import { useEffect, useId, useState } from "react";
import { getStockOptions, type StockOption } from "../api/detail";

export function StockSearch({ onSelect, label = "搜索股票名称或代码", disabled = false }: { disabled?: boolean; onSelect: (stock: StockOption) => void; label?: string }) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [rows, setRows] = useState<StockOption[]>([]);
  const [status, setStatus] = useState("");
  const [active, setActive] = useState(-1);
  useEffect(() => {
    let current = true;
    setRows([]); setActive(-1); setStatus(query.trim() ? "正在搜索…" : "");
    if (!query.trim()) return;
    const timer = window.setTimeout(() => { getStockOptions(query.trim()).then(({ stocks }) => {
      if (current) { setRows(stocks); setStatus(stocks.length ? "" : "未找到匹配股票"); }
    }).catch(() => { if (current) setStatus("搜索暂时不可用，请重新输入重试。"); }); }, 250);
    return () => { current = false; window.clearTimeout(timer); };
  }, [query]);
  const choose = (stock: StockOption) => { if (disabled) return; setQuery(""); setRows([]); onSelect(stock); };
  return <div className="stock-search"><label htmlFor={id}>{label}</label><input id={id} disabled={disabled} type="search" placeholder="例如：平安银行 / 000001" value={query} role="combobox" aria-autocomplete="list" aria-expanded={rows.length > 0} aria-controls={`${id}-results`} aria-activedescendant={active >= 0 ? `${id}-${active}` : undefined} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => {
    if (event.key === "ArrowDown") { event.preventDefault(); setActive(Math.min(rows.length - 1, active + 1)); }
    if (event.key === "ArrowUp") { event.preventDefault(); setActive(Math.max(0, active - 1)); }
    if (event.key === "Enter" && rows[active]) { event.preventDefault(); choose(rows[active]); }
    if (event.key === "Escape") setQuery("");
  }} />
    {query.trim() && <div className="search-results"><ul id={`${id}-results`} role="listbox">{rows.map((stock, index) => <li role="option" id={`${id}-${index}`} aria-selected={active === index} key={stock.code} onMouseDown={(event) => event.preventDefault()} onClick={() => choose(stock)}><strong>{stock.name}</strong><span>{stock.code}</span></li>)}</ul>{status && <p role="status">{status}</p>}{rows.length === 20 && <small>最多显示 20 条，请继续输入以缩小范围。</small>}</div>}
  </div>;
}
