import { useEffect, useRef, useState } from 'react';
import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { useUserStore } from '@/stores/userStore';
import { useEscalatedLogs, useDashboardTicketStats } from '@/admin/hooks/useDashboard';

// ──────────────────────────────────────────
// Agent status
// ──────────────────────────────────────────

type AgentStatus = 'online' | 'away' | 'busy' | 'offline';

const AGENT_STATUS_CONFIG: Record<AgentStatus, { label: string; dot: string; desc: string }> = {
  online:  { label: '온라인',    dot: 'bg-emerald-500', desc: '상담 가능' },
  away:    { label: '자리비움',  dot: 'bg-amber-400',   desc: '잠시 자리를 비웠습니다' },
  busy:    { label: '바쁨',      dot: 'bg-rose-500',    desc: '상담 불가 (처리 중)' },
  offline: { label: '오프라인',  dot: 'bg-stone-400',   desc: '오프라인 상태' },
};

// ──────────────────────────────────────────
// Types
// ──────────────────────────────────────────

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
  badge?: number;
}

interface SidebarSectionProps {
  title: string;
  items: NavItem[];
}

// ──────────────────────────────────────────
// SidebarSection
// ──────────────────────────────────────────

function SidebarSection({ title, items }: SidebarSectionProps) {
  return (
    <div className="mb-4">
      <p className="px-3 mb-2 text-[10px] uppercase tracking-widest text-stone-400 font-bold">
        {title}
      </p>
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            `flex items-center justify-between px-3 py-2 rounded-lg transition-all duration-200 group ${
              isActive
                ? 'text-emerald-700 font-semibold bg-emerald-50/50'
                : 'text-stone-500 hover:text-emerald-600 hover:bg-stone-100'
            }`
          }
        >
          <div className="flex items-center gap-3">
            <span
              className={`material-symbols-outlined text-[22px] transition-colors`}
              aria-hidden="true"
            >
              {item.icon}
            </span>
            <span className="text-sm tracking-tight">{item.label}</span>
          </div>
          {item.badge != null && item.badge > 0 && (
            <span className="text-[10px] bg-rose-100 text-rose-600 px-1.5 py-0.5 rounded-full font-bold min-w-[20px] text-center">
              {item.badge > 99 ? '99+' : item.badge}
            </span>
          )}
        </NavLink>
      ))}
    </div>
  );
}

// ──────────────────────────────────────────
// AdminLayout
// ──────────────────────────────────────────

export default function AdminLayout() {
  const { user, isLoggedIn, checkAuth } = useUserStore();
  const { data: escalated = [] } = useEscalatedLogs();
  const { data: ticketStats } = useDashboardTicketStats();

  // 에스컬레이션된 고유 세션 수 — 로그 건수가 아닌 세션 단위로 집계
  const escalatedSessionCount = new Set(
    escalated.map((l) => l.session_id).filter((id): id is number => id != null)
  ).size;
  const [searchQuery, setSearchQuery] = useState('');
  const [agentStatus, setAgentStatus] = useState<AgentStatus>('online');
  const [statusDropdownOpen, setStatusDropdownOpen] = useState(false);
  const statusDropdownRef = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();

  // 드롭다운 외부 클릭 시 닫기
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (statusDropdownRef.current && !statusDropdownRef.current.contains(e.target as Node)) {
        setStatusDropdownOpen(false);
      }
    }
    if (statusDropdownOpen) document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, [statusDropdownOpen]);

  const pendingTickets = (ticketStats?.received ?? 0) + (ticketStats?.processing ?? 0);

  useEffect(() => {
    checkAuth();
  }, [checkAuth]);

  function handleSearchSubmit(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const q = searchQuery.trim();
    if (q) {
      navigate(`/admin/tickets?q=${encodeURIComponent(q)}`);
    }
  }

  // ── Nav items ──
  const mainItems: NavItem[] = [
    { to: '/admin', label: '대시보드', icon: 'dashboard', end: true },
  ];

  const managementItems: NavItem[] = [
    {
      to: '/admin/tickets',
      label: 'CS / 티켓 관리',
      icon: 'support_agent',
      badge: pendingTickets,
    },
    {
      to: '/admin/chatbot',
      label: '챗봇 대화',
      icon: 'chat',
      badge: escalatedSessionCount,
    },
    { to: '/admin/cs-insights', label: 'CS 인사이트', icon: 'lightbulb' },
    { to: '/admin/faq', label: 'FAQ 관리', icon: 'menu_book' },
    { to: '/admin/shipments', label: '배송 관리', icon: 'local_shipping' },
    { to: '/admin/analytics', label: '분석', icon: 'bar_chart' },
  ];

  const businessItems: NavItem[] = [
    { to: '/admin/calendar', label: '판매 캘린더', icon: 'calendar_month' },
    { to: '/admin/reports', label: '리포트', icon: 'description' },
    { to: '/admin/expenses', label: '비용 관리', icon: 'account_balance_wallet' },
  ];

  // ── Notification count: escalated + pending ──
  const totalAlerts = escalatedSessionCount + pendingTickets;

  return (
    <div className="flex min-h-screen bg-stone-50">
      {/* ────── Sidebar ────── */}
      <aside
        className="w-[240px] h-screen sticky top-0 left-0 bg-white flex flex-col p-6 space-y-8 z-50 border-r border-stone-100"
        aria-label="관리자 사이드바"
      >
        {/* Brand Header */}
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-lg flex items-center justify-center text-white shadow-sm"
              style={{
                background: 'linear-gradient(135deg, #006933 0%, #008542 100%)',
              }}
            >
              <span
                className="material-symbols-outlined text-[20px]"
                style={{ fontVariationSettings: "'FILL' 1" }}
                aria-hidden="true"
              >
                eco
              </span>
            </div>
            <span className="text-base font-bold tracking-tighter text-emerald-900">
              FarmOS
            </span>
          </div>
          <span className="text-sm tracking-tight text-stone-400 mt-1">
            Agricultural Ledger
          </span>
        </div>

        {/* Navigation */}
        <nav className="flex-1 space-y-1 overflow-y-auto no-scrollbar" aria-label="메인 네비게이션">
          <SidebarSection title="Main" items={mainItems} />
          <SidebarSection title="Management" items={managementItems} />
          <SidebarSection title="Business" items={businessItems} />
        </nav>

        {/* Footer Nav */}
        <div className="pt-6 border-t border-stone-100 space-y-1">
          <NavLink
            to="/admin/settings"
            className="flex items-center gap-3 px-3 py-2 text-stone-500 hover:text-emerald-600 transition-all duration-200 rounded-lg"
            aria-label="설정"
          >
            <span className="material-symbols-outlined text-[22px]" aria-hidden="true">
              settings
            </span>
            <span className="text-sm tracking-tight">설정</span>
          </NavLink>
          <NavLink
            to="/"
            className="flex items-center gap-3 px-3 py-2 text-stone-500 hover:text-emerald-600 transition-all duration-200 rounded-lg"
            aria-label="로그아웃"
          >
            <span className="material-symbols-outlined text-[22px]" aria-hidden="true">
              logout
            </span>
            <span className="text-sm tracking-tight">쇼핑몰로 돌아가기</span>
          </NavLink>

          {/* Admin profile */}
          <div className="flex items-center gap-3 px-3 mt-6">
            <div
              className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold shrink-0"
              style={{ background: 'linear-gradient(135deg, #006933 0%, #008542 100%)' }}
              aria-hidden="true"
            >
              {user?.name?.charAt(0)?.toUpperCase() ?? 'A'}
            </div>
            <div className="flex flex-col min-w-0">
              <span className="text-xs font-bold text-stone-800 truncate">
                {isLoggedIn && user?.name ? user.name : 'Admin User'}
              </span>
              <span className="text-[10px] text-stone-400">Master Level</span>
            </div>
          </div>
        </div>
      </aside>

      {/* ────── Main Content ────── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top Header */}
        <header
          className="w-full h-16 flex items-center justify-between px-8 glass-nav sticky top-0 z-40 border-b border-stone-100"
          aria-label="관리자 상단 헤더"
        >
          {/* Search */}
          <form
            className="flex items-center flex-1 max-w-xl"
            onSubmit={handleSearchSubmit}
            role="search"
          >
            <div className="relative w-full group">
              <span
                className="material-symbols-outlined absolute left-3 top-1/2 -translate-y-1/2 text-stone-400 text-[20px] group-focus-within:text-emerald-600 transition-colors pointer-events-none"
                aria-hidden="true"
              >
                search
              </span>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="주문번호, 고객명 또는 티켓 검색..."
                className="w-full pl-11 pr-4 py-2 bg-stone-100/50 border-transparent rounded-xl focus:outline-none focus:ring-2 focus:ring-emerald-600/20 text-sm transition-all"
                aria-label="관리자 검색"
              />
            </div>
          </form>

          {/* Right actions */}
          <div className="flex items-center gap-4">
            {/* Notifications — 티켓 페이지로 이동 */}
            <button
              type="button"
              className="w-10 h-10 flex items-center justify-center text-stone-400 hover:text-emerald-600 transition-colors relative rounded-xl hover:bg-stone-100"
              aria-label={`알림 ${totalAlerts}건`}
              onClick={() => navigate('/admin/tickets')}
            >
              <span className="material-symbols-outlined text-[22px]" aria-hidden="true">
                notifications
              </span>
              {totalAlerts > 0 && (
                <span
                  className="absolute top-2 right-2 w-2 h-2 bg-rose-500 rounded-full ring-2 ring-white"
                  aria-hidden="true"
                />
              )}
            </button>

            {/* Help — 미구현, 비활성화 */}
            <button
              type="button"
              className="w-10 h-10 flex items-center justify-center text-stone-300 rounded-xl cursor-not-allowed opacity-50"
              aria-label="도움말 (준비 중)"
              aria-disabled="true"
              disabled
            >
              <span className="material-symbols-outlined text-[22px]" aria-hidden="true">
                help_outline
              </span>
            </button>

            {/* Divider */}
            <div className="h-6 w-px bg-stone-200 mx-2" aria-hidden="true" />

            {/* Admin profile + status */}
            <div className="relative" ref={statusDropdownRef}>
              <button
                type="button"
                onClick={() => setStatusDropdownOpen((v) => !v)}
                className="flex items-center gap-3 ml-1 rounded-xl px-2 py-1 hover:bg-stone-100 transition-colors"
                aria-haspopup="listbox"
                aria-expanded={statusDropdownOpen}
                aria-label="상담사 상태 설정"
              >
                <div className="text-right">
                  <p className="text-[10px] font-bold text-emerald-800 uppercase tracking-widest leading-tight">
                    Admin
                  </p>
                  <p className="text-xs text-stone-500">
                    {isLoggedIn && user?.name ? user.name : '관리자님'}
                  </p>
                </div>
                {/* Avatar with status dot */}
                <div className="relative shrink-0">
                  <div
                    className="w-8 h-8 rounded-full flex items-center justify-center text-white text-sm font-bold ring-2 ring-stone-100"
                    style={{ background: 'linear-gradient(135deg, #006933 0%, #008542 100%)' }}
                    aria-hidden="true"
                  >
                    {user?.name?.charAt(0)?.toUpperCase() ?? 'A'}
                  </div>
                  {/* Status dot */}
                  <span
                    className={`absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full ring-2 ring-white ${AGENT_STATUS_CONFIG[agentStatus].dot}`}
                    aria-label={AGENT_STATUS_CONFIG[agentStatus].label}
                  />
                </div>
              </button>

              {/* Status dropdown */}
              {statusDropdownOpen && (
                <div
                  className="absolute right-0 top-full mt-2 w-52 bg-white rounded-xl shadow-lg border border-stone-100 py-1.5 z-50"
                  role="listbox"
                  aria-label="상담사 상태 선택"
                >
                  <p className="px-4 py-2 text-[10px] font-bold text-stone-400 uppercase tracking-widest">
                    상담사 상태
                  </p>
                  {(Object.entries(AGENT_STATUS_CONFIG) as [AgentStatus, typeof AGENT_STATUS_CONFIG[AgentStatus]][]).map(
                    ([key, cfg]) => (
                      <button
                        key={key}
                        type="button"
                        role="option"
                        aria-selected={agentStatus === key}
                        onClick={() => { setAgentStatus(key); setStatusDropdownOpen(false); }}
                        className={`w-full flex items-center gap-3 px-4 py-2.5 text-sm transition-colors ${
                          agentStatus === key
                            ? 'bg-stone-50 text-stone-800'
                            : 'text-stone-600 hover:bg-stone-50'
                        }`}
                      >
                        <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${cfg.dot}`} aria-hidden="true" />
                        <span className="font-medium">{cfg.label}</span>
                        {agentStatus === key && (
                          <span
                            className="material-symbols-outlined text-emerald-600 text-[16px] ml-auto"
                            aria-hidden="true"
                          >
                            check
                          </span>
                        )}
                      </button>
                    ),
                  )}
                </div>
              )}
            </div>
          </div>
        </header>

        {/* Page Content */}
        <main className="flex-1">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
