// web/src/pages/ForgotPasswordPage.tsx
import { useState, type FormEvent } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  TextField, Button, Typography, Alert, Link, Stack,
} from "@mui/material";
import { auth as authApi } from "../api/client";
import { AuthLayout } from "./LoginPage";
import { Captcha } from "../components/Captcha";

// 本站没有绑定邮箱/手机，找回密码靠注册时填的姓名核身（见 app/api/auth.py 的 reset-password）。
// 重置成功后刻意不自动登录：让用户用新密码走一遍登录，确认它真的记住了。
export function ForgotPasswordPage() {
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaInput, setCaptchaInput] = useState("");
  const [captchaNonce, setCaptchaNonce] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  const refreshCaptcha = () => { setCaptchaInput(""); setCaptchaNonce((n) => n + 1); };

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim()) { setError("请输入账号"); return; }
    if (!fullName.trim()) { setError("请输入姓名"); return; }
    if (password.length < 6) { setError("新密码至少 6 位"); return; }
    if (password !== confirm) { setError("两次输入的密码不一致"); return; }
    if (!captchaInput.trim()) { setError("请输入验证码"); return; }
    setBusy(true);
    try {
      await authApi.resetPassword(
        username.trim(), fullName.trim(), password, captchaToken, captchaInput.trim());
      setDone(true);
    } catch (err: any) {
      setError(err?.message || "重置失败");
      refreshCaptcha();
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <AuthLayout title="密码已重置" subtitle="请用新密码登录">
        <Stack spacing={2}>
          <Alert severity="success">密码已更新，请使用新密码登录。</Alert>
          <Button variant="contained" size="large" onClick={() => navigate("/login")}>
            去登录
          </Button>
        </Stack>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="忘记密码" subtitle="用注册时填写的姓名重置密码">
      <form onSubmit={onSubmit}>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="账号" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <TextField label="姓名" value={fullName}
            slotProps={{ htmlInput: { maxLength: 64 } }}
            helperText="注册时填写的姓名"
            onChange={(e) => setFullName(e.target.value)} />
          <TextField label="新密码" type="password" value={password}
            helperText="至少 6 位"
            onChange={(e) => setPassword(e.target.value)} />
          <TextField label="确认新密码" type="password" value={confirm}
            error={confirm.length > 0 && confirm !== password}
            helperText={confirm.length > 0 && confirm !== password ? "两次密码不一致" : " "}
            onChange={(e) => setConfirm(e.target.value)} />
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-start" }}>
            <TextField label="验证码" value={captchaInput} fullWidth
              autoComplete="off"
              slotProps={{ htmlInput: { maxLength: 6 } }}
              onChange={(e) => setCaptchaInput(e.target.value)} />
            <Captcha key={captchaNonce} onToken={setCaptchaToken} />
          </Stack>
          <Button type="submit" variant="contained" size="large" disabled={busy}>
            重置密码
          </Button>
          <Typography variant="body2" color="text.secondary">
            想起来了？<Link component={RouterLink} to="/login">去登录</Link>
          </Typography>
        </Stack>
      </form>
    </AuthLayout>
  );
}
