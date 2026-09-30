import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HistoricalValidationPage } from "./HistoricalValidationPage";
import { TargetSimulationPanel } from "./TargetSimulationPanel";
import { TradingNotes } from "./TradingNotes";
import type { TargetSimulation, ValidationResponse } from "../../api/validation";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const result = (id: string, name: string): ValidationResponse => ({ status: "COMPLETED", reason: null, result: {
  experiment_id: id, experiment_name: name, template_id: "value_growth", start_date: "2024-01-01", end_date: "2024-03-01", top_n: 10, cost_bps: 10, initial_capital: 100000,
  coverage: { requested_periods: 2, valid_periods: 2, skipped_periods: 0 }, skipped_periods: [], equity_curve: [{ date: "2024-03-01", value: 1 }],
} });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });
const preview: TargetSimulation = { status: "READY", as_of_date: "2024-03-04", holding_date: "2024-03-01", template_id: "value_growth", top_n: 10, saved_at: "2024-03-04T12:00:00Z", initial_capital: 100000, portfolio_value: 110000, cash_before: 100, estimated_cost: 20, estimated_cash_after: 0, execution_rule: "按信号日收盘价估算。", rows: [{ ts_code: "000001.SZ", name: "平安银行", action: "INCREASE", reference_price: 10.12, holding_shares: 100, target_shares: 150, change_shares: 50, current_weight: .1, target_weight: .15, estimated_amount: 506, estimated_fee: .506, signal_day_note: null }] };

describe("saved experiments", () => {
  it("ignores an older in-flight response after switching experiments again", async () => {
    let finishOld!: (value: Response) => void;
    const oldRequest = new Promise<Response>((resolve) => { finishOld = resolve; });
    vi.stubGlobal("fetch", vi.fn().mockImplementation((url: string) => {
      if (url.endsWith("/experiments/old")) return oldRequest;
      if (url.endsWith("/experiments/new")) return Promise.resolve(json(result("new", "第二个实验")));
      return Promise.resolve(json({ templates: [{ id: "value_growth", name: "价值成长" }], latest: result("new", "第二个实验"), history: [
        { experiment_id: "old", experiment_name: "第一个实验", status: "COMPLETED" }, { experiment_id: "new", experiment_name: "第二个实验", status: "COMPLETED" },
      ] }));
    }));
    render(<HistoricalValidationPage />);
    fireEvent.click(await screen.findByRole("button", { name: "查看详情：第一个实验" }));
    expect(screen.queryByRole("region", { name: "验证结果" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "← 返回实验列表" }));
    fireEvent.click(screen.getByRole("button", { name: "查看详情：第二个实验" }));
    expect(await screen.findByRole("heading", { name: "第二个实验" })).toBeInTheDocument();
    await act(async () => { finishOld(json(result("old", "第一个实验"))); await oldRequest; });
    expect(screen.getByRole("heading", { name: "第二个实验" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "第一个实验" })).not.toBeInTheDocument();
  });

  it("sends the selected experiment and target parameters, saves previews, and clears failures", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(json(preview)).mockResolvedValueOnce(json({ detail: "缺少信号日行情" }, 422));
    vi.stubGlobal("fetch", fetchMock);
    render(<TargetSimulationPanel result={result("selected", "所选实验").result!} templates={[{ id: "value_growth", name: "价值成长" }]} latestDate="2024-03-04" />);
    fireEvent.click(screen.getByRole("button", { name: "模拟并保存下期目标" }));
    expect(await screen.findByRole("region", { name: "目标模拟结果" })).toHaveTextContent("计划加仓");
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/backtests/factor/experiments/selected/target-simulation", expect.objectContaining({ body: JSON.stringify({ as_of_date: "2024-03-04", template_id: "value_growth", top_n: 10 }) }));
    expect(screen.getByLabelText("已保存的目标模拟")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("下期目标股票数"), { target: { value: "12" } });
    expect(screen.queryByRole("region", { name: "目标模拟结果" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "模拟并保存下期目标" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("缺少信号日行情");
    expect(screen.queryByRole("region", { name: "目标模拟结果" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("已保存的目标模拟"), { target: { value: "0" } });
    expect(screen.getByRole("region", { name: "目标模拟结果" })).toHaveTextContent("平安银行");
    expect(screen.getByLabelText("下期目标股票数")).toHaveValue(10);
  });

  it("restores persisted target previews without creating a new simulation", () => {
    render(<TargetSimulationPanel result={{ ...result("selected", "所选实验").result!, target_simulations: [preview] }} templates={[{ id: "value_growth", name: "价值成长" }]} latestDate="2024-03-04" />);
    fireEvent.change(screen.getByLabelText("已保存的目标模拟"), { target: { value: "0" } });
    expect(screen.getByRole("region", { name: "目标模拟结果" })).toHaveTextContent("已保存");
  });

  it("keeps suspension explanations collapsed and groups them by stock", () => {
    const { container } = render(<TradingNotes notes={[{ kind: "suspension", ts_code: "688041.SH", name: "海光信息", start_date: "2025-05-26", end_date: "2025-06-09", session_count: 10, reason: null, resume_date: "2025-06-10", resolution_date: null }]} />);
    expect(screen.getByText(/1 只股票涉及停牌或延期/)).toBeInTheDocument();
    expect(container.querySelector("details")).not.toHaveAttribute("open");
    expect(screen.getByText(/共 10 个估值日/)).toBeInTheDocument();
  });
});
