import { useEffect, useState } from "react";
import {
  Dialog, Box, Typography, IconButton, CircularProgress, Alert, Stack, Card, Chip, Divider,
} from "@mui/material";
import type { ChipProps } from "@mui/material";
import { alpha } from "@mui/material/styles";
import CloseIcon from "@mui/icons-material/Close";
import DeleteOutlineIcon from "@mui/icons-material/DeleteOutlined";
import PsychologyIcon from "@mui/icons-material/Psychology";
import { statsApi, type MemoryItem } from "../api/stats";
import { Markdown } from "../components/Markdown";

function fromNow(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
}

// 时间远近上色：越新越「暖绿」，越旧越淡，一眼看出记忆新鲜度
function recencyColor(iso?: string): string {
  if (!iso) return "text.disabled";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 86400) return "success.main";        // 一天内
  if (s < 7 * 86400) return "info.main";        // 一周内
  if (s < 30 * 86400) return "warning.main";    // 一月内
  return "text.disabled";                       // 更久
}

const COLLECTION_LABEL: Record<string, string> = {
  semantic: "语义", episodic: "情景", procedural: "程序",
};

// 真实 collection 常形如 "conversation:<id>"，友好化为可读标签
function collLabel(c: string): string {
  if (!c) return "记忆";
  if (c.startsWith("conversation")) return "对话";
  return COLLECTION_LABEL[c] || "记忆";
}

// 不同 collection 用不同颜色的标签区分
function collColor(c: string): ChipProps["color"] {
  if (!c) return "default";
  if (c.startsWith("conversation")) return "primary";
  const map: Record<string, ChipProps["color"]> = {
    semantic: "info", episodic: "secondary", procedural: "warning", knowledge: "success",
  };
  return map[c] || "default";
}

// 粗略判断是否为 markdown：出现标题/列表/代码围栏/表格/加粗/链接等标记即按 md 渲染
function looksLikeMarkdown(text: string): boolean {
  return /(^|\n)\s{0,3}#{1,6}\s|(^|\n)\s*[-*+]\s|(^|\n)\s*\d+\.\s|```|(^|\n)\s*>\s|\|.*\|.*\||\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)/.test(text);
}

export function MemoryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setItems(null); setError(null);
    statsApi.memory(50)
      .then((d) => { if (alive) setItems(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [open]);

  async function remove(id: string) {
    setDeleting(id); setError(null);
    try {
      await statsApi.deleteMemory(id);
      setItems((cur) => (cur ? cur.filter((m) => m.id !== id) : cur));
    } catch (e) {
      setError(e instanceof Error ? e.message : "删除失败");
    } finally {
      setDeleting(null);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth
      slotProps={{ paper: { sx: { maxHeight: "85vh" } } }}>
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, maxHeight: "85vh" }}>
        <Stack direction="row" spacing={1.5} sx={{ alignItems: "center" }}>
          <PsychologyIcon color="primary" />
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>AI 记住的偏好</Typography>
            <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>
              它从你们的对话里沉淀下来的长期记忆
            </Typography>
          </Box>
          <IconButton size="small" onClick={onClose} aria-label="关闭"><CloseIcon /></IconButton>
        </Stack>
        <Divider />

        <Box sx={{ overflowY: "auto", flex: 1 }}>
          {error && <Alert severity="error">{error}</Alert>}
          {!items && !error && (
            <Box sx={{ display: "flex", justifyContent: "center", py: 6 }}><CircularProgress /></Box>
          )}
          {items && items.length === 0 && (
            <Typography sx={{ color: "text.secondary", py: 4, textAlign: "center" }}>
              还没有沉淀任何记忆。多聊几次，AI 会逐渐记住你的偏好。
            </Typography>
          )}
          {items && items.length > 0 && (
            <Stack spacing={1.5}>
              {items.map((m) => (
                <Card key={m.id} variant="outlined" sx={{ p: 1.75, borderRadius: 2 }}>
                  <Stack direction="row" spacing={0.5} sx={{ justifyContent: "space-between", alignItems: "center", mb: 0.75 }}>
                    <Chip size="small" label={collLabel(m.collection)} color={collColor(m.collection)}
                      variant="outlined" sx={{ height: 20, fontSize: 11 }} />
                    <Stack direction="row" spacing={0.5} sx={{ alignItems: "center" }}>
                      <Typography sx={{ fontSize: 11.5, fontWeight: 600, color: recencyColor(m.created_at) }}>
                        {fromNow(m.created_at)}
                      </Typography>
                      <IconButton size="small" aria-label="删除这条记忆" disabled={deleting === m.id}
                        onClick={() => remove(m.id)}
                        sx={{ p: 0.25, color: "error.light",
                          "&:hover": { color: "error.main", bgcolor: (t) => alpha(t.palette.error.main, 0.08) } }}>
                        <DeleteOutlineIcon sx={{ fontSize: 16 }} />
                      </IconButton>
                    </Stack>
                  </Stack>
                  {looksLikeMarkdown(m.text) ? (
                    <Box sx={{ fontSize: 13.5, lineHeight: 1.55,
                      "& p": { my: 0.5 }, "& :first-of-type": { mt: 0 }, "& :last-child": { mb: 0 },
                      "& pre": { overflowX: "auto" } }}>
                      <Markdown>{m.text}</Markdown>
                    </Box>
                  ) : (
                    <Typography sx={{ fontSize: 13.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
                      color: "text.primary", lineHeight: 1.55 }}>{m.text}</Typography>
                  )}
                </Card>
              ))}
            </Stack>
          )}
        </Box>
      </Box>
    </Dialog>
  );
}
