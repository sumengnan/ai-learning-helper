// web/src/pages/RegisterPage.tsx
import { useState, type FormEvent } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  TextField, Button, Typography, Alert, Link, Stack,
} from "@mui/material";
import { useAuth } from "../auth/AuthProvider";
import { AuthLayout } from "./LoginPage";
import { Captcha } from "../components/Captcha";

export function RegisterPage() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaInput, setCaptchaInput] = useState("");
  const [captchaNonce, setCaptchaNonce] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshCaptcha = () => { setCaptchaInput(""); setCaptchaNonce((n) => n + 1); };

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim()) { setError("请输入账号"); return; }
    if (password.length < 6) { setError("密码至少 6 位"); return; }
    if (password !== confirm) { setError("两次输入的密码不一致"); return; }
    if (!captchaInput.trim()) { setError("请输入验证码"); return; }
    setBusy(true);
    try {
      await register(username.trim(), password, captchaToken, captchaInput.trim());
      navigate("/", { replace: true });
    } catch (err: any) {
      setError(err?.message || "注册失败");
      refreshCaptcha();
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="注册" subtitle="创建你的 AI 学习助手账号">
      <form onSubmit={onSubmit}>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="账号" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <TextField label="密码" type="password" value={password}
            helperText="至少 6 位"
            onChange={(e) => setPassword(e.target.value)} />
          <TextField label="确认密码" type="password" value={confirm}
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
            注册
          </Button>
          <Typography variant="body2" color="text.secondary">
            已有账号？<Link component={RouterLink} to="/login">去登录</Link>
          </Typography>
        </Stack>
      </form>
    </AuthLayout>
  );
}
