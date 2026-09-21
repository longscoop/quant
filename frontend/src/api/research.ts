import { getJson } from "./client";

export type ResearchCandidate = {
  name: string;
  code: string;
  industry: string;
  tendency: string;
  confidence: string;
  risk: string;
  as_of_date: string | null;
  core_advantage: string;
  primary_risk: string;
  score: number | null;
  coverage: number | null;
};

export type ResearchHome = {
  status: string;
  reason: string | null;
  freshness: {
    latest_trade_date: string;
    research_snapshot_date: string;
    security_count: number;
    state: string;
    reason: string;
  };
  candidates: ResearchCandidate[];
};

export type CandidatePage = {
  status: string;
  reason: string | null;
  rows: ResearchCandidate[];
  total: number;
  current_page: number;
  page_count: number;
  page_size: number;
};

export type CandidateQuery = Record<string, string | number | undefined>;

export function getResearchHome(): Promise<ResearchHome> {
  return getJson<ResearchHome>("/api/v1/research/home");
}

export function getCandidates(query: CandidateQuery = {}): Promise<CandidatePage> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const suffix = params.size ? `?${params}` : "";
  return getJson<CandidatePage>(`/api/v1/research/candidates${suffix}`);
}
