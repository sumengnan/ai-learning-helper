// web/src/pages/KnowledgeView.tsx
import { useEffect, useRef, useState } from "react";
import {
  Box, Typography, Button, List, ListItem, ListItemText, IconButton,
  CircularProgress, Alert,
} from "@mui/material";
import UploadFileIcon from "@mui/icons-material/UploadFile";
import DeleteIcon from "@mui/icons-material/Delete";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";

type Doc = { id: string; filename: string; num_chunks: number; uploaded_at: string };

export function KnowledgeView() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const refresh = () => api.documents.list().then(setDocs);
  useEffect(() => { refresh(); }, []);

  async function upload(file: File) {
    setBusy(true); setError(null);
    try { await api.documents.upload(file); await refresh(); }
    catch (e: any) { setError(String(e?.message || e)); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  }
  async function remove(id: string) { await api.documents.remove(id); await refresh(); }

  return (
    <Box sx={{ p: 3, maxWidth: 720 }}>
      <Typography variant="h5" gutterBottom sx={{ fontWeight: 700 }}>知识库</Typography>
      <Box sx={{ mb: 2, display: "flex", alignItems: "center", gap: 1 }}>
        <Button component="label" variant="outlined" startIcon={<UploadFileIcon />} disabled={busy}>
          上传文档
          <input
            ref={fileRef} hidden type="file" accept=".pdf,.docx,.txt,.md"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }}
          />
        </Button>
        {busy && <CircularProgress size={20} />}
      </Box>
      {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
      {docs.length === 0 ? (
        <Typography color="text.secondary">还没有上传文档</Typography>
      ) : (
        <List component="div" sx={{ border: 1, borderColor: "divider", borderRadius: 2 }}>
          <AnimatePresence initial={false}>
          {docs.map((d) => (
            <motion.div key={d.id} layout variants={listItemVariants}
              initial="initial" animate="animate" exit="exit">
              <ListItem
                component="div" divider
                secondaryAction={
                  <IconButton edge="end" color="error" onClick={() => remove(d.id)} aria-label="删除文档">
                    <DeleteIcon />
                  </IconButton>
                }
              >
                <ListItemText primary={d.filename} secondary={`· ${d.num_chunks} 块`} />
              </ListItem>
            </motion.div>
          ))}
          </AnimatePresence>
        </List>
      )}
    </Box>
  );
}
