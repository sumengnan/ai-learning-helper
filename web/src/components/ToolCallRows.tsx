import { Accordion, AccordionSummary, AccordionDetails, Typography, CircularProgress } from "@mui/material";
import ExpandMoreIcon from "@mui/icons-material/ExpandMore";
import CheckCircleIcon from "@mui/icons-material/CheckCircle";
import CancelIcon from "@mui/icons-material/Cancel";
import { ToolCallDetail } from "./ToolCallDetail";
import { ToolLabel } from "./ToolLabel";

export type ToolRow = {
  // warn 见 ProgressBlock 里同名字段的注释（交付提醒借同一条 progress 列传输）
  text: string; status?: "running" | "ok" | "error" | "warn" | null; key?: string | null;
  detail?: { tool?: string; args?: unknown; result?: string; is_error?: boolean; elapsed_ms?: number } | null;
};

// 同 key 的开始/完成折叠成一行（后到覆盖），保留末态（带 result 的完成行）
export function mergeByKey(items: ToolRow[]): ToolRow[] {
  const rows: ToolRow[] = [];
  const pos = new Map<string, number>();
  for (const p of items) {
    if (p.key) {
      const i = pos.get(p.key);
      if (i !== undefined) rows[i] = p;
      else { pos.set(p.key, rows.length); rows.push(p); }
    } else rows.push(p);
  }
  return rows;
}

function rowIcon(p: ToolRow, live: boolean) {
  if (p.status === "error" || p.detail?.is_error)
    return <CancelIcon sx={{ fontSize: 16 }} color="error" />;
  if (p.status === "running")
    return live ? <CircularProgress size={12} /> : <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
  return <CheckCircleIcon sx={{ fontSize: 16 }} color="success" />;
}

// 一串可展开的工具调用行：每行展开看入参/返回。主聊天工具块与子代理/计划步的执行明细共用。
export function ToolCallRows({ rows, live }: { rows: ToolRow[]; live: boolean }) {
  return (
    <>
      {rows.map((p, i) => (
        <Accordion key={p.key ?? i} disableGutters elevation={0}
          sx={{ bgcolor: "transparent", "&:before": { display: "none" }, pl: 1 }}>
          <AccordionSummary expandIcon={<ExpandMoreIcon fontSize="small" />}
            sx={{ minHeight: 0, px: 0,
                  "& .MuiAccordionSummary-content": { my: 0.4, alignItems: "center", gap: 0.75 } }}>
            {rowIcon(p, live)}
            <Typography variant="caption" component="div">
              <ToolLabel name={p.detail?.tool || p.text} />
            </Typography>
          </AccordionSummary>
          <AccordionDetails sx={{ px: 0, pt: 0 }}>
            <ToolCallDetail args={p.detail?.args} result={p.detail?.result} isError={p.detail?.is_error} />
          </AccordionDetails>
        </Accordion>
      ))}
    </>
  );
}
