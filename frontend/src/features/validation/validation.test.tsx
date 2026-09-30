import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HistoricalValidationPage } from "./HistoricalValidationPage";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const json = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });
function stubResult(status: string, result: Record<string, unknown>, reason: string | null = null) {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => Promise.resolve(json(url.includes("/experiments/") ? { status, reason, result } : {
    templates: [{ id: "value_growth", name: "价值成长" }], history: [{ experiment_id: "saved", experiment_name: "保存的实验", status, metrics: result.metrics }], latest: { result: null },
  }))));
}
async function openSaved() { render(<HistoricalValidationPage />); fireEvent.click(await screen.findByRole("button", { name: "查看详情：保存的实验" })); }
describe("historical validation", () => {
  it("withholds performance metrics when the stored result is partial", async () => {
    stubResult("PARTIAL", { experiment_name: "价值成长", coverage: { valid_periods: 42, skipped_periods: 1 }, skipped_periods: [], metrics: { total_return: .99 } }, "停牌期间缺少估值证据");
    await openSaved();
    expect(await screen.findByText(/有效 42 期/)).toBeInTheDocument();
    expect(screen.getByText(/未达到完整历史验证条件/)).toBeInTheDocument();
    expect(screen.queryByText("99.00%")).not.toBeInTheDocument();
    expect(screen.queryByText("核心指标")).not.toBeInTheDocument();
  });
  it("shows stored metrics in the list and complete detail", async () => {
    stubResult("COMPLETED", { experiment_name: "价值成长", coverage: { valid_periods: 3 }, skipped_periods: [], metrics: { total_return: .12, annualized_return: .1, max_drawdown: -.03, sharpe: .7 } });
    render(<HistoricalValidationPage />);
    expect(await screen.findByText("12.00%")).toBeInTheDocument();
    expect(screen.getByText("10.00%")).toBeInTheDocument();
    expect(screen.queryByText("核心指标")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "查看详情：保存的实验" }));
    expect(await screen.findByText("核心指标")).toBeInTheDocument();
    expect(screen.getByText("12.00%")).toBeInTheDocument();
  });
  it("checks the chosen dates and passes wizard parameters to a new saved experiment", async () => {
    const fetchMock = vi.fn().mockImplementation((url: string, options: RequestInit) => Promise.resolve(json(
      url.includes("/check?") ? { complete_periods: 43, cached_snapshots: 42, status: "READY_TO_RUN" } : options.method === "POST" ? { status: "PARTIAL", reason: "样本不足", result: { experiment_name: "新实验", coverage: {}, skipped_periods: [] } } : { templates: [{ id: "quality_growth", name: "质量成长", weights: { growth: .25 } }], history: [], latest: { result: null } },
    )));
    vi.stubGlobal("fetch", fetchMock);
    render(<HistoricalValidationPage />);
    await screen.findByRole("region", { name: "已保存实验" });
    fireEvent.click(screen.getByRole("button", { name: "新建回测实验" }));
    fireEvent.click(screen.getByRole("button", { name: "下一步：设置实验" }));
    fireEvent.change(screen.getByLabelText("实验名称"), { target: { value: "新实验" } });
    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2024-01-01" } });
    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "2025-01-01" } });
    fireEvent.change(screen.getByLabelText("模拟本金（元）"), { target: { value: "200000" } });
    fireEvent.change(screen.getByLabelText("目标持仓数量"), { target: { value: "12" } });
    fireEvent.click(screen.getByRole("button", { name: "下一步：核对配置" }));
    fireEvent.click(screen.getByRole("button", { name: "检查所选区间" }));
    expect(await screen.findByText(/已缓存 PIT 因子快照：42 \/ 43 个/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/backtests/factor/check?start_date=2024-01-01&end_date=2025-01-01", expect.anything());
    fireEvent.click(screen.getByRole("button", { name: "运行并保存实验" }));
    expect(await screen.findByRole("heading", { name: "新实验" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/backtests/factor", expect.objectContaining({ method: "POST", body: JSON.stringify({ template_id: "quality_growth", experiment_name: "新实验", start_date: "2024-01-01", end_date: "2025-01-01", top_n: 12, cost_bps: 10, initial_capital: 200000 }) }));
  });
  it("shows actual holding changes and keeps unchanged holdings out of blocked orders", async () => {
    stubResult("COMPLETED", { experiment_name: "持仓变化", coverage: { valid_periods: 3 }, skipped_periods: [],
      adjustments: ["OPEN", "INCREASE", "REDUCE", "CLOSE", "HOLD"].map((action) => ({ date: "2024-02-29", execution_date: "2024-03-01", ts_code: action, action, quantity_before: 0, quantity_after: .05, quantity: .05, cost: 0, weight_before: .5, weight_after: .5, holding_shares: 500 })),
    });
    await openSaved();
    fireEvent.click(await screen.findByRole("tab", { name: "调仓明细" }));
    const table = screen.getByRole("table", { name: "历史调整" });
    for (const label of ["建仓", "加仓", "减仓", "清仓", "持有不变"]) expect(within(table).getByText(label)).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "异常记录" })).not.toBeInTheDocument();
  });
});
