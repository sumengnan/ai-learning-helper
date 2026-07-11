import { useEffect, useState } from "react";
import {
  Drawer, Box, Typography, IconButton, CircularProgress, Alert, Stack, Card, Chip,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import PsychologyIcon from "@mui/icons-material/Psychology";
import { statsApi, type MemoryItem } from "../api/stats";

function fromNow(iso?: string): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 3600) return `${Math.floor(s / 60)} 分钟前`;
  if (s < 86400) return `${Math.floor(s / 3600)} 小时前`;
  return `${Math.floor(s / 86400)} 天前`;
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

export function MemoryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [items, setItems] = useState<MemoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setItems(null); setError(null);
    statsApi.memory(50)
      .then((d) => { if (alive) setItems(d); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : "加载失败"); });
    return () => { alive = false; };
  }, [open]);

  return (
    <Drawer anchor="right" open={open} onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 460 }, maxWidth: "100%" } } }}>
      <Box sx={{ p: 2.5, display: "flex", alignItems: "center", gap: 1.5, borderBottom: 1, borderColor: "divider" }}>
        <PsychologyIcon color="primary" />
        <Box sx={{ flex: 1 }}>
          <Typography sx={{ fontWeight: 700, fontSize: 16 }}>AI 记住的偏好</Typography>
          <Typography sx={{ fontSize: 12.5, color: "text.secondary" }}>
            它从你们的对话里沉淀下来的长期记忆
          </Typography>
        </Box>
        <IconButton onClick={onClose} aria-label="关闭"><CloseIcon /></IconButton>
      </Box>
      <Box sx={{ p: 2.5, overflow: "auto" }}>
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
            {items.map((m, i) => (
              <Card key={i} variant="outlined" sx={{ p: 1.75, borderRadius: 2 }}>
                <Stack direction="row" sx={{ justifyContent: "space-between", alignItems: "center", mb: 0.75 }}>
                  <Chip size="small" label={collLabel(m.collection)}
                    variant="outlined" sx={{ height: 20, fontSize: 11 }} />
                  <Typography sx={{ fontSize: 11.5, color: "text.disabled" }}>{fromNow(m.created_at)}</Typography>
                </Stack>
                <Typography sx={{ fontSize: 13.5, whiteSpace: "pre-wrap", wordBreak: "break-word",
                  color: "text.primary", lineHeight: 1.55 }}>{m.text}</Typography>
              </Card>
            ))}
          </Stack>
        )}
      </Box>
    </Drawer>
  );
}
