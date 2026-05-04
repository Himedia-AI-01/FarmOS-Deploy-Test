/**
 * 공익직불 (정부 지원금) API 클라이언트.
 *
 * 백엔드 엔드포인트:
 *   GET  /api/v1/subsidy/match              자격 매칭 (eligible/ineligible/needs_review)
 *   POST /api/v1/subsidy/ask                자연어 질의응답 (RAG + LLM)
 *   GET  /api/v1/subsidy/detail/{code}      지원금 상세 정보
 */

import { API_BASE as ROOT_API_BASE } from '@/utils/api';

const API_BASE = `${ROOT_API_BASE}/subsidy`;

export type EligibilityStatus = 'eligible' | 'ineligible' | 'needs_review';

export interface EligibilityResult {
  subsidy_code: string;
  subsidy_name: string;
  status: EligibilityStatus;
  reasons: string[];
  estimated_amount_krw: number | null;
  source_articles: string[];
}

export interface MatchResponse {
  user_id: string;
  eligible: EligibilityResult[];
  ineligible: EligibilityResult[];
  needs_review: EligibilityResult[];
}

export interface Citation {
  article: string;
  chapter: string;
  snippet: string;
  similarity: number;
}

export interface SubsidyAskResponse {
  question: string;
  answer: string;
  citations: Citation[];
  escalation_needed: boolean;
}

export interface SubsidyDetail {
  id: number;
  code: string;
  name_ko: string;
  category: string;
  description: string;
  min_area_ha: number;
  max_area_ha: number | null;
  requires_promotion_area: boolean | null;
  requires_farm_registration: boolean;
  min_rural_residence_years: number;
  min_farming_years: number;
  eligible_farmland_types: string[];
  eligible_farmer_types: string[];
  payment_structure: Record<string, unknown>;
  source_articles: string[];
  payment_amount_krw: number | null;
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
    ...options,
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status}: ${text || response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export function fetchMatch(): Promise<MatchResponse> {
  return request<MatchResponse>('/match');
}

export function askSubsidy(question: string, subsidyCode?: string): Promise<SubsidyAskResponse> {
  return request<SubsidyAskResponse>('/ask', {
    method: 'POST',
    body: JSON.stringify({ question, subsidy_code: subsidyCode }),
  });
}

// NOTE: /subsidy/detail/{code} 엔드포인트는 백엔드에서 제공되고 있으나
// 현재 UI 는 /match 응답의 데이터만으로 드로어를 그리므로 클라이언트 헬퍼는 미사용.
// 추후 상세 정보(payment_structure 원문 등)를 드로어에 노출하게 되면 복원.
