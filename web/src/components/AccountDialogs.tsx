// web/src/components/AccountDialogs.tsx
import { useState, type FormEvent } from "react";
import {
  Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, Button, Stack, Alert,
} from "@mui/material";
import { auth } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

const MIN_PASSWORD = 6;   // 与后端 _MIN_PASSWORD 对齐

type Props = { open: boolean; onClose: () => void };

/** 改姓名。姓名同时是「忘记密码」的核身凭据，所以不允许留空（后端也会再挡一次）。 */
export function ChangeNameDialog({ open, onClose }: Props) {
  const { user, refreshUser } = useAuth();
  const [name, setName] = useState(user?.full_name || "");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError("请填写姓名");
      return;
    }
    setBusy(true);
    try {
      // 顶栏显示的就是姓名，改完必须回写本地 user，否则要等重新登录才更新
      refreshUser(await auth.updateName(name.trim()));
      onClose();
    } catch (err: any) {
      setError(err?.message || "修改姓名失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <form onSubmit={onSubmit}>
        <DialogTitle>修改姓名</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="姓名" value={name} autoFocus fullWidth
              helperText="姓名也是「忘记密码」时的核身凭据"
              onChange={(e) => setName(e.target.value)} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="contained" disabled={busy}>保存</Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}

/** 改密码。后端要核对当前密码，且不返回新 token——这里改完也不动登录态。 */
export function ChangePasswordDialog({ open, onClose }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    // 本地能判的先判掉，省一趟往返；当前密码对不对只有后端知道
    if (!current || !next) {
      setError("请填写完整");
      return;
    }
    if (next.length < MIN_PASSWORD) {
      setError(`密码至少 ${MIN_PASSWORD} 位`);
      return;
    }
    if (next !== confirm) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await auth.changePassword(current, next);
      onClose();
    } catch (err: any) {
      setError(err?.message || "修改密码失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} fullWidth maxWidth="xs">
      <form onSubmit={onSubmit}>
        <DialogTitle>修改密码</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            {error && <Alert severity="error">{error}</Alert>}
            <TextField label="当前密码" type="password" value={current} autoFocus fullWidth
              autoComplete="current-password"
              onChange={(e) => setCurrent(e.target.value)} />
            <TextField label="新密码" type="password" value={next} fullWidth
              autoComplete="new-password"
              helperText={`至少 ${MIN_PASSWORD} 位`}
              onChange={(e) => setNext(e.target.value)} />
            <TextField label="确认新密码" type="password" value={confirm} fullWidth
              autoComplete="new-password"
              onChange={(e) => setConfirm(e.target.value)} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={onClose}>取消</Button>
          <Button type="submit" variant="contained" disabled={busy}>保存</Button>
        </DialogActions>
      </form>
    </Dialog>
  );
}
