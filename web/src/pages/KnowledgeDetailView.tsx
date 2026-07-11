// web/src/pages/KnowledgeDetailView.tsx
import { useEffect, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  Box, Typography, Card, CardContent, Chip, Stack, IconButton,
  CircularProgress, Alert, Divider,
} from "@mui/material";
import ArrowBackIcon from "@mui/icons-material/ArrowBack";
import DescriptionIcon from "@mui/icons-material/Description";
import { api } from "../api/client";
import { categoryColor, fmtDate } from "./knowledgeUtils";

type FragmentDetail = {
  id: string; filename: string; text: string;
  category: string; uploaded_at: string; doc_id: string;
};

export function KnowledgeDetailView() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const [frag, setFrag] = useState<FragmentDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true); setError(null);
    api.documents.get(id)
      .then(setFrag)
      .catch((e: any) => setError(String(e?.message || e)))
      .finally(() => setLoading(false));
  }, [id]);

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2, maxWidth: 880, mx: "auto" }}>
      <Stack direction="row" spacing={1} sx={{ alignItems: "center" }}>
        <IconButton size="small" aria-label="返回" onClick={() => navigate("/knowledge")}>
          <ArrowBackIcon />
        </IconButton>
        <Typography variant="h6" sx={{ fontWeight: 700 }}>片段详情</Typography>
      </Stack>

      {loading && <CircularProgress size={24} />}
      {error && <Alert severity="error">{error}</Alert>}

      {frag && (
        <Card variant="outlined">
          <CardContent sx={{ display: "flex", flexDirection: "column", gap: 1.5 }}>
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
            {/* 完整片段正文 */}
            <Typography variant="body1" sx={{ whiteSpace: "pre-wrap", lineHeight: 1.8 }}>
              {frag.text}
            </Typography>
          </CardContent>
        </Card>
      )}
    </Box>
  );
}
