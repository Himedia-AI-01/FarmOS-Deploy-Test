import axios from 'axios';

/**
 * baseURL 우선순위:
 *   1. VITE_API_URL (빌드 타임 주입 — deploy.yml)
 *   2. '' (빈 문자열) → 같은 오리진 (shoppingmall.<도메인>) + 상대경로
 *   3. 'http://localhost:4000' (로컬 dev fallback)
 */
const fromEnv = (import.meta.env as Record<string, string | undefined>).VITE_API_URL;
const baseURL = fromEnv ?? (typeof window !== 'undefined' ? '' : 'http://localhost:4000');

const api = axios.create({
  baseURL,
  withCredentials: true, // farmos_token 쿠키 자동 전송 (cross-app JWT 공유)
  headers: {
    'Content-Type': 'application/json',
  },
});

export default api;
