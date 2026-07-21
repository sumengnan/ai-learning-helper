// web/src/auth/AuthProvider.tsx
import {
  createContext, useContext, useEffect, useState, type ReactNode,
} from "react";
import type { User } from "../types";
import { auth as authApi, getToken, setToken, setUnauthorizedHandler } from "../api/client";

const USER_KEY = "auth_user";

type AuthState = {
  user: User | null;
  login: (username: string, password: string,
          captchaToken?: string, captchaText?: string) => Promise<void>;
  register: (username: string, password: string, fullName: string,
             captchaToken?: string, captchaText?: string) => Promise<void>;
  /** 用后端返回的最新 user 覆盖本地缓存（改姓名后刷新顶栏显示） */
  refreshUser: (u: User) => void;
  logout: () => void;
};

const AuthContext = createContext<AuthState>({
  user: null,
  login: async () => {},
  register: async () => {},
  refreshUser: () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

function loadUser(): User | null {
  if (!getToken()) return null;
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? (JSON.parse(raw) as User) : null;
  } catch {
    return null;
  }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(loadUser);

  const persist = (token: string, u: User) => {
    setToken(token);
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setUser(u);
  };

  const logout = () => {
    setToken(null);
    localStorage.removeItem(USER_KEY);
    setUser(null);
  };

  // 任意请求返回 401 时（token 失效）自动登出
  useEffect(() => {
    setUnauthorizedHandler(() => {
      localStorage.removeItem(USER_KEY);
      setUser(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const value: AuthState = {
    user,
    login: async (username, password, captchaToken, captchaText) => {
      const { token, user: u } = await authApi.login(username, password, captchaToken, captchaText);
      persist(token, u);
    },
    register: async (username, password, fullName, captchaToken, captchaText) => {
      const { token, user: u } = await authApi.register(
        username, password, fullName, captchaToken, captchaText);
      persist(token, u);
    },
    refreshUser: (u) => {
      localStorage.setItem(USER_KEY, JSON.stringify(u));
      setUser(u);
    },
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
