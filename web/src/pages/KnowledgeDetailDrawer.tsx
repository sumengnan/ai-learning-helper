// web/src/pages/KnowledgeDetailDrawer.tsx
import { useEffect, useState } from "react";
import {
  Drawer, Box, Typography, Chip, Stack, IconButton,
  CircularProgress, Alert, Divider,
} from "@mui/material";
import CloseIcon from "@mui/icons-material/Close";
import DescriptionIcon from "@mui/icons-material/Description";
import { api } from "../api/client";
import { Markdown } from "../components/Markdown";
import { categoryColor, fmtDate } from "./knowledgeUtils";

type FragmentDetail = {
  id: string; filename: string; text: string;
  category: string; uploaded_at: string; doc_id: string;
};

// 片段详情抽屉：从右侧滑出，不跳转页面。Markdown 片段按 md 渲染，其余保留原文换行。
export function KnowledgeDetailDrawer({ id, onClose }: {
  id: string | null; onClose: () => void;
}) {
  const [frag, setFrag] = useState<FragmentDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    setLoading(true); setError(null); setFrag(null);
    api.documents.get(id)
      .then((f) => { if (!cancelled) setFrag(f); })
      .catch((e: any) => { if (!cancelled) setError(String(e?.message || e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [id]);

  const isMarkdown = frag?.category === "Markdown";

  return (
    <Drawer anchor="right" open={id !== null} onClose={onClose}
      slotProps={{ paper: { sx: { width: { xs: "100%", sm: 560 }, maxWidth: "100%" } } }}>
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 1.5, height: "100%" }}>
        <Stack direction="row" spacing={1}
          sx={{ alignItems: "center", justifyContent: "space-between" }}>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>片段详情</Typography>
          <IconButton size="small" aria-label="关闭" onClick={onClose}>
            <CloseIcon />
          </IconButton>
        </Stack>

        {loading && <CircularProgress size={24} />}
        {error && <Alert severity="error">{error}</Alert>}

        {frag && (
          <>
            {/* 来源信息：文件名（主色）+ 分类 + 日期 */}
            <Stack direction="row" spacing={1}
              sx={{ alignItems: "center", flexWrap: "wrap", rowGap: 1 }}>
              <DescriptionIcon color="primary" fontSize="small" />
              <Typography sx={{ fontWeight: 600, color: "primary.main", wordBreak: "break-all" }}>
                {frag.filename}
              </Typography>
              <Chip size="small" color={categoryColor(frag.category)} variant="outlined"
                label={frag.category} />
              <Typography variant="caption" color="text.secondary">
                {fmtDate(frag.uploaded_at)}
              </Typography>
            </Stack>
            <Divider />
            {/* 完整片段正文：Markdown 渲染成排版，其余保留换行 */}
            <Box sx={{ overflowY: "auto", flex: 1 }}>
              {isMarkdown ? (
                <Markdown>{frag.text}</Markdown>
              ) : (
                <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }}>
                  {frag.text}
                </Typography>
              )}
            </Box>
          </>
        )}
      </Box>
    </Drawer>
  );
}
