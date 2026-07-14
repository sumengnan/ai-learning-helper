import { Box, Paper, Typography } from "@mui/material";
import { motion } from "framer-motion";
import TravelExploreIcon from "@mui/icons-material/TravelExplore";
import AutoStoriesIcon from "@mui/icons-material/AutoStories";
import TrendingUpIcon from "@mui/icons-material/TrendingUp";
import EventNoteIcon from "@mui/icons-material/EventNote";
import QuizIcon from "@mui/icons-material/Quiz";
import SchoolIcon from "@mui/icons-material/School";
import AssessmentIcon from "@mui/icons-material/Assessment";
import TerminalIcon from "@mui/icons-material/Terminal";
import LightbulbIcon from "@mui/icons-material/Lightbulb";
import AutorenewIcon from "@mui/icons-material/Autorenew";
import type { ReactNode } from "react";

const MotionPaper = motion(Paper);

type Suggestion = { text: string; icon: ReactNode; color: string };

// 顺序即 2 列 grid 的 row-major 填充：左列取奇数条，右列取偶数条
export const SUGGESTIONS: Suggestion[] = [
  { text: "搜索最新的 AI 资讯，保存到知识库", icon: <TravelExploreIcon />, color: "#2563eb" },
  { text: "把知识库里关于 AI 的内容整理成学习笔记", icon: <AutoStoriesIcon />, color: "#db2777" },
  { text: "总结 AI 未来 3 年的发展情况", icon: <TrendingUpIcon />, color: "#059669" },
  { text: "帮我制定一份 30 天 AI 学习计划", icon: <EventNoteIcon />, color: "#0891b2" },
  { text: "随机生成 5 道 AI 相关的单选题，保存到题库", icon: <QuizIcon />, color: "#7c3aed" },
  { text: "从题库抽取 5 道题考试", icon: <SchoolIcon />, color: "#d97706" },
  { text: "生成我的学习报告：掌握了哪些、薄弱点在哪", icon: <AssessmentIcon />, color: "#dc2626" },
  { text: "随机生成一段 Python、Java 或 JS 代码并执行", icon: <TerminalIcon />, color: "#475569" },
  { text: "用最简单的话给我讲一个我总答错的概念", icon: <LightbulbIcon />, color: "#ca8a04" },
  { text: "根据我的错题，针对性地再出 5 道相似的题", icon: <AutorenewIcon />, color: "#4f46e5" },
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
        display: "grid",
        gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },   // 手机单列，桌面左右两列
        gap: 1.25,
        width: "100%", maxWidth: 760,
      }}>
        {SUGGESTIONS.map((s, i) => (
          <MotionPaper
            key={s.text} variant="outlined"
            onClick={() => onAsk(s.text)}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.28, delay: i * 0.05, ease: [0.22, 1, 0.36, 1] }}
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
