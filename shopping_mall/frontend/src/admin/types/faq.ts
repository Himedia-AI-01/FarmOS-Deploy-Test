// ──────────────────────────────────────────
// FAQ Category Types
// ──────────────────────────────────────────

export interface FaqCategory {
  id: number;
  name: string;
  slug: string;
  description: string | null;
  color: string;       // Tailwind classes, e.g. "bg-blue-100 text-blue-700"
  icon: string;        // Material Symbols icon name
  sort_order: number;
  is_active: boolean;
  doc_count: number;   // 활성 FAQ 문서 수
  created_at: string;
  updated_at: string;
}

export interface FaqCategoryCreate {
  name: string;
  slug: string;
  description?: string | null;
  color?: string;
  icon?: string;
  sort_order?: number;
}

export interface FaqCategoryUpdate {
  name?: string;
  description?: string | null;
  color?: string;
  icon?: string;
  sort_order?: number;
  is_active?: boolean;
}

// ──────────────────────────────────────────
// FAQ Doc Types
// ──────────────────────────────────────────

export interface FaqDoc {
  id: number;
  faq_category_id: number | null;
  faq_category_name: string | null;
  faq_category_slug: string | null;
  chroma_collection: string;
  chroma_doc_id: string;
  title: string;
  content: string;
  extra_metadata: Record<string, unknown> | null;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  // 애널리틱스
  citation_count: number;
}

export interface FaqDocCreate {
  faq_category_id?: number | null;
  title: string;
  content: string;
  extra_metadata?: Record<string, unknown> | null;
}

export interface FaqDocUpdate {
  faq_category_id?: number | null;
  title?: string;
  content?: string;
  extra_metadata?: Record<string, unknown> | null;
  is_active?: boolean;
}

// ──────────────────────────────────────────
// UI helpers — preset palette for categories
// ──────────────────────────────────────────

export const PRESET_COLORS: { label: string; value: string }[] = [
  { label: '에메랄드', value: 'bg-emerald-100 text-emerald-700' }, // primary — 배송·친환경
  { label: '하늘',     value: 'bg-sky-100 text-sky-700' },         // cool blue — 결제·계정
  { label: '보라',     value: 'bg-violet-100 text-violet-700' },   // purple — 멤버십·이벤트
  { label: '로즈',     value: 'bg-rose-100 text-rose-700' },       // tertiary — 교환·반품·CS
  { label: '황금',     value: 'bg-amber-100 text-amber-700' },     // warm — 제철·주의
  { label: '청록',     value: 'bg-teal-100 text-teal-700' },       // teal — 보관·신선
  { label: '회색',     value: 'bg-stone-100 text-stone-700' },     // neutral — 일반·기타
];

// ──────────────────────────────────────────
// FAQ Analytics Types
// ──────────────────────────────────────────

export interface UnansweredSample {
  id: number;
  question: string;
  intent: string;
  created_at: string;
}

export interface LeastCitedFaqItem {
  id: number;
  title: string;
  category_name: string | null;
  category_slug: string | null;
  citation_count: number;
  created_at: string;
}

export interface TopCitedFaqItem {
  id: number;
  title: string;
  citation_count: number;
  category_name: string | null;
}

export interface FaqActionSummary {
  total_docs: number;
  active_docs: number;
  unanswered_count: number;
  underperforming_count: number;
}

export interface TrendingQuestionItem {
  intent: string;
  intent_label: string;
  count: number;
  sample_question: string;
}

export interface TrendingQuestionsResponse {
  period_days: number;
  total_questions: number;
  items: TrendingQuestionItem[];
}

// ──────────────────────────────────────────
// FAQ 등록 추천 (faq-recommendations)
// ──────────────────────────────────────────

/** 갭 유형: FAQ 자체가 없었던 경우 vs 처리 완전 불가 */
export type FaqGapType = 'missing' | 'escalated';

export interface FaqRecommendationItem {
  rank: number;
  representative_question: string; // 대표 질문 원문 (가장 최근)
  count: number;                   // 같은 질문이 들어온 총 수
  recent_count: number;            // 최근 7일 수
  escalated_count: number;
  gap_type: FaqGapType;
  score: number;
  top_intent: string;
  top_intent_label: string;        // 주요 intent 한글 레이블
}

export interface FaqRecommendationsResponse {
  period_days: number;
  total_gap_questions: number;
  items: FaqRecommendationItem[];
}

// ──────────────────────────────────────────
// FAQ 초안 자동 생성 (AI draft generation)
// ──────────────────────────────────────────

export interface FaqDraftRequest {
  representative_question: string;
  top_intent: string;
  gap_type: FaqGapType;
  count: number;
  escalated_count: number;
}

export interface FaqDraftResponse {
  title: string;
  content: string;
  suggested_category_id: number | null;
  suggested_category_slug: string | null;
  model_used: string;
  citation_doc: string | null;
  citation_chapter: string | null;
  citation_article: string | null;
  citation_clause: string | null;
}

export interface PolicyArticleItem {
  chapter: string;  // "제1장 반품·교환·환불" or "" for uncategorized
  article: string;
  clauses: string[];
}

// ──────────────────────────────────────────

export const PRESET_ICONS: { label: string; value: string }[] = [
  { label: '물음표',    value: 'help' },
  { label: '배송',      value: 'local_shipping' },
  { label: '결제',      value: 'payments' },
  { label: '교환/반품', value: 'swap_horiz' },
  { label: '보관',      value: 'ac_unit' },
  { label: '제철',      value: 'eco' },
  { label: '농장',      value: 'agriculture' },
  { label: '별',        value: 'star' },
  { label: '정보',      value: 'info' },
];
