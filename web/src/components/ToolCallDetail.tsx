import { Box, Typography } from "@mui/material";
import { alpha } from "@mui/material/styles";

// 工具结果里的机读标记（〔下载ID:x〕〔知识ID:x〕〔题目ID:x〕）供后端追踪产物，展示时剥离不露给用户
const ID_MARKER_RE = /〔(?:下载|知识|题目)ID:[^〕]*〕/g;
export const stripIdMarkers = (t?: string) => (t || "").replace(ID_MARKER_RE, "").trimEnd();

// 工具调用的「参数 + 结果」明细：蓝框参数 / 绿成功·红失败结果。主聊天工具块与子代理块共用。
export function ToolCallDetail({ args, result, isError }: {
  args?: unknown; result?: string; isError?: boolean;
}) {
  return (
    <>
      <Box sx={{
        mb: 0.5, px: 1, py: 0.5, borderRadius: 0.5, borderLeft: 3,
        borderColor: "info.main", bgcolor: (t) => alpha(t.palette.info.main, 0.08),
      }}>
        <Typography variant="caption" sx={{ fontWeight: 700, color: "info.main" }}>参数</Typography>
        <Typography variant="caption" component="pre"
          sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>
          {JSON.stringify(args, null, 2)}
        </Typography>
      </Box>
      {result !== undefined && (
        <Box sx={{
          px: 1, py: 0.5, borderRadius: 0.5, borderLeft: 3,
          borderColor: isError ? "error.main" : "success.main",
          bgcolor: (t) => alpha((isError ? t.palette.error : t.palette.success).main, 0.1),
        }}>
          <Typography variant="caption"
            sx={{ fontWeight: 700, color: isError ? "error.main" : "success.main" }}>
            {isError ? "结果 · 失败" : "结果 · 成功"}
          </Typography>
          <Typography variant="caption" component="pre"
            sx={{ m: 0, fontFamily: "monospace", whiteSpace: "pre-wrap", wordBreak: "break-all",
                  color: "text.primary" }}>
            {stripIdMarkers(result)}
          </Typography>
        </Box>
      )}
    </>
  );
}
