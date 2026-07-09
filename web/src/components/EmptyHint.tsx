import { Box, Paper, Typography } from "@mui/material";

export const SUGGESTIONS = [
  "查询最新的 AI 资讯，保存到知识库",
  '执行代码：echo "Hello " + "AI"',
  "从题库抽 10 道题，开始模拟考试",
  "对我的错题集做个总结",
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
        display: "flex", flexWrap: "wrap", gap: 1.5,
        justifyContent: "center", maxWidth: 640,
      }}>
        {SUGGESTIONS.map((s) => (
          <Paper
            key={s} variant="outlined"
            onClick={() => onAsk(s)}
            sx={{
              px: 2, py: 1.5, borderRadius: 2, cursor: "pointer", maxWidth: 300,
              "&:hover": { borderColor: "primary.main", bgcolor: "action.hover" },
            }}
          >
            <Typography variant="body2">{s}</Typography>
          </Paper>
        ))}
      </Box>
    </Box>
  );
}
