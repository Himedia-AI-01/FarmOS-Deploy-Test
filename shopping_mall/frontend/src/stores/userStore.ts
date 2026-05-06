import { create } from 'zustand';

const FARMOS_API = import.meta.env.VITE_FARMOS_LOGIN_URL ?? 'http://localhost:5173/login';
// 빈 문자열이면 같은 오리진(상대경로). 미설정(undefined)이면 로컬 dev fallback.
// `||` 는 빈 문자열도 falsy 취급해 prod에서 localhost로 새므로 `??` 사용.
const SHOP_API = (import.meta.env.VITE_API_URL as string | undefined) ?? 'http://localhost:4000';

interface AuthUser {
  login_id: string;
  name: string;
  shop_user_id: number | null;
}

interface UserState {
  user: AuthUser | null;
  isLoggedIn: boolean;
  isLoading: boolean;
  checkAuth: () => Promise<void>;
  logout: () => Promise<void>;
}

export const useUserStore = create<UserState>((set) => ({
  user: null,
  isLoggedIn: false,
  isLoading: true,

  checkAuth: async () => {
    try {
      // 쇼핑몰 백엔드가 FarmOS 백엔드에 서버사이드 검증 수행
      const res = await fetch(`${SHOP_API}/api/users/auth/status`, {
        credentials: 'include',
      });
      if (res.ok) {
        const data = await res.json();
        if (data.authenticated) {
          set({
            user: {
              login_id: data.login_id,
              name: data.name,
              shop_user_id: data.shop_user_id,
            },
            isLoggedIn: true,
            isLoading: false,
          });
          return;
        }
      }
    } catch {
      // 무시
    }
    set({ user: null, isLoggedIn: false, isLoading: false });
  },

  logout: async () => {
    // FarmOS 쿠키 삭제 (같은 localhost 도메인)
    try {
      await fetch(`${FARMOS_API}/auth/logout`, {
        method: 'POST',
        credentials: 'include',
      });
    } catch {
      // 무시
    }
    set({ user: null, isLoggedIn: false });
  },
}));
