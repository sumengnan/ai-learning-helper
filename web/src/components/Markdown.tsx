import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box } from "@mui/material";

// AI 回复正文按 Markdown 渲染（标题/列表/代码块/表格/链接等）
export function Markdown({ children }: { children: string }) {
  return (
    <Box sx={{
      wordBreak: "break-word",
      "& > :first-of-type": { mt: 0 },
      "& > :last-child": { mb: 0 },
      "& p": { m: 0, mb: 1 },
      "& ul, & ol": { pl: 3, m: 0, mb: 1 },
      "& li": { mb: 0.25 },
      "& h1, & h2, & h3, & h4": { mt: 1, mb: 0.5, fontWeight: 700, lineHeight: 1.3 },
      "& h1": { fontSize: "1.3rem" },
      "& h2": { fontSize: "1.15rem" },
      "& h3": { fontSize: "1.05rem" },
      "& code": {
        fontFamily: "monospace", fontSize: "0.85em",
        bgcolor: "rgba(127,127,127,0.16)", px: 0.5, py: "1px", borderRadius: 0.5,
      },
      "& pre": {
        m: 0, mb: 1, p: 1, borderRadius: 1, overflowX: "auto",
        bgcolor: "rgba(127,127,127,0.16)",
      },
      "& pre code": { bgcolor: "transparent", p: 0, fontSize: "0.85em" },
      "& a": { color: "primary.main" },
      "& blockquote": {
        borderLeft: 3, borderColor: "divider", pl: 1, ml: 0, my: 1, color: "text.secondary",
      },
      "& table": { borderCollapse: "collapse", my: 1, display: "block", overflowX: "auto" },
      "& th, & td": { border: 1, borderColor: "divider", px: 1, py: 0.5 },
      "& img": { maxWidth: "100%" },
      "& hr": { border: 0, borderTop: 1, borderColor: "divider", my: 1 },
    }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </Box>
  );
}
