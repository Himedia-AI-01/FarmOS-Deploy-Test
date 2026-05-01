// Chat related types

export interface ChatSession {
  id: number;
  userId: number;
  status: 'active' | 'closed';
  title: string | null;
  createdAt: string;
  updatedAt: string;
  closedAt: string | null;
  messageCount?: number;
  messagePreview?: string;
}

export interface ChatMessage {
  role: 'user' | 'bot';
  text: string;
  intent?: string;
  escalated?: boolean;
  createdAt?: string;
}

export interface ChatSessionMessage {
  role: 'user' | 'bot';
  text: string;
  intent?: string;
  escalated?: boolean;
  createdAt?: string;
  /** DB chat_log.id — 피드백 제출에 사용 */
  chat_log_id?: number;
  /** 이 응답에서 인용된 FAQ 문서 ID 목록 */
  cited_faq_ids?: number[];
}

export type ChatView = 'list' | 'chat';
