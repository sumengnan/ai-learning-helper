import { createContext, useContext, useEffect, useState } from "react";
import {
  Dialog, Box, Typography, IconButton, TextField, Chip, Stack, Button,
  CircularProgress, Alert, Snackbar, Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import TuneIcon from "@mui/icons-material/Tune";
import { profileApi, EMPTY_PROFILE, type Profile } from "../api/profile";

// 讲解偏好（多选）与语气（单选）的预设项——存的就是这些可读文案，后端直接拼进提示
const EXPLAIN_PREFS = ["多用类比", "步骤拆细", "多给代码示例", "少代码重概念", "给要点总结", "配套练习题"];
const TONES = ["鼓励式", "严格教练", "简洁直接", "耐心细致"];

// ---- 全局入口：一次挂载，多处 open() ----
type Ctx = { open: () => void; savedTick: number };
const ProfileDrawerContext = createContext<Ctx>({ open: () => {}, savedTick: 0 });
export const useProfileDrawer = () => useContext(ProfileDrawerContext);

/** 在 AppShell 里包住内容：挂一次抽屉，向下提供 open()；保存后 savedTick 自增供页面刷新状态 */
export function ProfileDrawerProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [savedTick, setSavedTick] = useState(0);
  return (
    <ProfileDrawerContext.Provider value={{ open: () => setOpen(true), savedTick }}>
      {children}
      <ProfileDrawer open={open} onClose={() => setOpen(false)}
        onSaved={() => setSavedTick((t) => t + 1)} />
    </ProfileDrawerContext.Provider>
  );
}

function ProfileDrawer({ open, onClose, onSaved }: {
  open: boolean; onClose: () => void; onSaved: () => void;
}) {
  const [form, setForm] = useState<Profile>(EMPTY_PROFILE);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true); setError(null);
    profileApi.get()
      .then((p) => { if (alive) setForm(p); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [open]);

  const togglePref = (p: string) =>
    setForm((f) => ({
      ...f,
      explain_prefs: f.explain_prefs.includes(p)
        ? f.explain_prefs.filter((x) => x !== p)
        : [...f.explain_prefs, p],
    }));
  const pickTone = (t: string) =>
    setForm((f) => ({ ...f, tone: f.tone === t ? "" : t }));   // 再点一次取消

  async function save() {
    setSaving(true); setError(null);
    try {
      const saved = await profileApi.save(form);
      setForm(saved);
      onSaved();
      setToast("已保存，之后的对话与考试讲评都会参考你的偏好");
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth
        slotProps={{ paper: { sx: { maxHeight: "85vh" } } }}>
        <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxHeight: "85vh" }}>
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
            <TuneIcon color="primary" />
            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>AI 个性化</Typography>
              <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>
                告诉 AI 你是谁、想怎么学——一次设定，长期生效
              </Typography>
            </Box>
            <IconButton size="small" onClick={onClose} aria-label="关闭"><CloseIcon /></IconButton>
          </Stack>
          <Divider />

          {loading ? (
            <Box sx={{ display: "flex", justifyContent: "center", py: 8 }}><CircularProgress /></Box>
          ) : (
            <Box sx={{ overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 2.5,
              pt: 0.5 }}>
              {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

            <TextField label="身份 / 水平" fullWidth size="small" value={form.identity}
              onChange={(e) => setForm((f) => ({ ...f, identity: e.target.value }))}
              placeholder="如：职场转行，AI 完全零基础" slotProps={{ htmlInput: { maxLength: 200 } }} />

            <TextField label="学习目标" fullWidth size="small" value={form.goal}
              onChange={(e) => setForm((f) => ({ ...f, goal: e.target.value }))}
              placeholder="如：想做 AI 产品经理，能看懂技术方案" slotProps={{ htmlInput: { maxLength: 200 } }} />

            <Box>
              <Typography sx={{ fontSize: 13.5, fontWeight: 600, mb: 1 }}>讲解偏好（可多选）</Typography>
              <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
                {EXPLAIN_PREFS.map((p) => (
                  <Chip key={p} label={p} clickable size="small"
                    color={form.explain_prefs.includes(p) ? "primary" : "default"}
                    variant={form.explain_prefs.includes(p) ? "filled" : "outlined"}
                    onClick={() => togglePref(p)} />
                ))}
              </Stack>
            </Box>

            <Box>
              <Typography sx={{ fontSize: 13.5, fontWeight: 600, mb: 1 }}>语气（单选）</Typography>
              <Stack direction="row" sx={{ flexWrap: "wrap", gap: 1 }}>
                {TONES.map((t) => (
                  <Chip key={t} label={t} clickable size="small"
                    color={form.tone === t ? "primary" : "default"}
                    variant={form.tone === t ? "filled" : "outlined"}
                    onClick={() => pickTone(t)} />
                ))}
              </Stack>
            </Box>

            <TextField label="其他要求" fullWidth multiline minRows={2} maxRows={5} size="small"
              value={form.notes}
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
              placeholder="如：术语第一次出现时给英文原词" slotProps={{ htmlInput: { maxLength: 1000 } }} />

            <Divider />
            <Button variant="contained" disableElevation onClick={save} disabled={saving}
              startIcon={saving ? <CircularProgress size={16} color="inherit" /> : undefined}>
              {saving ? "保存中…" : "保存"}
            </Button>
            </Box>
          )}
        </Box>
      </Dialog>

      <Snackbar open={Boolean(toast)} autoHideDuration={3000} onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "center" }}>
        <Alert severity="success" variant="filled" onClose={() => setToast(null)}>{toast}</Alert>
      </Snackbar>
    </>
  );
}
