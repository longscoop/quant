import { useState, type ReactNode } from "react";

type Point = { date: string; value: number };
type Series = { label: string; color: string; points: Point[] };
export function TimeSeriesChart({ title, series, formatValue = (value) => value.toFixed(3), detail, onSelectDate }: {
  title: string; series: Series[]; formatValue?: (value: number) => string;
  detail?: (date: string) => ReactNode; onSelectDate?: (date: string) => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const valid = series.map((item) => ({ ...item, points: item.points.filter((point) => Number.isFinite(point.value)) }));
  const dates = [...new Set(valid.flatMap((item) => item.points.map((point) => point.date)))].sort();
  const values = valid.flatMap((item) => item.points.map((point) => point.value));
  if (dates.length < 2 || values.length < 2) return <p>样本不足，暂不展示走势。</p>;
  const low = Math.min(...values), high = Math.max(...values), span = high - low || 1;
  const width = 900, height = 280, left = 65, right = width - 15, top = 22, bottom = height - 32;
  const indices = new Map(dates.map((day, index) => [day, index]));
  const x = (day: string) => left + ((indices.get(day) ?? 0) / (dates.length - 1)) * (right - left);
  const y = (value: number) => top + ((high - value) / span) * (bottom - top);
  const active = hover ?? (selected && indices.has(selected) ? selected : dates.at(-1)!);
  function select(day: string) { setSelected(day); setHover(null); onSelectDate?.(day); }
  function dayAt(clientX: number, element: SVGSVGElement) {
    const rect = element.getBoundingClientRect();
    const pixel = (clientX - rect.left) / rect.width * width;
    return dates[Math.max(0, Math.min(dates.length - 1, Math.round((pixel - left) / (right - left) * (dates.length - 1))))];
  }
  return <figure className="time-chart">
    <figcaption>{title}</figcaption>
    <div className="chart-readout" aria-live="polite"><strong>{active}</strong>{detail ? detail(active) : valid.map((item) => { const point = item.points.find((p) => p.date === active); return <span key={item.label}>{item.label}：{point ? formatValue(point.value) : "--"}</span>; })}</div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}；可使用下方日期滑块选择日期`} preserveAspectRatio="none"
      onPointerMove={(event) => { if (event.pointerType !== "touch") setHover(dayAt(event.clientX, event.currentTarget)); }}
      onPointerLeave={() => setHover(null)} onClick={(event) => select(dayAt(event.clientX, event.currentTarget))}>
      {[0, 1, 2, 3].map((step) => { const value = low + step / 3 * span; return <g key={step}><line x1={left} x2={right} y1={y(value)} y2={y(value)} stroke="#e4eaf2" /><text x={left - 8} y={y(value) + 4} textAnchor="end">{formatValue(value)}</text></g>; })}
      <text x={left} y={height - 6}>{dates[0]}</text><text x={right} y={height - 6} textAnchor="end">{dates.at(-1)}</text>
      {valid.map((item) => <polyline key={item.label} fill="none" stroke={item.color} strokeWidth="2.5" strokeLinejoin="round" points={item.points.map((point) => `${x(point.date)},${y(point.value)}`).join(" ")} />)}
      <line x1={x(active)} x2={x(active)} y1={top} y2={bottom} stroke="#6d8099" strokeDasharray="4 4" />
      {valid.map((item) => { const point = item.points.find((p) => p.date === active); return point && <circle key={item.label} cx={x(active)} cy={y(point.value)} r="4" fill={item.color} stroke="white" strokeWidth="2" />; })}
    </svg>
    <div className="chart-legend">{valid.map((item) => <span key={item.label}><i style={{ backgroundColor: item.color }} />{item.label}</span>)}</div>
    <label className="chart-slider">选择日期 · {selected ?? dates.at(-1)}<input aria-label={`${title}日期`} aria-valuetext={active} type="range" min={0} max={dates.length - 1} value={indices.get(selected ?? dates.at(-1)!) ?? dates.length - 1} onChange={(event) => select(dates[Number(event.target.value)])} /></label>
    <small className="muted">悬停查看当日数据，点击固定日期；也可拖动滑块或使用方向键。</small>
  </figure>;
}
