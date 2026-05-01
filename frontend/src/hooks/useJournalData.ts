import { useState, useCallback } from "react";
import type {
  JournalEntryAPI,
  JournalListResponse,
  STTParseResult,
  JournalPhotoParseResult,
  DailySummaryAPI,
  MissingFieldAlert,
} from "@/types";
import { downsampleImage } from "@/utils/imageDownsample";
import { API_BASE } from "@/utils/api";

const opts: RequestInit = { credentials: "include" };

interface JournalFilters {
  page?: number;
  pageSize?: number;
  dateFrom?: string;
  dateTo?: string;
  workStage?: string;
  crop?: string;
}

interface JournalData {
  entries: JournalEntryAPI[];
  total: number;
  page: number;
  pageSize: number;
  loading: boolean;
  error: string | null;
}

export function useJournalData() {
  const [data, setData] = useState<JournalData>({
    entries: [],
    total: 0,
    page: 1,
    pageSize: 20,
    loading: false,
    error: null,
  });

  const fetchEntries = useCallback(async (filters: JournalFilters = {}) => {
    setData((prev) => ({ ...prev, loading: true, error: null }));
    try {
      const params = new URLSearchParams();
      if (filters.page) params.set("page", String(filters.page));
      if (filters.pageSize) params.set("page_size", String(filters.pageSize));
      if (filters.dateFrom) params.set("date_from", filters.dateFrom);
      if (filters.dateTo) params.set("date_to", filters.dateTo);
      if (filters.workStage) params.set("work_stage", filters.workStage);
      if (filters.crop) params.set("crop", filters.crop);

      const res = await fetch(`${API_BASE}/journal?${params}`, opts);
      if (!res.ok) throw new Error("목록 조회 실패");
      const json: JournalListResponse = await res.json();

      setData({
        entries: json.items,
        total: json.total,
        page: json.page,
        pageSize: json.page_size,
        loading: false,
        error: null,
      });
    } catch (e) {
      setData((prev) => ({
        ...prev,
        loading: false,
        error: (e as Error).message,
      }));
    }
  }, []);

  const createEntry = useCallback(
    async (body: Record<string, unknown>): Promise<JournalEntryAPI | null> => {
      try {
        const res = await fetch(`${API_BASE}/journal`, {
          ...opts,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error("생성 실패");
        return await res.json();
      } catch {
        return null;
      }
    },
    [],
  );

  const updateEntry = useCallback(
    async (
      id: number,
      body: Record<string, unknown>,
    ): Promise<JournalEntryAPI | null> => {
      try {
        const res = await fetch(`${API_BASE}/journal/${id}`, {
          ...opts,
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error("수정 실패");
        return await res.json();
      } catch {
        return null;
      }
    },
    [],
  );

  const deleteEntry = useCallback(async (id: number): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/journal/${id}`, {
        ...opts,
        method: "DELETE",
      });
      return res.ok;
    } catch {
      return false;
    }
  }, []);

  const transcribeAudio = useCallback(
    async (
      blob: Blob,
      context?: { field_name?: string; crop?: string },
    ): Promise<string | null> => {
      try {
        const form = new FormData();
        const ext = blob.type.includes("ogg")
          ? "ogg"
          : blob.type.includes("mp4")
            ? "mp4"
            : "webm";
        form.append("file", blob, `audio.${ext}`);
        if (context?.field_name) form.append("field_name", context.field_name);
        if (context?.crop) form.append("crop", context.crop);
        const res = await fetch(`${API_BASE}/journal/transcribe`, {
          ...opts,
          method: "POST",
          body: form,
        });
        if (!res.ok) throw new Error("전사 실패");
        const json: { text: string } = await res.json();
        return json.text;
      } catch {
        return null;
      }
    },
    [],
  );

  const parseSTT = useCallback(
    async (
      rawText: string,
      context?: { field_name?: string; crop?: string },
    ): Promise<STTParseResult> => {
      try {
        const res = await fetch(`${API_BASE}/journal/parse-stt`, {
          ...opts,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            raw_text: rawText,
            field_name: context?.field_name,
            crop: context?.crop,
          }),
        });
        if (!res.ok) {
          let detail = `파싱 실패 (${res.status})`;
          try {
            const body = await res.json();
            if (body?.detail) detail = String(body.detail);
          } catch {
            /* ignore */
          }
          return {
            entries: [],
            unparsed_text: rawText,
            rejected: true,
            reject_reason: detail,
          };
        }
        return await res.json();
      } catch (e) {
        return {
          entries: [],
          unparsed_text: rawText,
          rejected: true,
          reject_reason: `네트워크 오류: ${(e as Error).message}`,
        };
      }
    },
    [],
  );

  const parsePhotos = useCallback(
    async (
      files: File[],
      context?: { field_name?: string; crop?: string },
      signal?: AbortSignal,
    ): Promise<JournalPhotoParseResult> => {
      try {
        const form = new FormData();
        files.forEach((f) => form.append("files", f));
        if (context?.field_name) form.append("field_name", context.field_name);
        if (context?.crop) form.append("crop", context.crop);
        const res = await fetch(`${API_BASE}/journal/parse-photos`, {
          ...opts,
          method: "POST",
          body: form,
          signal,
        });
        if (!res.ok) {
          let detail = `사진 분석 실패 (${res.status})`;
          try {
            const body = await res.json();
            if (body?.detail) detail = String(body.detail);
          } catch {
            /* ignore */
          }
          return {
            entries: [],
            unparsed_text: "",
            rejected: true,
            reject_reason: detail,
          };
        }
        return await res.json();
      } catch (e) {
        // AbortError 는 호출자가 명시적으로 취소한 것 — 그대로 throw 해서 caller 가
        // silent 처리 가능하게.
        if (e instanceof DOMException && e.name === "AbortError") {
          throw e;
        }
        return {
          entries: [],
          unparsed_text: "",
          rejected: true,
          reject_reason: `네트워크 오류: ${(e as Error).message}`,
        };
      }
    },
    [],
  );

  const uploadPhoto = useCallback(
    async (file: File, signal?: AbortSignal): Promise<number | null> => {
      try {
        // 큰 사진(스마트폰 12MP+) 은 BE max bytes(5MB) 초과 가능 → 다운샘플 적용
        const downsampled = await downsampleImage(file);
        const form = new FormData();
        form.append("file", downsampled);
        const res = await fetch(`${API_BASE}/journal/photos`, {
          ...opts,
          method: "POST",
          body: form,
          signal,
        });
        if (!res.ok) return null;
        const json: { photo_id: number } = await res.json();
        return json.photo_id;
      } catch (e) {
        if (e instanceof DOMException && e.name === "AbortError") {
          throw e;
        }
        return null;
      }
    },
    [],
  );

  const deletePhoto = useCallback(async (photoId: number): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/journal/photos/${photoId}`, {
        ...opts,
        method: "DELETE",
      });
      return res.ok;
    } catch {
      return false;
    }
  }, []);

  const fetchDailySummary = useCallback(
    async (date: string): Promise<DailySummaryAPI | null> => {
      try {
        const res = await fetch(
          `${API_BASE}/journal/daily-summary?date=${date}`,
          opts,
        );
        if (!res.ok) throw new Error("요약 조회 실패");
        return await res.json();
      } catch {
        return null;
      }
    },
    [],
  );

  const fetchMissingFields = useCallback(
    async (
      dateFrom: string,
      dateTo: string,
    ): Promise<{
      missing_fields: MissingFieldAlert[];
      total: number;
    } | null> => {
      try {
        const res = await fetch(
          `${API_BASE}/journal/missing-fields?date_from=${dateFrom}&date_to=${dateTo}`,
          opts,
        );
        if (!res.ok) throw new Error("누락 조회 실패");
        return await res.json();
      } catch {
        return null;
      }
    },
    [],
  );

  return {
    ...data,
    fetchEntries,
    createEntry,
    updateEntry,
    deleteEntry,
    parseSTT,
    transcribeAudio,
    parsePhotos,
    uploadPhoto,
    deletePhoto,
    fetchDailySummary,
    fetchMissingFields,
  };
}
