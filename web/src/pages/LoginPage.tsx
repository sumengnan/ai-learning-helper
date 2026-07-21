// web/src/pages/LoginPage.tsx
import { useState, type FormEvent, type ReactNode } from "react";
import { Link as RouterLink, useNavigate } from "react-router-dom";
import {
  Box, Paper, TextField, Button, Typography, Alert, Link, Stack,
} from "@mui/material";
import AutoAwesomeRoundedIcon from "@mui/icons-material/AutoAwesomeRounded";
import SchoolRoundedIcon from "@mui/icons-material/SchoolRounded";
import QuizRoundedIcon from "@mui/icons-material/QuizRounded";
import MenuBookRoundedIcon from "@mui/icons-material/MenuBookRounded";
import InsightsRoundedIcon from "@mui/icons-material/InsightsRounded";
import { motion } from "framer-motion";
import { useAuth } from "../auth/AuthProvider";
import { Captcha } from "../components/Captcha";
import { BeianFooter } from "../components/BeianFooter";

const MotionPaper = motion(Paper);

export function LoginPage() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [captchaToken, setCaptchaToken] = useState("");
  const [captchaInput, setCaptchaInput] = useState("");
  const [captchaNonce, setCaptchaNonce] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refreshCaptcha = () => { setCaptchaInput(""); setCaptchaNonce((n) => n + 1); };

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!username.trim() || !password) {
      setError("请输入账号和密码");
      return;
    }
    if (!captchaInput.trim()) {
      setError("请输入验证码");
      return;
    }
    setBusy(true);
    try {
      await login(username.trim(), password, captchaToken, captchaInput.trim());
      navigate("/", { replace: true });
    } catch (err: any) {
      setError(err?.message || "登录失败");
      refreshCaptcha();
    } finally {
      setBusy(false);
    }
  }

  return (
    <AuthLayout title="登录" subtitle="欢迎回来，继续你的学习旅程">
      <form onSubmit={onSubmit}>
        <Stack spacing={2}>
          {error && <Alert severity="error">{error}</Alert>}
          <TextField label="账号" value={username} autoFocus
            onChange={(e) => setUsername(e.target.value)} />
          <TextField label="密码" type="password" value={password}
            onChange={(e) => setPassword(e.target.value)} />
          <Stack direction="row" spacing={1.5} sx={{ alignItems: "flex-start" }}>
            <TextField label="验证码" value={captchaInput} fullWidth
              autoComplete="off"
              slotProps={{ htmlInput: { maxLength: 6 } }}
              onChange={(e) => setCaptchaInput(e.target.value)} />
            <Captcha key={captchaNonce} onToken={setCaptchaToken} />
          </Stack>
          <Button type="submit" variant="contained" size="large" disabled={busy}>
            登录
          </Button>
          <Typography variant="body2" color="text.secondary">
            还没有账号？<Link component={RouterLink} to="/register">去注册</Link>
            {" · "}<Link component={RouterLink} to="/forgot">忘记密码</Link>
          </Typography>
        </Stack>
      </form>
    </AuthLayout>
  );
}

const FEATURES = [
  { icon: <SchoolRoundedIcon />, title: "智能答疑", desc: "AI 逐步拆解，讲透每一个知识点" },
  { icon: <QuizRoundedIcon />, title: "自动出题", desc: "按主题生成练习与模拟考试" },
  { icon: <MenuBookRoundedIcon />, title: "个人知识库", desc: "上传资料，随问随查有据可依" },
  { icon: <InsightsRoundedIcon />, title: "错题与统计", desc: "沉淀错题，看清学习进度" },
];

export function AuthLayout({ title, subtitle, children }:
  { title: string; subtitle: string; children: ReactNode }) {
  return (
    <Box sx={{
      minHeight: "100vh", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", p: { xs: 2, sm: 3 },
      background: (t) => t.palette.mode === "dark"
        ? "radial-gradient(1200px 600px at 15% 0%, #1e1b4b 0%, transparent 55%), radial-gradient(1000px 600px at 100% 100%, #312e81 0%, transparent 50%), #0f1016"
        : "radial-gradient(1200px 600px at 15% 0%, #eef2ff 0%, transparent 55%), radial-gradient(1000px 600px at 100% 100%, #faf5ff 0%, transparent 50%), #f6f7f9",
    }}>
      <MotionPaper
        elevation={8}
        initial={{ opacity: 0, y: 18, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.36, ease: [0.22, 1, 0.36, 1] }}
        sx={{
          display: "flex", width: "100%", maxWidth: 880, minHeight: 520,
          borderRadius: 4, overflow: "hidden",
        }}
      >
        <BrandPanel />
        <Box sx={{
          flex: 1, display: "flex", flexDirection: "column", justifyContent: "center",
          p: { xs: 3, sm: 5 }, minWidth: 0,
        }}>
          <Box sx={{ maxWidth: 360, width: "100%", mx: "auto" }}>
            <Typography variant="h5" sx={{ fontWeight: 700, mb: 0.5 }}>{title}</Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>{subtitle}</Typography>
            {children}
          </Box>
        </Box>
      </MotionPaper>
      <Box sx={{ mt: 1 }}><BeianFooter /></Box>
    </Box>
  );
}

function BrandPanel() {
  return (
    <Box sx={{
      display: { xs: "none", md: "flex" }, position: "relative", overflow: "hidden",
      flex: "0 0 44%", flexDirection: "column", justifyContent: "space-between",
      p: 5, color: "#fff",
      background: "linear-gradient(150deg, #818cf8 0%, #a78bfa 55%, #c084fc 100%)",
    }}>
      {/* 装饰光斑 */}
      <Box sx={{
        position: "absolute", top: -80, right: -60, width: 240, height: 240,
        borderRadius: "50%", bgcolor: "rgba(255,255,255,0.14)", filter: "blur(6px)",
      }} />
      <Box sx={{
        position: "absolute", bottom: -70, left: -50, width: 200, height: 200,
        borderRadius: "50%", bgcolor: "rgba(255,255,255,0.10)", filter: "blur(4px)",
      }} />

      <Stack direction="row" spacing={1.2} sx={{ position: "relative", alignItems: "center" }}>
        <Box sx={{
          width: 40, height: 40, borderRadius: 2, display: "grid", placeItems: "center",
          bgcolor: "rgba(255,255,255,0.18)", backdropFilter: "blur(4px)",
        }}>
          <AutoAwesomeRoundedIcon fontSize="small" />
        </Box>
        <Typography variant="h6" sx={{ fontWeight: 700, letterSpacing: 0.3 }}>
          AI 学习助手
        </Typography>
      </Stack>

      <Box sx={{ position: "relative" }}>
        <Typography variant="h4" sx={{ fontWeight: 800, lineHeight: 1.3, mb: 1 }}>
          让学习<br />更高效、更有据
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.85, maxWidth: 260 }}>
          答疑、出题、知识库与错题本，一站式陪你把每个知识点学透。
        </Typography>
      </Box>

      <Stack spacing={1.8} sx={{ position: "relative" }}>
        {FEATURES.map((f) => (
          <Stack key={f.title} direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
            <Box sx={{
              width: 36, height: 36, borderRadius: "50%", flex: "0 0 auto",
              display: "grid", placeItems: "center", bgcolor: "rgba(255,255,255,0.16)",
            }}>
              {f.icon}
            </Box>
            <Box sx={{ minWidth: 0 }}>
              <Typography variant="subtitle2" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
                {f.title}
              </Typography>
              <Typography variant="caption" sx={{ opacity: 0.8 }}>{f.desc}</Typography>
            </Box>
          </Stack>
        ))}
      </Stack>
    </Box>
  );
}
