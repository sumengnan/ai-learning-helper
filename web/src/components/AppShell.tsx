// web/src/components/AppShell.tsx
import { useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Toolbar, Typography, IconButton, Tooltip, Divider, AppBar,
  Button, Menu, MenuItem, Avatar,
} from "@mui/material";
import type { Theme } from "@mui/material/styles";
import { AnimatePresence, motion } from "framer-motion";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutlined";
import SpaceDashboardIcon from "@mui/icons-material/SpaceDashboard";
import MenuBookIcon from "@mui/icons-material/MenuBook";
import QuizIcon from "@mui/icons-material/Quiz";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import Brightness4Icon from "@mui/icons-material/Brightness4";
import Brightness7Icon from "@mui/icons-material/Brightness7";
import LogoutIcon from "@mui/icons-material/Logout";
import TuneIcon from "@mui/icons-material/Tune";
import MenuOpenIcon from "@mui/icons-material/MenuOpen";
import FaceRetouchingNaturalIcon from "@mui/icons-material/FaceRetouchingNatural";
import { useColorMode } from "../ThemeModeProvider";
import { useAuth } from "../auth/AuthProvider";
import { useProfileDrawer } from "../pages/ProfileDrawer";
import { VersionBadge } from "./VersionBadge";

const WIDTH = 220;
const MINI = 68;

// 菜单/历史等“外壳”统一的浅色背景，与白色内容区拉开层次
export const chromeBg = (t: Theme) => (t.palette.mode === "light" ? "#eceef2" : "#181a1f");

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/", label: "概览", icon: <SpaceDashboardIcon /> },
  { to: "/chat", label: "AI聊天", icon: <ChatBubbleOutlineIcon /> },
  { to: "/knowledge", label: "知识库", icon: <MenuBookIcon /> },
  { to: "/questions", label: "题库", icon: <QuizIcon /> },
  { to: "/wrong", label: "错题集", icon: <ErrorOutlineIcon /> },
  { to: "/downloads", label: "下载", icon: <DownloadIcon /> },
];

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useColorMode();
  const { user, logout } = useAuth();
  const { open: openProfile } = useProfileDrawer();
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);
  const [navOpen, setNavOpen] = useState(true);
  const width = navOpen ? WIDTH : MINI;

  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);
  const onLogout = () => {
    setMenuAnchor(null);
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <Box sx={{ display: "flex", height: "100%" }}>
      <Drawer
        variant="permanent"
        sx={{
          width,
          flexShrink: 0,
          whiteSpace: "nowrap",
          // 根节点也要动画 width：它占据布局宽度，主内容区（含折叠图标）跟着一点点移，
          // 否则根宽度瞬间跳变、只有抽屉纸面在滑动，图标会「蹦过去」。
          transition: (t) => t.transitions.create("width", {
            easing: t.transitions.easing.sharp,
            duration: t.transitions.duration.standard,
          }),
          "& .MuiDrawer-paper": {
            width,
            boxSizing: "border-box",
            overflowX: "hidden",
            bgcolor: chromeBg,
            transition: (t) => t.transitions.create("width", {
              easing: t.transitions.easing.sharp,
              duration: t.transitions.duration.standard,
            }),
          },
        }}
      >
        <Toolbar sx={{ px: 2, overflow: "hidden", gap: 1,
          justifyContent: navOpen ? "flex-start" : "center" }}>
          <FaceRetouchingNaturalIcon sx={{ color: "primary.main", fontSize: 28, flexShrink: 0 }} />
          <AnimatePresence initial={false}>
            {navOpen && (
              <motion.div
                initial={{ opacity: 0, width: 0 }}
                animate={{ opacity: 1, width: "auto" }}
                exit={{ opacity: 0, width: 0 }}
                transition={{ duration: 0.2 }}
                style={{ overflow: "hidden" }}
              >
                <Typography variant="h6" noWrap sx={{ fontWeight: 700 }}>
                  AI 学习助手
                </Typography>
              </motion.div>
            )}
          </AnimatePresence>
        </Toolbar>
        <Divider />
        <List sx={{ flex: 1, px: navOpen ? 1 : 0.5 }}>
          {NAV.map((n) => (
            <Tooltip key={n.to} title={navOpen ? "" : n.label} placement="right">
              <ListItemButton
                selected={isActive(n.to)}
                onClick={() => navigate(n.to)}
                sx={{
                  borderRadius: 1.5, mb: 0.5,
                  justifyContent: navOpen ? "initial" : "center",
                  px: navOpen ? 2 : 1.5,
                  "&.Mui-selected": {
                    "& .MuiListItemIcon-root": { color: "primary.main" },
                    "& .MuiListItemText-primary": { fontWeight: 600, color: "primary.main" },
                  },
                }}
              >
                <ListItemIcon sx={{ minWidth: 0, mr: navOpen ? 2 : 0, justifyContent: "center" }}>
                  {n.icon}
                </ListItemIcon>
                <AnimatePresence initial={false}>
                  {navOpen && (
                    <motion.div
                      initial={{ opacity: 0, width: 0 }}
                      animate={{ opacity: 1, width: "auto" }}
                      exit={{ opacity: 0, width: 0 }}
                      transition={{ duration: 0.2 }}
                      style={{ overflow: "hidden" }}
                    >
                      <ListItemText primary={n.label} sx={{ m: 0, whiteSpace: "nowrap" }} />
                    </motion.div>
                  )}
                </AnimatePresence>
              </ListItemButton>
            </Tooltip>
          ))}
        </List>
        <Divider />
        {/* 折叠/展开开关：仅一个方向图标，随状态旋转，无文字 */}
        <List sx={{ px: navOpen ? 1 : 0.5, py: 0.5 }}>
          <Tooltip title={navOpen ? "折叠菜单" : "展开菜单"} placement="right">
            <ListItemButton
              onClick={() => setNavOpen((o) => !o)}
              aria-label={navOpen ? "折叠菜单" : "展开菜单"}
              sx={{
                borderRadius: 1.5,
                justifyContent: "center",
              }}
            >
              <ListItemIcon sx={{ minWidth: 0, justifyContent: "center" }}>
                <MenuOpenIcon
                  sx={{
                    transition: (t) => t.transitions.create("transform", {
                      duration: t.transitions.duration.shorter,
                    }),
                    transform: navOpen ? "none" : "rotate(180deg)",
                  }}
                />
              </ListItemIcon>
            </ListItemButton>
          </Tooltip>
        </List>
        <VersionBadge open={navOpen} />
      </Drawer>

      <Box sx={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column" }}>
        <AppBar position="static" color="default" elevation={0}
          sx={{ borderBottom: 1, borderColor: "divider" }}>
          <Toolbar sx={{ gap: 1 }}>
            <Box sx={{ flex: 1 }} />
            <Tooltip title={mode === "light" ? "切换到暗色" : "切换到亮色"}>
              <IconButton onClick={toggleMode} aria-label="切换明暗主题">
                {mode === "light" ? <Brightness4Icon /> : <Brightness7Icon />}
              </IconButton>
            </Tooltip>
            <Button color="inherit" onClick={(e) => setMenuAnchor(e.currentTarget)}
              startIcon={<Avatar sx={{ width: 24, height: 24, fontSize: 14 }}>
                {user?.username?.[0]?.toUpperCase() || "?"}
              </Avatar>}>
              {user?.username || "未登录"}
            </Button>
            <Menu anchorEl={menuAnchor} open={Boolean(menuAnchor)}
              onClose={() => setMenuAnchor(null)}
              anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
              transformOrigin={{ vertical: "top", horizontal: "right" }}>
              <MenuItem onClick={() => { setMenuAnchor(null); openProfile(); }}>
                <ListItemIcon><TuneIcon fontSize="small" /></ListItemIcon>
                我的个性化
              </MenuItem>
              <Divider />
              <MenuItem onClick={onLogout}>
                <ListItemIcon><LogoutIcon fontSize="small" /></ListItemIcon>
                退出登录
              </MenuItem>
            </Menu>
          </Toolbar>
        </AppBar>
        <Box component="main" sx={{ flex: 1, minWidth: 0, overflow: "auto" }}>
          {children}
        </Box>
      </Box>
    </Box>
  );
}
