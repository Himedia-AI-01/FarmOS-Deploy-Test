import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useTickets, useTicketStats, useUpdateTicketStatus } from '@/admin/hooks/useTickets';
import { useCreateShipment } from '@/admin/hooks/useShipments';
import {
  TICKET_STATUS_LABEL,
  TICKET_STATUS_COLOR,
  TICKET_ACTION_LABEL,
  TICKET_ACTION_COLOR,
} from '@/admin/types/ticket';
import { parseTicketItems } from '@/admin/types/ticket';
import type { Ticket, TicketStatus, TicketActionType } from '@/admin/types/ticket';
import { formatDate, formatPrice } from '@/lib/utils';

// ──────────────────────────────────────────
// Constants
// ──────────────────────────────────────────

const CARRIERS = ['CJ대한통운', '한진', '로젠', '우체국택배', '롯데택배'];

type StatusFilter = TicketStatus | 'all';
type CategoryFilter = 'all' | 'product' | 'delivery';
type ActionFilter = TicketActionType | 'all';

const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: '전체' },
  { value: 'received', label: '접수됨' },
  { value: 'processing', label: '처리중' },
  { value: 'completed', label: '완료' },
  { value: 'cancelled', label: '취소' },
];

const CATEGORY_TABS: { value: CategoryFilter; label: string }[] = [
  { value: 'all',      label: '전체' },
  { value: 'product',  label: '상품' },
  { value: 'delivery', label: '배송' },
];

/** 카테고리별 소분류 액션 필터 */
const CATEGORY_SUB_ACTIONS: Record<CategoryFilter, { value: ActionFilter; label: string }[]> = {
  all: [
    { value: 'all',      label: '전체' },
    { value: 'exchange', label: '교환' },
    { value: 'cancel',   label: '취소' },
  ],
  product: [
    { value: 'all',      label: '전체' },
    { value: 'exchange', label: '교환' },
    { value: 'cancel',   label: '취소' },
  ],
  delivery: [],
};

/** 카테고리에 해당하는 action_type 목록 (프론트 필터링용) */
const CATEGORY_ACTION_TYPES: Record<CategoryFilter, TicketActionType[]> = {
  all:      ['cancel', 'exchange'],
  product:  ['cancel', 'exchange'],
  delivery: [],
};

// 다음 가능한 상태 전이
const NEXT_STATUSES: Record<TicketStatus, TicketStatus[]> = {
  received: ['processing', 'cancelled'],
  processing: ['completed', 'cancelled'],
  completed: [],
  cancelled: [],
};

const NEXT_STATUS_BUTTON: Partial<Record<TicketStatus, { label: string; isPrimary: boolean }>> = {
  processing: { label: '처리 시작', isPrimary: true },
  completed: { label: '처리 완료', isPrimary: true },
  cancelled: { label: '반려', isPrimary: false },
};

// ──────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────

/** Mask customer name for privacy: 김철수 → 김*수, 홍길동 → 홍*동 */
function maskName(name: string | null, userId: number): string {
  if (!name) return `사용자 #${userId}`;
  if (name.length <= 1) return name;
  if (name.length === 2) return `${name[0]}*`;
  return `${name[0]}${'*'.repeat(name.length - 2)}${name[name.length - 1]}`;
}

/** Returns a relative time string for a given ISO date */
function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60_000);
  if (mins < 1) return '방금 전';
  if (mins < 60) return `${mins}분 전`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}시간 전`;
  const days = Math.floor(hours / 24);
  return `${days}일 전`;
}

// ──────────────────────────────────────────
// ExchangeShipmentPanel
// ──────────────────────────────────────────

function ExchangeShipmentPanel({ orderId, relatedTicketId }: { orderId: number; relatedTicketId: number }) {
  const [open, setOpen] = useState(false);
  const [carrier, setCarrier] = useState(CARRIERS[0]);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [expectedArrival, setExpectedArrival] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const { mutate: create, isPending } = useCreateShipment();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!trackingNumber) return;
    create(
      { order_id: orderId, carrier, tracking_number: trackingNumber, expected_arrival: expectedArrival || undefined, related_ticket_id: relatedTicketId },
      {
        onSuccess: () => {
          setTrackingNumber('');
          setExpectedArrival('');
          setFeedback({ type: 'success', msg: '교환 배송이 등록됐습니다.' });
          setTimeout(() => { setFeedback(null); setOpen(false); }, 2500);
        },
        onError: () => {
          setFeedback({ type: 'error', msg: '등록 실패. 다시 시도해주세요.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      },
    );
  };

  return (
    <div className="border border-dashed border-emerald-600 rounded-xl p-4">
      <button
        type="button"
        className="flex items-center justify-between w-full cursor-pointer"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="flex items-center gap-2">
          <span
            className="material-symbols-outlined text-emerald-600 text-[20px]"
            aria-hidden="true"
          >
            local_shipping
          </span>
          <p className="text-sm font-medium text-emerald-600">교환 상품 배송 등록</p>
        </div>
        <span
          className="material-symbols-outlined text-stone-400 text-[18px]"
          aria-hidden="true"
        >
          {open ? 'expand_less' : 'expand_more'}
        </span>
      </button>

      {open && (
        <form onSubmit={handleSubmit} className="mt-4 pt-4 border-t border-dashed border-emerald-600/30 space-y-3">
          <p className="text-xs text-stone-500">주문 #{orderId}에 교환 상품을 발송하고 운송장을 등록하세요.</p>

          {feedback && (
            <div
              className={`px-3 py-2 rounded-lg text-xs font-medium ${
                feedback.type === 'success'
                  ? 'bg-green-50 border border-green-200 text-green-700'
                  : 'bg-red-50 border border-red-200 text-red-700'
              }`}
            >
              {feedback.msg}
            </div>
          )}

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-stone-500 mb-1" htmlFor="carrier-select">
                택배사
              </label>
              <select
                id="carrier-select"
                value={carrier}
                onChange={(e) => setCarrier(e.target.value)}
                className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white outline-none focus:border-emerald-600 transition-colors"
              >
                {CARRIERS.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="block text-xs text-stone-500 mb-1" htmlFor="tracking-input">
                운송장번호 *
              </label>
              <input
                id="tracking-input"
                type="text"
                value={trackingNumber}
                onChange={(e) => setTrackingNumber(e.target.value)}
                required
                className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-emerald-600 transition-colors"
                placeholder="운송장번호"
              />
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-stone-500 mb-1" htmlFor="arrival-input">
                도착 예정일
              </label>
              <input
                id="arrival-input"
                type="date"
                value={expectedArrival}
                onChange={(e) => setExpectedArrival(e.target.value)}
                className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-emerald-600 transition-colors"
              />
            </div>
          </div>
          <button
            type="submit"
            disabled={isPending || !trackingNumber}
            className="w-full bg-emerald-600 text-white py-2 rounded-lg text-sm font-medium hover:bg-emerald-700 disabled:opacity-40 transition-colors"
          >
            {isPending ? '등록 중...' : '배송 등록'}
          </button>
        </form>
      )}
    </div>
  );
}

// ──────────────────────────────────────────
// TicketCard (left panel list item)
// ──────────────────────────────────────────

function TicketCard({
  ticket,
  selected,
  onClick,
}: {
  ticket: Ticket;
  selected: boolean;
  onClick: () => void;
}) {
  const maskedName = maskName(ticket.user_name, ticket.user_id);

  return (
    <button
      type="button"
      onClick={onClick}
      className={`w-full text-left p-4 rounded-xl transition-all ${
        selected
          ? 'bg-emerald-50 border border-emerald-300 shadow-md ring-1 ring-emerald-200/60'
          : 'bg-white border border-stone-200 shadow-sm hover:border-emerald-200 hover:shadow-md'
      }`}
      aria-pressed={selected}
    >
      {/* Header row */}
      <div className="flex justify-between items-start mb-2">
        <span className="text-sm font-bold text-stone-800">
          {maskedName}
        </span>
        <span className="text-[10px] text-stone-400 shrink-0 ml-2">
          {timeAgo(ticket.created_at)}
        </span>
      </div>

      {/* Badge row */}
      <div className="flex gap-2 mb-2.5">
        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${TICKET_ACTION_COLOR[ticket.action_type]}`}>
          {TICKET_ACTION_LABEL[ticket.action_type]}
        </span>
        <span className={`px-2 py-0.5 rounded text-[10px] font-bold ${TICKET_STATUS_COLOR[ticket.status]}`}>
          {TICKET_STATUS_LABEL[ticket.status]}
        </span>
      </div>

      {/* Preview */}
      <p className="text-xs text-stone-600 line-clamp-2 leading-relaxed">
        {ticket.reason}
      </p>
    </button>
  );
}

// ──────────────────────────────────────────
// Timeline
// ──────────────────────────────────────────

interface TimelineStep {
  status: TicketStatus;
  label: string;
  icon: string;
  doneIcon: string;
}

const TIMELINE_STEPS: TimelineStep[] = [
  { status: 'received',   label: '접수됨', icon: 'check_circle', doneIcon: 'check_circle' },
  { status: 'processing', label: '처리중',  icon: 'pending',      doneIcon: 'check_circle' },
  { status: 'completed',  label: '완료',   icon: 'verified',     doneIcon: 'verified' },
];

/** Returns 0, 50, or 100 — the fill % for the connecting line */
function timelineFill(status: TicketStatus): number {
  if (status === 'processing') return 50;
  if (status === 'completed') return 100;
  return 0;
}

function StatusTimeline({ status }: { status: TicketStatus }) {
  const isCancelled = status === 'cancelled';
  const fill = timelineFill(status);

  return (
    <div className="bg-surface-container-lowest rounded-xl p-8 border border-stone-100/50">
      <div className="relative flex justify-between items-center px-12">
        {/* Background line */}
        <div
          className="absolute top-5 left-24 right-24 h-0.5 bg-stone-100 z-0"
          aria-hidden="true"
        >
          {!isCancelled && (
            <div
              className="h-full bg-primary-container transition-all duration-500"
              style={{ width: `${fill}%` }}
            />
          )}
        </div>

        {TIMELINE_STEPS.map((step) => {
          const stepIndex = TIMELINE_STEPS.findIndex((s) => s.status === step.status);
          const currentIndex = TIMELINE_STEPS.findIndex((s) => s.status === status);

          const isDone = !isCancelled && currentIndex > stepIndex;
          const isActive = !isCancelled && status === step.status;
          const isFuture = isCancelled || currentIndex < stepIndex;

          let circleClass: string;
          let iconName: string;

          if (isDone) {
            circleClass = 'bg-primary-container text-white ring-4 ring-emerald-50';
            iconName = step.doneIcon;
          } else if (isActive) {
            circleClass = 'border-2 border-primary-container text-primary-container bg-white';
            iconName = step.icon;
          } else {
            circleClass = 'border-2 border-stone-200 text-stone-300 bg-white';
            iconName = step.icon;
          }

          return (
            <div key={step.status} className="relative z-10 flex flex-col items-center">
              <div
                className={`w-10 h-10 rounded-full flex items-center justify-center shadow-sm ${circleClass}`}
              >
                <span
                  className="material-symbols-outlined text-[20px]"
                  style={isDone ? { fontVariationSettings: "'FILL' 1" } : undefined}
                  aria-hidden="true"
                >
                  {iconName}
                </span>
              </div>
              <span
                className={`mt-3 text-xs font-bold ${
                  isDone || isActive ? 'text-primary' : 'text-stone-400'
                }`}
              >
                {step.label}
              </span>
              <span className="text-[10px] text-stone-300 mt-1">
                {isActive || isDone ? '—' : '-'}
              </span>
            </div>
          );
        })}
      </div>

      {isCancelled && (
        <div className="mt-6 flex items-center justify-center gap-2">
          <span
            className="material-symbols-outlined text-stone-400 text-[16px]"
            aria-hidden="true"
          >
            cancel
          </span>
          <span className="text-xs text-stone-500 font-medium">이 티켓은 취소됐습니다.</span>
        </div>
      )}
    </div>
  );
}

// ──────────────────────────────────────────
// TicketDetail (right panel)
// ──────────────────────────────────────────

function TicketDetail({ ticket }: { ticket: Ticket }) {
  const { mutate: updateStatus, isPending } = useUpdateTicketStatus();
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const nextStatuses = NEXT_STATUSES[ticket.status];

  const parsedItems = parseTicketItems(ticket);

  const handleStatusChange = (status: TicketStatus) => {
    updateStatus(
      { ticketId: ticket.id, status },
      {
        onSuccess: () => {
          setFeedback({ type: 'success', msg: `티켓 #${ticket.id} 상태가 '${TICKET_STATUS_LABEL[status]}'으로 변경됐습니다.` });
          setTimeout(() => setFeedback(null), 3000);
        },
        onError: () => {
          setFeedback({ type: 'error', msg: '상태 변경에 실패했습니다.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      },
    );
  };

  const maskedName = maskName(ticket.user_name, ticket.user_id);

  // Separate cancel (반려) from forward-progress buttons
  const cancelStatus = nextStatuses.find((s) => s === 'cancelled');
  const progressStatuses = nextStatuses.filter((s) => s !== 'cancelled');

  return (
    <div className="max-w-5xl mx-auto space-y-8">

      {/* ── Meta Header ── */}
      <div className="flex items-end justify-between gap-4">
        <div>
          {/* Name + badges */}
          <div className="flex items-center flex-wrap gap-3 mb-2">
            <h3 className="text-2xl font-bold tracking-tight text-stone-900">
              {maskedName}
              <span className="text-base font-normal text-stone-400 ml-2">(ID: {ticket.user_id})</span>
            </h3>
            <div className="flex gap-2">
              <span className={`px-3 py-1 rounded-full text-[11px] font-bold tracking-wider ${TICKET_ACTION_COLOR[ticket.action_type]}`}>
                {TICKET_ACTION_LABEL[ticket.action_type]}
              </span>
              <span className={`px-3 py-1 rounded-full text-[11px] font-bold tracking-wider ${TICKET_STATUS_COLOR[ticket.status]}`}>
                {TICKET_STATUS_LABEL[ticket.status]}
              </span>
            </div>
          </div>

          {/* Date + amount */}
          <div className="flex items-center gap-4 text-sm text-stone-500">
            <span className="flex items-center gap-1">
              <span className="material-symbols-outlined text-base" aria-hidden="true">calendar_today</span>
              {formatDate(ticket.created_at)}
            </span>
            {ticket.order_total != null && (
              <span className="flex items-center gap-1">
                <span className="material-symbols-outlined text-base" aria-hidden="true">payments</span>
                총 주문 금액: {formatPrice(ticket.order_total)}
              </span>
            )}
          </div>
        </div>

        {/* Action buttons */}
        {nextStatuses.length > 0 && (
          <div className="flex items-center gap-3 shrink-0">
            {/* 반려 button */}
            {cancelStatus && (
              <button
                type="button"
                onClick={() => handleStatusChange(cancelStatus)}
                disabled={isPending}
                className="px-5 py-2 rounded-xl bg-secondary-container text-on-secondary-fixed-variant text-sm font-bold hover:opacity-90 disabled:opacity-50 transition-all"
              >
                반려
              </button>
            )}

            {/* Forward-progress buttons */}
            {progressStatuses.map((s) => {
              const btn = NEXT_STATUS_BUTTON[s];
              if (!btn) return null;
              return (
                <button
                  key={s}
                  type="button"
                  onClick={() => handleStatusChange(s)}
                  disabled={isPending}
                  className="px-5 py-2 rounded-xl bg-primary-container text-white text-sm font-bold hover:opacity-90 shadow-md disabled:opacity-50 transition-all"
                >
                  {btn.label}
                </button>
              );
            })}
          </div>
        )}
      </div>

      {/* ── Feedback banner ── */}
      {feedback && (
        <div
          className={`flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium ${
            feedback.type === 'success'
              ? 'bg-green-50 border border-green-200 text-green-700'
              : 'bg-red-50 border border-red-200 text-red-700'
          }`}
          role="status"
        >
          <span
            className="material-symbols-outlined text-[18px]"
            style={{ fontVariationSettings: "'FILL' 1" }}
            aria-hidden="true"
          >
            {feedback.type === 'success' ? 'check_circle' : 'error'}
          </span>
          {feedback.msg}
        </div>
      )}

      {/* ── Timeline ── */}
      <StatusTimeline status={ticket.status} />

      {/* ── Info grid ── */}
      <div className="grid grid-cols-2 gap-6">
        {/* Order info */}
        <div className="bg-surface-container-lowest rounded-xl p-6 border border-stone-100/50">
          <h4 className="text-sm font-bold text-emerald-900 mb-4 flex items-center gap-2">
            <span className="material-symbols-outlined text-lg" aria-hidden="true">inventory_2</span>
            주문 정보
          </h4>
          <div className="space-y-3">
            <div className="flex justify-between py-2 border-b border-stone-50">
              <span className="text-xs text-stone-500">주문 번호</span>
              <span className="text-xs font-medium text-stone-800">#{ticket.order_id}</span>
            </div>
            <div className="flex justify-between py-2 border-b border-stone-50">
              <span className="text-xs text-stone-500">주문 금액</span>
              <span className="text-xs font-medium text-stone-800">
                {ticket.order_total != null ? formatPrice(ticket.order_total) : '-'}
              </span>
            </div>
            <div className="flex justify-between py-2">
              <span className="text-xs text-stone-500">고객</span>
              <span className="text-xs font-medium text-stone-800">
                {maskName(ticket.user_name, ticket.user_id)}
              </span>
            </div>
            {ticket.refund_method && (
              <div className="flex justify-between py-2 border-t border-stone-50">
                <span className="text-xs text-stone-500">환불 방법</span>
                <span className="text-xs font-medium text-stone-800">{ticket.refund_method}</span>
              </div>
            )}
          </div>
        </div>

        {/* Reason */}
        <div className="bg-surface-container-lowest rounded-xl p-6 border border-stone-100/50">
          <h4 className="text-sm font-bold text-emerald-900 mb-4 flex items-center gap-2">
            <span className="material-symbols-outlined text-lg" aria-hidden="true">description</span>
            {ticket.action_type === 'exchange' ? '교환 사유' : '취소 사유'}
          </h4>
          <div className="bg-stone-50/80 rounded-lg p-4 border border-stone-100">
            <p className="text-xs text-stone-700 leading-relaxed italic">
              "{ticket.reason}"
            </p>
          </div>
        </div>
      </div>

      {/* ── Items table ── */}
      {parsedItems && parsedItems.length > 0 && (
        <div className="bg-surface-container-lowest rounded-xl overflow-hidden border border-stone-100/50">
          <div className="p-6 border-b border-stone-50">
            <h4 className="text-sm font-bold text-emerald-900">상품 리스트</h4>
          </div>
          <table className="w-full text-left">
            <thead className="bg-surface-container-low">
              <tr>
                <th className="px-6 py-4 text-[11px] font-bold uppercase tracking-wider text-stone-500">
                  상품명
                </th>
                <th className="px-6 py-4 text-[11px] font-bold uppercase tracking-wider text-stone-500 text-center">
                  수량
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-stone-50">
              {parsedItems.map((item) => (
                <tr key={item.item_id} className="hover:bg-stone-50/50 transition-colors">
                  <td className="px-6 py-4">
                    <span className="text-xs font-medium text-stone-800">{item.name}</span>
                  </td>
                  <td className="px-6 py-4 text-center">
                    <span className="text-xs font-medium text-stone-600">{item.qty}개</span>
                  </td>
                </tr>
              ))}
            </tbody>
            {ticket.order_total != null && (
              <tfoot className="bg-emerald-50/30">
                <tr>
                  <td className="px-6 py-4 text-right text-xs font-bold text-emerald-900">
                    합계 금액
                  </td>
                  <td className="px-6 py-4 text-center text-sm font-extrabold text-emerald-900">
                    {formatPrice(ticket.order_total)}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      )}

      {/* ── Exchange shipment panel ── */}
      {ticket.action_type === 'exchange' && ticket.status === 'completed' && (
        <ExchangeShipmentPanel orderId={ticket.order_id} relatedTicketId={ticket.id} />
      )}

      {/* ── Bottom actions ── */}
      <div className="flex justify-center gap-4 pt-2 pb-8">
        <button
          type="button"
          className="flex items-center gap-2 px-6 py-3 text-stone-500 text-xs font-bold hover:text-stone-800 transition-colors rounded-xl"
        >
          <span className="material-symbols-outlined text-lg" aria-hidden="true">print</span>
          문서 출력
        </button>
        <button
          type="button"
          className="flex items-center gap-2 px-6 py-3 text-stone-500 text-xs font-bold hover:text-stone-800 transition-colors rounded-xl"
        >
          <span className="material-symbols-outlined text-lg" aria-hidden="true">history</span>
          처리 이력 보기
        </button>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// TicketsPage
// ──────────────────────────────────────────

export default function TicketsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');
  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>('all');
  const [actionFilter, setActionFilter] = useState<ActionFilter>('all');
  const [selected, setSelected] = useState<Ticket | null>(null);

  const handleCategoryChange = (cat: CategoryFilter) => {
    setCategoryFilter(cat);
    setActionFilter('all');
    setSelected(null);
  };

  const { data: tickets = [], isLoading } = useTickets({
    status: statusFilter === 'all' ? undefined : statusFilter,
    action_type: actionFilter === 'all' ? undefined : actionFilter,
  });
  const { data: stats } = useTicketStats();

  const q = searchParams.get('q')?.trim() ?? '';
  const qLower = q.toLowerCase();

  // 카테고리 대분류 필터 (프론트 필터링)
  const allowedActionTypes = CATEGORY_ACTION_TYPES[categoryFilter];
  const categoryFiltered = categoryFilter === 'all'
    ? tickets
    : tickets.filter((t) => allowedActionTypes.includes(t.action_type));

  const visibleTickets = q
    ? categoryFiltered.filter(
        (t) =>
          String(t.id).includes(qLower) ||
          String(t.order_id).includes(qLower) ||
          (t.user_name?.toLowerCase().includes(qLower) ?? false),
      )
    : categoryFiltered;

  // URL ?ticketId=X 로 진입하면 해당 티켓을 자동 선택
  useEffect(() => {
    const ticketId = Number(searchParams.get('ticketId'));
    if (!ticketId || tickets.length === 0) return;
    const target = tickets.find((t) => t.id === ticketId);
    if (target) {
      setSelected(target);
      setSearchParams(q ? { q } : {}, { replace: true }); // ticketId 소비, q는 유지
    }
  }, [tickets, searchParams, setSearchParams]);

  return (
    <div className="h-[calc(100vh-4rem)] flex flex-col">

      {/* ── Layout: left panel + right panel ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ────── Left Panel ────── */}
        <div className="w-[320px] shrink-0 bg-surface-container-low flex flex-col border-r border-stone-200/60">

          {/* Panel header */}
          <div className="p-6 pb-4 shrink-0">
            <h2 className="text-xl font-bold text-on-surface mb-6">CS 운영</h2>

            {/* Status tabs — pill style */}
            <div className="grid grid-cols-5 gap-0.5 mb-4 bg-surface-container rounded-lg p-1">
              {STATUS_TABS.map((tab) => {
                const count =
                  tab.value === 'all'
                    ? stats?.total
                    : stats?.[tab.value as TicketStatus];
                const isActive = statusFilter === tab.value;

                return (
                  <button
                    key={tab.value}
                    type="button"
                    onClick={() => { setStatusFilter(tab.value); setSelected(null); }}
                    className={`flex flex-col items-center justify-center py-1.5 text-[11px] font-semibold rounded-md transition-colors ${
                      isActive
                        ? 'bg-white text-primary shadow-sm'
                        : 'text-stone-500 hover:bg-white/50'
                    }`}
                    aria-pressed={isActive}
                  >
                    <span>{tab.label}</span>
                    <span className={`text-[10px] font-bold leading-tight ${isActive ? 'text-primary/70' : 'text-stone-400'}`}>
                      {count ?? 0}
                    </span>
                  </button>
                );
              })}
            </div>

            {/* Category (대분류) */}
            <div className="flex gap-1.5 mb-3">
              {CATEGORY_TABS.map((cat) => {
                const isActive = categoryFilter === cat.value;
                // 카테고리별 카운트 계산
                const catCount = cat.value === 'all'
                  ? (stats?.total ?? 0)
                  : cat.value === 'delivery'
                    ? 0
                    : (stats?.exchange ?? 0) + (stats?.cancel ?? 0);
                return (
                  <button
                    key={cat.value}
                    type="button"
                    onClick={() => handleCategoryChange(cat.value)}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[11px] font-bold border transition-colors ${
                      isActive
                        ? 'bg-stone-800 text-white border-stone-800 shadow-sm'
                        : 'bg-white text-stone-600 border-stone-300 hover:border-stone-500 hover:text-stone-800'
                    }`}
                    aria-pressed={isActive}
                  >
                    {cat.label}
                    <span className={`text-[10px] font-medium ${isActive ? 'text-white/70' : 'text-stone-400'}`}>
                      {catCount}
                    </span>
                  </button>
                );
              })}
              <span className="ml-auto text-[11px] text-stone-400 font-medium self-center">
                {visibleTickets.length}건
              </span>
            </div>

            {/* Action type (소분류) */}
            {categoryFilter === 'delivery' ? (
              <p className="text-[11px] text-stone-400 mb-2 pl-1">배송 관련 티켓 유형은 준비 중입니다.</p>
            ) : (
              <div className="flex items-center gap-1.5 mb-2">
                {CATEGORY_SUB_ACTIONS[categoryFilter].map(({ value: val, label }) => {
                  const isActive = actionFilter === val;
                  let baseClass = '';
                  if (val === 'all') {
                    baseClass = isActive
                      ? 'bg-stone-700 text-white border-stone-700 shadow-sm'
                      : 'bg-white text-stone-600 border-stone-300 hover:border-stone-500 hover:text-stone-800';
                  } else if (val === 'exchange') {
                    baseClass = isActive
                      ? 'bg-violet-600 text-white border-violet-600 shadow-sm'
                      : 'bg-white text-violet-700 border-violet-400 hover:border-violet-600 hover:bg-violet-50';
                  } else {
                    baseClass = isActive
                      ? 'bg-rose-500 text-white border-rose-500 shadow-sm'
                      : 'bg-white text-rose-600 border-rose-400 hover:border-rose-600 hover:bg-rose-50';
                  }
                  return (
                    <button
                      key={val}
                      type="button"
                      onClick={() => { setActionFilter(val); setSelected(null); }}
                      className={`px-3 py-1 rounded-full text-[11px] font-bold tracking-wider border transition-colors ${baseClass}`}
                      aria-pressed={isActive}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          {/* Ticket list */}
          <div className="flex-1 overflow-y-auto px-4 space-y-3 pb-6">
            {isLoading ? (
              /* Loading skeletons */
              Array.from({ length: 4 }).map((_, i) => (
                <div
                  key={i}
                  className="p-4 rounded-xl bg-white/60 border border-stone-100 animate-pulse space-y-2"
                  aria-hidden="true"
                >
                  <div className="flex justify-between">
                    <div className="h-3 w-20 bg-stone-200 rounded" />
                    <div className="h-2 w-10 bg-stone-100 rounded" />
                  </div>
                  <div className="flex gap-2">
                    <div className="h-4 w-10 bg-stone-100 rounded" />
                    <div className="h-4 w-12 bg-stone-100 rounded" />
                  </div>
                  <div className="h-3 w-full bg-stone-100 rounded" />
                  <div className="h-3 w-2/3 bg-stone-100 rounded" />
                </div>
              ))
            ) : q && visibleTickets.length === 0 ? (
              <div className="pt-12 text-center space-y-2">
                <span className="material-symbols-outlined text-stone-200 text-[48px] block" aria-hidden="true">
                  search_off
                </span>
                <p className="text-sm text-stone-400">
                  <span className="font-medium text-stone-500">"{q}"</span> 검색 결과 없음
                </p>
              </div>
            ) : tickets.length === 0 ? (
              <div className="pt-12 text-center space-y-2">
                <span
                  className="material-symbols-outlined text-stone-200 text-[48px] block"
                  aria-hidden="true"
                >
                  inbox
                </span>
                <p className="text-sm text-stone-400">티켓이 없습니다</p>
              </div>
            ) : (
              visibleTickets.map((ticket) => (
                <TicketCard
                  key={ticket.id}
                  ticket={ticket}
                  selected={selected?.id === ticket.id}
                  onClick={() => setSelected(ticket)}
                />
              ))
            )}
          </div>
        </div>

        {/* ────── Right Panel ────── */}
        <div className="flex-1 bg-background overflow-y-auto p-8">
          {selected ? (
            <TicketDetail
              key={selected.id}
              ticket={tickets.find((t) => t.id === selected.id) ?? selected}
            />
          ) : (
            <div className="h-full flex items-center justify-center">
              <div className="text-center space-y-3">
                <span
                  className="material-symbols-outlined text-stone-200 text-[64px] block"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                  aria-hidden="true"
                >
                  support_agent
                </span>
                <p className="text-sm text-stone-400 font-medium">목록에서 티켓을 선택하세요</p>
                <p className="text-xs text-stone-300">
                  좌측 패널에서 티켓을 클릭하면 상세 내용을 확인할 수 있습니다.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
