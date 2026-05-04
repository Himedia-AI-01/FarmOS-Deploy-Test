import { useListSessions, useCreateSession, useActiveSession, useDeleteSession } from './useChatSession.ts';
import { format } from 'date-fns';
import { ko } from 'date-fns/locale';
import { useState } from 'react';
import toast from 'react-hot-toast';

const WELCOME_MESSAGE = '안녕하세요! FarmOS 마켓 고객지원입니다.\n무엇이든 물어보세요 😊';

interface ChatSessionListProps {
  userId: number | null;
  onSessionSelect: (sessionId: number) => void;
  onClose: () => void;
}

export default function ChatSessionList({ userId, onSessionSelect, onClose }: ChatSessionListProps) {
  const { data: sessions = [] } = useListSessions(userId);
  const { data: activeSession } = useActiveSession(userId);
  const { mutate: createSession, isPending: isCreating } = useCreateSession();
  const { mutate: deleteSession, isPending: isDeleting } = useDeleteSession();
  const [menuOpen, setMenuOpen] = useState<number | null>(null);

  const handleInquiry = () => {
    if (!userId) return;

    // If there's already an active session, use it
    if (activeSession) {
      onSessionSelect(activeSession.id);
      return;
    }

    // Otherwise, create a new session
    createSession(userId, {
      onSuccess: (newSession) => {
        onSessionSelect(newSession.id);
      },
      onError: () => {
        toast.error('채팅 세션 생성에 실패했습니다. 잠시 후 다시 시도해주세요.');
      },
    });
  };

  const handleDelete = (sessionId: number) => {
    if (!userId) return;
    deleteSession({ sessionId, userId }, {
      onSuccess: () => {
        setMenuOpen(null);
      },
    });
  };

  const formatTime = (dateString: string) => {
    try {
      return format(new Date(dateString), 'MM-dd HH:mm', { locale: ko });
    } catch {
      return dateString;
    }
  };

  return (
    <div className="flex flex-col h-full bg-white">
      {/* Header with Logo and Info */}
      <div className="px-4 py-4 bg-[#03C75A] text-white shrink-0">
        <div className="flex items-start justify-between mb-3">
          <div className="flex items-center gap-2">
            <span className="text-2xl">🤖</span>
            <div>
              <h2 className="text-base font-bold">FarmOS 고객지원</h2>
              <p className="text-xs opacity-90">AI 챗봇</p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="text-white/80 hover:text-white text-lg leading-none"
            aria-label="닫기"
          >
            ✕
          </button>
        </div>

        {/* Operating Info */}
        <div className="text-xs opacity-90 space-y-1 border-t border-white/20 pt-3">
          <p>⏰ 운영시간: 09:00 - 18:00</p>
          <p>📞 1588-0000</p>
        </div>
      </div>

      {/* Welcome Message Preview */}
      <div className="px-4 py-3 bg-gray-50 border-b border-gray-100 shrink-0">
        <p className="text-xs text-gray-600 leading-relaxed">
          안녕하세요! FarmOS 마켓 고객지원입니다.<br />
          무엇이든 물어보세요 😊
        </p>
      </div>

      {/* Inquiry Button */}
      <div className="px-3 py-3 border-b border-gray-100 shrink-0 bg-gray-50">
        <button
          onClick={handleInquiry}
          disabled={isCreating}
          className="w-full py-2.5 px-3 bg-[#03C75A] text-white text-sm font-semibold rounded-lg hover:bg-[#02b050] transition-colors disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
        >
          {isCreating ? '연결 중...' : '문의하기'}
        </button>
      </div>

      {/* Sessions List */}
      <div className="flex-1 overflow-y-auto shrink-0">
        {sessions.length > 0 && (
          <div className="px-4 py-3 text-xs font-semibold text-gray-500 bg-gray-50 border-b border-gray-100">
            채팅 내역
          </div>
        )}
        <div className="divide-y divide-gray-100">
          {sessions.map((session) => (
            <div key={session.id} className="relative">
              {/* button 안에 button 중첩 불가 — div + role="button"으로 외부 클릭 영역 처리 */}
              <div
                role="button"
                tabIndex={0}
                onClick={() => onSessionSelect(session.id)}
                onKeyDown={(e) => e.key === 'Enter' && onSessionSelect(session.id)}
                className={`w-full text-left px-4 py-3 transition-all cursor-pointer ${
                  session.status === 'active'
                    ? 'bg-gradient-to-r from-green-50 to-white border-l-4 border-l-[#03C75A] hover:from-green-100 hover:to-white'
                    : 'bg-white hover:bg-gray-50'
                }`}
              >
                <div className="flex items-start gap-3">
                  <div className="flex items-start justify-center mt-1.5">
                    {session.status === 'active' ? (
                      <div className="relative">
                        <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
                        <div className="absolute inset-0 w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
                      </div>
                    ) : (
                      <span className="text-xs text-gray-400">✓</span>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1.5">
                      <p className="text-sm text-gray-900 truncate flex-1">
                        {session.messagePreview
                          ? session.messagePreview.substring(0, 50) + (session.messagePreview.length > 50 ? '...' : '')
                          : WELCOME_MESSAGE.substring(0, 50) + '...'}
                      </p>
                      {session.status === 'active' && (
                        <span className="shrink-0 text-xs bg-blue-100 text-blue-700 px-2 py-0.5 rounded-full font-medium whitespace-nowrap">
                          🔵 상담 중
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-gray-500 space-x-1">
                      <span>{formatTime(session.updatedAt)}</span>
                      <span>·</span>
                      <span>메시지 {session.messageCount || 0}개</span>
                    </p>
                  </div>
                  <div className="relative shrink-0">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpen(menuOpen === session.id ? null : session.id);
                      }}
                      className="p-1.5 text-gray-400 hover:text-gray-600 transition-colors"
                      title="옵션"
                    >
                      ⋯
                    </button>

                    {/* Dropdown Menu */}
                    {menuOpen === session.id && (
                      <div className="absolute right-0 top-full mt-1 bg-white border border-gray-200 rounded-lg shadow-lg z-20 w-32">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDelete(session.id);
                          }}
                          disabled={isDeleting}
                          className="w-full text-left px-4 py-2 text-sm text-red-500 hover:bg-red-50 transition-colors first:rounded-t-lg last:rounded-b-lg disabled:opacity-50"
                        >
                          {isDeleting ? '삭제 중...' : '삭제'}
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>

        {/* Empty State */}
        {sessions.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center px-4 py-8">
            <p className="text-gray-400 text-sm mb-2">📝 아직 채팅 내역이 없습니다</p>
            <p className="text-gray-500 text-xs">새 채팅을 시작해보세요!</p>
          </div>
        )}
      </div>
    </div>
  );
}
