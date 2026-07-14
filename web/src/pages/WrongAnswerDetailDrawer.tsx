import {
  Dialog, Box, Typography, Chip, Stack, IconButton, Divider, type ChipProps,
} from "@mui/material";
import { alpha } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";

export interface WrongItem {
  id: string;
  user_answer: unknown;
  snapshot: {
    type: string;
    stem: string;
    options?: string[] | null;
    answer: unknown;
    explanation: string;
  };
}

const TYPE_LABEL: Record<string, string> = {
  single: "单选", multiple: "多选", truefalse: "判断", short: "简答",
};
// 题型配色：与题库页保持一致，各题型一色
export const typeColor = (t: string): ChipProps["color"] => {
  switch (t) {
    case "single": return "primary";
    case "multiple": return "secondary";
    case "truefalse": return "success";
    case "short": return "warning";
    default: return "default";
  }
};

// 选项下标 → 字母（0→A、1→B…）
const optionLetter = (i: number) => String.fromCharCode(65 + i);

// 把答案值（选项下标/数组/布尔/文本）渲染成人类可读文本。
// 选择题的正确/作答用字母（A/B/C…）而非选项内容——详情弹框另有完整选项列表可对照。
export function answerText(type: string, _options: string[] | null | undefined, value: unknown): string {
  if (type === "truefalse") return value ? "正确" : "错误";
  if (type === "single" && typeof value === "number") return optionLetter(value);
  if (type === "multiple" && Array.isArray(value))
    return (value as unknown[])
      .map((i) => (typeof i === "number" ? optionLetter(i) : String(i))).join("、");
  if (value == null || value === "") return "（未作答）";
  return String(value);
}

// 某选项下标是否属于正确答案（单选=相等，多选=数组含）
function isCorrect(type: string, answer: unknown, idx: number): boolean {
  if (type === "single") return answer === idx;
  if (type === "multiple") return Array.isArray(answer) && (answer as number[]).includes(idx);
  return false;
}
// 某选项下标是否是「我的（错误）作答」
function isMine(type: string, userAnswer: unknown, idx: number): boolean {
  if (type === "single") return userAnswer === idx;
  if (type === "multiple") return Array.isArray(userAnswer) && (userAnswer as number[]).includes(idx);
  return false;
}

// 错题详情弹框：页面居中弹出，直接渲染内存中的快照（列表已含全字段，无需再请求）。
// 选项逐条标注：✓ 正确答案（绿）、✗ 我的错误作答（红）。
export function WrongAnswerDetailDrawer({ item, onClose }: {
  item: WrongItem | null; onClose: () => void;
}) {
  return (
    <Dialog open={item !== null} onClose={onClose} maxWidth="sm" fullWidth
      slotProps={{ paper: { sx: { maxHeight: "85vh" } } }}>
      {item && (() => {
        const { type, stem, options, answer, explanation } = item.snapshot;
        return (
          <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, maxHeight: "85vh" }}>
            <Stack direction="row" spacing={1}
              sx={{ alignItems: "center", justifyContent: "space-between" }}>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>错题详情</Typography>
              <IconButton size="small" aria-label="关闭" onClick={onClose}>
                <CloseIcon />
              </IconButton>
            </Stack>

            <Stack direction="row" spacing={1} sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
              <Chip size="small" color={typeColor(type)} label={TYPE_LABEL[type] ?? type} />
            </Stack>
            <Divider />

            <Box sx={{ overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 2 }}>
              {/* 题目 */}
              <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", fontWeight: 600 }}>
                {stem}
              </Typography>

              {/* 选项：逐条标注正确 / 我的错误作答 */}
              {options && options.length > 0 && (
                <Stack spacing={0.75}>
                  {options.map((opt, i) => {
                    const correct = isCorrect(type, answer, i);
                    const mine = isMine(type, item.user_answer, i);
                    return (
                      <Stack key={i} direction="row" spacing={1} sx={{ alignItems: "center" }}>
                        {correct
                          ? <CheckCircleIcon color="success" fontSize="small" />
                          : mine
                            ? <CancelIcon color="error" fontSize="small" />
                            : <Box sx={{ width: 20 }} />}
                        <Typography variant="body2" sx={{
                          fontWeight: correct || mine ? 700 : 400,
                          color: correct ? "success.main" : mine ? "error.main" : "text.primary",
                        }}>
                          {String.fromCharCode(65 + i)}. {opt}
                        </Typography>
                      </Stack>
                    );
                  })}
                </Stack>
              )}

              {/* 我的答案（红）*/}
              <Box sx={{
                px: 1.25, py: 0.75, borderRadius: 1, borderLeft: "3px solid",
                borderColor: "error.main", bgcolor: (t) => alpha(t.palette.error.main, 0.06),
              }}>
                <Typography variant="subtitle2" color="error.main">我的答案</Typography>
                <Typography variant="body1" sx={{ whiteSpace: "pre-wrap" }}>
                  {answerText(type, options, item.user_answer)}
                </Typography>
              </Box>

              {/* 正确答案（绿）*/}
              <Box sx={{
                px: 1.25, py: 0.75, borderRadius: 1, borderLeft: "3px solid",
                borderColor: "success.main", bgcolor: (t) => alpha(t.palette.success.main, 0.06),
              }}>
                <Typography variant="subtitle2" color="success.main">正确答案</Typography>
                <Typography variant="body1" sx={{ whiteSpace: "pre-wrap" }}>
                  {answerText(type, options, answer)}
                </Typography>
              </Box>

              {/* 解析 */}
              {explanation && (
                <Box>
                  <Typography variant="subtitle2" color="text.secondary">解析</Typography>
                  <Typography variant="body2" sx={{ whiteSpace: "pre-wrap" }}>{explanation}</Typography>
                </Box>
              )}
            </Box>
          </Box>
        );
      })()}
    </Dialog>
  );
}
