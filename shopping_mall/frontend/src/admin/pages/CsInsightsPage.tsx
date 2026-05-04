import { useState, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatLogs, useEscalatedChatLogs } from '@/admin/hooks/useChatbot';
import { useFaqTrendingQuestions } from '@/admin/hooks/useFaqDocs';
import {
  INTENT_LABEL,
  INTENT_COLOR_BAR,
  INTENT_COLOR_BADGE,
  INTENT_ICON,
} from '@/admin/constants/chatbot';
import type { ChatLog } from '@/admin/types/chatlog';

type TrendingQuestionItem = {
  intent: string;
  intent_label: string;
  count: number;
  sample_question: string;
};

// ── 기간 필터 옵션 ─────────────────────────────────────────────────────────────

const PERIOD_OPTIONS = [
  { label: '7일', days: 7 },
  { label: '30일', days: 30 },
  { label: '전체', days: 0 },
] as const;

type PeriodDays = 7 | 30 | 0;

/**
 * FastAPI는 microseconds(소수점 6자리)를 포함한 datetime을 반환하는데,
 * JS Date는 milliseconds(3자리)까지만 표준 지원하므로 초과 자리를 잘라냄.
 * "2026-04-29T12:21:21.844361" → "2026-04-29T12:21:21.844"
 */
function parseKstDate(str: string): Date {
  return new Date(str.replace(/(\.\d{3})\d+/, '$1'));
}

function filterByDays(logs: ChatLog[], days: PeriodDays): ChatLog[] {
  if (days === 0) return logs;
  const since = new Date();
  since.setDate(since.getDate() - days);
  return logs.filter((l) => parseKstDate(l.created_at) >= since);
}

// ── 의도 분포 계산 ─────────────────────────────────────────────────────────────

function calcIntentDist(logs: ChatLog[]) {
  const counts: Record<string, number> = {};
  for (const l of logs) {
    counts[l.intent] = (counts[l.intent] ?? 0) + 1;
  }
  return Object.entries(counts).sort((a, b) => b[1] - a[1]);
}

const NOISY_SAMPLE_TERMS = ['그만', '취소할게요', '채팅 시작', '안녕', 'hi', 'test'];

function isUsefulSample(question: string): boolean {
  const cleaned = question.trim();
  if (cleaned.length < 3) return false;
  if (/^\?+$/.test(cleaned.replace(/\s/g, ''))) return false;
  return !NOISY_SAMPLE_TERMS.some((term) => cleaned.toLowerCase() === term.toLowerCase());
}

function displaySample(question: string): string {
  return isUsefulSample(question) ? question : '대표 질문 확인 필요';
}

// ── 메인 컴포넌트 ──────────────────────────────────────────────────────────────

export default function CsInsightsPage() {
  const navigate = useNavigate();
  const [period, setPeriod] = useState<PeriodDays>(7);

  const { data: allLogs = [], isLoading } = useChatLogs();
  const { data: escalatedLogs = [] } = useEscalatedChatLogs();
  const { data: trending } = useFaqTrendingQuestions(period === 0 ? 90 : period, 8);

  // 기간 필터 적용
  const logs = useMemo(() => filterByDays(allLogs, period), [allLogs, period]);
  const escalated = useMemo(() => filterByDays(escalatedLogs, period), [escalatedLogs, period]);

  // CS 인사이트는 분석 지표 → 로그 건수 기준 (같은 세션에서 여러 번 에스컬레이션 발생 시 각각 카운트)
  const total = logs.length;
  const escalatedCount = escalated.length;
  const resolvedCount = total - escalatedCount;
  const resolveRate = total > 0 ? Math.round((resolvedCount / total) * 100) : 0;
  const escalationRate = total > 0 ? Math.round((escalatedCount / total) * 100) : 0;

  const intentDist = useMemo(() => calcIntentDist(logs), [logs]);
  const maxIntentCount = intentDist[0]?.[1] ?? 1;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full text-stone-400 text-sm">
        <span className="material-symbols-outlined animate-spin mr-2 text-[18px]">progress_activity</span>
        로딩 중...
      </div>
    );
  }

  return (
    <div className="h-[calc(100vh-4rem)] overflow-y-auto bg-[#f8f9ff]">
      <div className="max-w-6xl mx-auto px-6 py-6 space-y-6">

        {/* ── 헤더 ── */}
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-xl font-bold text-stone-900">CS 인사이트</h2>
            <p className="text-xs text-stone-400 mt-0.5">챗봇 대화 데이터 기반 고객 문의 분석</p>
          </div>

          {/* 기간 토글 */}
          <div className="flex bg-white border border-stone-200 rounded-xl p-1 gap-0.5">
            {PERIOD_OPTIONS.map((opt) => (
              <button
                key={opt.days}
                type="button"
                onClick={() => setPeriod(opt.days as PeriodDays)}
                className={`px-4 py-1.5 rounded-lg text-xs font-semibold transition-all ${
                  period === opt.days
                    ? 'bg-emerald-600 text-white shadow-sm'
                    : 'text-stone-500 hover:text-stone-700'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </div>

        {/* ── 핵심 지표 ── */}
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4">

          {/* 총 문의 */}
          <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-xl bg-emerald-50 flex items-center justify-center">
                <span className="material-symbols-outlined text-emerald-600 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  forum
                </span>
              </div>
              <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">총 문의</span>
            </div>
            <p className="text-3xl font-extrabold text-stone-900 tabular-nums leading-none">{total.toLocaleString()}</p>
            <p className="text-xs text-stone-400 mt-1.5">
              {period === 0 ? '전체 누적' : `최근 ${period}일`} 대화 수
            </p>
          </div>

          {/* 자동 해결률 */}
          <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-xl bg-emerald-50 flex items-center justify-center">
                <span className="material-symbols-outlined text-emerald-600 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  verified
                </span>
              </div>
              <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">자동 해결</span>
            </div>
            <p className="text-3xl font-extrabold text-emerald-700 tabular-nums leading-none">{resolveRate}%</p>
            <div className="mt-2 h-1.5 bg-stone-100 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 rounded-full transition-all duration-500"
                style={{ width: `${resolveRate}%` }}
              />
            </div>
            <p className="text-xs text-stone-400 mt-1.5">{resolvedCount.toLocaleString()}건 처리 완료</p>
          </div>

          {/* 에스컬레이션 */}
          <div className={`border rounded-2xl p-5 shadow-sm ${
            escalatedCount > 0 ? 'bg-rose-50 border-rose-100' : 'bg-white border-stone-100'
          }`}>
            <div className="flex items-center gap-2 mb-3">
              <div className={`w-8 h-8 rounded-xl flex items-center justify-center ${
                escalatedCount > 0 ? 'bg-rose-100' : 'bg-stone-50'
              }`}>
                <span
                  className={`material-symbols-outlined text-[18px] ${escalatedCount > 0 ? 'text-rose-500' : 'text-stone-400'}`}
                  style={{ fontVariationSettings: "'FILL' 1" }}
                  aria-hidden="true"
                >
                  support_agent
                </span>
              </div>
              <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">에스컬레이션</span>
            </div>
            <p className={`text-3xl font-extrabold tabular-nums leading-none ${
              escalatedCount > 0 ? 'text-rose-600' : 'text-stone-400'
            }`}>
              {escalatedCount}건
            </p>
            <p className="text-xs text-stone-400 mt-1.5">
              {escalatedCount > 0 ? '상담원 연결 필요' : '모두 자동 해결됨 ✓'}
            </p>
          </div>

          <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm">
            <div className="flex items-center gap-2 mb-3">
              <div className="w-8 h-8 rounded-xl bg-amber-50 flex items-center justify-center">
                <span className="material-symbols-outlined text-amber-600 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  monitoring
                </span>
              </div>
              <span className="text-[11px] font-bold text-stone-400 uppercase tracking-wide">상담 전환율</span>
            </div>
            <p className="text-3xl font-extrabold text-stone-900 tabular-nums leading-none">{escalationRate}%</p>
            <p className="text-xs text-stone-400 mt-1.5">전체 문의 중 상담원 확인 비율</p>
          </div>

        </div>

        {/* ── 2-column: 트렌딩 + 에스컬레이션 목록 ── */}
        <div className="grid grid-cols-2 gap-4">

          {/* 트렌딩 질문 토픽 */}
          <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-emerald-600 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  trending_up
                </span>
                <h3 className="text-sm font-bold text-stone-800">트렌딩 질문 토픽</h3>
              </div>
              <span className="text-[11px] text-stone-400">
                {period === 0 ? '전체' : `최근 ${period}일`} · {trending?.total_questions ?? 0}건
              </span>
            </div>

            {!trending || trending.items.length === 0 ? (
              <p className="text-sm text-stone-400 py-4 text-center">데이터 없음</p>
            ) : (
              <ol className="space-y-3">
                {trending.items.map((item: TrendingQuestionItem, idx: number) => (
                  <li key={item.intent} className="flex items-start gap-3">
                    <span className={`mt-0.5 w-5 h-5 rounded-full flex items-center justify-center text-[11px] font-bold shrink-0 ${
                      idx === 0 ? 'bg-emerald-600 text-white' :
                      idx === 1 ? 'bg-stone-200 text-stone-600' :
                      'bg-stone-100 text-stone-500'
                    }`}>
                      {idx + 1}
                    </span>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <div className="flex items-center gap-1.5">
                          <span
                            className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${INTENT_COLOR_BADGE[item.intent] ?? 'bg-stone-100 text-stone-500'}`}
                          >
                            <span className="material-symbols-outlined text-[11px]" aria-hidden="true">
                              {INTENT_ICON[item.intent] ?? 'chat_bubble'}
                            </span>
                            {item.intent_label}
                          </span>
                        </div>
                        <span className="text-xs font-bold text-stone-700 tabular-nums shrink-0">{item.count}건</span>
                      </div>
                      <p className="text-xs text-stone-500 truncate">"{displaySample(item.sample_question)}"</p>
                    </div>
                    <button
                      type="button"
                      onClick={() => navigate('/admin/faq')}
                      className="shrink-0 mt-0.5 p-1 rounded-lg text-stone-400 hover:text-emerald-600 hover:bg-emerald-50 transition-colors"
                      title="FAQ 등록"
                      aria-label={`${item.intent_label} FAQ 등록`}
                    >
                      <span className="material-symbols-outlined text-[16px]" aria-hidden="true">add_circle</span>
                    </button>
                  </li>
                ))}
              </ol>
            )}
          </div>

          {/* 최근 에스컬레이션 */}
          <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-rose-500 text-[18px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  warning
                </span>
                <h3 className="text-sm font-bold text-stone-800">최근 에스컬레이션</h3>
              </div>
              {escalatedCount > 0 && (
                <span className="px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-100 text-rose-600">
                  {escalatedCount}건
                </span>
              )}
            </div>

            {escalated.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-8 text-center gap-2">
                <span className="material-symbols-outlined text-emerald-400 text-[32px]" style={{ fontVariationSettings: "'FILL' 1" }} aria-hidden="true">
                  check_circle
                </span>
                <p className="text-sm text-stone-400">에스컬레이션 없음</p>
              </div>
            ) : (
              <div className="space-y-2 max-h-72 overflow-y-auto pr-1">
                {escalated.slice(0, 15).map((log: ChatLog) => (
                  <div key={log.id} className="border border-rose-100 bg-rose-50/60 rounded-xl px-3 py-2.5">
                    <div className="flex items-start gap-2">
                      <span
                        className={`mt-0.5 shrink-0 inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${INTENT_COLOR_BADGE[log.intent] ?? 'bg-stone-100 text-stone-500'}`}
                      >
                        {INTENT_LABEL[log.intent] ?? log.intent}
                      </span>
                      <p className="text-xs text-stone-700 leading-relaxed flex-1">{log.question}</p>
                    </div>
                    <p className="text-[11px] text-stone-400 mt-1.5 pl-0.5">
                      {log.user_id ? `회원 #${log.user_id}` : '비회원'} · {parseKstDate(log.created_at).toLocaleDateString('ko-KR')}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </div>

        </div>

        {/* ── 의도별 누적 분포 ── */}
        <div className="bg-white border border-stone-100 rounded-2xl p-5 shadow-sm space-y-4">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-stone-500 text-[18px]" aria-hidden="true">bar_chart</span>
            <h3 className="text-sm font-bold text-stone-800">의도별 문의 분포</h3>
            <span className="text-[11px] text-stone-400 ml-auto">
              {period === 0 ? '전체 기간' : `최근 ${period}일`} · 총 {total}건
            </span>
          </div>

          {intentDist.length === 0 ? (
            <p className="text-sm text-stone-400 py-4 text-center">데이터 없음</p>
          ) : (
            <div className="grid grid-cols-2 gap-x-8 gap-y-3">
              {intentDist.map(([intent, count]) => {
                const pct = total > 0 ? Math.round((count / total) * 100) : 0;
                const barWidth = Math.round((count / maxIntentCount) * 100);
                return (
                  <div key={intent}>
                    <div className="flex items-center justify-between text-xs mb-1.5">
                      <div className="flex items-center gap-1.5">
                        <span className="material-symbols-outlined text-[13px] text-stone-400" aria-hidden="true">
                          {INTENT_ICON[intent] ?? 'chat_bubble'}
                        </span>
                        <span className="text-stone-600 font-medium">{INTENT_LABEL[intent] ?? intent}</span>
                      </div>
                      <span className="font-semibold text-stone-700 tabular-nums">{count}건 <span className="font-normal text-stone-400">({pct}%)</span></span>
                    </div>
                    <div className="h-2 bg-stone-100 rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${INTENT_COLOR_BAR[intent] ?? 'bg-stone-300'}`}
                        style={{ width: `${barWidth}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

      </div>
    </div>
  );
}
