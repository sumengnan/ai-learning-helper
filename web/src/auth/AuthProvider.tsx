// web/src/auth/AuthProvider.tsx
import {
  createContext, useContext, useEffect, useState, type ReactNode,
} from "react";
import type { User } from "../types";
import { auth as authApi, getToken, setToken, setUnauthorizedHandler } from "../api/client";

const USER_KEY = "auth_user";

type AuthState = {
  user: User | null;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => void;
};

const AuthContext = createContext<AuthState>({
  user: null,
  login: async () => {},
  register: async () => {},
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
    login: async (username, password) => {
      const { token, user: u } = await authApi.login(username, password);
      persist(token, u);
    },
    register: async (username, password) => {
      const { token, user: u } = await authApi.register(username, password);
      persist(token, u);
    },
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
