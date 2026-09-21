import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CandidatePoolPage } from "./CandidatePoolPage";
import { ResearchHomePage } from "./ResearchHomePage";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("research reading pages", () => {
  it("shows API-backed candidate evidence on the home page", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "COMPLETED",
      reason: null,
      freshness: {
        latest_trade_date: "2024-06-15",
        research_snapshot_date: "2024-06-15",
        security_count: 3,
        state: "可信",
        reason: "已覆盖最近可确认的交易日，研究快照已更新。",
      },
      candidates: [{
        name: "研究样本1", code: "000001.SZ", industry: "银行", tendency: "优先研究",
        confidence: "可信", risk: "较低", as_of_date: "2024-06-15",
        core_advantage: "盈利质量相对较强。", primary_risk: "风险维度相对稳健。",
        score: 86, coverage: 0.95,
      }],
    }), { status: 200 })));

    render(<ResearchHomePage />);

    expect(await screen.findByText("研究样本1（000001.SZ）")).toBeInTheDocument();
    expect(screen.getByText("核心优势：盈利质量相对较强。")).toBeInTheDocument();
  });

  it("does not turn insufficient data into a zero-valued candidate table", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: "INSUFFICIENT_DATA", reason: "暂时没有可研究的候选。", rows: [],
      total: 0, current_page: 1, page_count: 1, page_size: 10,
    }), { status: 200 })));

    render(<CandidatePoolPage />);

    expect(await screen.findByText("暂时没有可研究的候选。")).toBeInTheDocument();
    expect(screen.queryByText("0.00")).not.toBeInTheDocument();
  });
});
