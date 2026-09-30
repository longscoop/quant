import { getJson } from "./client";

export type StockOption = { code: string; name: string };
export type StockDetail = {
  status: string; reason: string | null; name: string; code: string; industry: string;
  as_of_date: string; tendency: string; confidence: string; risk: string;
  core_advantage: string; primary_risk: string; sufficiency: string;
  factor_score: number | null; coverage: number | null; model_score: number | null;
  model_date: string; factor_date: string; evidence_status: string; evidence_reason: string;
  model_version: string; factor_version: string; factor_model_version: string; factor_data_version: string;
  evidence_groups: { 类别: string; 方向: string; 说明: string; 局限: string }[];
  factor_evidence: Record<string, string | number | null>[];
  financial_disclosures: Record<string, string>[];
  valuation_trends: Record<string, { 数据日期: string; 数值: number }[]>;
  relative_history: { status: string; reason: string; rows: { date: string; stock: number; benchmark: number }[] };
};
export type IndustryPage = {
  status: string; reason: string | null; as_of_date: string | null;
  rows: { name: string; security_count: number; candidate_count: number; label: string;
    relative_20d: number | null; relative_60d: number | null; members: StockOption[] }[];
};

export const getStockOptions = (query: string) => getJson<{ stocks: StockOption[] }>(`/api/v1/research/stocks?${new URLSearchParams({ query, limit: "20" })}`);
export const getStockDetail = (code: string) => getJson<StockDetail>(`/api/v1/research/stocks/${encodeURIComponent(code)}`);
export const getIndustryPage = () => getJson<IndustryPage>("/api/v1/research/industries");
