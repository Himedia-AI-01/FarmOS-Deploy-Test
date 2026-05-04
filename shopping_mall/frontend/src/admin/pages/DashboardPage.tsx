import { NavLink } from 'react-router-dom';
import { useDashboard, useEscalatedLogs, useDashboardTicketStats } from '@/admin/hooks/useDashboard';
import StatCard from '@/admin/components/dashboard/StatCard';
import RevenueChart from '@/admin/components/dashboard/RevenueChart';
import SegmentPie from '@/admin/components/dashboard/SegmentPie';
import { formatPrice, formatDate, truncate } from '@/lib/utils';
import {
  TICKET_STATUS_LABEL,
  TICKET_STATUS_COLOR,
  TICKET_ACTION_LABEL,
  TICKET_ACTION_COLOR,
  TICKET_FLAG_COLOR,
} from '@/admin/types/ticket';
import { useTickets } from '@/admin/hooks/useTickets';
import type { ChatLog } from '@/admin/types/chatlog';
import type { Ticket, TicketFlag } from '@/admin/types/ticket';
import { useMemo, useState } from 'react';

// ──────────────────────────────────────────
// Alert Banner
// ──────────────────────────────────────────

interface AlertBannerProps {
  variant: 'error' | 'warning';
  icon: string;
  title: string;
  message: string;
  linkTo?: string;
  onDismiss?: () => void;
}

function AlertBanner({ variant, icon, title, message, linkTo, onDismiss }: AlertBannerProps) {
  const styles = {
    error: {
      wrapper: 'bg-white border-red-100/50 bg-red-50/10 hover:shadow-[0_4px_16px_-4px_rgba(0,0,0,0.08)]',
      iconBg: 'bg-red-100/50',
      iconColor: 'text-red-500',
    },
    warning: {
      wrapper: 'bg-white border-amber-100/50 bg-amber-50/10 hover:shadow-[0_4px_16px_-4px_rgba(0,0,0,0.08)]',
      iconBg: 'bg-amber-100/50',
      iconColor: 'text-amber-600',
    },
  };

  const s = styles[variant];

  const wrapperClass = `flex items-center justify-between px-5 py-4 border rounded-2xl shadow-[0_1px_3px_rgba(0,0,0,0.05)] transition-all group ${s.wrapper}`;

  const inner = (
    <div className="flex items-center gap-4">
      <div className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 ${s.iconBg}`}>
        <span
          className={`material-symbols-outlined text-[18px] ${s.iconColor}`}
          style={{ fontVariationSettings: "'FILL' 1" }}
          aria-hidden="true"
        >
          {icon}
        </span>
      </div>
      <div>
        <span className="font-bold text-sm text-stone-900 block">{title}</span>
        <span className="text-[13px] text-stone-500 font-medium">{message}</span>
      </div>
    </div>
  );

  const dismissButton = onDismiss ? (
    <button
      type="button"
      onClick={(e) => { e.preventDefault(); onDismiss(); }}
      className="p-1.5 hover:bg-stone-50 rounded-lg transition-colors shrink-0 ml-4"
      aria-label="알림 닫기"
    >
      <span className="material-symbols-outlined text-stone-400 text-[18px]" aria-hidden="true">
        close
      </span>
    </button>
  ) : null;

  if (linkTo) {
    return (
      <div className={wrapperClass}>
        <NavLink to={linkTo} className="flex-1">
          {inner}
        </NavLink>
        {dismissButton}
      </div>
    );
  }
  return (
    <div className={wrapperClass}>
      {inner}
      {dismissButton}
    </div>
  );
}

// ──────────────────────────────────────────
// Ticket Card (bottom feed)
// ──────────────────────────────────────────

interface TicketRowProps {
  icon: string;
  title: string;
  preview: string;
  timeAgo: string;
  statusLabel: string;
  statusClass: string;
  actionLabel: string;
  actionClass: string;
  flags?: TicketFlag[];
}

function TicketRow({
  icon,
  title,
  preview,
  timeAgo,
  statusLabel,
  statusClass,
  actionLabel,
  actionClass,
  flags = [],
}: TicketRowProps) {
  return (
    <div className="p-5 hover:bg-stone-50/50 transition-colors flex gap-4">
      <div className="w-10 h-10 rounded-xl bg-stone-50 flex items-center justify-center shrink-0">
        <span className="material-symbols-outlined text-stone-400 text-[18px]" aria-hidden="true">
          {icon}
        </span>
      </div>
      <div className="flex-1 space-y-1 min-w-0">
        <div className="flex justify-between items-start gap-2">
          <h4 className="text-sm font-bold text-stone-900 truncate">{title}</h4>
          <span className="text-[10px] text-stone-400 whitespace-nowrap shrink-0">{timeAgo}</span>
        </div>
        <p className="text-xs text-stone-500 line-clamp-1">{preview}</p>
        <div className="flex flex-wrap items-center gap-2 mt-1.5">
          <span className={`px-2 py-0.5 text-[10px] font-bold rounded-md ${actionClass}`}>
            {actionLabel}
          </span>
          <span className={`px-2 py-0.5 text-[10px] font-bold rounded-md ${statusClass}`}>
            {statusLabel}
          </span>
          {flags.map((flag) => (
            <span
              key={flag.code}
              className={`px-2 py-0.5 text-[10px] font-bold rounded-md ${TICKET_FLAG_COLOR[flag.severity]}`}
            >
              {flag.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

interface OpsMetricProps {
  icon: string;
  label: string;
  value: string;
  tone: 'normal' | 'warning' | 'danger';
  to: string;
}

function OpsMetric({ icon, label, value, tone, to }: OpsMetricProps) {
  const toneClass = {
    normal: 'bg-white border-stone-200 text-stone-700',
    warning: 'bg-amber-50 border-amber-300 text-amber-800',
    danger: 'bg-rose-50 border-rose-300 text-rose-800',
  }[tone];

  return (
    <NavLink
      to={to}
      className={`flex items-center gap-3 rounded-xl border px-4 py-3 transition-colors hover:bg-white ${toneClass}`}
    >
      <span className="material-symbols-outlined text-[20px]" aria-hidden="true">
        {icon}
      </span>
      <div>
        <p className="text-[11px] font-bold text-stone-500">{label}</p>
        <p className="text-lg font-black tabular-nums">{value}</p>
      </div>
    </NavLink>
  );
}

// ──────────────────────────────────────────
// Escalation card
// ──────────────────────────────────────────

interface EscalationCardProps {
  log: ChatLog;
  variant: 'critical' | 'warning' | 'resolved';
}

function EscalationCard({ log, variant }: EscalationCardProps) {
  const styles = {
    critical: {
      wrapper: 'bg-red-50/30 border-red-100/50',
      dot: 'bg-red-500',
    },
    warning: {
      wrapper: 'bg-amber-50/30 border-amber-100/50',
      dot: 'bg-amber-500',
    },
    resolved: {
      wrapper: 'bg-stone-50/50 border-stone-100/50 opacity-60',
      dot: 'bg-stone-300',
    },
  };
  const s = styles[variant];

  return (
    <div className={`border rounded-2xl p-4 transition-all hover:brightness-95 ${s.wrapper}`}>
      <div className="flex gap-4">
        <div
          className={`w-2 h-2 mt-1.5 rounded-full shrink-0 ${s.dot}`}
          aria-hidden="true"
        />
        <div className="space-y-1.5 flex-1 min-w-0">
          <div className="flex justify-between items-center gap-2">
            <span className="text-xs font-bold text-stone-900 truncate">
              {truncate(log.question, 40)}
            </span>
            <span className="text-[10px] text-stone-400 whitespace-nowrap shrink-0">
              {formatDate(log.created_at, 'HH:mm')}
            </span>
          </div>
          <p className="text-xs text-stone-500 leading-relaxed line-clamp-2">
            {log.question}
          </p>
          <p className="text-[10px] text-stone-400">
            {log.user_id ? `회원 #${log.user_id}` : '비회원'}
          </p>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// DashboardPage
// ──────────────────────────────────────────

// Map TicketActionType to appropriate ticket icon
const ACTION_ICON_MAP: Record<string, string> = {
  cancel: 'undo',
  exchange: 'swap_horiz',
  change: 'edit_note',
};

export default function DashboardPage() {
  const { data: dashboard, isLoading, isError } = useDashboard();
  const { data: escalated = [] } = useEscalatedLogs();
  const { data: ticketStats } = useDashboardTicketStats();
  const { data: recentTickets = [] } = useTickets({ limit: 5 });

  const [dismissedEscalation, setDismissedEscalation] = useState(false);
  const [dismissedTickets, setDismissedTickets] = useState(false);

  const pendingTickets = (ticketStats?.received ?? 0) + (ticketStats?.processing ?? 0);
  const flaggedTickets = useMemo(
    () => recentTickets.filter((ticket: Ticket) => (ticket.flags ?? []).length > 0).length,
    [recentTickets],
  );

  // 에스컬레이션된 고유 세션 수 — AdminLayout과 동일 기준
  const escalatedSessionCount = new Set(
    escalated.map((l: ChatLog) => l.session_id).filter((id: number | null): id is number => id != null),
  ).size;

  if (isLoading) {
    return (
      <div className="p-8 space-y-8 max-w-7xl mx-auto w-full">
        <DashboardSkeleton />
      </div>
    );
  }

  if (isError || !dashboard) {
    return (
      <div className="p-8 max-w-7xl mx-auto w-full">
        <div className="bg-white border border-red-100 rounded-3xl p-8 flex items-center gap-4">
          <div className="w-10 h-10 rounded-xl bg-red-50 flex items-center justify-center">
            <span
              className="material-symbols-outlined text-red-500 text-[22px]"
              style={{ fontVariationSettings: "'FILL' 1" }}
              aria-hidden="true"
            >
              error
            </span>
          </div>
          <div>
            <p className="text-sm font-bold text-stone-900">데이터를 불러올 수 없습니다</p>
            <p className="text-xs text-stone-500 mt-0.5">서버 연결 상태를 확인해 주세요.</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-8 space-y-8 max-w-7xl mx-auto w-full">

      {/* ── Page header ── */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-stone-900">대시보드</h1>
          <p className="text-sm text-stone-400 mt-0.5">
            {new Date().toLocaleDateString('ko-KR', {
              year: 'numeric',
              month: 'long',
              day: 'numeric',
              weekday: 'long',
            })}
          </p>
        </div>
      </div>

      {/* ── Alert Banners ── */}
      {(escalated.length > 0 || pendingTickets > 0) && (
        <div className="space-y-3">
          {escalated.length > 0 && !dismissedEscalation && (
            <AlertBanner
              variant="error"
              icon="priority_high"
              title="에스컬레이션 알림"
              message={`${escalatedSessionCount}건의 긴급 문의가 상담원 연결을 대기 중입니다.`}
              linkTo="/admin/chatbot"
              onDismiss={() => setDismissedEscalation(true)}
            />
          )}
          {pendingTickets > 0 && !dismissedTickets && (
            <AlertBanner
              variant="warning"
              icon="hourglass_empty"
              title="대기 중인 티켓"
              message={`미처리 티켓 ${pendingTickets}건이 처리를 기다리고 있습니다.`}
              linkTo="/admin/tickets"
              onDismiss={() => setDismissedTickets(true)}
            />
          )}
        </div>
      )}

      {/* ── KPI Stats ── */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatCard
          title="오늘 매출"
          value={formatPrice(dashboard.today_revenue)}
          change={dashboard.revenue_change}
          icon="payments"
          iconBgClass="bg-emerald-50"
          iconColorClass="text-emerald-600"
        />
        <StatCard
          title="신규 주문"
          value={`${dashboard.today_orders}`}
          change={dashboard.orders_change}
          icon="shopping_basket"
          iconBgClass="bg-stone-50"
          iconColorClass="text-stone-600"
        />
        <StatCard
          title="신규 고객"
          value={`${dashboard.new_customers}명`}
          change={dashboard.customers_change}
          icon="person_add"
          iconBgClass="bg-stone-50"
          iconColorClass="text-stone-600"
        />
        <StatCard
          title="처리 대기 티켓"
          value={`${pendingTickets}`}
          change={pendingTickets > 0 ? pendingTickets : null}
          changeSuffix="건"
          icon="confirmation_number"
          iconBgClass="bg-red-50"
          iconColorClass="text-red-500"
          invertTrend
        />
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <OpsMetric
          icon="support_agent"
          label="상담원 확인 필요"
          value={`${escalatedSessionCount}건`}
          tone={escalatedSessionCount > 0 ? 'danger' : 'normal'}
          to="/admin/chatbot"
        />
        <OpsMetric
          icon="confirmation_number"
          label="처리 중인 티켓"
          value={`${pendingTickets}건`}
          tone={pendingTickets > 0 ? 'warning' : 'normal'}
          to="/admin/tickets"
        />
        <OpsMetric
          icon="priority_high"
          label="운영 플래그"
          value={`${flaggedTickets}건`}
          tone={flaggedTickets > 0 ? 'warning' : 'normal'}
          to="/admin/tickets"
        />
      </div>

      {/* ── Charts ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2">
          <RevenueChart data={dashboard.weekly_revenue ?? []} />
        </div>
        <div>
          <SegmentPie data={dashboard.segments ?? []} />
        </div>
      </div>

      {/* ── Bottom feeds ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 pb-12">

        {/* Recent Tickets */}
        <div className="bg-white rounded-3xl shadow-sm overflow-hidden flex flex-col border border-stone-100">
          <div className="p-6 border-b border-stone-50 flex justify-between items-center">
            <h3 className="font-bold text-stone-900">최근 티켓</h3>
            <NavLink
              to="/admin/tickets"
              className="text-xs font-bold text-emerald-700 hover:underline"
            >
              모두 보기
            </NavLink>
          </div>

          {recentTickets.length === 0 ? (
            <div className="p-8 text-center">
              <span
                className="material-symbols-outlined text-stone-200 text-[48px] block mb-2"
                aria-hidden="true"
              >
                inbox
              </span>
              <p className="text-sm text-stone-400">접수된 티켓이 없습니다</p>
            </div>
          ) : (
            <div className="divide-y divide-stone-50">
              {recentTickets.map((ticket: Ticket) => (
                <TicketRow
                  key={ticket.id}
                  icon={ACTION_ICON_MAP[ticket.action_type] ?? 'chat_bubble'}
                  title={`${TICKET_ACTION_LABEL[ticket.action_type]} 요청 - #${ticket.id}`}
                  preview={ticket.reason}
                  timeAgo={formatDate(ticket.created_at, 'MM/dd HH:mm')}
                  statusLabel={TICKET_STATUS_LABEL[ticket.status]}
                  statusClass={TICKET_STATUS_COLOR[ticket.status]}
                  actionLabel={TICKET_ACTION_LABEL[ticket.action_type]}
                  actionClass={TICKET_ACTION_COLOR[ticket.action_type]}
                  flags={ticket.flags}
                />
              ))}
            </div>
          )}
        </div>

        {/* Escalation Feed */}
        <div className="bg-white rounded-3xl shadow-sm overflow-hidden flex flex-col border border-stone-100">
          <div className="p-6 border-b border-stone-50 flex justify-between items-center">
            <h3 className="font-bold text-stone-900">
              에스컬레이션 알림
              {escalatedSessionCount > 0 && (
                <span className="ml-2 text-xs bg-red-100 text-red-600 px-1.5 py-0.5 rounded-full font-bold">
                  {escalatedSessionCount}건
                </span>
              )}
            </h3>
            <div className="flex gap-1.5 items-center">
              <div
                className={`w-1.5 h-1.5 rounded-full ${escalated.length > 0 ? 'bg-red-500 animate-pulse' : 'bg-stone-300'}`}
                aria-hidden="true"
              />
              <span
                className={`text-[10px] font-bold uppercase tracking-widest ${escalated.length > 0 ? 'text-red-500' : 'text-stone-400'}`}
              >
                Live
              </span>
            </div>
          </div>

          <div className="flex-1 p-6 space-y-4">
            {escalated.length === 0 ? (
              <div className="text-center py-4">
                <span
                  className="material-symbols-outlined text-emerald-300 text-[48px] block mb-2"
                  style={{ fontVariationSettings: "'FILL' 1" }}
                  aria-hidden="true"
                >
                  check_circle
                </span>
                <p className="text-sm text-stone-400">미처리 에스컬레이션 없음</p>
              </div>
            ) : (
              escalated.slice(0, 5).map((log: ChatLog, idx: number) => (
                <EscalationCard
                  key={log.id}
                  log={log}
                  variant={idx === 0 ? 'critical' : idx === 1 ? 'warning' : 'resolved'}
                />
              ))
            )}
          </div>

          {escalated.length > 0 && (
            <div className="p-4 border-t border-stone-50">
              <NavLink
                to="/admin/chatbot"
                className="w-full block text-center text-xs font-bold text-emerald-700 hover:underline"
              >
                전체 에스컬레이션 보기
              </NavLink>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────
// Loading skeleton
// ──────────────────────────────────────────

function DashboardSkeleton() {
  return (
    <>
      {/* Page header skeleton */}
      <div className="h-8 w-48 bg-stone-100 rounded-xl animate-pulse" />

      {/* KPI skeletons */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="bg-white p-6 rounded-3xl border border-stone-100 space-y-4">
            <div className="flex justify-between">
              <div className="h-3 w-20 bg-stone-100 rounded animate-pulse" />
              <div className="w-9 h-9 bg-stone-100 rounded-xl animate-pulse" />
            </div>
            <div className="h-9 w-28 bg-stone-100 rounded-xl animate-pulse" />
          </div>
        ))}
      </div>

      {/* Chart skeletons */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        <div className="lg:col-span-2 bg-white p-8 rounded-3xl border border-stone-100 h-[360px] animate-pulse" />
        <div className="bg-white p-8 rounded-3xl border border-stone-100 h-[360px] animate-pulse" />
      </div>

      {/* Feed skeletons */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 pb-12">
        {Array.from({ length: 2 }).map((_, i) => (
          <div key={i} className="bg-white rounded-3xl border border-stone-100 h-[300px] animate-pulse" />
        ))}
      </div>
    </>
  );
}
