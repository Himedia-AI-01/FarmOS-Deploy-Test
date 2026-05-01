import {
  useState,
  useRef,
  useCallback,
  forwardRef,
  useImperativeHandle,
} from "react";
import { MdPhotoCamera, MdClose } from "react-icons/md";
import type { JournalPhotoParseResult } from "@/types";
import { downsampleImage } from "@/utils/imageDownsample";

interface Props {
  onParsed: (result: JournalPhotoParseResult) => void;
  parsePhotos: (
    files: File[],
    context?: { field_name?: string; crop?: string },
    signal?: AbortSignal,
  ) => Promise<JournalPhotoParseResult>;
  photoContext?: { field_name?: string; crop?: string };
}

export type PhotoStatus = "idle" | "downsampling" | "uploading" | "parsing";

export interface PhotoInputHandle {
  start: () => void;
}

const MAX_SIDE = 1280;
const QUALITY = 0.85;
const MAX_FILES = 10;

const PhotoInput = forwardRef<PhotoInputHandle, Props>(function PhotoInput(
  { onParsed, parsePhotos, photoContext },
  ref,
) {
  const [status, setStatus] = useState<PhotoStatus>("idle");
  const [progress, setProgress] = useState<number>(0); // 0~100
  const [phaseText, setPhaseText] = useState<string>("");
  const inputRef = useRef<HTMLInputElement | null>(null);
  const cancelledRef = useRef<boolean>(false);
  // 진행 중인 upload/parse 요청 abort 용 — handleBusyCancel 에서 abort() 호출.
  const abortRef = useRef<AbortController | null>(null);
  const interpRef = useRef<number | null>(null);

  const stopInterp = useCallback(() => {
    if (interpRef.current !== null) {
      window.clearInterval(interpRef.current);
      interpRef.current = null;
    }
  }, []);

  const startInterp = useCallback(
    (from: number, to: number, durationMs: number) => {
      stopInterp();
      setProgress(from);
      const startAt = Date.now();
      interpRef.current = window.setInterval(() => {
        const t = Math.min(1, (Date.now() - startAt) / durationMs);
        setProgress(from + (to - from) * t);
        if (t >= 1) stopInterp();
      }, 80);
    },
    [stopInterp],
  );

  const openPicker = useCallback(() => {
    cancelledRef.current = false;
    inputRef.current?.click();
  }, []);

  const handleFiles = useCallback(
    async (fileList: FileList) => {
      const files = Array.from(fileList);
      if (files.length === 0) return;
      if (files.length > MAX_FILES) {
        onParsed({
          entries: [],
          unparsed_text: "",
          rejected: true,
          reject_reason: `최대 ${MAX_FILES}장까지 업로드 가능합니다.`,
        });
        return;
      }
      // 진행 중인 fetch 를 실제로 abort 가능하게 controller 새로 생성 (취소 버튼이 단순 UI 차단이
      // 아니라 네트워크/서버 작업까지 진짜 중단 — 비용·대역폭 절약).
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        cancelledRef.current = false;

        // 1) 다운샘플
        setStatus("downsampling");
        setPhaseText("사진을 준비하는 중입니다...");
        startInterp(5, 25, 2500);
        const downsampled = await Promise.all(
          files.map((f) => downsampleImage(f, MAX_SIDE, QUALITY)),
        );
        if (cancelledRef.current) {
          stopInterp();
          setProgress(0);
          setStatus("idle");
          return;
        }

        // 2) 업로드 + 분석 (BE 단일 호출이라 둘이 합쳐짐 — UX 상 분리 표시)
        setStatus("uploading");
        setPhaseText("사진을 업로드하고 있습니다...");
        startInterp(28, 55, 4000);
        // parsePhotos 호출 전 분석 phase로 자연스럽게 전환
        const analyzingTimer = window.setTimeout(() => {
          setStatus("parsing");
          setPhaseText("AI가 사진을 분석하고 있습니다...");
          startInterp(58, 95, 12000);
        }, 1500);

        const result = await parsePhotos(
          downsampled,
          photoContext,
          controller.signal,
        );
        window.clearTimeout(analyzingTimer);
        stopInterp();

        if (cancelledRef.current) {
          setProgress(0);
          setStatus("idle");
          return;
        }

        // 오버레이를 먼저 닫은 뒤 onParsed 호출.
        // onParsed 는 부모 state(showForm 등)를 변경하므로, 만약 onParsed 를 먼저 호출하면
        // confirm 다이얼로그(handlePhotoParsed 의 거절 분기)가 동기적으로 실행되는 동안
        // 자식의 setStatus 가 schedule 되지 않거나 부모 re-render 와 충돌해 overlay 가
        // 잔존하는 사례가 발생함. status 를 먼저 idle 로 바꿔 AnimatePresence 가 exit
        // 처리하도록 한 뒤 onParsed 를 호출한다.
        setProgress(100);
        setProgress(0);
        setStatus("idle");
        setPhaseText("");
        onParsed(result);
      } catch (e) {
        stopInterp();
        setProgress(0);
        setStatus("idle");
        setPhaseText("");
        // AbortError 는 사용자가 취소 버튼을 눌러 의도적으로 끊은 것 — silent (toast 없음).
        if (e instanceof DOMException && e.name === "AbortError") {
          return;
        }
        onParsed({
          entries: [],
          unparsed_text: "",
          rejected: true,
          reject_reason: `사진 처리 실패: ${(e as Error).message}`,
        });
      } finally {
        abortRef.current = null;
      }
    },
    [onParsed, parsePhotos, photoContext, startInterp, stopInterp],
  );

  const handleBusyCancel = useCallback(() => {
    cancelledRef.current = true;
    // 진행 중인 fetch 를 실제로 중단 (서버측 vision LLM 호출/디스크 저장도 중단됨).
    abortRef.current?.abort();
    stopInterp();
    setProgress(0);
    setStatus("idle");
    setPhaseText("");
  }, [stopInterp]);

  useImperativeHandle(ref, () => ({ start: openPicker }), [openPicker]);

  const isBusy =
    status === "downsampling" ||
    status === "uploading" ||
    status === "parsing";

  return (
    <>
      {/* 파일 선택 input — 카메라/갤러리 둘 다 (모바일은 capture 힌트로 카메라 우선) */}
      <input
        ref={inputRef}
        type="file"
        accept="image/*"
        multiple
        capture="environment"
        className="hidden"
        onChange={(e) => {
          const fl = e.target.files;
          if (fl) void handleFiles(fl);
          // 같은 파일 재선택 가능하도록 reset
          e.target.value = "";
        }}
      />

      {/* FAB — STT FAB 위에 수직으로 쌓이도록 (모바일/데스크톱 모두 right 정렬 동일) */}
      {status === "idle" && (
        <button
          onClick={openPicker}
          className="fixed bottom-[152px] right-4 lg:bottom-[96px] lg:right-8 z-30
            h-12 px-5 rounded-full shadow-lg flex items-center justify-center gap-2
            bg-primary hover:bg-primary/90 active:scale-95 cursor-pointer
            transition-colors duration-200"
        >
          <MdPhotoCamera className="text-white text-xl" />
          <span className="text-white text-sm font-medium">사진으로 작성</span>
        </button>
      )}

      {/* 분석 중 오버레이 — AnimatePresence 가 unmount 시점에 잔존 이슈가 있어 단순 conditional 로 처리 */}
      {isBusy && (
        <div
          className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px]"
        >
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-5">
              <p className="text-white text-lg font-medium">{phaseText}</p>
              <div className="w-64 h-1.5 rounded-full bg-white/20 overflow-hidden">
                <div
                  className="h-full bg-white/90 transition-[width] duration-200"
                  style={{ width: `${Math.round(progress)}%` }}
                />
              </div>
              <p className="text-white/60 text-xs">
                AI 결과는 미리보기 화면에서 검수·편집할 수 있습니다
              </p>
            </div>

            {/* 취소 버튼 */}
            <div className="fixed bottom-[88px] right-4 lg:bottom-8 lg:right-8 z-50">
              <button
                onClick={handleBusyCancel}
                className="h-12 px-5 rounded-full shadow-lg flex items-center justify-center gap-2
                  bg-white/90 cursor-pointer transition-colors hover:bg-white"
              >
                <MdClose className="text-gray-600 text-xl" />
                <span className="text-gray-600 text-sm font-medium">취소</span>
              </button>
            </div>
        </div>
      )}
    </>
  );
});

export default PhotoInput;
