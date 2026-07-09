// web/src/components/AppShell.tsx
import type { ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Toolbar, Typography, IconButton, Tooltip, Divider,
} from "@mui/material";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutlined";
import MenuBookIcon from "@mui/icons-material/MenuBook";
import QuizIcon from "@mui/icons-material/Quiz";
import AssignmentIcon from "@mui/icons-material/Assignment";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import Brightness4Icon from "@mui/icons-material/Brightness4";
import Brightness7Icon from "@mui/icons-material/Brightness7";
import { useColorMode } from "../ThemeModeProvider";

const WIDTH = 220;

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/", label: "聊天", icon: <ChatBubbleOutlineIcon /> },
  { to: "/knowledge", label: "知识库", icon: <MenuBookIcon /> },
  { to: "/questions", label: "题库", icon: <QuizIcon /> },
  { to: "/exam", label: "考试", icon: <AssignmentIcon /> },
  { to: "/wrong", label: "错题集", icon: <ErrorOutlineIcon /> },
  { to: "/downloads", label: "下载", icon: <DownloadIcon /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useColorMode();
  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <Drawer
        variant="permanent"
        sx={{
          width: WIDTH,
          flexShrink: 0,
          "& .MuiDrawer-paper": { width: WIDTH, boxSizing: "border-box" },
        }}
      >
        <Toolbar sx={{ px: 2 }}>
          <Typography variant="h6" noWrap sx={{ fontWeight: 700 }}>
            AI 学习助手
          </Typography>
        </Toolbar>
        <Divider />
        <List sx={{ flex: 1 }}>
          {NAV.map((n) => (
            <ListItemButton
              key={n.to}
              selected={isActive(n.to)}
              onClick={() => navigate(n.to)}
            >
              <ListItemIcon sx={{ minWidth: 40 }}>{n.icon}</ListItemIcon>
              <ListItemText primary={n.label} />
            </ListItemButton>
          ))}
        </List>
        <Divider />
        <Box sx={{ p: 1, display: "flex", justifyContent: "flex-end" }}>
          <Tooltip title={mode === "light" ? "切换到暗色" : "切换到亮色"}>
            <IconButton onClick={toggleMode} aria-label="切换明暗主题">
              {mode === "light" ? <Brightness4Icon /> : <Brightness7Icon />}
            </IconButton>
          </Tooltip>
        </Box>
      </Drawer>
      <Box component="main" sx={{ flex: 1, minWidth: 0, height: "100%", overflow: "auto" }}>
        {children}
      </Box>
    </Box>
  );
}
