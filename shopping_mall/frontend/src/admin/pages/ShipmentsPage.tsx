import { useState } from 'react';
import { NavLink } from 'react-router-dom';
import { useAdminShipments, useCheckShipment, useCreateShipment } from '@/admin/hooks/useShipments';
import {
  SHIPMENT_STATUS_LABEL,
  SHIPMENT_STATUS_COLOR,
  SHIPMENT_STATUS_STEP,
} from '@/admin/types/shipment';
import type { AdminShipment } from '@/admin/types/shipment';
import { formatDate, formatPrice } from '@/lib/utils';

const CARRIERS = ['CJ대한통운', '한진', '로젠', '우체국택배', '롯데택배'];

const STATUS_TABS: { value: string; label: string }[] = [
  { value: '', label: '전체' },
  { value: 'registered', label: '등록됨' },
  { value: 'picked_up', label: '집화' },
  { value: 'in_transit', label: '배송중' },
  { value: 'delivered', label: '배송완료' },
];

// ── 배송 등록 폼 ──────────────────────────────────────────────────────────────

function ShipmentRegisterForm({
  defaultOrderId,
  relatedTicketId,
  onSuccess,
}: {
  defaultOrderId?: number;
  relatedTicketId?: number;
  onSuccess?: () => void;
}) {
  const [orderId, setOrderId] = useState(defaultOrderId ? String(defaultOrderId) : '');
  const [carrier, setCarrier] = useState(CARRIERS[0]);
  const [trackingNumber, setTrackingNumber] = useState('');
  const [expectedArrival, setExpectedArrival] = useState('');
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  const { mutate: create, isPending } = useCreateShipment();

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!orderId || !trackingNumber) return;
    create(
      {
        order_id: Number(orderId),
        carrier,
        tracking_number: trackingNumber,
        expected_arrival: expectedArrival || undefined,
        ...(relatedTicketId != null && { related_ticket_id: relatedTicketId }),
      },
      {
        onSuccess: () => {
          setTrackingNumber('');
          setExpectedArrival('');
          if (!defaultOrderId) setOrderId('');
          setFeedback({ type: 'success', msg: '배송이 등록됐습니다.' });
          setTimeout(() => setFeedback(null), 3000);
          onSuccess?.();
        },
        onError: () => {
          setFeedback({ type: 'error', msg: '등록 실패. 다시 시도해주세요.' });
          setTimeout(() => setFeedback(null), 3000);
        },
      },
    );
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-3">
      {feedback && (
        <div
          className={`px-3 py-2 rounded-lg text-xs font-medium ${
            feedback.type === 'success'
              ? 'bg-green-50 border border-green-200 text-green-700'
              : 'bg-red-50 border border-red-200 text-red-700'
          }`}
        >
          {feedback.type === 'success' ? '✓ ' : '✕ '}{feedback.msg}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-500 mb-1">주문번호 *</label>
          <input
            type="number"
            value={orderId}
            onChange={(e) => setOrderId(e.target.value)}
            disabled={!!defaultOrderId}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-[#03C75A] disabled:bg-gray-50 disabled:text-gray-500"
            placeholder="주문 ID"
            required
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">택배사 *</label>
          <select
            value={carrier}
            onChange={(e) => setCarrier(e.target.value)}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-[#03C75A] bg-white"
          >
            {CARRIERS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">운송장번호 *</label>
          <input
            type="text"
            value={trackingNumber}
            onChange={(e) => setTrackingNumber(e.target.value)}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-[#03C75A]"
            placeholder="운송장번호"
            required
          />
        </div>
        <div>
          <label className="block text-xs text-gray-500 mb-1">도착 예정일</label>
          <input
            type="date"
            value={expectedArrival}
            onChange={(e) => setExpectedArrival(e.target.value)}
            className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm outline-none focus:border-[#03C75A]"
          />
        </div>
      </div>

      <button
        type="submit"
        disabled={isPending || !orderId || !trackingNumber}
        className="w-full bg-[#03C75A] text-white py-2 rounded-lg text-sm font-medium hover:bg-[#02b050] disabled:opacity-40 transition-colors"
      >
        {isPending ? '등록 중...' : '배송 등록'}
      </button>
    </form>
  );
}

// ── 배송 목록 아이템 ───────────────────────────────────────────────────────────

function ShipmentRow({
  shipment,
  selected,
  onClick,
}: {
  shipment: AdminShipment;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3.5 border-b border-gray-100 transition-colors ${
        selected
          ? 'bg-green-50 border-l-4 border-l-[#03C75A]'
          : 'border-l-4 border-l-transparent hover:bg-gray-50'
      }`}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-xs font-medium text-gray-700">주문 #{shipment.order_id}</span>
        <span
          className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
            SHIPMENT_STATUS_COLOR[shipment.status]
          }`}
        >
          {SHIPMENT_STATUS_LABEL[shipment.status]}
        </span>
      </div>
      <p className="text-xs text-gray-500 mb-1">
        {shipment.carrier} · {shipment.tracking_number}
      </p>
      <div className="flex items-center justify-between">
        {shipment.related_ticket ? (
          <span className="text-[10px] bg-purple-50 text-purple-600 px-1.5 py-0.5 rounded font-medium">
            교환 티켓 #{shipment.related_ticket.id}
          </span>
        ) : (
          <span className="text-[10px] text-gray-300">일반 배송</span>
        )}
        <span className="text-[10px] text-gray-400">
          {shipment.created_at ? formatDate(shipment.created_at, 'MM/dd') : '-'}
        </span>
      </div>
    </button>
  );
}

// ── 배송 상세 ─────────────────────────────────────────────────────────────────

function ShipmentDetail({ shipment }: { shipment: AdminShipment }) {
  const { mutate: check, isPending: checking } = useCheckShipment();
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);
  const [showExchangeForm, setShowExchangeForm] = useState(false);

  const currentStep = SHIPMENT_STATUS_STEP.indexOf(shipment.status);

  const handleCheck = () => {
    check(shipment.id, {
      onSuccess: () => {
        setFeedback({ type: 'success', msg: '배송 상태가 업데이트됐습니다.' });
        setTimeout(() => setFeedback(null), 3000);
      },
      onError: () => {
        setFeedback({ type: 'error', msg: '상태 업데이트 실패.' });
        setTimeout(() => setFeedback(null), 3000);
      },
    });
  };

  return (
    <div className="p-6 space-y-5">
      {/* 헤더 */}
      <div className="flex items-start justify-between">
        <div>
          <h3 className="text-lg font-bold text-gray-900">배송 #{shipment.id}</h3>
          <p className="text-xs text-gray-400 mt-0.5">
            주문 #{shipment.order_id}
            {shipment.order_total != null && ` · ${formatPrice(shipment.order_total)}`}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {feedback && (
            <span
              className={`text-xs px-3 py-1.5 rounded-lg font-medium ${
                feedback.type === 'success'
                  ? 'bg-green-50 text-green-700'
                  : 'bg-red-50 text-red-700'
              }`}
            >
              {feedback.msg}
            </span>
          )}
          <button
            onClick={handleCheck}
            disabled={checking || shipment.status === 'delivered'}
            className="flex items-center gap-1.5 px-3 py-2 border border-gray-200 rounded-lg text-sm text-gray-600 hover:bg-gray-50 disabled:opacity-40 transition-colors"
          >
            <span className={checking ? 'animate-spin' : ''}>↻</span>
            상태 업데이트
          </button>
        </div>
      </div>

      {/* 배송 단계 타임라인 */}
      <div className="bg-gray-50 rounded-xl p-4">
        <p className="text-xs font-semibold text-gray-500 mb-3">배송 단계</p>
        <div className="flex items-center gap-1">
          {SHIPMENT_STATUS_STEP.map((step, i) => {
            const isDone = i <= currentStep;
            const isActive = i === currentStep;
            return (
              <div key={step} className="flex items-center gap-1 flex-1">
                <div className="flex flex-col items-center gap-1 min-w-0">
                  <div
                    className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold border-2 transition-colors shrink-0 ${
                      isDone
                        ? isActive
                          ? 'border-[#03C75A] bg-white text-[#03C75A]'
                          : 'border-[#03C75A] bg-[#03C75A] text-white'
                        : 'border-gray-200 bg-white text-gray-300'
                    }`}
                  >
                    {isDone && !isActive ? '✓' : i + 1}
                  </div>
                  <span
                    className={`text-[10px] text-center leading-tight whitespace-nowrap ${
                      isActive ? 'text-gray-800 font-semibold' : isDone ? 'text-gray-500' : 'text-gray-300'
                    }`}
                  >
                    {SHIPMENT_STATUS_LABEL[step]}
                  </span>
                </div>
                {i < SHIPMENT_STATUS_STEP.length - 1 && (
                  <div
                    className={`flex-1 h-0.5 mb-4 rounded-full ${
                      i < currentStep ? 'bg-[#03C75A]' : 'bg-gray-200'
                    }`}
                  />
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* 배송 정보 */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">배송 정보</h4>
        <div className="grid grid-cols-2 gap-y-2.5 text-sm">
          <span className="text-gray-400">택배사</span>
          <span className="font-medium text-gray-800">{shipment.carrier}</span>
          <span className="text-gray-400">운송장번호</span>
          <span className="font-mono text-gray-800">{shipment.tracking_number}</span>
          <span className="text-gray-400">도착 예정</span>
          <span className="text-gray-800">
            {shipment.expected_arrival
              ? formatDate(shipment.expected_arrival, 'yyyy-MM-dd')
              : '-'}
          </span>
          <span className="text-gray-400">마지막 확인</span>
          <span className="text-gray-500 text-xs">
            {shipment.last_checked_at
              ? formatDate(shipment.last_checked_at)
              : '-'}
          </span>
          {shipment.delivered_at && (
            <>
              <span className="text-gray-400">배송완료일</span>
              <span className="text-[#03C75A] font-medium">
                {formatDate(shipment.delivered_at, 'yyyy-MM-dd')}
              </span>
            </>
          )}
        </div>
      </div>

      {/* 연관 교환 티켓 */}
      {shipment.related_ticket && (
        <div className="bg-purple-50 rounded-xl border border-purple-200 p-4 space-y-2">
          <div className="flex items-center justify-between">
            <h4 className="text-xs font-semibold text-purple-700">연관 교환 티켓 #{shipment.related_ticket.id}</h4>
            <NavLink
              to="/admin/tickets"
              className="text-xs text-purple-600 hover:underline"
            >
              티켓 보기 →
            </NavLink>
          </div>
          <p className="text-xs text-purple-800">{shipment.related_ticket.reason}</p>
          <div className="flex items-center gap-2">
            <span
              className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                shipment.related_ticket.status === 'completed'
                  ? 'bg-green-100 text-green-700'
                  : shipment.related_ticket.status === 'processing'
                  ? 'bg-amber-100 text-amber-700'
                  : 'bg-blue-100 text-blue-700'
              }`}
            >
              {shipment.related_ticket.status === 'completed'
                ? '완료'
                : shipment.related_ticket.status === 'processing'
                ? '처리중'
                : '접수됨'}
            </span>
          </div>
        </div>
      )}

      {/* 교환 배송 등록 (교환 티켓이 완료됐고 이 배송이 원본 배송일 때만) */}
      {shipment.related_ticket?.status === 'completed' && shipment.related_ticket_id == null && (
        <div className="border border-dashed border-[#03C75A] rounded-xl p-4">
          <button
            type="button"
            className="flex items-center justify-between cursor-pointer w-full text-left"
            onClick={() => setShowExchangeForm((v) => !v)}
            aria-expanded={showExchangeForm}
            aria-controls="exchange-shipment-form"
          >
            <div className="flex items-center gap-2">
              <span className="text-[#03C75A] text-lg" aria-hidden="true">+</span>
              <p className="text-sm font-medium text-[#03C75A]">교환 상품 배송 등록</p>
            </div>
            <span className="text-gray-400 text-xs" aria-hidden="true">{showExchangeForm ? '▲ 닫기' : '▼ 열기'}</span>
          </button>
          {showExchangeForm && (
            <div id="exchange-shipment-form" className="mt-4 pt-4 border-t border-dashed border-[#03C75A]/30">
              <p className="text-xs text-gray-500 mb-3">
                주문 #{shipment.order_id}에 교환 상품을 발송하고 운송장을 등록하세요.
              </p>
              <ShipmentRegisterForm
                defaultOrderId={shipment.order_id}
                relatedTicketId={shipment.related_ticket?.id}
                onSuccess={() => setShowExchangeForm(false)}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 메인 페이지 ───────────────────────────────────────────────────────────────

export default function ShipmentsPage() {
  const [statusFilter, setStatusFilter] = useState('');
  const [selected, setSelected] = useState<AdminShipment | null>(null);
  const [showRegisterForm, setShowRegisterForm] = useState(false);

  const { data: shipments = [], isLoading } = useAdminShipments(statusFilter || undefined);

  // 선택된 배송의 최신 상태 (상태 업데이트 후 반영)
  const selectedShipment = selected
    ? (shipments.find((s) => s.id === selected.id) ?? selected)
    : null;

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      {/* 상단 헤더 */}
      <div className="px-6 pt-5 pb-0 border-b border-gray-200 bg-white shrink-0">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-xl font-bold text-gray-900">배송 관리</h2>
          <div className="flex items-center gap-3">
            <button
              onClick={() => setShowRegisterForm((v) => !v)}
              className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                showRegisterForm
                  ? 'bg-gray-100 text-gray-700'
                  : 'bg-[#03C75A] text-white hover:bg-[#02b050]'
              }`}
            >
              <span>{showRegisterForm ? '✕' : '+'}</span>
              배송 등록
            </button>
            <span className="text-sm text-gray-400">{shipments.length}건</span>
          </div>
        </div>

        {/* 상태 탭 */}
        <div className="flex gap-0">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => { setStatusFilter(tab.value); setSelected(null); }}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${
                statusFilter === tab.value
                  ? 'border-[#03C75A] text-[#03C75A]'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* 배송 등록 폼 드롭다운 */}
      {showRegisterForm && (
        <div className="px-6 py-4 bg-white border-b border-gray-200 shrink-0">
          <div className="max-w-lg">
            <p className="text-sm font-semibold text-gray-700 mb-3">새 배송 등록</p>
            <ShipmentRegisterForm
              onSuccess={() => setShowRegisterForm(false)}
            />
          </div>
        </div>
      )}

      {/* split 레이아웃 */}
      <div className="flex flex-1 overflow-hidden">
        {/* 배송 목록 */}
        <div className="w-72 shrink-0 border-r border-gray-200 bg-white overflow-y-auto">
          {isLoading ? (
            <div className="p-6 text-center text-gray-400 text-sm">로딩 중...</div>
          ) : shipments.length === 0 ? (
            <div className="p-8 text-center">
              <p className="text-2xl mb-2">🚚</p>
              <p className="text-sm text-gray-400">배송 내역이 없습니다</p>
            </div>
          ) : (
            shipments.map((s) => (
              <ShipmentRow
                key={s.id}
                shipment={s}
                selected={selected?.id === s.id}
                onClick={() => setSelected(s)}
              />
            ))
          )}
        </div>

        {/* 배송 상세 */}
        <div className="flex-1 bg-gray-50 overflow-y-auto">
          {selectedShipment ? (
            <ShipmentDetail
              key={selectedShipment.id}
              shipment={selectedShipment}
            />
          ) : (
            <div className="h-full flex items-center justify-center text-gray-400">
              <div className="text-center space-y-2">
                <div className="text-4xl">🚚</div>
                <p className="text-sm">목록에서 배송을 선택하세요</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
