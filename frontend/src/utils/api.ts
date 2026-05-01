/**
 * 백엔드 API 베이스 URL — 환경변수 우선, fallback 은 로컬 개발 default.
 *
 * 운영/스테이징 배포 시 `VITE_API_BASE=https://api.example.com/api/v1` 으로 주입.
 * (Vite 가 빌드 타임에 import.meta.env 를 inline 한다.)
 */
export const API_BASE: string =
  ((import.meta.env as Record<string, string | undefined>).VITE_API_BASE) ??
  "http://localhost:8000/api/v1";
