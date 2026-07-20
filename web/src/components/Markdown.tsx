import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box } from "@mui/material";

// href="#cite-<key>-<n>" 的链接是来源角标：渲染成可点上标，回调交由上层滚动/高亮到来源。
function citeNumber(href?: string): number | null {
  const m = href ? /#cite-.+-(\d+)$/.exec(href) : null;
  return m ? Number(m[1]) : null;
}

// 死链降级：模型手上没有下载地址，却常出于「给个入口」的好意自己编一个 markdown 链接
// （实测编出 [下载 xx.md](#)，点了停在当前页）。真正的下载入口是消息下方那排按钮。
// 这类链接一律渲染成纯文本——点不动的链接比没有链接更糟，用户会以为功能坏了。
// 判定只认「必然无效」的形态：空、纯 #（锚点 #cite-… 是来源角标，另有分支先行处理）、
// javascript: 伪协议。外站链接与真实锚点不受影响。
function isDeadLink(href?: string): boolean {
  const h = (href || "").trim();
  if (!h || h === "#") return true;
  if (h.startsWith("javascript:")) return true;
  return false;
}

// 段内单换行渲染为 <br>：模型常用单个 \n 表示换行（如逐行列出选项 A./B./C./D.），
// 但 CommonMark/GFM 会把段内单换行折叠成空格，导致选项挤成一行。这里在 mdast 层把
// text 节点里的 \n 拆成 break 节点。只动 text 节点，代码块/表格结构/列表项均不受影响
// （等价 remark-breaks，内联实现免加依赖）。
function remarkSoftBreaks() {
  return (tree: any) => {
    const walk = (node: any) => {
      if (!node.children) return;
      const out: any[] = [];
      for (const child of node.children) {
        if (child.type === "text" && typeof child.value === "string" && child.value.includes("\n")) {
          child.value.split("\n").forEach((seg: string, i: number) => {
            if (i > 0) out.push({ type: "break" });
            if (seg) out.push({ type: "text", value: seg });
          });
        } else {
          walk(child);
          out.push(child);
        }
      }
      node.children = out;
    };
    walk(tree);
  };
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
        remarkPlugins={[remarkGfm, remarkSoftBreaks]}
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
            if (isDeadLink(href)) return <>{children}</>;   // 死链降级为纯文本
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
            );
          },
        }}
      >{children}</ReactMarkdown>
    </Box>
  );
}
