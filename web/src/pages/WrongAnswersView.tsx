import { useEffect, useState } from "react";
import { Box, Typography, Card, CardContent, Chip, IconButton, Stack } from "@mui/material";
import { alpha } from "@mui/material/styles";
import DeleteIcon from "@mui/icons-material/Delete";
import SentimentSatisfiedAltOutlinedIcon from "@mui/icons-material/SentimentSatisfiedAltOutlined";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";
import { EmptyState } from "../components/EmptyState";
import {
  WrongAnswerDetailDrawer, answerText, typeColor, type WrongItem,
} from "./WrongAnswerDetailDrawer";

const TYPE_LABEL: Record<string, string> = {
  single: "单选", multiple: "多选", truefalse: "判断", short: "简答",
};
const typeLabel = (t: string) => TYPE_LABEL[t] ?? t;

// 多行截断（答案过长显示 …）
const clampSx = {
  display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical" as const,
  overflow: "hidden", wordBreak: "break-all" as const,
};

export default function WrongAnswersView() {
  const [items, setItems] = useState<WrongItem[]>([]);
  const [preview, setPreview] = useState<WrongItem | null>(null);

  const refresh = () => api.wrong.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  async function removeOne(id: string) {
    await api.wrong.removeMany([id]);
    await refresh();
  }

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      {/* 头部：标题 + 计数 */}
      <Box>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>错题集</Typography>
        <Typography color="text.secondary" variant="body2">共 {items.length} 道错题</Typography>
      </Box>

      {items.length === 0 ? (
        <EmptyState icon={<SentimentSatisfiedAltOutlinedIcon />} title="暂无错题"
          hint="答错的题会自动收集到这里，方便你复习巩固" />
      ) : (
        <Box sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
          <AnimatePresence initial={false}>
            {items.map((w) => (
              <motion.div key={w.id} layout variants={listItemVariants}
                initial="initial" animate="animate" exit="exit">
                <Card variant="outlined"
                  sx={{ "&:hover": { borderColor: "primary.main", boxShadow: 2 } }}>
                  <CardContent sx={{ display: "flex", gap: 1, "&:last-child": { pb: 2 } }}>
                    <Box sx={{ minWidth: 0, flex: 1, cursor: "pointer" }} onClick={() => setPreview(w)}>
                      {/* 题目：正文色加粗，作为主内容突出 */}
                      <Typography variant="body1" sx={{
                        fontWeight: 700, color: "text.primary",
                        display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                        overflow: "hidden", wordBreak: "break-all",
                      }}>
                        {w.snapshot.stem}
                      </Typography>

                      {/* 我的答案：红色淡块 + 左侧红条 */}
                      <Box sx={{
                        mt: 0.75, px: 1, py: 0.5, borderRadius: 1,
                        borderLeft: "2px solid", borderColor: "error.light",
                        bgcolor: (t) => alpha(t.palette.error.main, 0.05),
                      }}>
                        <Typography variant="body2" sx={{ color: "text.secondary", ...clampSx }}>
                          <Box component="span" sx={{ color: "error.main", fontWeight: 600 }}>我的答案：</Box>
                          {answerText(w.snapshot.type, w.snapshot.options, w.user_answer)}
                        </Typography>
                      </Box>

                      {/* 正确答案：绿色淡块 + 左侧绿条 */}
                      <Box sx={{
                        mt: 0.5, px: 1, py: 0.5, borderRadius: 1,
                        borderLeft: "2px solid", borderColor: "success.light",
                        bgcolor: (t) => alpha(t.palette.success.main, 0.05),
                      }}>
                        <Typography variant="body2" sx={{ color: "text.secondary", ...clampSx }}>
                          <Box component="span" sx={{ color: "success.main", fontWeight: 600 }}>正确答案：</Box>
                          {answerText(w.snapshot.type, w.snapshot.options, w.snapshot.answer)}
                        </Typography>
                      </Box>

                      {/* 底部：题型标签，分隔线与上方隔开 */}
                      <Stack direction="row" spacing={1} sx={{
                        mt: 1, pt: 1, alignItems: "center", flexWrap: "wrap",
                        borderTop: "1px dashed", borderColor: "divider",
                      }}>
                        <Chip size="small" variant="outlined"
                          label={typeLabel(w.snapshot.type)} color={typeColor(w.snapshot.type)} />
                      </Stack>
                    </Box>
                    <IconButton size="small" color="error" aria-label="删除错题"
                      onClick={() => removeOne(w.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </CardContent>
                </Card>
              </motion.div>
            ))}
          </AnimatePresence>
        </Box>
      )}

      <WrongAnswerDetailDrawer item={preview} onClose={() => setPreview(null)} />
    </Box>
  );
}
