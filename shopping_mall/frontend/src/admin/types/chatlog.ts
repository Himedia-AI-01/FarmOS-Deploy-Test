export interface ChatLog {
  id: number;
  user_id: number | null;
  session_id: number | null;
  intent: string;
  question: string;
  answer: string;
  escalated: boolean;
  created_at: string;
}

export interface ChatSession {
  id: number;
  user_id: number;
  title: string | null;
  status: 'active' | 'closed';
  log_count: number;
  last_question: string | null;
  last_message_at: string | null;
  has_escalation: boolean;
  /** 이 세션에서 생성된 미처리 티켓의 최고 심각도. null=없음 */
  pending_ticket_status: 'received' | 'processing' | null;
  created_at: string;
  updated_at: string;
}
