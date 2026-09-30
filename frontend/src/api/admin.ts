import { getJson, postJson } from "./client";

export type AdminRun = {
  run_id: string | null; run_type: string | null; status: string | null;
  created_at: string | null; completed_at: string | null; error: string | null;
  retry_parameters: Record<string, unknown> | null;
};
export type AdminOverview = {
  quality: { latest_trade_date: string | null; is_complete: boolean; is_stale: boolean;
    security_count: number | null; missing_latest_price_count: number; missing_financial_count: number; valuation_count: number | null };
  steps: { id: string; title: string; state: string; summary: string; next_step: string; can_run: boolean }[];
  factor_runs: { run_id: string; date_end: string | null }[];
  runs: AdminRun[];
};
export const getCapabilities = () => getJson<{ admin_enabled: boolean }>("/api/v1/capabilities");
export const getAdminOverview = () => getJson<AdminOverview>("/api/v1/admin/overview");
export const runSync = (start_date: string, end_date: string, token: string) => postJson<AdminRun>("/api/v1/admin/sync", { start_date, end_date, token });
export const runFactors = (cutoff: string) => postJson<AdminRun>("/api/v1/admin/factors", { cutoff });
export const runModel = (request: Record<string, string>) => postJson<AdminRun>("/api/v1/admin/model", request);
