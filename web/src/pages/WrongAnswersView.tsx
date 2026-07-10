import { useEffect, useState } from "react";
import { Box, Typography, Button, Card, CardContent, Checkbox, Chip, Stack } from "@mui/material";
import DeleteSweepIcon from "@mui/icons-material/DeleteSweep";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api/client";
import { listItemVariants } from "../components/motion";

interface Wrong {
  id: string;
  user_answer: unknown;
  snapshot: { type: string; stem: string; answer: unknown; explanation: string };
}

export default function WrongAnswersView() {
  const [items, setItems] = useState<Wrong[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const refresh = () => api.wrong.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const toggle = (id: string) =>
    setSelected((s) => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });

  const removeSelected = async () => {
    if (selected.size === 0) return;
    await api.wrong.removeMany([...selected]);
    setSelected(new Set());
    await refresh();
  };

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Box sx={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>错题集</Typography>
        <Button
          variant="contained" color="error" startIcon={<DeleteSweepIcon />}
          onClick={removeSelected} disabled={selected.size === 0}
        >
          批量删除（{selected.size}）
        </Button>
      </Box>
      {items.length === 0 ? (
        <Typography color="text.secondary">暂无错题。</Typography>
      ) : (
        <Stack spacing={1.5} component="div">
          <AnimatePresence initial={false}>
          {items.map((w) => (
            <motion.div key={w.id} layout variants={listItemVariants}
              initial="initial" animate="animate" exit="exit">
            <Card variant="outlined">
              <CardContent sx={{ display: "flex", gap: 1 }}>
                <Checkbox
                  sx={{ p: 0, mt: 0.25 }}
                  checked={selected.has(w.id)}
                  onChange={() => toggle(w.id)}
                />
                <Box>
                  <Typography component="div">
                    <Chip size="small" label={w.snapshot.type} sx={{ mr: 1 }} />
                    {w.snapshot.stem}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    你的作答：{JSON.stringify(w.user_answer)}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    正确答案：{JSON.stringify(w.snapshot.answer)}
                  </Typography>
                  {w.snapshot.explanation && (
                    <Typography variant="body2" color="text.disabled">
                      解析：{w.snapshot.explanation}
                    </Typography>
                  )}
                </Box>
              </CardContent>
            </Card>
            </motion.div>
          ))}
          </AnimatePresence>
        </Stack>
      )}
    </Box>
  );
}
