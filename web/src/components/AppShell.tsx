// web/src/components/AppShell.tsx
import { createContext, useContext, useState, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Toolbar, Typography, IconButton, Tooltip, Divider, AppBar,
  Button, Menu, MenuItem, Avatar,
} from "@mui/material";
import ChatBubbleOutlineIcon from "@mui/icons-material/ChatBubbleOutlined";
import MenuBookIcon from "@mui/icons-material/MenuBook";
import QuizIcon from "@mui/icons-material/Quiz";
import ErrorOutlineIcon from "@mui/icons-material/ErrorOutlined";
import DownloadIcon from "@mui/icons-material/Download";
import Brightness4Icon from "@mui/icons-material/Brightness4";
import Brightness7Icon from "@mui/icons-material/Brightness7";
import AddIcon from "@mui/icons-material/Add";
import LogoutIcon from "@mui/icons-material/Logout";
import { useColorMode } from "../ThemeModeProvider";
import { useAuth } from "../auth/AuthProvider";

const WIDTH = 220;

const NAV: { to: string; label: string; icon: ReactNode }[] = [
  { to: "/", label: "聊天", icon: <ChatBubbleOutlineIcon /> },
  { to: "/knowledge", label: "知识库", icon: <MenuBookIcon /> },
  { to: "/questions", label: "题库", icon: <QuizIcon /> },
  { to: "/wrong", label: "错题集", icon: <ErrorOutlineIcon /> },
  { to: "/downloads", label: "下载", icon: <DownloadIcon /> },
];

// 顶部栏「新建对话」→ ChatPage 之间的解耦通道：点一次自增 nonce，ChatPage 监听。
const ShellContext = createContext<{ newChatNonce: number; requestNewChat: () => void }>({
  newChatNonce: 0,
  requestNewChat: () => {},
});
export function useShell() {
  return useContext(ShellContext);
}

export function AppShell({ children }: { children: ReactNode }) {
  const location = useLocation();
  const navigate = useNavigate();
  const { mode, toggleMode } = useColorMode();
  const { user, logout } = useAuth();
  const [newChatNonce, setNewChatNonce] = useState(0);
  const [menuAnchor, setMenuAnchor] = useState<null | HTMLElement>(null);

  const isActive = (to: string) =>
    to === "/" ? location.pathname === "/" : location.pathname.startsWith(to);
  const requestNewChat = () => {
    setNewChatNonce((n) => n + 1);
    navigate("/");
  };
  const onLogout = () => {
    setMenuAnchor(null);
    logout();
    navigate("/login", { replace: true });
  };

  return (
    <ShellContext.Provider value={{ newChatNonce, requestNewChat }}>
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
        </Drawer>

        <Box sx={{ flex: 1, minWidth: 0, height: "100%", display: "flex", flexDirection: "column" }}>
          <AppBar position="static" color="default" elevation={0}
            sx={{ borderBottom: 1, borderColor: "divider" }}>
            <Toolbar sx={{ gap: 1 }}>
              <Button startIcon={<AddIcon />} variant="outlined" size="small"
                onClick={requestNewChat}>
                新建对话
              </Button>
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
    </ShellContext.Provider>
  );
}
