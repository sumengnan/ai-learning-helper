import { useState } from "react";
import {
  Box, Typography, Card, CardContent, TextField, ToggleButton, ToggleButtonGroup,
  Button, Alert, Chip, Radio, RadioGroup, FormControlLabel, Checkbox, Stack,
} from "@mui/material";
import { api } from "../api/client";

interface PaperQ { id: string; type: string; stem: string; options: string[] | null; }
interface DetailItem {
  question_id: string; type?: string; stem?: string; correct: boolean;
  correct_answer?: unknown; explanation?: string; feedback?: string | null;
}
interface Result { total: number; correct: number; score: number; detail: DetailItem[]; }

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

export default function ExamView() {
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>([]);
  const [paper, setPaper] = useState<PaperQ[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    setBusy(true); setResult(null); setAnswers({}); setError("");
    try {
      const data = await api.exams.compose(count, types.length ? types : null);
      if (!data.questions.length) {
        setError("题库暂无符合条件的题，请先到题库出题。");
      } else {
        setPaper(data.questions);
      }
    } catch {
      setError("组卷失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  const setAns = (id: string, v: unknown) => setAnswers((a) => ({ ...a, [id]: v }));
  const toggleMulti = (id: string, idx: number) =>
    setAnswers((a) => {
      const cur = (a[id] as number[] | undefined) || [];
      return { ...a, [id]: cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx] };
    });

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const payload = paper.map((q) => ({ question_id: q.id, user_answer: answers[q.id] ?? null }));
      setResult(await api.exams.submit(payload));
    } catch {
      setError("交卷失败，请重试。");
    } finally {
      setBusy(false);
    }
  };

  if (result) {
    return (
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography variant="h5" component="div" sx={{ fontWeight: 700 }}>
          成绩：{result.correct}/{result.total}
          <Chip label={`${result.score} 分`} color="primary" sx={{ ml: 1 }} />
        </Typography>
        <Stack spacing={1.5}>
          {result.detail.map((d, i) => (
            <Card key={i} variant="outlined" sx={{ borderColor: d.correct ? "success.main" : "error.main" }}>
              <CardContent>
                <Typography>{d.correct ? "✅" : "❌"} {d.stem}</Typography>
                {!d.correct && (
                  <Typography variant="body2" color="text.secondary">
                    正确答案：{JSON.stringify(d.correct_answer)}
                  </Typography>
                )}
                {d.explanation && (
                  <Typography variant="body2" color="text.secondary">解析：{d.explanation}</Typography>
                )}
                {d.feedback && (
                  <Typography variant="body2" color="primary">点评：{d.feedback}</Typography>
                )}
              </CardContent>
            </Card>
          ))}
        </Stack>
        <Box>
          <Button variant="contained" onClick={() => { setPaper([]); setResult(null); }}>
            再考一次
          </Button>
        </Box>
      </Box>
    );
  }

  if (paper.length === 0) {
    return (
      <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
        <Typography variant="h5" sx={{ fontWeight: 700 }}>模拟考试</Typography>
        <Card variant="outlined">
          <CardContent>
            <Stack direction="row" spacing={2} sx={{ alignItems: "center", flexWrap: "wrap" }}>
              <TextField
                type="number" size="small" label="题数" sx={{ width: 96 }}
                slotProps={{ htmlInput: { min: 1, max: 20 } }}
                value={count} onChange={(e) => setCount(Number(e.target.value))}
              />
              <ToggleButtonGroup size="small" value={types} onChange={(_, v: string[]) => setTypes(v)}>
                {TYPES.map((t) => (
                  <ToggleButton key={t.key} value={t.key}>{t.label}</ToggleButton>
                ))}
              </ToggleButtonGroup>
              <Button variant="contained" onClick={start} disabled={busy}>
                {busy ? "组卷中…" : "开始考试"}
              </Button>
            </Stack>
          </CardContent>
        </Card>
        <Typography variant="body2" color="text.secondary">
          不勾题型=全部题型。若无题，请先到题库出题。
        </Typography>
        {error && <Alert severity="error">{error}</Alert>}
      </Box>
    );
  }

  return (
    <Box sx={{ p: 3, display: "flex", flexDirection: "column", gap: 2 }}>
      <Typography variant="h5" sx={{ fontWeight: 700 }}>答题（{paper.length} 题）</Typography>
      {paper.map((q, qi) => (
        <Card key={q.id} variant="outlined">
          <CardContent>
            <Typography gutterBottom>{qi + 1}. {q.stem}</Typography>
            {q.type === "single" && (
              <RadioGroup onChange={(e) => setAns(q.id, Number(e.target.value))}>
                {q.options?.map((o, i) => (
                  <FormControlLabel key={i} value={i} control={<Radio />} label={o} />
                ))}
              </RadioGroup>
            )}
            {q.type === "multiple" && q.options?.map((o, i) => (
              <FormControlLabel
                key={i}
                control={<Checkbox onChange={() => toggleMulti(q.id, i)} />}
                label={o}
              />
            ))}
            {q.type === "truefalse" && (
              <RadioGroup row onChange={(e) => setAns(q.id, e.target.value === "true")}>
                <FormControlLabel value="true" control={<Radio />} label="对" />
                <FormControlLabel value="false" control={<Radio />} label="错" />
              </RadioGroup>
            )}
            {q.type === "short" && (
              <TextField fullWidth multiline minRows={2} onChange={(e) => setAns(q.id, e.target.value)} />
            )}
          </CardContent>
        </Card>
      ))}
      <Box>
        <Button variant="contained" color="success" onClick={submit} disabled={busy}>
          {busy ? "判分中…" : "交卷"}
        </Button>
      </Box>
      {error && <Alert severity="error">{error}</Alert>}
    </Box>
  );
}
