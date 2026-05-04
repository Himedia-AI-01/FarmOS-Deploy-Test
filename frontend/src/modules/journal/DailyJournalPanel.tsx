import { useState, useEffect, useCallback } from "react";
import {
  MdAutoAwesome,
  MdAutorenew,
  MdCheckCircle,
  MdUndo,
  MdChevronLeft,
  MdChevronRight,
  MdHistory,
  MdEdit,
  MdEditNote,
  MdToday,
  MdLock,
  MdNotificationsActive,
  MdWarning,
} from "react-icons/md";
import toast from "react-hot-toast";
import { useDailyJournal } from "@/hooks/useDailyJournal";
import type { DailyJournalAPI } from "@/types";
import { parseLocalDate, toLocalDateString } from "@/utils/date";
import DailyJournalEditor from "./DailyJournalEditor";

interface Props {
  /** 초기 선택 날짜. 미지정 시 오늘. */
  initialDate?: string;
  /**
   * 상위 목록 갱신을 트리거할 때마다 증가하는 숫자.
   * (아직 사용 안 함 — Step 12에서 편집 후 목록 자동 갱신용으로 예약)
   */
  refreshToken?: number;
}

import { API_BASE } from "@/utils/api";

const JOURNAL_API = `${API_BASE}/journal`;

const SOURCE_LABEL: Record<string, string> = {
  llm: "AI 생성",
  llm_edited: "AI 생성 + 편집",
  manual: "직접 작성",
  template_fallback: "템플릿 (AI 실패)",
};

/**
 * 하루치 통합 영농일지 패널 (Step 11 — read-only + 생성/재생성/확정/되돌리기).
 *
 * 편집 UI는 Step 12에서 별도 모달로 추가 예정.
 * PDF 내보내기는 Step 13에서 추가 예정.
 */
export default function DailyJournalPanel({ initialDate, refreshToken }: Props) {
  const dj = useDailyJournal();

  const [date, setDate] = useState<string>(
    initialDate || toLocalDateString(),
  );
  const [current, setCurrent] = useState<DailyJournalAPI | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false); // 생성/재생성/확정 중
  const [entryCount, setEntryCount] = useState(0);
  const [editorOpen, setEditorOpen] = useState(false);
  // 통합본 updated_at 이후 새로 추가된 entry 수 (stale 판정용)
  const [newSinceUpdate, setNewSinceUpdate] = useState(0);

  const refresh = useCallback(
    async (targetDate: string) => {
      setLoading(true);
      // 1) 해당 날짜의 JournalEntry 목록 조회 (개수 + stale 비교용 id)
      let allItems: Array<{ id: number }> = [];
      try {
        const qs = new URLSearchParams({
          date_from: targetDate,
          date_to: targetDate,
          page_size: "100",
        });
        const res = await fetch(`${JOURNAL_API}?${qs}`, { credentials: "include" });
        if (res.ok) {
          const body = (await res.json()) as {
            total: number;
            items?: Array<{ id: number }>;
          };
          setEntryCount(body.total || 0);
          allItems = body.items || [];
        } else {
          setEntryCount(0);
        }
      } catch {
        setEntryCount(0);
      }

      // 2) 해당 날짜의 DailyJournal 조회 (없으면 null)
      const r = await dj.fetchByDate(targetDate);
      const djData = r.ok ? r.data ?? null : null;
      if (r.ok) setCurrent(djData);
      else {
        setCurrent(null);
        toast.error(r.error?.message || "조회 실패");
      }

      // 3) stale 판정 — 통합본의 source_entry_ids에 없는 entry 수.
      // (updated_at 기반 비교는 본문 편집/확정/확정 해제로 updated_at이 갱신되면
      //  중간에 추가된 미통합 entry가 누락되므로 source_entry_ids 기반이 안전.)
      if (djData) {
        const sourceIds = new Set(djData.source_entry_ids);
        const newCount = allItems.filter((e) => !sourceIds.has(e.id)).length;
        setNewSinceUpdate(newCount);
      } else {
        setNewSinceUpdate(0);
      }

      setLoading(false);
    },
    [dj],
  );

  useEffect(() => {
    refresh(date);
  }, [date, refresh, refreshToken]);

  const moveDate = (days: number) => {
    const d = parseLocalDate(date);
    d.setDate(d.getDate() + days);
    setDate(toLocalDateString(d));
  };

  const handleGenerate = async (overwrite = false) => {
    if (entryCount === 0) {
      toast.error("이 날짜에 기록된 영농일지(entry)가 없습니다.");
      return;
    }
    setBusy(true);
    const r = await dj.generate(date, { overwrite });
    setBusy(false);
    if (r.ok && r.data) {
      setCurrent(r.data);
      toast.success(
        r.data.narrative_source === "template_fallback"
          ? "통합본이 생성되었습니다 (AI 실패 → 템플릿 사용)"
          : "통합 영농일지가 생성되었습니다.",
      );
      return;
    }
    // 이미 draft가 존재하는 경우(예: 이전 요청이 네트워크 실패로 중단됐지만 DB엔 저장된 상태) →
    // 기존 것을 자동으로 로드해서 사용자 혼란 최소화.
    if (r.error?.code === "already_exists") {
      toast("이미 생성된 통합 영농일지가 있어 불러왔습니다.", { icon: "ℹ️" });
      await refresh(date);
      return;
    }
    toast.error(r.error?.message || "생성 실패");
  };

  const handleRegenerate = async () => {
    if (!current) return;
    if (!confirm("기존 본문을 히스토리에 보존하고 새로 생성합니다. 계속할까요?")) return;
    setBusy(true);
    const r = await dj.regenerate(current.id);
    setBusy(false);
    if (r.ok && r.data) {
      setCurrent(r.data);
      setNewSinceUpdate(0); // 방금 갱신했으니 stale 아님
      toast.success("재생성되었습니다.");
    } else {
      toast.error(r.error?.message || "재생성 실패");
    }
  };

  const handleConfirm = async () => {
    if (!current) return;
    setBusy(true);
    const r = await dj.confirm(current.id);
    setBusy(false);
    if (r.ok && r.data) {
      setCurrent(r.data);
      toast.success("확정되었습니다.");
    } else {
      toast.error(r.error?.message || "확정 실패");
    }
  };

  const handleUnconfirm = async () => {
    if (!current) return;
    setBusy(true);
    const r = await dj.unconfirm(current.id);
    setBusy(false);
    if (r.ok && r.data) {
      setCurrent(r.data);
      toast.success("다시 편집 가능 상태로 되돌렸습니다.");
    } else {
      toast.error(r.error?.message || "되돌리기 실패");
    }
  };

  const todayStr = toLocalDateString();
  const isToday = date === todayStr;
  const dateLabel = parseLocalDate(date).toLocaleDateString("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  });

  return (
    <div className="rounded-2xl border border-indigo-100 shadow-sm overflow-hidden bg-white">
      {/* 헤더 (그라디언트 바) */}
      <div className="bg-gradient-to-r from-indigo-50 via-white to-purple-50 px-5 py-4 border-b border-indigo-100">
        <div className="flex items-center justify-between">
          <button
            onClick={() => moveDate(-1)}
            className="p-1.5 rounded-lg text-gray-400 hover:text-indigo-600 hover:bg-white/70 transition cursor-pointer"
            aria-label="이전 날짜"
          >
            <MdChevronLeft className="text-xl" />
          </button>

          <div className="flex flex-col items-center gap-1">
            <div className="flex items-center gap-1.5 text-xs font-medium text-indigo-500">
              <MdAutoAwesome />
              <span>AI 통합 영농일지</span>
            </div>
            <h3 className="text-base font-bold text-gray-900">{dateLabel}</h3>
            {!isToday && (
              <button
                type="button"
                onClick={() => setDate(todayStr)}
                className="text-[11px] text-indigo-500 hover:text-indigo-700 flex items-center gap-0.5 cursor-pointer"
              >
                <MdToday className="text-sm" /> 오늘로
              </button>
            )}
          </div>

          <button
            onClick={() => moveDate(1)}
            disabled={isToday}
            className="p-1.5 rounded-lg text-gray-400 hover:text-indigo-600 hover:bg-white/70 transition cursor-pointer disabled:text-gray-200 disabled:cursor-not-allowed disabled:hover:bg-transparent"
            aria-label="다음 날짜"
          >
            <MdChevronRight className="text-xl" />
          </button>
        </div>
      </div>

      {/* 바디 */}
      <div className="px-5 py-5">
        {/* 로딩 */}
        {loading && (
          <div className="flex items-center justify-center py-10 text-gray-400">
            <MdAutorenew className="animate-spin mr-2 text-xl" />
            <span className="text-sm">불러오는 중...</span>
          </div>
        )}

        {/* 상태 A: 기록 자체가 0건 */}
        {!loading && !current && entryCount === 0 && (
          <div className="py-8 flex flex-col items-center text-center">
            <div className="w-14 h-14 rounded-full bg-gray-100 flex items-center justify-center mb-3">
              <MdEditNote className="text-gray-400 text-3xl" />
            </div>
            <p className="text-sm font-medium text-gray-600 mb-1">
              아직 기록된 작업이 없어요
            </p>
            <p className="text-xs text-gray-400">
              오늘의 영농 작업을 먼저 기록해주세요
            </p>
          </div>
        )}

        {/* 상태 B: 기록은 있는데 통합본 아직 없음 → CTA */}
        {!loading && !current && entryCount > 0 && (
          <div className="py-6 flex flex-col items-center text-center">
            <div className="w-14 h-14 rounded-full bg-gradient-to-br from-indigo-400 to-purple-500 flex items-center justify-center mb-4 shadow-md">
              <MdAutoAwesome className="text-white text-2xl" />
            </div>
            <p className="text-sm text-gray-700 mb-1">
              오늘 기록한{" "}
              <strong className="text-indigo-600 text-base">{entryCount}건</strong>
              의 작업을
            </p>
            <p className="text-sm text-gray-700 mb-5">
              하나의 영농일지로 자동 정리해 드려요
            </p>
            <button
              onClick={() => handleGenerate(false)}
              disabled={busy}
              className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl font-semibold text-sm text-white bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 shadow-md hover:shadow-lg active:scale-95 transition disabled:opacity-50 disabled:cursor-not-allowed disabled:active:scale-100 cursor-pointer"
            >
              <MdAutoAwesome className="text-base" />
              {busy ? "AI가 작성 중..." : "통합 영농일지 생성"}
            </button>
          </div>
        )}

        {/* 상태 C/D: 통합본 존재 (draft 또는 confirmed) */}
        {!loading && current && (
          <div className="space-y-4">
            {/* Stale 배너 — 통합 이후 새로 추가된 entry가 있을 때만 */}
            {newSinceUpdate > 0 && (
              <div
                className={`px-3.5 py-2.5 rounded-lg flex items-center gap-2.5 text-xs ${
                  current.status === "confirmed"
                    ? "bg-red-50 border border-red-200 text-red-800"
                    : "bg-amber-50 border border-amber-200 text-amber-800"
                }`}
              >
                {current.status === "confirmed" ? (
                  <MdWarning className="text-base flex-shrink-0" />
                ) : (
                  <MdNotificationsActive className="text-base flex-shrink-0" />
                )}
                <span className="flex-1">
                  {current.status === "confirmed" ? (
                    <>
                      통합 이후 새 작업{" "}
                      <strong>{newSinceUpdate}건</strong>이 있어요. 지금 PDF로
                      내보내면 누락됩니다.
                    </>
                  ) : (
                    <>
                      통합 이후 새 작업{" "}
                      <strong>{newSinceUpdate}건</strong>이 있어요. 다시 통합할까요?
                    </>
                  )}
                </span>
                {current.status === "draft" ? (
                  <button
                    onClick={handleRegenerate}
                    disabled={busy}
                    className="px-2.5 py-1 rounded-md bg-amber-600 hover:bg-amber-700 text-white text-[11px] font-medium whitespace-nowrap disabled:opacity-50 cursor-pointer"
                  >
                    지금 재생성
                  </button>
                ) : (
                  <button
                    onClick={handleUnconfirm}
                    disabled={busy}
                    className="px-2.5 py-1 rounded-md bg-red-600 hover:bg-red-700 text-white text-[11px] font-medium whitespace-nowrap disabled:opacity-50 cursor-pointer"
                  >
                    확정 해제
                  </button>
                )}
              </div>
            )}

            {/* 메타 뱃지 */}
            <div className="flex items-center gap-2 flex-wrap text-xs">
              {current.status === "confirmed" ? (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-green-100 text-green-700 font-medium">
                  <MdLock className="text-sm" /> 확정됨
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-amber-100 text-amber-700 font-medium">
                  <MdEdit className="text-sm" /> 초안
                </span>
              )}
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-indigo-50 text-indigo-600 font-medium">
                <MdAutoAwesome className="text-sm" />
                {SOURCE_LABEL[current.narrative_source] || current.narrative_source}
              </span>
              <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">
                원본 {current.source_entry_ids.length}건
              </span>
              {current.revisions && current.revisions.length > 0 && (
                <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full bg-gray-100 text-gray-600">
                  <MdHistory className="text-sm" /> 수정 {current.revisions.length}회
                </span>
              )}
            </div>

            {/* 본문 */}
            <div
              className={`p-5 rounded-xl border ${
                current.status === "confirmed"
                  ? "bg-green-50/40 border-green-100"
                  : "bg-gray-50/40 border-gray-100"
              }`}
            >
              <pre className="whitespace-pre-wrap font-sans text-[14px] text-gray-800 leading-[1.8]">
                {current.narrative}
              </pre>
            </div>

            {/* 액션 바 */}
            <div className="flex gap-2 flex-wrap justify-end">
              {current.status === "draft" ? (
                <>
                  <button
                    onClick={() => setEditorOpen(true)}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 active:scale-95 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <MdEdit className="text-base" /> 편집
                  </button>
                  <button
                    onClick={handleRegenerate}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium text-indigo-600 bg-white border border-indigo-200 hover:bg-indigo-50 active:scale-95 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                    title="기존 본문을 히스토리로 보내고 새로 생성"
                  >
                    <MdAutorenew className={`text-base ${busy ? "animate-spin" : ""}`} /> 재생성
                  </button>
                  <button
                    onClick={handleConfirm}
                    disabled={busy}
                    className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-semibold text-white bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 shadow-sm hover:shadow active:scale-95 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed disabled:active:scale-100"
                  >
                    <MdCheckCircle className="text-base" /> 확정
                  </button>
                </>
              ) : (
                <button
                  onClick={handleUnconfirm}
                  disabled={busy}
                  className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-50 active:scale-95 transition cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                >
                  <MdUndo className="text-base" /> 확정 해제
                </button>
              )}
            </div>

            {/* 하단 메타 */}
            <div className="text-[11px] text-gray-400 text-right">
              최종 수정 {new Date(current.updated_at).toLocaleString("ko-KR")}
            </div>
          </div>
        )}
      </div>

      {/* 편집 모달 (Step 12) */}
      {editorOpen && current && (
        <DailyJournalEditor
          dailyJournal={current}
          onSaved={(updated) => setCurrent(updated)}
          onClose={() => setEditorOpen(false)}
        />
      )}
    </div>
  );
}
