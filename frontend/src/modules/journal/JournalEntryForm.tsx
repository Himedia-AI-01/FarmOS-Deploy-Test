import { useState, useRef, forwardRef, useImperativeHandle } from "react";
import { MdExpandMore, MdExpandLess, MdMic, MdClose, MdAddPhotoAlternate } from "react-icons/md";
import type { JournalEntryAPI } from "@/types";

import AuthenticatedPhoto from "./AuthenticatedPhoto";

export interface JournalEntryFormHandle {
  getFormData: () => Record<string, unknown>;
}

const WORK_STAGES = [
  "사전준비",
  "경운",
  "파종",
  "정식",
  "작물관리",
  "수확",
] as const;
const WEATHER_OPTIONS = ["맑음", "흐림", "비", "눈", "바람"];

interface Props {
  initialData?: JournalEntryAPI | null;
  isEdit?: boolean;
  onSubmit: (data: Record<string, unknown>) => Promise<void>;
  onCancel: () => void;
  onRequestRecord?: () => void;
  submitLabel?: string;
  headerSlot?: React.ReactNode;
  pesticideUncertain?: boolean;

  /** parse-photos 응답으로 미리 저장된 사진 ID — 신규 entry 첨부에 사용 */
  initialPhotoIds?: number[];
  /** 사진 추가 (분석 없이 단순 업로드) */
  uploadPhoto?: (file: File) => Promise<number | null>;
  /** 사진 명시적 삭제 (× 버튼) */
  deletePhoto?: (photoId: number) => Promise<boolean>;
}

const JournalEntryForm = forwardRef<JournalEntryFormHandle, Props>(
  function JournalEntryForm(
    {
      initialData,
      isEdit = false,
      onSubmit,
      onCancel,
      onRequestRecord,
      submitLabel,
      headerSlot,
      pesticideUncertain = false,
      initialPhotoIds,
      uploadPhoto,
      deletePhoto,
    },
    ref,
  ) {
    const [workDate, setWorkDate] = useState(
      initialData?.work_date || new Date().toISOString().slice(0, 10),
    );
    const [fieldName, setFieldName] = useState(initialData?.field_name || "");
    const [crop, setCrop] = useState(initialData?.crop || "");
    const [workStage, setWorkStage] = useState(
      initialData?.work_stage || "작물관리",
    );
    const [weather, setWeather] = useState(initialData?.weather || "");
    const [detail, setDetail] = useState(initialData?.detail || "");

    // 농약/비료 사용
    const [usagePesticideProduct, setUsagePesticideProduct] = useState(
      initialData?.usage_pesticide_product || "",
    );
    const [usagePesticideAmount, setUsagePesticideAmount] = useState(
      initialData?.usage_pesticide_amount || "",
    );
    const [usageFertilizerProduct, setUsageFertilizerProduct] = useState(
      initialData?.usage_fertilizer_product || "",
    );
    const [usageFertilizerAmount, setUsageFertilizerAmount] = useState(
      initialData?.usage_fertilizer_amount || "",
    );

    const [showChemicals, setShowChemicals] = useState(
      !!(
        initialData?.usage_pesticide_product ||
        initialData?.usage_fertilizer_product
      ),
    );
    const [submitting, setSubmitting] = useState(false);

    // 첨부 사진 ID 목록 — 편집 모드는 entry.photos 에서, 신규는 prop.initialPhotoIds 에서.
    const [photoIds, setPhotoIds] = useState<number[]>(
      initialData?.photos?.map((p) => p.id) ?? initialPhotoIds ?? [],
    );
    // 폼 진입 시점의 "기존" 사진 ID 집합 — useRef 로 평생 고정 (첫 mount 값 그대로 유지).
    // ✕ 누른 사진이 originalPhotoIds 에 속하면 즉시 삭제하지 않고 PATCH reconcile
    // 시점에만 BE 가 정리하도록 미룬다. 그래야 사용자가 편집 중 취소했을 때
    // 원본 entry 의 사진이 손실되지 않는다 (CodeRabbit 리뷰).
    const originalPhotoIdsRef = useRef<Set<number>>(
      new Set(initialData?.photos?.map((p) => p.id) ?? []),
    );
    const [photoBusy, setPhotoBusy] = useState(false);

    const handleAddPhoto = async (e: React.ChangeEvent<HTMLInputElement>) => {
      if (!uploadPhoto) return;
      const file = e.target.files?.[0];
      e.target.value = ""; // 같은 파일 재선택 가능하도록
      if (!file) return;
      setPhotoBusy(true);
      try {
        const id = await uploadPhoto(file);
        if (id != null) setPhotoIds((prev) => [...prev, id]);
      } finally {
        setPhotoBusy(false);
      }
    };

    const handleRemovePhoto = async (id: number) => {
      // state 에서 즉시 제거 (UI 반응 빠름)
      setPhotoIds((prev) => prev.filter((p) => p !== id));

      // 기존 사진(편집 시작 시점에 entry 와 연결된 사진) 은 즉시 삭제 X.
      // 사용자가 저장하지 않고 폼을 닫으면 원본 entry 에 그대로 남아있어야 한다.
      // 저장 시 PATCH 의 photo_ids reconcile 이 BE 측에서 정리한다.
      if (originalPhotoIdsRef.current.has(id)) {
        return;
      }

      // 신규 추가한 임시 사진 (이번 세션에서 uploadPhoto 로 올린 것) 만 즉시 삭제.
      // 이건 폼을 취소해도 어차피 entry 와 연결되지 않은 orphan 이라 누수 회피용.
      if (deletePhoto) {
        try {
          await deletePhoto(id);
        } catch {
          // 실패해도 UI 는 이미 제거됨; orphan cleanup 으로 후속 처리
        }
      }
    };

    const buildBody = (): Record<string, unknown> => ({
      work_date: workDate,
      field_name: fieldName.trim(),
      crop: crop.trim(),
      work_stage: workStage,
      weather: weather || null,
      usage_pesticide_product: usagePesticideProduct || null,
      usage_pesticide_amount: usagePesticideAmount || null,
      usage_fertilizer_product: usageFertilizerProduct || null,
      usage_fertilizer_amount: usageFertilizerAmount || null,
      detail: detail || null,
      source: "text",
      photo_ids: photoIds,
    });

    useImperativeHandle(ref, () => ({ getFormData: buildBody }));

    const handleSubmit = async (e: React.FormEvent) => {
      e.preventDefault();
      if (!fieldName.trim() || !crop.trim()) return;
      // 사진 업로드 진행 중이면 submit 차단 — 업로드 완료 전 buildBody() 가
      // 부르면 photo_ids 에 새 사진 ID 가 빠져 첨부 유실 + orphan 발생.
      if (photoBusy) return;

      setSubmitting(true);
      await onSubmit(buildBody());
      setSubmitting(false);
    };

    return (
      <form onSubmit={handleSubmit} className="space-y-4">
        {headerSlot}
        {/* 필수 필드 */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              작업일 *
            </label>
            <input
              type="date"
              value={workDate}
              onChange={(e) => setWorkDate(e.target.value)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              필지 *
            </label>
            <input
              type="text"
              value={fieldName}
              onChange={(e) => setFieldName(e.target.value)}
              placeholder="예: 1번 필지, 하우스 2호"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              작목 *
            </label>
            <input
              type="text"
              value={crop}
              onChange={(e) => setCrop(e.target.value)}
              placeholder="예: 사과, 고추, 토마토"
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30"
              required
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              작업단계 *
            </label>
            <select
              value={workStage}
              onChange={(e) => setWorkStage(e.target.value as typeof workStage)}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30"
            >
              {WORK_STAGES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* 날씨 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            날씨
          </label>
          <div className="flex gap-2 flex-wrap">
            {WEATHER_OPTIONS.map((w) => (
              <button
                key={w}
                type="button"
                onClick={() => setWeather(weather === w ? "" : w)}
                className={`px-3 py-1.5 rounded-full text-sm cursor-pointer transition-colors ${
                  weather === w
                    ? "bg-primary text-white"
                    : "bg-gray-100 text-gray-600 hover:bg-gray-200"
                }`}
              >
                {w}
              </button>
            ))}
          </div>
        </div>

        {/* 농약/비료 사용 (접기/펼치기) */}
        <div>
          <button
            type="button"
            onClick={() => setShowChemicals(!showChemicals)}
            className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 cursor-pointer"
          >
            {showChemicals ? <MdExpandLess /> : <MdExpandMore />}
            농약/비료 사용 정보
          </button>

          {showChemicals && (
            <div className="mt-3 grid grid-cols-2 gap-3 p-3 bg-gray-50 rounded-lg">
              <div>
                <label className="flex items-center gap-1 text-xs font-medium text-gray-500 mb-1">
                  농약 제품명
                  {pesticideUncertain && (
                    <span
                      className="px-1.5 py-0.5 rounded bg-yellow-100 text-yellow-700 text-[10px] font-semibold"
                      title="음성 인식된 농약이 DB와 정확히 일치하지 않습니다. 제품명을 한번 더 확인해주세요."
                    >
                      확인 필요
                    </span>
                  )}
                </label>
                <input
                  type="text"
                  value={usagePesticideProduct}
                  onChange={(e) => setUsagePesticideProduct(e.target.value)}
                  placeholder="예: 프로피네브 수화제"
                  className={`w-full px-3 py-2 border rounded-lg text-sm focus:outline-none focus:ring-2 ${
                    pesticideUncertain
                      ? "border-yellow-300 bg-yellow-50 focus:ring-yellow-300/40"
                      : "border-gray-200 focus:ring-primary/30"
                  }`}
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  농약 사용량
                </label>
                <input
                  type="text"
                  value={usagePesticideAmount}
                  onChange={(e) => setUsagePesticideAmount(e.target.value)}
                  placeholder="예: 500배액"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  비료 제품명
                </label>
                <input
                  type="text"
                  value={usageFertilizerProduct}
                  onChange={(e) => setUsageFertilizerProduct(e.target.value)}
                  placeholder="예: 요소비료"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-500 mb-1">
                  비료 사용량
                </label>
                <input
                  type="text"
                  value={usageFertilizerAmount}
                  onChange={(e) => setUsageFertilizerAmount(e.target.value)}
                  placeholder="예: 10kg"
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-primary/30"
                />
              </div>
            </div>
          )}
        </div>

        {/* 첨부 사진 */}
        {(uploadPhoto || photoIds.length > 0) && (
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">
              첨부 사진 {photoIds.length > 0 && `(${photoIds.length})`}
            </label>
            <div className="grid grid-cols-4 gap-2">
              {photoIds.map((id) => (
                <div
                  key={id}
                  className="relative aspect-square rounded-lg overflow-hidden border border-gray-200 bg-gray-50"
                >
                  <AuthenticatedPhoto
                    photoId={id}
                    thumb
                    className="w-full h-full object-cover"
                  />
                  <button
                    type="button"
                    onClick={() => handleRemovePhoto(id)}
                    className="absolute top-1 right-1 w-6 h-6 rounded-full bg-black/60 hover:bg-black/80 text-white flex items-center justify-center cursor-pointer"
                    aria-label="사진 제거"
                  >
                    <MdClose className="text-sm" />
                  </button>
                </div>
              ))}
              {uploadPhoto && (
                <label
                  className={`aspect-square rounded-lg border-2 border-dashed border-gray-300 flex flex-col items-center justify-center text-gray-400 cursor-pointer hover:border-primary hover:text-primary ${
                    photoBusy ? "opacity-50 pointer-events-none" : ""
                  }`}
                >
                  <MdAddPhotoAlternate className="text-2xl" />
                  <span className="text-xs mt-1">
                    {photoBusy ? "추가 중" : "사진 추가"}
                  </span>
                  <input
                    type="file"
                    accept="image/*"
                    capture="environment"
                    className="hidden"
                    onChange={handleAddPhoto}
                  />
                </label>
              )}
            </div>
          </div>
        )}

        {/* 세부작업내용 */}
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            세부작업내용
          </label>
          <textarea
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            rows={3}
            placeholder="작업 내용을 자유롭게 입력하세요"
            className="w-full px-3 py-2 border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
          />
        </div>

        {/* 버튼 */}
        <div className="flex gap-3 justify-end items-center">
          {!isEdit && onRequestRecord && (
            <button
              type="button"
              onClick={onRequestRecord}
              className="mr-auto flex items-center gap-2 px-6 py-3 text-base font-medium text-white bg-red-500 rounded-xl shadow-sm hover:bg-red-600 cursor-pointer"
            >
              <MdMic className="text-xl" /> 녹음
            </button>
          )}
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm text-gray-600 bg-gray-100 rounded-lg hover:bg-gray-200 cursor-pointer"
          >
            취소
          </button>
          <button
            type="submit"
            disabled={
              submitting || photoBusy || !fieldName.trim() || !crop.trim()
            }
            className="btn-primary disabled:opacity-50"
            title={photoBusy ? "사진 업로드 중에는 저장할 수 없습니다." : undefined}
          >
            {submitting
              ? "저장 중..."
              : photoBusy
                ? "사진 업로드 중..."
                : submitLabel || (isEdit ? "수정" : "등록")}
          </button>
        </div>
      </form>
    );
  },
);

export default JournalEntryForm;
