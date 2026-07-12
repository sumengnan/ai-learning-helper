import { Box, Paper, Typography } from "@mui/material";
import { motion } from "framer-motion";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import QuizIcon from "@mui/icons-material/Quiz";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import TerminalIcon from "@mui/icons-material/Terminal";
import AutoStoriesIcon from "@mui/icons-material/AutoStories";
import type { ReactNode } from "react";

const MotionPaper = motion(Paper);

type Suggestion = { text: string; icon: ReactNode; color: string };

export const SUGGESTIONS: Suggestion[] = [
  { text: "搜索最新的 AI 资讯，并标注时间", icon: <TravelExploreIcon />, color: "#2563eb" },
  { text: "考考我 AI 知识", icon: <QuizIcon />, color: "#7c3aed" },
  { text: "总结 AI 未来 3 年的发展情况", icon: <TrendingUpIcon />, color: "#059669" },
  { text: "随机生成一段 Python、Java 或 JS 代码并执行", icon: <TerminalIcon />, color: "#d97706" },
  { text: "总结一下我知识库的 AI 相关内容", icon: <AutoStoriesIcon />, color: "#db2777" },
];

// 空对话引导：新建但未聊天、或尚未选中对话时展示；点击卡片即以该问题发问
export function EmptyHint({ onAsk }: { onAsk: (q: string) => void }) {
  return (
    <Box sx={{
      height: "100%", display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center", gap: 3, p: 3,
    }}>
      <Typography variant="h5" sx={{ fontWeight: 700, textAlign: "center" }}>
        基于 Harness 架构的 AI 学习助手
      </Typography>
      <Box sx={{
        display: "flex", flexDirection: "column", gap: 1.25,
        width: "100%", maxWidth: 460,
      }}>
        {SUGGESTIONS.map((s, i) => (
          <MotionPaper
            key={s.text} variant="outlined"
            onClick={() => onAsk(s.text)}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, delay: i * 0.06, ease: [0.22, 1, 0.36, 1] }}
            whileHover={{ x: 4 }}
            whileTap={{ scale: 0.98 }}
            sx={{
              display: "flex", alignItems: "center", gap: 1.5, textAlign: "left",
              px: 2, py: 1.5, borderRadius: 2, cursor: "pointer",
              "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            }}
          >
            <Box sx={{
              flex: "none", width: 34, height: 34, borderRadius: 1.5, display: "grid",
              placeItems: "center", color: s.color, bgcolor: `${s.color}1a`,
            }}>
              {s.icon}
            </Box>
            <Typography variant="body2" sx={{ fontWeight: 500 }}>{s.text}</Typography>
          </MotionPaper>
        ))}
      </Box>
    </Box>
  );
}
