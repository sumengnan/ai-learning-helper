// web/src/pages/LoginPage.tsx
import { useState, type FormEvent } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Box, Paper, TextField, Button, Typography, Alert, Link, Stack,
} from "@mui/material";
import { useAuth } from "../auth/AuthProvider";

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password);
      navigate("/", { replace: true });
    } catch (err: any) {
      setError(err?.message || "登录失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="登录" subtitle="AI 学习助手">
      <form onSubmit={onSubmit}>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="账号" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <TextField label="密码" type="password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
          <Button type="submit" variant="contained" size="large" disabled={busy}>
            登录
          </Button>
          <Typography variant="body2" color="text.secondary">
            还没有账号？<Link component={RouterLink} to="/register">去注册</Link>
          </Typography>
        </Stack>
      </form>
    </AuthLayout>
  );
}

export function AuthLayout({ title, subtitle, children }:
  { title: string; subtitle: string; children: React.ReactNode }) {
  return (
    <Box sx={{
      minHeight: "100vh", display: "flex", alignItems: "center",
      justifyContent: "center", bgcolor: "background.default", p: 2,
    }}>
      <Paper elevation={3} sx={{ p: 4, width: "100%", maxWidth: 380, borderRadius: 3 }}>
        <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>{title}</Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>{subtitle}</Typography>
        {children}
      </Paper>
    </Box>
  );
}
