// 실제 백엔드 intent 값 기준 (ai/agent/cs_tools.py TOOL_TO_INTENT)
// delivery | faq | stock | cancel | escalation | policy | refusal | other | greeting

export const INTENT_LABEL: Record<string, string> = {
  delivery:   '배송·조회',
  faq:        '자주 묻는 질문',
  stock:      '상품·재고',
  cancel:     '취소·환불',
  escalation: '처리 불가',
  policy:     '정책·약관',
  refusal:    '거절됨',
  other:      '기타',
  greeting:   '인사',
};

// CsInsightsPage에서 사용 (이모지 없음)
export const INTENT_LABEL_FULL: Record<string, string> = INTENT_LABEL;

export const INTENT_COLOR_BADGE: Record<string, string> = {
  delivery:   'bg-emerald-100 text-emerald-700',
  faq:        'bg-sky-100 text-sky-700',
  stock:      'bg-teal-100 text-teal-700',
  cancel:     'bg-amber-100 text-amber-700',
  escalation: 'bg-rose-100 text-rose-700',
  policy:     'bg-violet-100 text-violet-700',
  refusal:    'bg-stone-100 text-stone-600',
  other:      'bg-stone-100 text-stone-500',
  greeting:   'bg-emerald-50 text-emerald-600',
};

export const INTENT_COLOR_BAR: Record<string, string> = {
  delivery:   'bg-emerald-500',
  faq:        'bg-sky-400',
  stock:      'bg-teal-500',
  cancel:     'bg-amber-400',
  escalation: 'bg-rose-500',
  policy:     'bg-violet-400',
  refusal:    'bg-stone-400',
  other:      'bg-stone-300',
  greeting:   'bg-emerald-300',
};

export const INTENT_ICON: Record<string, string> = {
  delivery:   'local_shipping',
  faq:        'help',
  stock:      'inventory_2',
  cancel:     'cancel',
  escalation: 'support_agent',
  policy:     'gavel',
  refusal:    'block',
  other:      'chat_bubble',
  greeting:   'waving_hand',
};
