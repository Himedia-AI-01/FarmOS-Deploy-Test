import { useState, useEffect, useRef } from 'react';
import { useUserStore } from '@/stores/userStore';
import { useActiveSession, useCreateSession } from './useChatSession.ts';
import ChatSessionList from './ChatSessionList.tsx';
import ChatMessageView from './ChatMessageView.tsx';
import GuestChatWidget from './GuestChatWidget.tsx';
import type { ChatView } from './types.ts';

export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [view, setView] = useState<ChatView>('list');
  const [activeSessionId, setActiveSessionId] = useState<number | null>(null);
  const { user } = useUserStore();
  const userId = user?.shop_user_id ?? null;
  const wasOpenRef = useRef(false);
  // 위젯이 열릴 때 초기화 의도를 보관 — activeSessionLoading이 true인 동안에도 유지
  const shouldInitRef = useRef(false);

  // Fetch active session on mount/user change (회원만)
  const { data: activeSession, isLoading: activeSessionLoading } = useActiveSession(userId);
  const { mutate: createSession } = useCreateSession();

  // Step 1: 위젯이 열리는 순간 초기화 의도를 표시
  useEffect(() => {
    if (open && !wasOpenRef.current) {
      shouldInitRef.current = true;
    }
    wasOpenRef.current = open;
  }, [open]);

  // Step 2: 초기화 의도가 있고 로딩이 완료되면 실제로 chat view로 전환
  // activeSessionLoading이 true → false로 바뀔 때도 트리거되므로 race condition 없음
  useEffect(() => {
    if (!shouldInitRef.current || !open || activeSessionLoading || !userId) return;

    shouldInitRef.current = false;

    if (activeSession) {
      setActiveSessionId(activeSession.id);
      setView('chat');
    } else if (activeSessionId === null) {
      createSession(userId, {
        onSuccess: (newSession) => {
          setActiveSessionId(newSession.id);
          setView('chat');
        },
      });
    }
  }, [open, userId, activeSessionLoading, activeSession, activeSessionId, createSession]);

  // Reset to list view when widget is closed
  const handleClose = () => {
    setOpen(false);
    // Keep view and activeSessionId for next open
  };

  // 비회원은 GuestChatWidget 사용
  if (!userId) {
    return <GuestChatWidget />;
  }

  return (
    <>
      {open && (
        <div className="fixed bottom-24 right-6 w-[430px] max-w-[calc(100vw-1.5rem)] h-[660px] max-h-[calc(100vh-6rem)] bg-white rounded-2xl shadow-2xl border border-gray-200 flex flex-col z-50 overflow-hidden">
          {view === 'list' ? (
            <ChatSessionList
              userId={userId}
              onSessionSelect={(sessionId) => {
                setActiveSessionId(sessionId);
                setView('chat');
              }}
              onClose={handleClose}
            />
          ) : activeSessionId !== null ? (
            <ChatMessageView
              sessionId={activeSessionId}
              userId={userId}
              onBackClick={() => {
                setView('list');
              }}
            />
          ) : (
            <div className="flex flex-col h-full items-center justify-center bg-gray-50">
              <div className="animate-spin w-8 h-8 border-4 border-gray-200 border-t-[#03C75A] rounded-full mb-4" />
              <p className="text-gray-600 text-sm">세션 생성 중...</p>
            </div>
          )}
        </div>
      )}

      {/* Floating Button */}
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
