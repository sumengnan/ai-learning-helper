// web/src/components/VersionBadge.tsx
import { useEffect, useState } from "react";
import { Box, Divider, Tooltip, Typography } from "@mui/material";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import SyncProblemIcon from "@mui/icons-material/SyncProblem";
import HelpOutlineIcon from "@mui/icons-material/HelpOutlineOutlined";
import { fetchVersion, type VersionInfo } from "../api/client";

const short = (s: string) => (s && s !== "dev" ? s.slice(0, 7) : s || "dev");

// 前端版本：构建期由 vite define 注入（未走 CI 时为 "dev"）
const FE_SHA = __GIT_SHA__;
const FE_BUILT = __BUILD_TIME__;

type State = "loading" | "ok" | "error";

export function VersionBadge({ open }: { open: boolean }) {
  const [backend, setBackend] = useState<VersionInfo | null>(null);
  const [state, setState] = useState<State>("loading");

  useEffect(() => {
    let alive = true;
    fetchVersion()
      .then((v) => alive && (setBackend(v), setState("ok")))
      .catch(() => alive && setState("error"));
    return () => {
      alive = false;
    };
  }, []);

  const fe = short(FE_SHA);
  const be = backend ? short(backend.git_sha) : state === "error" ? "?" : "…";
  // 都取到真实 sha 且相等 => 前后端来自同一次部署
  const matched = state === "ok" && backend != null
    && FE_SHA !== "dev" && backend.git_sha === FE_SHA;

  const { icon, color } =
    state === "loading" ? { icon: <HelpOutlineIcon fontSize="inherit" />, color: "text.disabled" }
    : matched ? { icon: <CheckCircleIcon fontSize="inherit" />, color: "success.main" }
    : { icon: <SyncProblemIcon fontSize="inherit" />, color: "warning.main" };

  const tip = (
    <Box sx={{ lineHeight: 1.7 }}>
      <div>前端 {fe}</div>
      <div>后端 {be}{backend?.version ? ` (v${backend.version})` : ""}</div>
      {FE_BUILT && <div>前端构建 {FE_BUILT}</div>}
      {backend?.built_at && <div>后端构建 {backend.built_at}</div>}
      <div style={{ marginTop: 4 }}>
        {state === "error" ? "后端未响应 /api/version"
          : matched ? "✓ 前后端版本一致" : "⚠ 前后端版本不一致（可能有一端未更新/缓存）"}
      </div>
    </Box>
  );

  return (
    <Box sx={{ mt: "auto" }}>
      <Divider />
      <Tooltip title={tip} placement="right">
        <Box sx={{
          display: "flex", alignItems: "center", gap: 1,
          px: open ? 2 : 0, py: 1,
          justifyContent: open ? "flex-start" : "center",
          color: "text.secondary", fontSize: 18,
        }}>
          <Box component="span" sx={{ color, display: "flex", fontSize: 18 }}>{icon}</Box>
          {open && (
            <Typography variant="caption" noWrap sx={{ fontFamily: "monospace" }}>
              前端 {fe} · 后端 {be}
            </Typography>
          )}
        </Box>
      </Tooltip>
    </Box>
  );
}
