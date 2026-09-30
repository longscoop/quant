import { getJson, postJson } from "./client";

export type CurvePoint = { date: string; value: number };
export type Adjustment = {
  date?: string | null; execution_date?: string | null; ts_code?: string | null; name?: string | null;
  action?: string | null; side?: string | null; deferred?: boolean | null;
  market_open?: number | null; price_status?: string | null; price?: number | null;
  weight?: number | null; weight_before?: number | null; weight_after?: number | null; trade_weight?: number | null;
  quantity?: number | null; quantity_before?: number | null; quantity_after?: number | null; cost?: number | null;
  first_buy_date?: string | null; last_buy_date?: string | null; last_buy_price?: number | null;
  holding_shares?: number | null; traded_shares?: number | null; average_cost?: number | null;
  holding_return?: number | null; holding_value?: number | null; trade_amount?: number | null; fee_amount?: number | null;
};
export type TradingNote = { kind: string; ts_code: string; name: string | null; start_date: string; end_date: string; session_count: number; reason: string | null; resume_date: string | null; resolution_date: string | null };
export type TargetSimulation = {
  status: string; as_of_date: string; holding_date: string; template_id: string; top_n: number; saved_at: string | null;
  initial_capital: number; portfolio_value: number; cash_before: number; estimated_cost: number; estimated_cash_after: number; execution_rule: string;
  rows: { ts_code: string; name: string | null; action: string; reference_price: number; holding_shares: number; target_shares: number; change_shares: number; current_weight: number; target_weight: number; estimated_amount: number; estimated_fee: number; signal_day_note: string | null }[];
};
export type ValidationResult = {
  experiment_id?: string | null;
  initial_capital?: number;
  capital_is_display_assumption?: boolean;
  created_at?: string | null;
  trading_notes?: TradingNote[];
  target_simulations?: TargetSimulation[];
  experiment_name: string;
  template_id: string | null;
  start_date: string | null;
  end_date: string | null;
  top_n: number | null;
  cost_bps: number | null;
  coverage: { requested_periods: number | null; valid_periods: number | null; skipped_periods: number | null };
  skipped_periods: { date: string | null; exit_date: string | null; reason: string | null }[];
  metrics?: Record<string, number | null>;
  equity_curve?: CurvePoint[];
  benchmark_curve?: CurvePoint[];
  excess_curve?: CurvePoint[];
  annual_returns?: { year: number; strategy: number | null; benchmark: number | null }[];
  trades?: Record<string, string | number | boolean | null>[];
  adjustments?: Adjustment[];
  valuation_audit?: Record<string, string | number | null>[];
  order_audit?: Record<string, string | number | null>[];
};
export type ValidationResponse = { status: string; reason: string | null; result: ValidationResult | null };
export type ValidationPage = {
  latest_market_date?: string | null;
  templates: { id: string; name: string; description?: string; weights?: Record<string, number>; version?: string }[];
  latest: ValidationResponse;
  history: { actual_start_date?: string | null; actual_end_date?: string | null; metrics?: Record<string, number | null>; experiment_id?: string | null; experiment_name: string; template_id: string | null; status: string; top_n: number | null; created_at: string | null; start_date?: string | null; end_date?: string | null; initial_capital?: number }[];
};
export type ValidationRequest = {
  template_id: string;
  experiment_name: string;
  start_date: string;
  end_date: string;
  top_n: number;
  cost_bps: number;
  initial_capital?: number;
};

export const getValidationPage = () => getJson<ValidationPage>("/api/v1/backtests/factor?include_latest=false");
export const checkValidationData = (start_date: string, end_date: string) => getJson<{ start_date: string; end_date: string; complete_periods: number; cached_snapshots: number; status: string }>(`/api/v1/backtests/factor/check?${new URLSearchParams({ start_date, end_date })}`);
export const runValidation = (request: ValidationRequest) => postJson<ValidationResponse>("/api/v1/backtests/factor", request);
export const getValidationExperiment = (id: string) => getJson<ValidationResponse>(`/api/v1/backtests/factor/experiments/${encodeURIComponent(id)}`);
export const simulateValidationTarget = (id: string, request: { as_of_date: string; template_id: string; top_n: number }) => postJson<TargetSimulation>(`/api/v1/backtests/factor/experiments/${encodeURIComponent(id)}/target-simulation`, request);

export type DayHoldings = { date: string; cash: number; total_value: number; status: string; rows: { ts_code: string; name: string | null; first_buy_date: string | null; last_buy_date: string | null; last_buy_price: number | null; holding_shares: number | null; average_cost: number | null; holding_return: number | null; holding_value: number | null; reference_price: number | null }[] };
export const getDayHoldings = (id: string, day: string) => getJson<DayHoldings>(`/api/v1/backtests/factor/experiments/${encodeURIComponent(id)}/holdings?${new URLSearchParams({ as_of_date: day })}`);
