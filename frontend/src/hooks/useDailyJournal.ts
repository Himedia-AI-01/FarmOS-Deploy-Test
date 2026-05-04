import { useCallback, useMemo } from "react";
import type {
  DailyJournalAPI,
  DailyJournalRevisionAPI,
  DailyJournalNarrativeSource,
} from "@/types";
import { API_BASE } from "@/utils/api";
const opts: RequestInit = { credentials: "include" };

/** 서버에서 내려오는 에러 응답 본문 형태 (라우터에서 detail={code, message}로 감싸서 내려줌). */
export interface DailyJournalErrorDetail {
  code?: string;
  message?: string;
}

export interface DailyJournalResult<T> {
  ok: boolean;
  data?: T;
  status?: number;
  error?: DailyJournalErrorDetail;
}

async function parseError(res: Response): Promise<DailyJournalErrorDetail> {
  try {
    const body = await res.json();
    // FastAPI는 HTTPException(detail=...)을 {"detail": ...} 로 감싼다.
    const detail = body?.detail;
    if (detail && typeof detail === "object") return detail as DailyJournalErrorDetail;
    if (typeof detail === "string") return { message: detail };
    return { message: `요청 실패 (${res.status})` };
  } catch {
    return { message: `요청 실패 (${res.status})` };
  }
}

/**
 * 일일 통합 영농일지 전용 hook.
 *
 * 기존 `useJournalData`(개별 JournalEntry CRUD)와는 별개로 운영.
 * 내부 상태는 보유하지 않고 모든 메서드가 Promise를 반환하는 "API 래퍼" 형태.
 * Panel 컴포넌트 쪽에서 state를 관리한다.
 */
export function useDailyJournal() {
  const fetchByDate = useCallback(
    async (date: string): Promise<DailyJournalResult<DailyJournalAPI | null>> => {
      try {
        const res = await fetch(
          `${API_BASE}/daily-journal?date=${encodeURIComponent(date)}`,
          opts,
        );
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        // 서버가 통합본 없는 날짜엔 200 + null body로 응답 (404 아님).
        const body = (await res.json()) as DailyJournalAPI | null;
        return { ok: true, status: res.status, data: body };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const fetchById = useCallback(
    async (
      id: number,
      includeRevisions = false,
    ): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const url =
          `${API_BASE}/daily-journal/${id}` +
          (includeRevisions ? "?include_revisions=true" : "");
        const res = await fetch(url, opts);
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const generate = useCallback(
    async (
      work_date: string,
      opts_: { overwrite?: boolean; entry_ids?: number[] } = {},
    ): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/generate`, {
          ...opts,
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            work_date,
            overwrite: opts_.overwrite ?? false,
            entry_ids: opts_.entry_ids,
          }),
        });
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const regenerate = useCallback(
    async (id: number): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/${id}/regenerate`, {
          ...opts,
          method: "POST",
        });
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const updateNarrative = useCallback(
    async (
      id: number,
      narrative: string,
      narrative_source: Exclude<DailyJournalNarrativeSource, "llm" | "template_fallback"> = "llm_edited",
    ): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/${id}`, {
          ...opts,
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ narrative, narrative_source }),
        });
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const confirmDaily = useCallback(
    async (id: number): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/${id}/confirm`, {
          ...opts,
          method: "POST",
        });
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const unconfirm = useCallback(
    async (id: number): Promise<DailyJournalResult<DailyJournalAPI>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/${id}/unconfirm`, {
          ...opts,
          method: "POST",
        });
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  const fetchRevisions = useCallback(
    async (id: number): Promise<DailyJournalResult<DailyJournalRevisionAPI[]>> => {
      try {
        const res = await fetch(`${API_BASE}/daily-journal/${id}/revisions`, opts);
        if (!res.ok) {
          return { ok: false, status: res.status, error: await parseError(res) };
        }
        return { ok: true, status: res.status, data: await res.json() };
      } catch (e) {
        return { ok: false, error: { message: `네트워크 오류: ${(e as Error).message}` } };
      }
    },
    [],
  );

  // ⚠️ 반환 객체는 반드시 useMemo로 안정화할 것.
  // 그렇지 않으면 이 hook을 쓰는 컴포넌트의 useEffect/useCallback에 deps로 들어갔을 때
  // 매 렌더마다 새 레퍼런스가 되어 무한 루프를 유발한다.
  return useMemo(
    () => ({
      fetchByDate,
      fetchById,
      generate,
      regenerate,
      updateNarrative,
      confirm: confirmDaily,
      unconfirm,
      fetchRevisions,
    }),
    [
      fetchByDate,
      fetchById,
      generate,
      regenerate,
      updateNarrative,
      confirmDaily,
      unconfirm,
      fetchRevisions,
    ],
  );
}
