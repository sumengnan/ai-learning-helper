import { authFetch } from "./client";

export interface Profile {
  identity: string;
  goal: string;
  explain_prefs: string[];
  tone: string;
  notes: string;
}

export const EMPTY_PROFILE: Profile = {
  identity: "", goal: "", explain_prefs: [], tone: "", notes: "",
};

// 有任一字段非空即视为「已设置」
export function isProfileSet(p: Profile): boolean {
  return Boolean(p.identity || p.goal || p.tone || p.notes || p.explain_prefs.length);
}

export const profileApi = {
  get: (): Promise<Profile> =>
    authFetch("/api/profile").then((r) => {
      if (!r.ok) throw new Error(`加载失败：${r.status}`);
      return r.json();
    }),
  save: (p: Profile): Promise<Profile> =>
    authFetch("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(p),
    }).then((r) => {
      if (!r.ok) throw new Error(`保存失败：${r.status}`);
      return r.json();
    }),
};
