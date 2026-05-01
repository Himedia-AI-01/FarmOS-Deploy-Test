import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useChatSessions, useSessionLogs } from '@/admin/hooks/useChatbot';
import { useUserExchangeTickets } from '@/admin/hooks/useTickets';
import { INTENT_LABEL, INTENT_COLOR_BADGE as INTENT_COLOR } from '@/admin/constants/chatbot';
import { TICKET_STATUS_LABEL, TICKET_STATUS_COLOR, TICKET_ACTION_LABEL, TICKET_ACTION_COLOR } from '@/admin/types/ticket';
import { formatDate } from '@/lib/utils';
import type { ChatSession } from '@/admin/types/chatlog';
import type { Ticket } from '@/admin/types/ticket';

// ──────────────────────────────────────────
// RelatedTickets
// ──────────────────────────────────────────

/** 교환 intent 챗로그에서 연관 티켓을 표시하는 컴포넌트 */
function RelatedTickets({ tickets, isLoading }: { tickets: Ticket[]; isLoading: boolean }) {
  if (isLoading) {
    return (
      <div className="space-y-2.5">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="p-3 rounded-xl bg-stone-50 border border-stone-100 animate-pulse space-y-2">
            <div className="flex justify-between">
              <div className="h-4 w-12 bg-stone-200 rounded-full" />
              <div className="h-3 w-8 bg-stone-100 rounded" />
            </div>
            <div className="h-3 w-full bg-stone-100 rounded" />
            <div className="flex justify-between">
              <div className="h-4 w-10 bg-stone-100 rounded-full" />
              <div className="h-4 w-14 bg-stone-100 rounded" />
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (tickets.length === 0) {
    return (
      <div className="py-6 flex flex-col items-center gap-2 text-center">
        <span className="material-symbols-outlined text-stone-200 text-[32px]" aria-hidden="true">
          inventory_2
        </span>
        <p className="text-xs text-stone-400">연관된 티켓이 없습니다.</p>
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {tickets.map((ticket) => {
        const isDone = ticket.status === 'completed' || ticket.status === 'cancelled';
        return (
          <div
            key={ticket.id}
            className={`rounded-xl border overflow-hidden ${
              isDone ? 'border-stone-200' : 'border-emerald-200'
            }`}
          >
            {/* Card header */}
            <div className={`flex items-center justify-between px-3 py-2 ${isDone ? 'bg-stone-50' : 'bg-emerald-50'}`}>
              <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${TICKET_ACTION_COLOR[ticket.action_type]} ${
                ticket.action_type === 'exchange' ? 'border-purple-200' : 'border-red-200'
              }`}>
                {TICKET_ACTION_LABEL[ticket.action_type]}
              </span>
              <span className="text-[10px] text-stone-400 font-medium">티켓 #{ticket.id}</span>
            </div>

            {/* Card body */}
            <div className="px-3 py-2.5 bg-white">
              <p className="text-xs text-stone-700 line-clamp-2 leading-relaxed mb-2.5">
                {ticket.reason}
              </p>
              <div className="flex items-center justify-between">
                <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${TICKET_STATUS_COLOR[ticket.status]}`}>
                  {TICKET_STATUS_LABEL[ticket.status]}
                </span>
                <NavLink
                  to={`/admin/tickets?ticketId=${ticket.id}`}
                  className="flex items-center gap-0.5 text-[11px] font-bold text-emerald-700 hover:text-emerald-900 transition-colors"
                >
                  상세보기
                  <span className="material-symbols-outlined text-[13px]" aria-hidden="true">arrow_forward</span>
                </NavLink>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ──────────────────────────────────────────
// ChatbotPage
// ──────────────────────────────────────────

export default function ChatbotPage() {
  const [tab, setTab] = useState<'all' | 'escalated'>('all');
  const [selectedSession, setSelectedSession] = useState<ChatSession | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  const { data: allSessions = [], isLoading: loadingAll } = useChatSessions(false);
  const { data: escalatedSessions = [], isLoading: loadingEsc } = useChatSessions(true);
  const { data: sessionLogs = [], isLoading: loadingLogs } = useSessionLogs(selectedSession?.id ?? null);
  const { data: selectedUserTickets = [], isLoading: loadingSelectedTickets } = useUserExchangeTickets(selectedSession?.user_id ?? null);

  // 선택된 세션에서 발생한 미처리 티켓 — 상세 헤더 배너용
  const pendingSelectedTickets = selectedUserTickets.filter(
    (t) => t.session_id === selectedSession?.id &&
      (t.status === 'received' || t.status === 'processing'),
  );

  const isLoading = tab === 'all' ? loadingAll : loadingEsc;
  const sessions = tab === 'all' ? allSessions : escalatedSessions;

  // Track previous log date for date separators
  let prevDateStr = '';

  return (
    <div className="h-[calc(100vh-4rem)] flex overflow-hidden">

      {/* ────── Left Panel: Session List ────── */}
      <aside className="w-[288px] shrink-0 border-r border-zinc-100 flex flex-col bg-surface">

        {/* Header + Tab switcher */}
        <div className="p-4 pb-3 border-b border-zinc-100 space-y-3">
          <h2 className="text-base font-bold text-on-surface">챗봇 대화 모니터링</h2>
          <div className="grid grid-cols-2 gap-1 p-1 bg-surface-container rounded-xl">
            <button
              type="button"
              onClick={() => { setTab('all'); setSelectedSession(null); }}
              className={`flex flex-col items-center py-2 text-xs font-semibold rounded-lg transition-colors ${
                tab === 'all'
                  ? 'bg-white shadow-sm text-on-surface'
                  : 'text-stone-500 hover:bg-white/50'
              }`}
              aria-pressed={tab === 'all'}
            >
              <span>전체 대화</span>
              <span className={`text-[10px] font-bold leading-tight mt-0.5 ${tab === 'all' ? 'text-stone-400' : 'text-stone-300'}`}>
                {allSessions.length}
              </span>
            </button>
            <button
              type="button"
              onClick={() => { setTab('escalated'); setSelectedSession(null); }}
              className={`flex flex-col items-center py-2 text-xs font-medium rounded-lg transition-colors ${
                tab === 'escalated'
                  ? 'bg-white shadow-sm text-on-surface'
                  : 'text-stone-500 hover:bg-white/50'
              }`}
              aria-pressed={tab === 'escalated'}
            >
              <span className="flex items-center gap-1">
                에스컬레이션
                {escalatedSessions.length > 0 && (
                  <span className="bg-red-500 text-white text-[10px] px-1.5 rounded-full leading-none py-0.5 font-bold">
                    {escalatedSessions.length}
                  </span>
                )}
              </span>
              <span className={`text-[10px] font-bold leading-tight mt-0.5 ${tab === 'escalated' ? 'text-stone-400' : 'text-stone-300'}`}>
                {escalatedSessions.length}
              </span>
            </button>
          </div>
        </div>

        {/* Session list */}
        <div className="flex-1 overflow-y-auto px-3 py-2 space-y-1.5">
          {isLoading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="p-4 rounded-xl bg-white border border-stone-100 animate-pulse space-y-2" aria-hidden="true">
                <div className="flex justify-between">
                  <div className="h-3 w-20 bg-stone-200 rounded" />
                  <div className="h-2 w-12 bg-stone-100 rounded" />
                </div>
                <div className="h-3 w-40 bg-stone-100 rounded" />
                <div className="flex gap-2">
                  <div className="h-4 w-14 bg-stone-100 rounded" />
                  <div className="h-4 w-10 bg-stone-100 rounded" />
                </div>
              </div>
            ))
          ) : sessions.length === 0 ? (
            <div className="py-12 text-center space-y-2">
              <span className="material-symbols-outlined text-stone-200 text-[40px] block" aria-hidden="true">
                chat_bubble
              </span>
              <p className="text-sm text-stone-400">대화 내역이 없습니다.</p>
            </div>
          ) : (
            sessions.map((session) => {
              const preview = session.title
                ?? (session.last_question ? session.last_question.slice(0, 40) : '내용 없음');
              const isSelected = selectedSession?.id === session.id;
              return (
                <button
                  key={session.id}
                  type="button"
                  onClick={() => setSelectedSession(session)}
                  className={`w-full text-left p-3.5 rounded-xl border transition-all ${
                    isSelected
                      ? 'bg-emerald-50 border-emerald-300 shadow-sm ring-1 ring-emerald-200/60'
                      : 'bg-white border-stone-200 hover:border-emerald-200 hover:shadow-sm'
                  }`}
                  aria-pressed={isSelected}
                >
                  {/* Row 1: user ID + indicators + time */}
                  <div className="flex justify-between items-center mb-1.5">
                    <div className="flex items-center gap-1.5">
                      <span className={`text-sm font-bold ${isSelected ? 'text-emerald-800' : 'text-on-surface'}`}>
                        회원 #{session.user_id}
                      </span>
                      {session.has_escalation && (
                        <span
                          className="w-1.5 h-1.5 rounded-full bg-red-500 animate-pulse shrink-0"
                          title="에스컬레이션"
                          aria-label="에스컬레이션"
                        />
                      )}
                      {session.pending_ticket_status && (
                        <span
                          className={`text-[9px] font-black px-1.5 py-0.5 rounded-full shrink-0 ${
                            session.pending_ticket_status === 'received'
                              ? 'bg-amber-100 text-amber-700 border border-amber-200'
                              : 'bg-sky-100 text-sky-700 border border-sky-200'
                          }`}
                          title={session.pending_ticket_status === 'received' ? '미접수 티켓 있음' : '처리 중인 티켓 있음'}
                        >
                          티켓
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-stone-400 font-medium shrink-0">
                      {session.last_message_at
                        ? formatDate(session.last_message_at, 'MM/dd HH:mm')
                        : formatDate(session.created_at, 'MM/dd HH:mm')}
                    </span>
                  </div>

                  {/* Row 2: title / question preview */}
                  <p className="text-xs text-stone-600 line-clamp-1 mb-1.5">{preview}</p>

                  {/* Row 3: message count badge + status */}
                  <div className="flex items-center gap-2">
                    <span className="px-2 py-0.5 bg-stone-100 text-stone-500 text-[10px] font-bold rounded-full">
                      {session.log_count}개
                    </span>
                    <span className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
                      session.status === 'active'
                        ? 'bg-emerald-100 text-emerald-700'
                        : 'bg-stone-100 text-stone-500'
                    }`}>
                      {session.status === 'active' ? '진행중' : '종료'}
                    </span>
                  </div>
                </button>
              );
            })
          )}
        </div>
      </aside>

      {/* ────── Main Panel: Session Detail ────── */}
      <section className="flex-1 flex flex-col bg-surface-container-low min-w-0">
        {selectedSession ? (
          <>
            {/* Detail header */}
            <div className="p-6 bg-surface-container-lowest border-b border-stone-100 shrink-0">
              <div className="flex justify-between items-start mb-4">
                <div>
                  {/* Row 1: user ID + message count */}
                  <div className="flex items-center gap-3 mb-1">
                    <h2 className="text-xl font-black text-on-surface">
                      회원 #{selectedSession.user_id}
                    </h2>
                    <span className="text-[11px] text-stone-400 font-medium">
                      {selectedSession.log_count}개 메시지
                    </span>
                  </div>
                  {/* Session start date */}
                  <p className="text-sm text-stone-500 font-medium">
                    채팅 시작 · {formatDate(selectedSession.created_at)}
                  </p>
                </div>

                {/* Action buttons */}
                <div className="flex gap-2 shrink-0">
                  <button
                    type="button"
                    className="flex items-center gap-1.5 bg-stone-100 text-stone-600 px-4 py-2 rounded-xl text-sm font-bold hover:bg-stone-200 transition-colors"
                  >
                    <span className="material-symbols-outlined text-[16px]" aria-hidden="true">history</span>
                    이력 보기
                  </button>
                </div>
              </div>

              {/* Escalation warning banner */}
              {selectedSession.has_escalation && (
                <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-rose-50 border border-rose-300 rounded-lg">
                  <span className="material-symbols-outlined text-rose-400 text-[15px] shrink-0" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                    warning
                  </span>
                  <p className="text-xs font-medium text-rose-900 flex-1">상담원 개입 권장</p>
                  <button
                    type="button"
                    className="shrink-0 px-2.5 py-1 border border-rose-300 text-rose-700 bg-white text-[11px] font-semibold rounded-md hover:bg-rose-500 hover:border-rose-500 hover:text-white transition-colors"
                  >
                    직접 상담 전환
                  </button>
                </div>
              )}

              {/* Pending ticket warning banner */}
              {pendingSelectedTickets.length > 0 && (
                <div className="mt-2 flex items-center gap-2 px-3 py-2 bg-amber-50 border border-amber-300 rounded-lg">
                  <span className="material-symbols-outlined text-amber-400 text-[15px] shrink-0" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                    assignment_late
                  </span>
                  <p className="text-xs font-medium text-amber-900 flex-1">
                    미처리 티켓
                    <span className="ml-1 inline-flex items-center justify-center w-4 h-4 bg-amber-200 text-amber-800 text-[10px] font-black rounded-full">
                      {pendingSelectedTickets.length}
                    </span>
                  </p>
                  <NavLink
                    to={`/admin/tickets?userId=${selectedSession.user_id}`}
                    className="shrink-0 px-2.5 py-1 border border-amber-300 text-amber-700 bg-white text-[11px] font-semibold rounded-md hover:bg-amber-500 hover:border-amber-500 hover:text-white transition-colors"
                  >
                    티켓 보기
                  </NavLink>
                </div>
              )}
            </div>

            {/* Chat area */}
            <div className="flex-1 overflow-y-auto p-6 space-y-6">
              {loadingLogs ? (
                <div className="text-center text-stone-400 text-sm py-8">
                  대화 내역을 불러오는 중...
                </div>
              ) : sessionLogs.length === 0 ? (
                <div className="h-full flex items-center justify-center">
                  <p className="text-sm text-stone-400 font-medium">대화 내역이 없습니다.</p>
                </div>
              ) : (
                (() => {
                  prevDateStr = '';
                  return sessionLogs.map((log) => {
                    const logDateStr = formatDate(log.created_at, 'yyyy-MM-dd');
                    const showDateSep = logDateStr !== prevDateStr;
                    prevDateStr = logDateStr;

                    return (
                      <div key={log.id} className="space-y-6">
                        {/* Date separator */}
                        {showDateSep && (
                          <div className="flex justify-center">
                            <span className="bg-stone-200/50 text-stone-500 text-[10px] font-bold px-3 py-1 rounded-full uppercase tracking-widest">
                              {formatDate(log.created_at, 'yyyy년 MM월 dd일')}
                            </span>
                          </div>
                        )}

                        {/* User message bubble */}
                        <div className="flex justify-end">
                          <div className="bg-primary text-on-primary p-4 rounded-xl rounded-tr-none shadow-lg shadow-primary/10 max-w-[80%]">
                            <p className="text-sm leading-relaxed">{log.question}</p>
                            <span className="block text-[10px] text-primary-fixed/70 mt-2 text-right">
                              {formatDate(log.created_at, 'HH:mm')}
                            </span>
                          </div>
                        </div>

                        {/* Bot message bubble */}
                        <div className="flex gap-4 items-start">
                          <div
                            className="w-8 h-8 rounded-full bg-primary-container flex items-center justify-center text-on-primary-container shadow-sm shrink-0"
                            aria-hidden="true"
                          >
                            <span className="material-symbols-outlined text-[18px]">smart_toy</span>
                          </div>
                          <div className="space-y-1.5 max-w-[80%]">
                            <div className="bg-surface-container-lowest p-4 rounded-xl rounded-tl-none shadow-sm">
                              <p className="text-sm text-on-surface leading-relaxed whitespace-pre-wrap">
                                {log.answer}
                              </p>
                            </div>
                            {/* Intent badge */}
                            <span
                              className={`inline-block px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-tighter ${
                                INTENT_COLOR[log.intent] ?? 'bg-gray-100 text-gray-600'
                              }`}
                            >
                              {INTENT_LABEL[log.intent] ?? log.intent}
                            </span>
                          </div>
                        </div>

                        {/* Rating section */}
                        {log.rating != null && (
                          <div className="flex flex-col items-center gap-3 pt-2">
                            <p className="text-xs font-bold text-stone-400 uppercase tracking-widest">
                              상담 품질 피드백
                            </p>
                            <div className="flex gap-1" aria-label={`평점 ${log.rating}점`}>
                              {Array.from({ length: 5 }, (_, i) => (
                                <span
                                  key={i}
                                  className={`material-symbols-outlined text-[22px] ${
                                    i < log.rating! ? 'text-primary' : 'text-stone-300'
                                  }`}
                                  style={i < log.rating! ? { fontVariationSettings: "'FILL' 1" } : undefined}
                                  aria-hidden="true"
                                >
                                  star
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  });
                })()
              )}
            </div>

            {/* Input area (display-only) */}
            <div className="p-4 bg-surface-container-lowest border-t border-stone-100 shrink-0">
              <div className="bg-stone-100 rounded-xl px-4 py-2 flex items-center gap-4">
                <button
                  type="button"
                  className="text-stone-400 hover:text-primary transition-colors"
                  aria-label="첨부"
                  disabled
                >
                  <span className="material-symbols-outlined text-[22px]">add_circle</span>
                </button>
                <span className="flex-1 text-sm text-stone-400 py-2 select-none">
                  직접 메시지를 입력하세요...
                </span>
                <button
                  type="button"
                  className="text-primary"
                  aria-label="전송"
                  disabled
                >
                  <span
                    className="material-symbols-outlined text-[22px]"
                    style={{ fontVariationSettings: "'FILL' 1" }}
                  >
                    send
                  </span>
                </button>
              </div>
            </div>
          </>
        ) : (
          /* Empty state */
          <div className="h-full flex items-center justify-center">
            <div className="text-center space-y-3">
              <span
                className="material-symbols-outlined text-stone-200 text-[64px] block"
                style={{ fontVariationSettings: "'FILL' 1" }}
                aria-hidden="true"
              >
                chat_bubble
              </span>
              <p className="text-sm text-stone-400 font-medium">목록에서 대화를 선택하세요</p>
            </div>
          </div>
        )}
      </section>

      {/* ────── Right Sidebar: Context Info ────── */}
      <aside className={`shrink-0 bg-white border-l border-stone-100 flex flex-col transition-[width] duration-200 overflow-hidden ${sidebarOpen ? 'w-[320px]' : 'w-10'}`}>
        {/* Toggle button */}
        <button
          type="button"
          onClick={() => setSidebarOpen((prev) => !prev)}
          className="flex items-center justify-start px-2 h-10 w-full shrink-0 border-b border-stone-100 text-stone-400 hover:text-stone-600 hover:bg-stone-50 transition-colors"
          aria-label={sidebarOpen ? '사이드바 닫기' : '사이드바 열기'}
          aria-expanded={sidebarOpen}
        >
          <span
            className={`material-symbols-outlined text-[18px] transition-transform duration-200 ${sidebarOpen ? 'rotate-0' : 'rotate-180'}`}
            aria-hidden="true"
          >
            chevron_right
          </span>
        </button>

        <div className={`flex-1 overflow-y-auto ${sidebarOpen ? '' : 'hidden'}`}>

        {selectedSession ? (
          <>
            {/* Section 1: Related tickets */}
            <div className="px-5 py-4 border-b border-stone-100">
              <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[14px] text-stone-400" aria-hidden="true">
                  inventory_2
                </span>
                연관 티켓
              </h3>

              {sessionLogs.some((l) => l.intent === 'exchange') ? (
                <RelatedTickets
                  tickets={selectedUserTickets.filter((t) => t.session_id === selectedSession.id)}
                  isLoading={loadingSelectedTickets}
                />
              ) : (
                <div className="py-5 flex flex-col items-center gap-2 text-center">
                  <span className="material-symbols-outlined text-stone-200 text-[28px]" aria-hidden="true">
                    inventory_2
                  </span>
                  <p className="text-xs text-stone-400">교환 관련 대화가 없습니다.</p>
                </div>
              )}
            </div>

            {/* Section 2: User summary */}
            <div className="px-5 py-4">
              <h3 className="text-xs font-bold text-stone-500 uppercase tracking-wider mb-3 flex items-center gap-1.5">
                <span className="material-symbols-outlined text-[14px] text-stone-400" aria-hidden="true">
                  person
                </span>
                회원 정보
              </h3>

              <div className="space-y-1">
                {/* Row items */}
                {[
                  { label: '회원 ID', value: `#${selectedSession.user_id}` },
                  { label: '메시지 수', value: `${selectedSession.log_count}개` },
                  {
                    label: '세션 상태',
                    value: selectedSession.status === 'active' ? '진행중' : '종료',
                    valueClass: selectedSession.status === 'active' ? 'text-emerald-600' : 'text-stone-400',
                  },
                  {
                    label: '에스컬레이션',
                    value: selectedSession.has_escalation ? '있음' : '없음',
                    valueClass: selectedSession.has_escalation ? 'text-red-600' : 'text-stone-500',
                  },
                  { label: '세션 시작', value: formatDate(selectedSession.created_at, 'MM.dd HH:mm') },
                ].map(({ label, value, valueClass }) => (
                  <div key={label} className="flex items-center justify-between py-2 border-b border-stone-50 last:border-0">
                    <span className="text-xs text-stone-400">{label}</span>
                    <span className={`text-xs font-semibold ${valueClass ?? 'text-stone-700'}`}>{value}</span>
                  </div>
                ))}
              </div>
            </div>
          </>
        ) : (
          /* Right sidebar empty state */
          <div className="h-full flex flex-col items-center justify-center gap-2 p-6">
            <span className="material-symbols-outlined text-stone-200 text-[36px]" aria-hidden="true" style={{ fontVariationSettings: "'FILL' 1" }}>
              chat_bubble
            </span>
            <p className="text-xs text-stone-300 text-center leading-relaxed">
              대화를 선택하면<br />상세 정보가 표시됩니다.
            </p>
          </div>
        )}
        </div>
      </aside>
    </div>
  );
}
