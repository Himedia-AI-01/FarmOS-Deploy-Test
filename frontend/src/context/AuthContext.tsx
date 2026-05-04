import { createContext, useContext, useState, useEffect, useCallback, useRef, type ReactNode } from 'react';
import { API_BASE } from '@/utils/api';

// Access token 만료 5분 전에 자동 갱신 (55분)
const TOKEN_REFRESH_INTERVAL = 55 * 60 * 1000;

export interface AuthUser {
  user_id: string;
  name: string;
  email: string;
  onboarding_completed: boolean;
  farmname: string;
  location: string;
  location_category: string; // 💡 백엔드에서 파싱된 지역명 추가
  area: number;
  main_crop: string;
  crop_variety: string;
  farmland_type: string;
  farmer_type: string;
  is_promotion_area: boolean;
  has_farm_registration: boolean;
  years_rural_residence: number;
  years_farming: number;
}

interface AuthContextType {
  user: AuthUser | null;
  login: (userId: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  isAuthenticated: boolean;
  isLoading: boolean;
  needsOnboarding: boolean;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const refreshTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current) {
      clearInterval(refreshTimerRef.current);
      refreshTimerRef.current = null;
    }
  }, []);

  // Refresh Token으로 Access Token 갱신
  const refreshAccessToken = useCallback(async (): Promise<boolean> => {
    try {
      const res = await fetch(`${API_BASE}/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
      });
      return res.ok;
    } catch {
      return false;
    }
  }, []);

  // 서버에서 현재 사용자 정보 조회
  const fetchUser = useCallback(async (): Promise<AuthUser | null> => {
    try {
      const res = await fetch(`${API_BASE}/auth/me`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        return {
          user_id: data.user_id,
          name: data.name,
          email: data.email ?? '',
          onboarding_completed: data.onboarding_completed ?? false,
          farmname: data.farmname ?? '',
          location: data.location ?? '',
          location_category: data.location_category ?? '', // 💡 백엔드에서 받은 파싱된 지역명
          area: data.area ?? 0,
          main_crop: data.main_crop ?? '',
          crop_variety: data.crop_variety ?? '',
          farmland_type: data.farmland_type ?? '',
          farmer_type: data.farmer_type ?? '일반',
          is_promotion_area: data.is_promotion_area ?? false,
          has_farm_registration: data.has_farm_registration ?? false,
          years_rural_residence: data.years_rural_residence ?? 0,
          years_farming: data.years_farming ?? 0,
        };
      }
      return null;
    } catch {
      return null;
    }
  }, []);

  // 토큰 자동 갱신 타이머 시작
  const startRefreshTimer = useCallback(() => {
    clearRefreshTimer();
    refreshTimerRef.current = setInterval(async () => {
      const refreshed = await refreshAccessToken();
      if (!refreshed) {
        clearRefreshTimer();
        setUser(null);
      }
    }, TOKEN_REFRESH_INTERVAL);
  }, [refreshAccessToken, clearRefreshTimer]);

  // 앱 시작 시 인증 상태 확인 (refresh 포함)
  const checkAuth = useCallback(async () => {
    let userData = await fetchUser();
    if (!userData) {
      // Access token 만료 → refresh 시도
      const refreshed = await refreshAccessToken();
      if (refreshed) {
        userData = await fetchUser();
      }
    }
    if (userData) {
      setUser(userData);
      startRefreshTimer();
    } else {
      setUser(null);
      clearRefreshTimer();
    }
    setIsLoading(false);
  }, [fetchUser, refreshAccessToken, startRefreshTimer, clearRefreshTimer]);

  useEffect(() => {
    checkAuth();
    return () => clearRefreshTimer();
  }, [checkAuth, clearRefreshTimer]);

  const login = async (userId: string, password: string) => {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ user_id: userId, password }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || '로그인에 실패했습니다.');
    }
    await checkAuth();
  };

  const logout = async () => {
    clearRefreshTimer();
    await fetch(`${API_BASE}/auth/logout`, {
      method: 'POST',
      credentials: 'include',
    });
    setUser(null);
  };

  const refreshUser = useCallback(async () => {
    const userData = await fetchUser();
    if (userData) {
      setUser(userData);
    }
  }, [fetchUser]);

  const needsOnboarding = !!user && !user.onboarding_completed;

  return (
    <AuthContext.Provider value={{ user, login, logout, refreshUser, isAuthenticated: !!user, isLoading, needsOnboarding }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
