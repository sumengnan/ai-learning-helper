import { useEffect, useState } from "react";
import {
  Box, Typography, Card, CardContent, TextField, ToggleButton, ToggleButtonGroup,
  Button, Alert, List, ListItem, ListItemText, IconButton, Chip, Stack,
} from "@mui/material";
import DeleteIcon from "@mui/icons-material/Delete";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

interface Question { id: string; type: string; stem: string; source: string; }

export default function QuestionBankView() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>(["single"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => api.questions.list().then(setQuestions);
  useEffect(() => { refresh(); }, []);

  const generate = async () => {
    if (!topic.trim() || types.length === 0) return;
    setBusy(true); setError("");
    try {
      await api.questions.generate(topic.trim(), count, types);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "出题失败");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (id: string) => { await api.questions.remove(id); await refresh(); };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>题库</Typography>
      <Card variant="outlined">
        <CardContent>
          <Stack spacing={2}>
            <TextField
              fullWidth size="small" label="出题主题（从知识库检索）"
              value={topic} onChange={(e) => setTopic(e.target.value)}
            />
            <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
              <TextField
                type="number" size="small" label="题数" sx={{ width: 96 }}
                slotProps={{ htmlInput: { min: 1, max: 20 } }}
                value={count} onChange={(e) => setCount(Number(e.target.value))}
              />
              <ToggleButtonGroup
                size="small" value={types}
                onChange={(_, v: string[]) => setTypes(v)}
              >
                {TYPES.map((t) => (
                  <ToggleButton key={t.key} value={t.key}>{t.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Button
                variant="contained" onClick={generate}
                disabled={busy || !topic.trim() || types.length === 0}
              >
                {busy ? "出题中…" : "出题"}
              </Button>
            </Stack>
            {error && <Alert severity="error">{error}</Alert>}
          </Stack>
        </CardContent>
      </Card>

      {questions.length === 0 ? (
        <Typography color="text.secondary">暂无题目，先出题吧。</Typography>
      ) : (
        <List component="div" sx={{ display: "flex", flexDirection: "column", gap: 1, py: 0 }}>
          <AnimatePresence initial={false}>
          {questions.map((q) => (
            <motion.div key={q.id} layout variants={listItemVariants}
              initial="initial" animate="animate" exit="exit">
              <ListItem
                component="div"
                sx={{ border: 1, borderColor: "divider", borderRadius: 2 }}
                secondaryAction={
                  <IconButton edge="end" color="error" onClick={() => remove(q.id)} aria-label="删除题目">
                    <DeleteIcon />
                  </IconButton>
                }
              >
                <Chip size="small" label={TYPES.find((t) => t.key === q.type)?.label ?? q.type} sx={{ mr: 1 }} />
                <ListItemText
                  primary={q.stem}
                  secondary={q.source ? `· ${q.source}` : undefined}
                />
              </ListItem>
            </motion.div>
          ))}
          </AnimatePresence>
        </List>
      )}
    </Box>
  );
}
