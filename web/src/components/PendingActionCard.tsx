import { useEffect, useState } from "react";
import { Alert, Box, Button, Chip, CircularProgress, Stack, Typography } from "@mui/material";
import WarningAmberIcon from "@mui/icons-material/WarningAmber";
import { api, type PendingAction } from "../api/client";

const KIND_LABEL: Record<string, string> = {
  delete_questions: "从题库删除题目",
  delete_wrong_answers: "从错题集删除错题",
};

/**
 * 破坏性操作的确认卡片。
 *
 * AI 不直接执行删除，只登记一条待确认操作；这里把它渲染成「确认 / 取消」两个按钮，
 * 点确认后由服务端执行真正的删除。
 *
 * 状态以**服务端为准**：挂载时拉一次当前状态，故刷新后已确认/已取消/已过期的卡片
 * 不会又变回可点——否则用户会以为自己没点、再点一次（服务端虽有一次性保护会返回
 * 409，但让用户看见一个假的可点按钮本身就是缺陷）。
 */
export function PendingActionCard({ id, onDone }: { id: string; onDone?: () => void }) {
  const [action, setAction] = useState<PendingAction | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [gone, setGone] = useState(false);   // 记录已不存在（被清理），与"加载中"区分

  useEffect(() => {
    let alive = true;
    api.pendingActions.get(id)
      .then((a) => { if (alive) setAction(a); })
      .catch(() => { if (alive) setGone(true); });
    return () => { alive = false; };
  }, [id]);

  async function decide(confirm: boolean) {
    setBusy(true); setErr("");
    try {
      if (confirm) await api.pendingActions.confirm(id);
      else await api.pendingActions.reject(id);
      setAction((a) => (a ? { ...a, status: confirm ? "confirmed" : "rejected" } : a));
      onDone?.();
    } catch (e: any) {
      setErr(e?.message || "操作失败");
      // 失败多半是状态已变（他处已确认/已过期）：重新拉一次，让按钮反映真实状态
      api.pendingActions.get(id).then(setAction).catch(() => setGone(true));
    } finally {
      setBusy(false);
    }
  }

  if (gone || (!action && !err)) return null;   // 不存在或尚在加载：不占位、不闪
  if (!action) return null;

  const decided = action.status !== "pending";
  const title = KIND_LABEL[action.kind] || "危险操作";

  return (
    <Box sx={{ mt: 1, p: 1.5, borderRadius: 1, border: 1, borderColor: "warning.main",
               bgcolor: (t) => t.palette.mode === "dark"
                 ? "rgba(255,167,38,0.08)" : "rgba(255,167,38,0.06)" }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center", mb: 0.5 }}>
        <WarningAmberIcon fontSize="small" color="warning" />
        <Typography variant="subtitle2">{title}（{action.count} 项）</Typography>
        {action.status === "confirmed" && <Chip size="small" color="error" label="已删除" />}
        {action.status === "rejected" && <Chip size="small" label="已取消" />}
        {action.status === "expired" && <Chip size="small" label="已过期" />}
      </Stack>

      <Box component="ul" sx={{ m: 0, pl: 2.5, mb: decided ? 0 : 1 }}>
        {action.labels.slice(0, 8).map((s, i) => (
          <Typography key={i} component="li" variant="body2" color="text.secondary">
            {s || "（无题干）"}
          </Typography>
        ))}
        {action.labels.length > 8 && (
          <Typography component="li" variant="body2" color="text.secondary">
            …另有 {action.labels.length - 8} 项
          </Typography>
        )}
      </Box>

      {!decided && (
        <>
          <Typography variant="caption" color="text.secondary">
            AI 已列出待删内容，尚未执行。确认后立即删除，不可恢复。
          </Typography>
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <Button size="small" variant="contained" color="error"
              disabled={busy} onClick={() => decide(true)}
              startIcon={busy ? <CircularProgress size={14} color="inherit" /> : undefined}>
              确认删除
            </Button>
            <Button size="small" variant="outlined" disabled={busy}
              onClick={() => decide(false)}>取消</Button>
          </Stack>
        </>
      )}
      {err && <Alert severity="error" sx={{ mt: 1 }}>{err}</Alert>}
    </Box>
  );
}
