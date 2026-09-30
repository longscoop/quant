import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import { PortfolioPage } from "./PortfolioPage";
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
const data = { portfolio: { portfolio_id: "p1", name: "长期组合", initial_capital: 1000000, transaction_cost_bps: 5 }, positions: [], draft_positions: [{ code: "000001.SZ", name: "平安银行", weight: .2 }], target_revisions: [], orders: [], valuation: {}, metrics: {}, nav_rows: [], research_scores: {}, industry_exposure: {}, factor_exposure: {} };
function start(tab: string) { render(<MemoryRouter initialEntries={[`/portfolios/p1?tab=${tab}`]}><Routes><Route path="/portfolios/:portfolioId" element={<PortfolioPage />} /></Routes></MemoryRouter>); }
it("preserves the form and portfolio when cash-flow validation fails", async () => {
  vi.stubGlobal("fetch", vi.fn().mockImplementation((_url: string, opts: RequestInit) => Promise.resolve(new Response(JSON.stringify(opts.method === "POST" ? { detail: "资金流水不能早于组合成立日" } : data), { status: opts.method === "POST" ? 422 : 200 }))));
  start("cash");
  await screen.findByRole("heading", { name: "资金管理" });
  fireEvent.change(screen.getByLabelText("金额（元）"), { target: { value: "123" } });
  fireEvent.click(screen.getByRole("button", { name: "保存资金调整" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("资金流水不能早于组合成立日");
  expect(screen.getByLabelText("金额（元）")).toHaveValue(123);
  expect(screen.getByRole("heading", { name: "长期组合" })).toBeInTheDocument();
  expect(screen.queryByText(/组合服务暂时不可用/)).not.toBeInTheDocument();
});
it("previews human percentages and saves only when the plan is confirmed", async () => {
  const fetchMock = vi.fn().mockImplementation((_url: string, opts: RequestInit) => Promise.resolve(new Response(JSON.stringify(opts.method === "POST" ? { revision_id: "new" } : data))));
  vi.stubGlobal("fetch", fetchMock); start("targets");
  const input = await screen.findByLabelText("000001.SZ 目标比例（%）");
  expect(input).toHaveValue(20);
  fireEvent.change(input, { target: { value: "40" } });
  fireEvent.click(screen.getByRole("button", { name: "预览调整" }));
  expect(screen.getByRole("region", { name: "调整预览" })).toHaveTextContent("建仓");
  expect(fetchMock.mock.calls.some(([, opts]) => opts.method === "POST")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "保存模拟计划" }));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/v1/portfolios/p1/targets", expect.objectContaining({ body: '{"targets":{"000001.SZ":0.4}}' })));
  expect(await screen.findByRole("status")).toHaveTextContent("模拟计划已保存");
});
