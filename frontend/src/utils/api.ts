/**
 * 백엔드 API 베이스 URL — 단일 진실 소스(SoT).
 *
 * 우선순위:
 *  1. VITE_API_BASE (빌드 타임 주입 — deploy.yml 의 build env)
 *  2. 같은 호스트 + /api/v1 (배포 환경 자동 탐지 — CF 도메인이든 EIP든 자동)
 *  3. 'http://localhost:8000/api/v1' (로컬 dev fallback — vite proxy 가 받음)
 */
const fromEnv = (import.meta.env as Record<string, string | undefined>).VITE_API_BASE;

const fromHost =
  typeof window !== 'undefined' && window.location?.origin
    ? `${window.location.origin}/api/v1`
    : null;

export const API_BASE: string =
  fromEnv ?? fromHost ?? 'http://localhost:8000/api/v1';

/**
 * 백엔드 origin (스킴 + 호스트, /api/v1 접두사 없음).
 * 정적 자원(이미지 등) URL 조립용. 같은 오리진이면 빈 문자열로 둠 → 상대경로화.
 */
const fromBackendEnv = (import.meta.env as Record<string, string | undefined>).VITE_BACKEND_ORIGIN;

export const BACKEND_ORIGIN: string =
  fromBackendEnv ?? '';
