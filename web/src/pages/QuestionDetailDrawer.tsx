import {
  Drawer, Box, Typography, Chip, Stack, IconButton, Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import type { Question } from "../api/client";

const TYPE_LABEL: Record<string, string> = {
  single: "单选", multiple: "多选", truefalse: "判断", short: "简答",
};

// 正确答案是否包含某选项下标（单选=相等，多选=数组含）
function isCorrect(q: Question, idx: number): boolean {
  if (q.type === "single") return q.answer === idx;
  if (q.type === "multiple") return Array.isArray(q.answer) && (q.answer as number[]).includes(idx);
  return false;
}

function answerText(q: Question): string {
  if (q.type === "truefalse") return q.answer ? "正确" : "错误";
  if (q.type === "single" && q.options && typeof q.answer === "number")
    return q.options[q.answer] ?? String(q.answer);
  if (q.type === "multiple" && q.options && Array.isArray(q.answer))
    return (q.answer as number[]).map((i) => q.options![i] ?? i).join("、");
  return String(q.answer ?? "");
}

// 题目详情抽屉：右侧滑出，直接渲染内存中的题目对象（列表已含全字段，无需再请求）。
export function QuestionDetailDrawer({ question, onClose }: {
  question: Question | null; onClose: () => void;
}) {
  return (
    <Drawer anchor="right" open={question !== null} onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 }, maxWidth: "100%" } } }}>
      {question && (
        <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, height: "100%" }}>
          <Stack direction="row" spacing={1}
            sx={{ alignItems: "center", justifyContent: "space-between" }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>题目详情</Typography>
            <IconButton size="small" aria-label="关闭" onClick={onClose}>
              <CloseIcon />
            </IconButton>
          </Stack>

          <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
            <Chip size="small" color="primary" label={TYPE_LABEL[question.type] ?? question.type} />
            {question.source && (
              <Typography variant="caption" color="text.secondary">来源：{question.source}</Typography>
            )}
          </Stack>
          <Divider />

          <Box sx={{ overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
            <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", fontWeight: 600 }}>
              {question.stem}
            </Typography>

            {question.options && question.options.length > 0 && (
              <Stack spacing={0.75}>
                {question.options.map((opt, i) => (
                  <Stack key={i} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                    {isCorrect(question, i)
                      ? <CheckCircleIcon color="success" fontSize="small" />
                      : <Box sx={{ width: 20 }} />}
                    <Typography variant="body2"
                      sx={{ fontWeight: isCorrect(question, i) ? 700 : 400 }}>
                      {String.fromCharCode(65 + i)}. {opt}
                    </Typography>
                  </Stack>
                ))}
              </Stack>
            )}

            <Box>
              <Typography variant="subtitle2" color="text.secondary">正确答案</Typography>
              <Typography variant="body1" sx={{ whiteSpace: "pre-wrap" }}>{answerText(question)}</Typography>
            </Box>

            {question.explanation && (
              <Box>
                <Typography variant="subtitle2" color="text.secondary">解析</Typography>
                <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{question.explanation}</Typography>
              </Box>
            )}
          </Box>
        </Box>
      )}
    </Drawer>
  );
}
