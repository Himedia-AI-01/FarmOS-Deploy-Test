import { useState, useRef, useEffect } from 'react';
import { useMutation } from '@tanstack/react-query';
import api from '@/lib/api';
import { INTENT_LABEL } from '@/admin/constants/chatbot';
import { renderChatText } from './ChatTextRenderer';

interface Message {
  id: string;
  role: 'user' | 'bot';
  text: string;
  intent?: string;
  escalated?: boolean;
}

const STORAGE_KEY = 'chat_session_guest';
const GUEST_ID_KEY = 'guest_chat_id';
const WELCOME: Message = { id: 'welcome', role: 'bot', text: '안녕하세요! FarmOS 마켓 고객지원입니다.\n무엇이든 물어보세요 😊' };

function getOrCreateGuestId(): number {
  const existing = sessionStorage.getItem(GUEST_ID_KEY);
  if (existing) return parseInt(existing, 10);
  // Generate a temporary guest ID for this session
  const guestId = Math.floor(Math.random() * 1000000);
  sessionStorage.setItem(GUEST_ID_KEY, guestId.toString());
  return guestId;
}

const QUICK_ACTIONS = [
  { id: 'delivery', label: '📦 배송 조회', intent: 'delivery', text: '배송 현황을 알고 싶어요' },
  { id: 'stock', label: '🍎 재고 확인', intent: 'stock', text: '재고 확인해 주세요' },
  { id: 'storage', label: '❄️ 보관 방법', intent: 'storage', text: '상품 보관 방법이 궁금해요' },
  { id: 'exchange', label: '↩️ 교환', intent: 'exchange', text: '교환하고 싶어요' },
  { id: 'refund', label: '💳 환불', intent: 'cancel', text: '환불하고 싶어요' },
  { id: 'season', label: '🌸 제철 상품', intent: 'season', text: '요즘 제철 상품이 뭔가요?' },
];

function loadGuestSession(): Message[] {
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [WELCOME];
  } catch {
    return [WELCOME];
  }
}

function saveGuestSession(messages: Message[]) {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
  } catch {}
}

export default function GuestChatWidget() {
  const [open, setOpen] = useState(false);
  const [messages, setMessages] = useState<Message[]>([WELCOME]);
  const [initialized, setInitialized] = useState(false);
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // 초기 로드 (sessionStorage)
  useEffect(() => {
    setMessages(loadGuestSession());
    setInitialized(true);
  }, []);

  // initialized 이후 메시지 변경 시 sessionStorage 저장
  useEffect(() => {
    if (initialized) {
      saveGuestSession(messages);
    }
  }, [messages, initialized]);

  // 메시지 전송
  const { mutate: ask, isPending } = useMutation({
    mutationFn: async ({ question, intent }: { question: string; intent?: string }) => {
      const guestId = getOrCreateGuestId();
      const { data } = await api.post('/api/chatbot/ask', {
        question,
        user_id: null, // 비회원
        session_id: null, // 비회원은 세션 없음
        history: messages.slice(-4).map(({ role, text }) => ({ role, text })),
        ...(intent ? { intent } : {}),
      }, {
        headers: {
          'X-User-Id': guestId.toString(),
        },
      });
      return data as { answer: string; intent: string; escalated: boolean };
    },
    onSuccess: (data) => {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'bot', text: data.answer, intent: data.intent, escalated: data.escalated },
      ]);
    },
    onError: () => {
      setMessages((prev) => [
        ...prev,
        { id: crypto.randomUUID(), role: 'bot', text: '죄송합니다. 잠시 후 다시 시도해 주세요.' },
      ]);
    },
  });

  // 스크롤
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isPending]);

  // 포커스
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const send = (question: string, intent?: string) => {
    if (!question.trim() || isPending) return;
    setMessages((prev) => [...prev, { id: crypto.randomUUID(), role: 'user', text: question }]);
    setInput('');
    ask({ question, intent });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    send(input);
  };

  return (
    <>
      {open && (
        <div className="fixed bottom-24 right-6 w-[430px] max-h-[min(660px,calc(100dvh-7rem))] bg-white rounded-2xl shadow-2xl border border-gray-200 flex flex-col z-50 overflow-hidden">
          {/* 헤더 */}
          <div className="flex items-center justify-between px-4 py-3 bg-[#03C75A] text-white shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-lg">🤖</span>
              <div>
                <p className="text-sm font-semibold">FarmOS 고객지원</p>
                <p className="text-xs opacity-80">AI 챗봇</p>
              </div>
            </div>
            <button
              onClick={() => setOpen(false)}
              className="text-white/80 hover:text-white text-lg leading-none"
            >
              ✕
            </button>
          </div>

          {/* 메시지 영역 */}
          <div className="flex-1 overflow-y-auto p-4 space-y-3 min-h-0">
            {messages.map((msg) => (
              <div key={msg.id} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
                <div className={`max-w-[82%] ${msg.role === 'bot' ? 'space-y-1' : ''}`}>
                  <div
                    className={`px-3 py-2 rounded-2xl text-sm leading-relaxed whitespace-pre-wrap ${
                      msg.role === 'user'
                        ? 'bg-[#03C75A] text-white rounded-br-sm'
                        : 'bg-gray-100 text-gray-800 rounded-bl-sm'
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
                </div>
              </div>
            ))}
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
            <div ref={bottomRef} />
          </div>

          {/* 빠른 액션 */}
          <div className="px-3 py-2 border-t border-gray-100 flex gap-1.5 overflow-x-auto shrink-0" style={{ scrollbarWidth: 'none' }}>
            {QUICK_ACTIONS.map((action) => (
              <button
                key={action.id}
                onClick={() => send(action.text, action.intent)}
                disabled={isPending}
                className="shrink-0 text-xs px-2.5 py-1.5 rounded-full border border-gray-200 text-gray-600 hover:border-[#03C75A] hover:text-[#03C75A] transition-colors disabled:opacity-40"
              >
                {action.label}
              </button>
            ))}
          </div>

          {/* 입력 */}
          <form onSubmit={handleSubmit} className="px-3 py-3 border-t border-gray-100 flex gap-2 shrink-0">
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
          </form>
        </div>
      )}

      {/* 플로팅 버튼 */}
      <button
        onClick={() => setOpen((v) => !v)}
        className="fixed bottom-6 right-6 w-14 h-14 bg-[#03C75A] text-white rounded-full shadow-lg flex items-center justify-center text-2xl hover:bg-[#02b050] transition-all z-50 hover:scale-105"
        aria-label="고객지원 챗봇"
      >
        {open ? '✕' : '💬'}
      </button>
    </>
  );
}
