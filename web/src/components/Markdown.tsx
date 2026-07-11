import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box } from "@mui/material";

// href="#cite-<key>-<n>" 的链接是来源角标：渲染成可点上标，回调交由上层滚动/高亮到来源。
function citeNumber(href?: string): number | null {
  const m = href ? /#cite-.+-(\d+)$/.exec(href) : null;
  return m ? Number(m[1]) : null;
}

// AI 回复正文按 Markdown 渲染（标题/列表/代码块/表格/链接等）。
// onCitationClick 传入时，正文里的 [n] 来源角标可点击。
export function Markdown({ children, onCitationClick }: {
  children: string; onCitationClick?: (n: number) => void;
}) {
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
      "& sup.cite": {
        color: "primary.main", fontWeight: 700, cursor: "pointer", ml: "1px",
        "&:hover": { textDecoration: "underline" },
      },
    }}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ node: _node, href, children, ...props }: any) {
            const n = citeNumber(href);
            if (n !== null && onCitationClick) {
              return (
                <Box component="sup" className="cite" role="button" tabIndex={0}
                  onClick={(e: any) => { e.preventDefault(); onCitationClick(n); }}>
                  [{n}]
                </Box>
              );
            }
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
            );
          },
        }}
      >{children}</ReactMarkdown>
    </Box>
  );
}
