import { useState, useRef, useEffect } from 'react';
import { useSessionMessages, useSendMessage, useCloseSession, useGetSession } from './useChatSession.ts';
import { INTENT_LABEL } from '@/admin/constants/chatbot';
import { renderChatText } from './ChatTextRenderer';
import {
  parseOrderFlowMessage,
  type OrderFlowUI,
} from './parseOrderFlowMessage';
import api from '@/lib/api';
import type { ChatSessionMessage } from './types';

const QUICK_ACTIONS = [
  { label: '📦 배송 조회', intent: 'delivery', text: '배송 현황을 알고 싶어요' },
  { label: '🍎 재고 확인', intent: 'stock', text: '재고 확인해 주세요' },
  { label: '❄️ 보관 방법', intent: 'storage', text: '상품 보관 방법이 궁금해요' },
  { label: '↩️ 교환/환불', intent: 'exchange', text: '교환/환불하고 싶어요' },
  { label: '🌸 제철 상품', intent: 'season', text: '요즘 제철 상품이 뭔가요?' },
];

const WELCOME = {
  role: 'bot' as const,
  text: '안녕하세요! FarmOS 마켓 고객지원입니다.\n무엇이든 물어보세요 😊',
};

// ── 인터랙티브 액션 컴포넌트 ──────────────────────────────────────────────────

function OrderFlowActions({
  text,
  onSend,
  disabled,
}: {
  text: string;
  onSend: (value: string) => void;
  disabled: boolean;
}) {
  const [otherInput, setOtherInput] = useState('');
  const [showOtherInput, setShowOtherInput] = useState(false);

  let parsed: OrderFlowUI = null;
  try {
    parsed = parseOrderFlowMessage(text);
  } catch {
    return null;
  }
  if (!parsed) return null;

  const base = 'transition-colors disabled:opacity-40 text-sm font-medium';
  const primary = `${base} px-4 py-1.5 bg-[#03C75A] text-white rounded-full hover:bg-[#02b050]`;
  const outline = `${base} px-4 py-1.5 border border-gray-300 text-gray-700 rounded-full hover:border-[#03C75A] hover:text-[#03C75A]`;
  const chip = `${base} px-3 py-1.5 border border-[#03C75A]/50 text-[#03C75A] rounded-full hover:bg-[#03C75A]/10`;
  const chipGray = `${base} px-3 py-1.5 border border-gray-300 text-gray-600 rounded-full hover:border-[#03C75A] hover:text-[#03C75A]`;
  const card = `${base} w-full text-left px-3 py-2.5 border border-[#03C75A]/30 rounded-xl hover:bg-[#03C75A]/8 text-gray-700`;

  // CS 핸드오프: 교환 / 반품·환불
  if (parsed.type === 'cs-handoff') {
    return (
      <div className="flex gap-2 mt-2">
        <button onClick={() => onSend('교환')} disabled={disabled} className={primary}>
          교환
        </button>
        <button onClick={() => onSend('반품')} disabled={disabled} className={outline}>
          반품·환불
        </button>
      </div>
    );
  }

  // 네 / 아니오
  if (parsed.type === 'confirm') {
    return (
      <div className="flex gap-2 mt-2">
        <button onClick={() => onSend('네')} disabled={disabled} className={primary}>
          네
        </button>
        <button onClick={() => onSend('아니오')} disabled={disabled} className={outline}>
          아니오
        </button>
      </div>
    );
  }

  // 주문 카드 선택
  if (parsed.type === 'order-select') {
    return (
      <div className="flex flex-col gap-1.5 mt-2">
        {parsed.items.map((item) => (
          <button
            key={item.num}
            onClick={() => onSend(item.num)}
            disabled={disabled}
            className={card}
          >
            <span className="font-semibold text-[#03C75A]">{item.orderId}</span>
            {item.summary && (
              <span className="text-gray-600"> · {item.summary}</span>
            )}
            {item.date && (
              <span className="text-gray-400 text-xs"> ({item.date})</span>
            )}
          </button>
        ))}
      </div>
    );
  }

  // 사유 / 방법 선택 (기타 → 입력창)
  if (parsed.type === 'simple-options') {
    return (
      <div className="mt-2">
        <div className="flex flex-wrap gap-1.5">
          {parsed.items.map((item) =>
            item.isOther ? (
              <button
                key={item.num}
                onClick={() => setShowOtherInput(true)}
                disabled={disabled || showOtherInput}
                className={chipGray}
              >
                {item.label}
              </button>
            ) : (
              <button
                key={item.num}
                onClick={() => onSend(item.num)}
                disabled={disabled}
                className={chip}
              >
                {item.label}
              </button>
            )
          )}
        </div>
        {showOtherInput && (
          <div className="flex gap-2 mt-2">
            <input
              type="text"
              value={otherInput}
              onChange={(e) => setOtherInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && otherInput.trim()) onSend(otherInput.trim());
              }}
              placeholder="기타 사유를 입력하세요..."
              autoFocus
              className="flex-1 text-sm border border-gray-200 rounded-full px-3 py-1.5 outline-none focus:border-[#03C75A]"
            />
            <button
              onClick={() => { if (otherInput.trim()) onSend(otherInput.trim()); }}
              disabled={!otherInput.trim() || disabled}
              className="px-3 py-1.5 bg-[#03C75A] text-white text-sm rounded-full disabled:opacity-40"
            >
              전송
            </button>
          </div>
        )}
      </div>
    );
  }

  // 교환 품목 선택 (전체 선택 버튼 + 직접 입력 안내)
  if (parsed.type === 'item-select') {
    return (
      <div className="flex flex-col gap-1.5 mt-2">
        {parsed.items.map((item) => (
          <button
            key={item.num}
            onClick={() => onSend(`${item.num}번 상품 전체`)}
            disabled={disabled}
            className={card}
          >
            {item.label}
            <span className="ml-2 text-[#03C75A] text-xs font-semibold">전체 선택</span>
          </button>
        ))}
        <p className="text-xs text-gray-400 mt-0.5 px-0.5">
          일부 수량만 교환하려면 직접 입력하세요
        </p>
      </div>
    );
  }

  return null;
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────

interface ChatMessageViewProps {
  sessionId: number;
  userId: number | null;
  onBackClick: () => void;
}

export default function ChatMessageView({ sessionId, userId, onBackClick }: ChatMessageViewProps) {
  const { data: sessionMessages = [] } = useSessionMessages(sessionId, userId);
  const { data: session } = useGetSession(sessionId, userId);
  const { mutate: send, isPending } = useSendMessage();
  const { mutate: closeSession, isPending: isClosing } = useCloseSession();
  const [input, setInput] = useState('');
  const [messages, setMessages] = useState<ChatSessionMessage[]>([WELCOME]);
  const messagesContainerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isClosed = session?.status === 'closed';

  const handleCloseSession = () => {
    closeSession(
      { sessionId, userId },
      {
        onSuccess: () => {
          setTimeout(() => { onBackClick(); }, 0);
        },
        onError: () => {
          onBackClick();
        },
      }
    );
  };

  useEffect(() => {
    setMessages(sessionMessages.length === 0 ? [WELCOME] : sessionMessages);
  }, [sessionMessages]);

  useEffect(() => {
    if (messagesContainerRef.current) {
      messagesContainerRef.current.scrollTop = messagesContainerRef.current.scrollHeight;
    }
  }, [messages, isPending]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const handleSend = (question: string, intent?: string) => {
    if (!question.trim() || isPending) return;

    setMessages((prev) => [...prev, { role: 'user', text: question }]);
    setInput('');

    send(
      {
        question,
        userId,
        sessionId,
        history: messages.slice(-4).map(({ role, text, escalated }) => ({ role, text, escalated })),
        intent,
      },
      {
        onSuccess: () => {
          // useSendMessage.onSuccess has already written the bot response to
          // the query cache via setQueryData. The useEffect watching
          // sessionMessages propagates it to local state — no direct
          // setMessages call needed. Removing the optimistic add eliminates
          // the text-equality dedup that incorrectly suppressed legitimate
          // consecutive responses with identical text.
        },
        onError: () => {
          setMessages((prev) => [
            ...prev,
            { role: 'bot', text: '죄송합니다. 잠시 후 다시 시도해 주세요.' },
          ]);
        },
      }
    );
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    handleSend(input);
  };

  const lastIdx = messages.length - 1;

  return (
    <div className="flex flex-col h-full relative">
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-4 bg-[#03C75A] text-white shrink-0 gap-3 sticky top-0 z-20">
        <div className="flex items-start gap-2">
          <button
            onClick={onBackClick}
            className="text-white/80 hover:text-white text-lg leading-none flex-shrink-0 mt-1"
            aria-label="뒤로가기"
          >
            ←
          </button>
          <span className="text-lg flex-shrink-0">🤖</span>
          <div className="min-w-0">
            <p className="text-sm font-semibold">FarmOS 고객지원</p>
            <p className="text-xs opacity-80">{isClosed ? '상담 종료' : 'AI 챗봇'}</p>
          </div>
        </div>
        {!isClosed && (
          <div className="flex items-start gap-2 flex-shrink-0 mt-1">
            <button
              onClick={handleCloseSession}
              disabled={isClosing}
              className="text-xs bg-white/40 hover:bg-white/50 disabled:opacity-50 px-2.5 py-1.5 rounded text-white font-medium transition-colors whitespace-nowrap"
              title="채팅 종료"
            >
              {isClosing ? '종료 중' : '종료'}
            </button>
          </div>
        )}
      </div>

      {/* Messages */}
      <div
        ref={messagesContainerRef}
        className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0 [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-gray-200 [&::-webkit-scrollbar-thumb]:rounded-full"
      >
        {messages.map((msg, idx) => {
          const key = msg.createdAt ? `${msg.role}-${msg.createdAt}` : `${msg.role}-${idx}`;
          // 마지막 봇 메시지에만 인터랙티브 버튼 표시
          const showActions = msg.role === 'bot' && idx === lastIdx && !isClosed;

          return (
            <div key={key} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
              <div className={`${msg.role === 'user' ? 'max-w-[78%]' : 'max-w-[85%]'} ${msg.role === 'bot' ? 'space-y-1' : ''}`}>
                <div
                  className={`px-4 py-3 rounded-2xl text-sm whitespace-pre-wrap ${
                    msg.role === 'user'
                      ? 'leading-relaxed bg-[#03C75A] text-white rounded-br-sm'
                      : 'leading-[1.7] bg-gray-50 text-gray-800 rounded-bl-sm border border-[#03C75A]/30'
                  }`}
                >
                  {msg.role === 'bot' ? renderChatText(msg.text) : msg.text}
                </div>

                {msg.role === 'bot' && msg.intent && (
                  <div className="flex items-center gap-1.5 px-1">
                    <span className="text-xs text-gray-400">
                      [{INTENT_LABEL[msg.intent] ?? msg.intent}]
                    </span>
                    {msg.escalated && (
                      <span className="text-xs text-red-500 font-medium">상담원 연결 필요</span>
                    )}
                  </div>
                )}

                {showActions && (
                  <OrderFlowActions
                    text={msg.text}
                    onSend={handleSend}
                    disabled={isPending}
                  />
                )}

              </div>
            </div>
          );
        })}

        {isPending && (
          <div className="flex justify-start">
            <div className="bg-gray-100 px-4 py-2.5 rounded-2xl rounded-bl-sm">
              <span className="flex gap-1 items-center h-4">
                {[0, 150, 300].map((delay) => (
                  <span
                    key={delay}
                    className="w-1.5 h-1.5 bg-gray-400 rounded-full animate-bounce"
                    style={{ animationDelay: `${delay}ms` }}
                  />
                ))}
              </span>
            </div>
          </div>
        )}
      </div>

      {/* Quick Actions */}
      {!isClosed && (
        <div
          className="px-3 py-2 border-t border-gray-100 flex gap-1.5 overflow-x-auto shrink-0 bg-white"
          style={{ scrollbarWidth: 'thin', scrollbarColor: '#d1d5db #f3f4f6' }}
        >
          {QUICK_ACTIONS.map((action) => (
            <button
              key={action.intent}
              onClick={() => handleSend(action.text, action.intent)}
              disabled={isPending}
              className="shrink-0 text-xs px-2.5 py-1.5 rounded-full border border-gray-200 text-gray-600 hover:border-[#03C75A] hover:text-[#03C75A] transition-colors disabled:opacity-40"
            >
              {action.label}
            </button>
          ))}
        </div>
      )}

      {/* Input */}
      <form onSubmit={handleSubmit} className="px-3 py-3 border-t border-gray-100 shrink-0 bg-white">
        {isClosed ? (
          <div className="flex items-center justify-center py-2 text-gray-500 text-sm">
            이 상담은 종료되었어요
          </div>
        ) : (
          <div className="flex gap-2">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder="메시지를 입력하세요..."
              disabled={isPending}
              className="flex-1 text-sm border border-gray-200 rounded-full px-4 py-2 outline-none focus:border-[#03C75A] transition-colors disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!input.trim() || isPending}
              className="w-9 h-9 bg-[#03C75A] text-white rounded-full flex items-center justify-center shrink-0 hover:bg-[#02b050] transition-colors disabled:opacity-40 text-lg"
            >
              ↑
            </button>
          </div>
        )}
      </form>
    </div>
  );
}
