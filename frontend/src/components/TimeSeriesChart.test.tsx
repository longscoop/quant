import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { TimeSeriesChart } from "./TimeSeriesChart";
afterEach(cleanup);
it("keeps date/value alignment, exposes missing benchmarks, and commits slider selection", () => {
  const select = vi.fn();
  render(<TimeSeriesChart title="收益" onSelectDate={select} series={[{ label: "策略", color: "blue", points: [{ date: "2024-01-01", value: 1 }, { date: "2024-01-02", value: 1.1 }, { date: "2024-01-03", value: 1.05 }] }, { label: "基准", color: "orange", points: [{ date: "2024-01-01", value: 1 }, { date: "2024-01-03", value: 1.02 }] }]} />);
  fireEvent.change(screen.getByRole("slider", { name: "收益日期" }), { target: { value: "1" } });
  expect(select).toHaveBeenCalledWith("2024-01-02");
  expect(screen.getByText("策略：1.100")).toBeInTheDocument();
  expect(screen.getByText("基准：--")).toBeInTheDocument();
});
