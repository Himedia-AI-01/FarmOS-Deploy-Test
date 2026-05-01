export type TicketStatus = 'received' | 'processing' | 'completed' | 'cancelled';
export type TicketActionType = 'cancel' | 'exchange';

export interface TicketItem {
  item_id: number;
  name: string;
  qty: number;
}

function isTicketItem(item: unknown): item is TicketItem {
  if (typeof item !== 'object' || item === null) return false;
  const r = item as Record<string, unknown>;
  return (
    typeof r.item_id === 'number' &&
    typeof r.name === 'string' &&
    typeof r.qty === 'number'
  );
}

/**
 * Backend returns `items` as a JSON string. Use this helper instead of
 * inlining JSON.parse — it validates the array shape and returns null on
 * any parse failure.
 */
export function parseTicketItems(ticket: Pick<Ticket, 'items'>): TicketItem[] | null {
  if (!ticket.items) return null;
  try {
    const parsed: unknown = JSON.parse(ticket.items);
    if (!Array.isArray(parsed)) return null;
    if (!parsed.every(isTicketItem)) return null;
    return parsed;
  } catch {
    return null;
  }
}

export interface Ticket {
  id: number;
  user_id: number;
  session_id: number | null;
  order_id: number;
  action_type: TicketActionType;
  reason: string;
  refund_method: string | null;
  items: string | null; // JSON 배열 문자열 — [{"item_id":1,"name":"딸기","qty":2}]
  status: TicketStatus;
  created_at: string;
  user_name: string | null;
  order_total: number | null;
}

export interface TicketStats {
  received: number;
  processing: number;
  completed: number;
  cancelled: number;
  total: number;
  exchange: number;
  cancel: number;
}

export const TICKET_STATUS_LABEL: Record<TicketStatus, string> = {
  received: '접수됨',
  processing: '처리중',
  completed: '완료',
  cancelled: '취소',
};

export const TICKET_STATUS_COLOR: Record<TicketStatus, string> = {
  received:   'bg-emerald-100 text-emerald-700',  // primary
  processing: 'bg-amber-100 text-amber-700',       // amber — 진행중 범용 관례 유지
  completed:  'bg-emerald-50 text-emerald-800',    // primary (lighter shade)
  cancelled:  'bg-stone-100 text-stone-500',        // neutral
};

export const TICKET_ACTION_LABEL: Record<TicketActionType, string> = {
  cancel: '취소',
  exchange: '교환',
};

export const TICKET_ACTION_COLOR: Record<TicketActionType, string> = {
  cancel:   'bg-rose-50 text-rose-700',     // tertiary (#BD4E61)
  exchange: 'bg-emerald-50 text-emerald-700', // primary (#008542)
};
