import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AdjustmentLedger, formatPrice, formatWeight } from "./AdjustmentLedger";
import type { Adjustment } from "../../api/validation";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const fill: Adjustment = { date: "2023-01-31", execution_date: "2023-02-01", ts_code: "002001.SZ", name: "新和成", action: "OPEN", market_open: 19.5, price: 829.7835, quantity: .00008026197990206678, weight_before: 0, weight_after: 1 / 15, trade_weight: .0666, weight: 1 / 15, price_status: "verified", holding_shares: 3415.388, first_buy_date: "2023-02-01", last_buy_date: "2023-02-01", last_buy_price: 19.5, average_cost: 19.5, holding_return: 0, holding_value: 66600.0666, traded_shares: 3415.388, trade_amount: 66600.0666, fee_amount: 66.6 };

describe("adjustment ledger", () => {
  it("shows the actual raw open, name and weights without presenting normalized units as shares", () => {
    render(<AdjustmentLedger rows={[fill]} />);
    const table = screen.getByRole("table", { name: "历史调整" });
    expect(within(table).getByRole("link", { name: "新和成" })).toHaveAttribute("href", "/stocks/002001.SZ");
    expect(within(table).getByText("19.50")).toBeInTheDocument();
    expect(within(table).getByText("6.67%")).toBeInTheDocument();
    expect(within(table).queryByText("829.7835")).not.toBeInTheDocument();
    expect(table.textContent).not.toContain("0.00008026197990206678");
    expect(within(table).queryByRole("columnheader", { name: "数量" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "展开新和成持仓详情" }));
    expect(screen.getByText("持仓成本价（元）")).toBeVisible();
    expect(screen.getByText("首次买入日期")).toBeVisible();
    expect(screen.getAllByText("3,415.39").length).toBeGreaterThan(0);
    expect(screen.queryByText("复权计算价")).not.toBeInTheDocument();
    expect(formatPrice(null)).toBe("--");
    expect(formatPrice(NaN)).toBe("--");
    expect(formatPrice(150.73564000000002)).toBe("150.74");
    expect(formatWeight(-1.11e-16)).toBe("0.00%");
    expect(formatWeight(null)).toBe("--");
  });

  it("defaults to the latest period and supports search, filters, pagination and clearing empty results", () => {
    const rows: Adjustment[] = [fill, ...Array.from({ length: 32 }, (_, index) => ({ ...fill, date: "2023-02-28", name: `测试股票${index}`, ts_code: `CODE${index}`, action: index === 20 ? "HOLD" : "INCREASE", deferred: index === 20 }))];
    render(<AdjustmentLedger rows={rows} />);
    expect(screen.getByLabelText("调仓期")).toHaveValue("2023-02-28");
    expect(screen.getByRole("table", { name: "历史调整" }).querySelectorAll("tbody tr")).toHaveLength(15);
    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(screen.getByText("第 2 / 3 页")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("搜索股票"), { target: { value: "CODE20" } });
    expect(screen.getByText("第 1 / 1 页")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "测试股票20" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("调仓动作"), { target: { value: "OPEN" } });
    expect(screen.getByText("没有符合条件的调仓记录")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    fireEvent.click(screen.getByLabelText("仅看延期"));
    expect(screen.getByText("筛选后 1 条 · 每页 15 条")).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText("仅看延期"));
    fireEvent.change(screen.getByLabelText("搜索股票"), { target: { value: "新和成" } });
    expect(screen.getByRole("link", { name: "新和成" })).toBeInTheDocument();
  });

  it("keeps unverified raw prices missing and preserves legacy buy direction", () => {
    render(<AdjustmentLedger rows={[{ ...fill, action: null, side: "BUY", market_open: null, price_status: "mismatch", weight_before: null, weight_after: null }]} />);
    expect(screen.getByText("买入 · 动作未记录")).toBeInTheDocument();
    expect(screen.getByText(/部分成交缺少可核验/)).toBeInTheDocument();
    expect(screen.queryByText("19.50")).not.toBeInTheDocument();
    expect(screen.queryByText("829.78")).not.toBeInTheDocument();
  });
});
