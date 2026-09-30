import { getJson, postJson } from "./client";

export type PortfolioSummary = { portfolio_id: string; name: string; status: string };
export type PortfolioBacktest = {
  status: string; reason: string | null;
  metrics?: { total_return: number | null; annualized_return: number | null; max_drawdown: number | null; sharpe: number | null };
  equity_curve?: { date: string; value: number }[];
  benchmark_curve?: { date: string; value: number }[];
};
export type PortfolioDashboard = {
  portfolio: { portfolio_id: string; name: string; initial_capital: number; transaction_cost_bps: number; benchmark_code: string; status: string };
  metrics: { total_return: number | null; max_drawdown: number | null };
  valuation: { valuation_date: string | null; status: string | null; cash: number | null; total_value: number | null };
  valuation_date: string | null;
  positions: { first_buy_date?: string | null; holding_shares?: number | null; market_price?: number | null; buy_cost?: number | null; holding_return?: number | null; ts_code: string; name: string; industry: string; quantity: number; target_weight: number; current_weight: number | null; average_cost: number | null; current_price: number | null; unrealized_pnl: number | null; realized_pnl: number | null; research_score: number | null; research_coverage: number | null }[];
  draft_positions: { code: string; name?: string | null; weight: number }[];
  target_revision: { revision_no: number; signal_date: string; status: string } | null;
  target_revisions: { revision_id: string; revision_no: number; signal_date: string; status: string }[];
  orders: { name?: string | null; market_price?: number | null; traded_shares?: number | null; revision_no: number | null; signal_date: string | null; planned_trade_date: string | null; actual_trade_date: string | null; code: string; side: string; target_weight: number | null; quantity: number | null; price: number | null; fee: number | null; status: string; reason: string | null }[];
  industry_exposure: Record<string, number>;
  factor_exposure: Record<string, { value: number | null; coverage: number | null }>;
  factor_snapshot: { as_of_date: string; factor_version: string; pit_version: string } | null;
  evidence_coverage: number | null;
  research_scores: Record<string, number | null>;
  nav_rows: { date: string; nav: number; benchmark_nav: number | null }[];
  backtest: PortfolioBacktest | null;
};

const root = "/api/v1/portfolios";
const path = (id: string) => `${root}/${encodeURIComponent(id)}`;
export const getPortfolioList = () => getJson<{ portfolios: PortfolioSummary[] }>(root);
export const getPortfolioDashboard = (id: string) => getJson<PortfolioDashboard>(path(id));
export const createPortfolio = (name: string, initial_capital: number, transaction_cost_bps: number) => postJson<{ portfolio_id: string }>(root, { name, initial_capital, transaction_cost_bps });
export const copyPortfolio = (id: string) => postJson<{ portfolio_id: string }>(`${path(id)}/copy`, {});
export const archivePortfolio = (id: string) => postJson<{ status: string }>(`${path(id)}/archive`, {});
export const saveTargets = (id: string, targets: Record<string, number>) => postJson<{ revision_id: string }>(`${path(id)}/targets`, { targets });
export const addDraftSecurity = (id: string, code: string) => postJson<{ status: string }>(`${path(id)}/draft-securities`, { code });
export const recordCashFlow = (id: string, flow_date: string, amount: number, note: string) => postJson<{ status: string }>(`${path(id)}/cash-flows`, { flow_date, amount, note });
export const runPortfolioBacktest = (id: string, revision_id: string, start_date: string, end_date: string, cost_bps: number) => postJson<PortfolioBacktest>(`${path(id)}/backtests`, { revision_id, start_date, end_date, cost_bps });
