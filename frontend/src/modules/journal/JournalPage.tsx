import { useState, useEffect, useRef } from "react";
import {
  MdAdd,
  MdEdit,
  MdDelete,
  MdClose,
  MdChevronLeft,
  MdChevronRight,
  MdFileDownload,
} from "react-icons/md";
import toast from "react-hot-toast";
import { useJournalData } from "@/hooks/useJournalData";
import JournalEntryForm, {
  type JournalEntryFormHandle,
} from "./JournalEntryForm";
import STTInput, { type STTInputHandle } from "./STTInput";
import PhotoInput, { type PhotoInputHandle } from "./PhotoInput";
import PhotoLightbox from "./PhotoLightbox";
import AuthenticatedPhoto from "./AuthenticatedPhoto";
import MissingFieldsAlert from "./MissingFieldsAlert";
import DailyJournalPanel from "./DailyJournalPanel";
import type { JournalEntryAPI, STTParseResult } from "@/types";
import { toLocalDateString } from "@/utils/date";

const STAGE_COLORS: Record<string, string> = {
  사전준비: "bg-gray-100 text-gray-700",
  경운: "bg-amber-100 text-amber-700",
  파종: "bg-green-100 text-green-700",
  정식: "bg-emerald-100 text-emerald-700",
  작물관리: "bg-blue-100 text-blue-700",
  수확: "bg-orange-100 text-orange-700",
};

const FILTER_STAGES = [
  "all",
  "사전준비",
  "경운",
  "파종",
  "정식",
  "작물관리",
  "수확",
];

export default function JournalPage() {
  const {
    entries,
    total,
    loading,
    fetchEntries,
    createEntry,
    updateEntry,
    deleteEntry,
    parseSTT,
    transcribeAudio,
    parsePhotos,
    uploadPhoto,
    deletePhoto,
    fetchMissingFields,
  } = useJournalData();
  const [filter, setFilter] = useState<string>("all");
  const [showForm, setShowForm] = useState(false);
  const [editingEntry, setEditingEntry] = useState<JournalEntryAPI | null>(
    null,
  );
  const [sttPrefill, setSttPrefill] = useState<Record<string, unknown> | null>(
    null,
  );
  // 다중 엔트리 상태 (STT/Vision이 여러 작업을 감지한 경우)
  const [sttEntries, setSttEntries] = useState<Record<string, unknown>[]>([]);
  const [currentEntryIdx, setCurrentEntryIdx] = useState(0);
  // 마지막으로 prefill 을 채운 입력 채널 — 저장 시 source 필드로 사용
  const [inputSource, setInputSource] = useState<"stt" | "vision" | "text">("text");
  // 폼 remount 강제용 — prefill 이 바뀔 때마다 증가시켜 React 가 form 을 새로 mount
  const [prefillVersion, setPrefillVersion] = useState(0);
  // parse-photos 응답으로 미리 저장된 사진 ID — 폼 첨부 사진 초기값 (신규 entry)
  const [pendingPhotoIds, setPendingPhotoIds] = useState<number[]>([]);
  // 타임라인 사진 클릭 시 lightbox 표시
  const [lightboxPhotoId, setLightboxPhotoId] = useState<number | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  // DailyJournalPanel에 "지금 entry 목록 다시 보세요" 신호를 보내는 토큰.
  // entry 생성/수정/삭제 직후 증가시키면 Panel이 즉시 stale 카운트를 재계산함.
  const [panelRefreshToken, setPanelRefreshToken] = useState(0);
  const bumpPanel = () => setPanelRefreshToken((t) => t + 1);
  const sttRef = useRef<STTInputHandle>(null);
  const photoRef = useRef<PhotoInputHandle>(null);
  const formRef = useRef<JournalEntryFormHandle>(null);

  const handleRequestRecord = () => {
    setShowForm(false);
    setEditingEntry(null);
    setSttPrefill(null);
    setSttEntries([]);
    setCurrentEntryIdx(0);
    setTimeout(() => sttRef.current?.start(), 0);
  };

  const gotoEntry = (idx: number) => {
    if (idx < 0 || idx >= sttEntries.length || idx === currentEntryIdx) return;
    // 현재 폼 스냅샷 + 다음 엔트리 로드를 하나의 렌더 사이클에서 처리
    const snapshot = formRef.current?.getFormData();
    const next = [...sttEntries];
    if (snapshot) next[currentEntryIdx] = snapshot;
    setSttEntries(next);
    setCurrentEntryIdx(idx);
    setSttPrefill(next[idx] || null);
  };

  const removeCurrentEntry = () => {
    if (sttEntries.length <= 1) return;
    const newEntries = sttEntries.filter((_, i) => i !== currentEntryIdx);
    const newIdx = Math.min(currentEntryIdx, newEntries.length - 1);
    setSttEntries(newEntries);
    setCurrentEntryIdx(newIdx);
    setSttPrefill(newEntries[newIdx] || null);
  };

  const closeForm = () => {
    setShowForm(false);
    setSttPrefill(null);
    setEditingEntry(null);
    setSttEntries([]);
    setCurrentEntryIdx(0);
    setPendingPhotoIds([]);
  };

  useEffect(() => {
    fetchEntries(filter === "all" ? {} : { workStage: filter });
  }, [filter, fetchEntries]);

  const handleCreate = async (data: Record<string, unknown>) => {
    // 다중 엔트리 모드: 현재 값을 반영한 전체를 일괄 등록
    if (sttEntries.length > 1) {
      const allEntries = [...sttEntries];
      allEntries[currentEntryIdx] = data;
      let okCount = 0;
      for (const e of allEntries) {
        // 서버 스키마에 없는 메타 필드(_pesticide_uncertain 등) 제거
        const { _pesticide_uncertain: _u, ...cleanEntry } = e as Record<
          string,
          unknown
        >;
        void _u;
        const r = await createEntry({ ...cleanEntry, source: inputSource });
        if (r) okCount += 1;
      }
      if (okCount === allEntries.length) {
        toast.success(`${okCount}건의 영농일지가 저장되었습니다.`);
      } else {
        toast.error(`${okCount}/${allEntries.length}건만 저장되었습니다.`);
      }
      closeForm();
      fetchEntries(filter === "all" ? {} : { workStage: filter });
      bumpPanel();
      return;
    }

    // 입력 채널(stt/vision/text)을 inputSource state 로 추적하므로 그대로 source 에 적용.
    // - "새 일지" 버튼 → "text"
    // - STT 인식 prefill → "stt"
    // - Vision prefill 또는 거절 후 사용자 직접 진행 → "vision"
    const finalData = { ...data, source: inputSource };
    const result = await createEntry(finalData);
    if (result) {
      toast.success("영농일지가 저장되었습니다.");
      closeForm();
      fetchEntries(filter === "all" ? {} : { workStage: filter });
      bumpPanel();
    } else {
      toast.error("저장에 실패했습니다.");
    }
  };

  const handleUpdate = async (data: Record<string, unknown>) => {
    if (!editingEntry) return;
    const result = await updateEntry(editingEntry.id, data);
    if (result) {
      toast.success("영농일지가 수정되었습니다.");
      setEditingEntry(null);
      fetchEntries(filter === "all" ? {} : { workStage: filter });
      bumpPanel();
    } else {
      toast.error("수정에 실패했습니다.");
    }
  };

  const handleDelete = async (id: number) => {
    if (!confirm("이 영농일지를 삭제하시겠습니까?")) return;
    const ok = await deleteEntry(id);
    if (ok) {
      toast.success("삭제되었습니다.");
      fetchEntries(filter === "all" ? {} : { workStage: filter });
      bumpPanel();
    } else {
      toast.error("삭제에 실패했습니다.");
    }
  };

  const handleSTTParsed = (result: STTParseResult) => {
    if (result.rejected || !result.entries || result.entries.length === 0) {
      toast.error(result.reject_reason || "영농 작업 내용을 찾지 못했습니다.", {
        duration: 6000,
      });
      return;
    }

    const entries = result.entries.map((e) => {
      const match = e.pesticide_match as { uncertain?: boolean } | null;
      return {
        ...(e.parsed as Record<string, unknown>),
        _pesticide_uncertain: Boolean(match?.uncertain),
      };
    });

    if (entries.length === 1) {
      toast.success("음성이 분석되었습니다. 확인 후 저장하세요.");
    } else {
      toast.success(`${entries.length}건의 작업이 감지되었습니다.`);
    }

    setInputSource("stt");
    setSttEntries(entries);
    setCurrentEntryIdx(0);
    setSttPrefill(entries[0]);
    setEditingEntry(null);
    setPrefillVersion((v) => v + 1);
    setShowForm(true);
  };

  const handlePhotoParsed = (result: STTParseResult) => {
    if (result.rejected || !result.entries || result.entries.length === 0) {
      // LLM 재현율은 100%가 아니므로 거절을 hard block 으로 만들지 않는다.
      // 사용자에게 "그래도 직접 작성?" 선택권을 주고, 확인 시 빈 폼을 연다.
      // setTimeout 으로 confirm 호출을 다음 task 로 미루어, 자식 PhotoInput 의
      // setStatus("idle") 이 먼저 적용되어 분석 오버레이가 unmount 된 후 confirm
      // 다이얼로그가 뜨도록 한다 (sync confirm 이 자식 re-render 와 race 하던 문제).
      const reason =
        result.reject_reason || "사진에서 영농 작업 단서를 찾지 못했습니다.";
      const photoIdsFromReject =
        (result as { photo_ids?: number[] }).photo_ids ?? [];
      setTimeout(() => {
        const proceed = window.confirm(
          `${reason}\n\n그래도 영농일지를 직접 작성하시겠어요?`,
        );
        if (!proceed) return;
        setInputSource("vision");
        setSttEntries([]);
        setCurrentEntryIdx(0);
        setSttPrefill(null);
        setEditingEntry(null);
        // 거절 응답에도 photo_ids 가 함께 와서 사진은 디스크에 저장돼있음 — 첨부 살림
        setPendingPhotoIds(photoIdsFromReject);
        setPrefillVersion((v) => v + 1);
        setShowForm(true);
      }, 0);
      return;
    }

    const entries = result.entries.map((e) => {
      const match = e.pesticide_match as { uncertain?: boolean } | null;
      return {
        ...(e.parsed as Record<string, unknown>),
        _pesticide_uncertain: Boolean(match?.uncertain),
      };
    });

    if (entries.length === 1) {
      toast.success("사진이 분석되었습니다. 확인 후 저장하세요.");
    } else {
      toast.success(`사진에서 ${entries.length}건의 작업이 감지되었습니다.`);
    }

    setInputSource("vision");
    setSttEntries(entries);
    setCurrentEntryIdx(0);
    setSttPrefill(entries[0]);
    setEditingEntry(null);
    setPendingPhotoIds(
      (result as { photo_ids?: number[] }).photo_ids ?? [],
    );
    setPrefillVersion((v) => v + 1);
    setShowForm(true);
  };

  const handleExportPDF = async () => {
    // 기간 통합 영농일지 PDF — 올해 1/1 ~ 오늘 범위.
    // (시작일 하드코딩을 피해 연도 전환 시 자동으로 범위 재설정되도록 동적 계산)
    // window.open은 새 탭에서 쿠키 SameSite로 인증 실패할 수 있어 fetch+blob 사용.
    const today = toLocalDateString();
    const dateFrom = `${new Date().getFullYear()}-01-01`;
    const url = `http://localhost:8000/api/v1/daily-journal/export-pdf?date_from=${dateFrom}&date_to=${today}`;
    try {
      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) {
        toast.error(`PDF 다운로드 실패 (${res.status})`);
        return;
      }
      const blob = await res.blob();
      const objUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = objUrl;
      a.download = `daily_journal_${dateFrom}_${today}.pdf`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(objUrl), 1000);
    } catch (e) {
      toast.error(`PDF 다운로드 실패: ${(e as Error).message}`);
    }
  };

  // 날짜별 그룹핑
  const grouped = entries.reduce<Record<string, JournalEntryAPI[]>>(
    (acc, entry) => {
      const d = entry.work_date;
      if (!acc[d]) acc[d] = [];
      acc[d].push(entry);
      return acc;
    },
    {},
  );
  const sortedDates = Object.keys(grouped).sort((a, b) => b.localeCompare(a));

  // STT prefill을 JournalEntryAPI 형태로 변환
  const prefillAsEntry = sttPrefill
    ? ({
        ...({} as JournalEntryAPI),
        work_date:
          (sttPrefill.work_date as string) || toLocalDateString(),
        field_name: (sttPrefill.field_name as string) || "",
        crop: (sttPrefill.crop as string) || "",
        work_stage:
          (sttPrefill.work_stage as JournalEntryAPI["work_stage"]) ||
          "작물관리",
        weather: (sttPrefill.weather as string) || null,
        usage_pesticide_product:
          (sttPrefill.usage_pesticide_product as string) || null,
        usage_pesticide_amount:
          (sttPrefill.usage_pesticide_amount as string) || null,
        usage_fertilizer_product:
          (sttPrefill.usage_fertilizer_product as string) || null,
        usage_fertilizer_amount:
          (sttPrefill.usage_fertilizer_amount as string) || null,
        detail: (sttPrefill.detail as string) || null,
      } as JournalEntryAPI)
    : null;

  return (
    <div className="space-y-6">
      {/* 액션 바 */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500">총 {total}건</p>
        <div className="flex gap-2">
          <button
            onClick={handleExportPDF}
            className="btn-outline text-sm"
            title="기간 내 모든 통합 영농일지를 한 PDF로 받습니다"
          >
            <MdFileDownload /> PDF 내보내기
          </button>
          <button
            onClick={() => {
              setShowForm(true);
              setSttPrefill(null);
              setSttEntries([]);
              setCurrentEntryIdx(0);
              setEditingEntry(null);
              setInputSource("text");
              setPendingPhotoIds([]);
              setPrefillVersion((v) => v + 1);
            }}
            className="btn-primary text-sm"
          >
            <MdAdd /> 새 일지
          </button>
        </div>
      </div>

      {/* 오늘의 통합 영농일지 (하루치 개별 entry들을 서술형 1부로 통합) */}
      <DailyJournalPanel refreshToken={panelRefreshToken} />

      {/* 누락 경고 */}
      <MissingFieldsAlert
        fetchMissingFields={fetchMissingFields}
        onEditEntry={(entryId) => {
          const entry = entries.find((e) => e.id === entryId);
          if (entry) setEditingEntry(entry);
        }}
      />

      {/* 음성 입력 FAB (항상 렌더링) */}
      <STTInput
        ref={sttRef}
        onParsed={handleSTTParsed}
        parseSTT={parseSTT}
        transcribeAudio={transcribeAudio}
        sttContext={
          entries.length > 0
            ? { field_name: entries[0].field_name, crop: entries[0].crop }
            : undefined
        }
      />

      {/* 사진 입력 FAB (항상 렌더링) */}
      <PhotoInput
        ref={photoRef}
        onParsed={handlePhotoParsed}
        parsePhotos={parsePhotos}
        photoContext={
          entries.length > 0
            ? { field_name: entries[0].field_name, crop: entries[0].crop }
            : undefined
        }
      />

      {/* 폼 모달 (생성/수정 공용) */}
      {(showForm || editingEntry) && (
        <div className="fixed inset-0 z-40 flex items-center justify-center">
          <div onClick={closeForm} className="absolute inset-0 bg-black/30" />
          <div className="relative bg-white rounded-2xl shadow-xl w-[90vw] max-w-lg max-h-[85vh] overflow-y-auto">
            <div className="sticky top-0 bg-white rounded-t-2xl z-10 border-b border-gray-100">
              <div className="flex items-center justify-between px-5 py-4">
                <h3 className="text-base font-semibold text-gray-900">
                  {editingEntry ? "영농일지 수정" : "새 영농일지 작성"}
                </h3>
                <button
                  onClick={closeForm}
                  className="p-1 text-gray-400 hover:text-gray-600 cursor-pointer"
                >
                  <MdClose className="text-xl" />
                </button>
              </div>
              {/* 다중 엔트리 네비게이터 */}
              {sttEntries.length > 1 && (
                <div className="flex items-center justify-between px-5 py-2 bg-blue-50 border-t border-blue-100">
                  <button
                    type="button"
                    onClick={() => gotoEntry(currentEntryIdx - 1)}
                    disabled={currentEntryIdx === 0}
                    className="p-1 text-blue-700 disabled:text-gray-300 cursor-pointer disabled:cursor-not-allowed"
                  >
                    <MdChevronLeft className="text-2xl" />
                  </button>
                  <span className="text-sm font-medium text-blue-800">
                    {currentEntryIdx + 1} / {sttEntries.length}
                    <span className="ml-2 text-xs text-blue-600">
                      건 감지됨
                    </span>
                  </span>
                  <div className="flex items-center gap-1">
                    <button
                      type="button"
                      onClick={removeCurrentEntry}
                      className="p-1 text-red-500 hover:text-red-600 cursor-pointer"
                      title="이 작업 제외"
                    >
                      <MdDelete className="text-lg" />
                    </button>
                    <button
                      type="button"
                      onClick={() => gotoEntry(currentEntryIdx + 1)}
                      disabled={currentEntryIdx === sttEntries.length - 1}
                      className="p-1 text-blue-700 disabled:text-gray-300 cursor-pointer disabled:cursor-not-allowed"
                    >
                      <MdChevronRight className="text-2xl" />
                    </button>
                  </div>
                </div>
              )}
            </div>
            <div className="px-5 pb-6 pt-4">
              <JournalEntryForm
                ref={formRef}
                key={`${editingEntry?.id || "new"}-${currentEntryIdx}-${prefillVersion}`}
                initialData={editingEntry || prefillAsEntry}
                isEdit={!!editingEntry}
                onSubmit={editingEntry ? handleUpdate : handleCreate}
                onCancel={closeForm}
                onRequestRecord={handleRequestRecord}
                submitLabel={
                  sttEntries.length > 1
                    ? `전체 ${sttEntries.length}건 등록`
                    : undefined
                }
                pesticideUncertain={
                  !editingEntry &&
                  Boolean(sttPrefill?._pesticide_uncertain)
                }
                initialPhotoIds={
                  editingEntry ? undefined : pendingPhotoIds
                }
                uploadPhoto={uploadPhoto}
                deletePhoto={deletePhoto}
              />
            </div>
          </div>
        </div>
      )}

      {/* 필터 */}
      <div className="flex gap-2 flex-wrap">
        {FILTER_STAGES.map((f) => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-2 rounded-full text-sm font-medium whitespace-nowrap cursor-pointer transition-colors ${
              filter === f
                ? "bg-primary text-white"
                : "bg-white text-gray-600 border border-gray-200 hover:bg-gray-50"
            }`}
          >
            {f === "all" ? "전체" : f}
          </button>
        ))}
      </div>

      {/* 로딩 */}
      {loading && (
        <div className="text-center py-10 text-gray-400">불러오는 중...</div>
      )}

      {/* 빈 상태 */}
      {!loading && entries.length === 0 && (
        <div className="text-center py-10">
          <p className="text-gray-400">기록된 영농일지가 없습니다.</p>
          <button
            onClick={() => setShowForm(true)}
            className="mt-3 text-primary text-sm font-medium cursor-pointer hover:underline"
          >
            첫 영농일지를 작성해보세요
          </button>
        </div>
      )}

      {/* 타임라인 */}
      <div className="space-y-0">
        {sortedDates.map((dateStr) => (
          <div key={dateStr}>
            <div className="flex items-center gap-3 py-2">
              <span className="text-sm font-bold text-gray-400">
                {new Date(dateStr).toLocaleDateString("ko-KR", {
                  month: "long",
                  day: "numeric",
                })}
              </span>
              <div className="flex-1 h-px bg-gray-200" />
            </div>

            {grouped[dateStr].map((entry, i) => (
              <div key={entry.id} className="flex gap-4 relative">
                <div className="flex flex-col items-center">
                  <div
                    className={`w-3 h-3 rounded-full ${entry.source === "stt" ? "bg-red-400" : entry.source === "vision" ? "bg-amber-400" : "bg-primary"} z-10`}
                  />
                  {i < grouped[dateStr].length - 1 && (
                    <div className="w-0.5 flex-1 bg-gray-200" />
                  )}
                </div>

                <div className="flex-1 pb-4">
                  {/* 카드 (클릭으로 펼치기) */}
                  <div
                    onClick={() =>
                      setExpandedId(expandedId === entry.id ? null : entry.id)
                    }
                    className="p-4 rounded-xl bg-white border border-gray-100 hover:shadow-sm transition-shadow cursor-pointer"
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span
                          className={`badge text-xs ${STAGE_COLORS[entry.work_stage] || "bg-gray-100 text-gray-600"}`}
                        >
                          {entry.work_stage}
                        </span>
                        <span className="text-xs text-gray-400">
                          {entry.crop}
                        </span>
                        <span className="text-xs text-gray-300">
                          {entry.field_name}
                        </span>
                        {entry.weather && (
                          <span className="text-xs text-cyan-500">
                            {entry.weather}
                          </span>
                        )}
                      </div>
                      <div
                        className="flex gap-1"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button
                          onClick={() => setEditingEntry(entry)}
                          className="p-1 text-gray-400 hover:text-primary cursor-pointer"
                        >
                          <MdEdit className="text-sm" />
                        </button>
                        <button
                          onClick={() => handleDelete(entry.id)}
                          className="p-1 text-gray-400 hover:text-red-500 cursor-pointer"
                        >
                          <MdDelete className="text-sm" />
                        </button>
                      </div>
                    </div>

                    {entry.detail && (
                      <p className="text-sm text-gray-600 mt-2">
                        {entry.detail}
                      </p>
                    )}
                  </div>

                  {/* 펼쳐진 상세 정보 */}
                  {expandedId === entry.id && editingEntry?.id !== entry.id && (
                    <div className="mt-2 p-4 rounded-xl border border-gray-200 bg-gray-50/50">
                      <div className="grid grid-cols-2 gap-3 text-sm">
                        <div>
                          <span className="text-xs font-medium text-gray-400">
                            작업일
                          </span>
                          <p className="text-gray-700">{entry.work_date}</p>
                        </div>
                        <div>
                          <span className="text-xs font-medium text-gray-400">
                            필지
                          </span>
                          <p className="text-gray-700">{entry.field_name}</p>
                        </div>
                        <div>
                          <span className="text-xs font-medium text-gray-400">
                            작목
                          </span>
                          <p className="text-gray-700">{entry.crop}</p>
                        </div>
                        <div>
                          <span className="text-xs font-medium text-gray-400">
                            작업단계
                          </span>
                          <p className="text-gray-700">{entry.work_stage}</p>
                        </div>
                        {entry.weather && (
                          <div>
                            <span className="text-xs font-medium text-gray-400">
                              날씨
                            </span>
                            <p className="text-gray-700">{entry.weather}</p>
                          </div>
                        )}
                        {entry.usage_pesticide_product && (
                          <div>
                            <span className="text-xs font-medium text-gray-400">
                              농약 사용
                            </span>
                            <p className="text-gray-700">
                              {entry.usage_pesticide_product}{" "}
                              {entry.usage_pesticide_amount || ""}
                            </p>
                          </div>
                        )}
                        {entry.usage_fertilizer_product && (
                          <div>
                            <span className="text-xs font-medium text-gray-400">
                              비료 사용
                            </span>
                            <p className="text-gray-700">
                              {entry.usage_fertilizer_product}{" "}
                              {entry.usage_fertilizer_amount || ""}
                            </p>
                          </div>
                        )}
                        {entry.detail && (
                          <div className="col-span-2">
                            <span className="text-xs font-medium text-gray-400">
                              세부작업내용
                            </span>
                            <p className="text-gray-700">{entry.detail}</p>
                          </div>
                        )}
                      </div>

                      {entry.photos && entry.photos.length > 0 && (
                        <div className="mt-3">
                          <span className="text-xs font-medium text-gray-400 mb-1 block">
                            첨부 사진 ({entry.photos.length})
                          </span>
                          {/* 화면 폭과 무관하게 일정한 썸네일 크기 — PC 에서도 과하게 커지지 않도록 고정 */}
                          <div className="flex flex-wrap gap-2">
                            {entry.photos.map((p) => (
                              <button
                                key={p.id}
                                type="button"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setLightboxPhotoId(p.id);
                                }}
                                className="w-24 h-24 rounded-lg overflow-hidden border border-gray-200 bg-gray-50 cursor-zoom-in hover:opacity-80 transition-opacity"
                              >
                                <AuthenticatedPhoto
                                  photoId={p.id}
                                  thumb
                                  className="w-full h-full object-cover"
                                />
                              </button>
                            ))}
                          </div>
                        </div>
                      )}

                      <div className="text-xs text-gray-300 mt-3">
                        {entry.source === "stt"
                          ? "음성 입력"
                          : entry.source === "vision"
                            ? "사진 입력"
                            : "직접 입력"}{" "}
                        |{" "}
                        {new Date(entry.created_at).toLocaleString("ko-KR")}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
        ))}
      </div>

      {lightboxPhotoId !== null && (
        <PhotoLightbox
          photoId={lightboxPhotoId}
          onClose={() => setLightboxPhotoId(null)}
        />
      )}
    </div>
  );
}
