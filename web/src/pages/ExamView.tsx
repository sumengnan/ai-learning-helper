import { useState } from "react";
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

  const toggleType = (k: string) =>
    setTypes((ts) => (ts.includes(k) ? ts.filter((t) => t !== k) : [...ts, k]));

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
      <div className="p-6 space-y-3">
        <h2 className="text-xl font-bold">成绩：{result.correct}/{result.total}（{result.score} 分）</h2>
        <ul className="space-y-2">
          {result.detail.map((d, i) => (
            <li key={i} className={`border rounded p-3 ${d.correct ? "border-green-400" : "border-red-400"}`}>
              <div>{d.correct ? "✅" : "❌"} {d.stem}</div>
              {!d.correct && <div className="text-sm text-gray-600">正确答案：{JSON.stringify(d.correct_answer)}</div>}
              {d.explanation && <div className="text-sm text-gray-500">解析：{d.explanation}</div>}
              {d.feedback && <div className="text-sm text-blue-600">点评：{d.feedback}</div>}
            </li>
          ))}
        </ul>
        <button onClick={() => { setPaper([]); setResult(null); }}
          className="bg-blue-600 text-white px-3 py-1 rounded">再考一次</button>
      </div>
    );
  }

  if (paper.length === 0) {
    return (
      <div className="p-6 space-y-3">
        <h2 className="text-xl font-bold">模拟考试</h2>
        <div className="flex items-center gap-3 flex-wrap">
          <label>题数
            <input type="number" min={1} max={20} value={count}
              onChange={(e) => setCount(Number(e.target.value))}
              className="border rounded px-2 py-1 w-16 ml-1" />
          </label>
          {TYPES.map((t) => (
            <label key={t.key} className="flex items-center gap-1">
              <input type="checkbox" checked={types.includes(t.key)}
                onChange={() => toggleType(t.key)} />{t.label}
            </label>
          ))}
          <button onClick={start} disabled={busy}
            className="bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50">
            {busy ? "组卷中…" : "开始考试"}
          </button>
        </div>
        <p className="text-gray-500 text-sm">不勾题型=全部题型。若无题，请先到题库出题。</p>
        {error && <p className="text-red-600 text-sm">{error}</p>}
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-bold">答题（{paper.length} 题）</h2>
      {paper.map((q, qi) => (
        <div key={q.id} className="border rounded p-3 space-y-1">
          <div>{qi + 1}. {q.stem}</div>
          {q.type === "single" && q.options?.map((o, i) => (
            <label key={i} className="block">
              <input type="radio" name={q.id} onChange={() => setAns(q.id, i)} /> {o}
            </label>
          ))}
          {q.type === "multiple" && q.options?.map((o, i) => (
            <label key={i} className="block">
              <input type="checkbox" onChange={() => toggleMulti(q.id, i)} /> {o}
            </label>
          ))}
          {q.type === "truefalse" && (
            <div>
              <label className="mr-3"><input type="radio" name={q.id} onChange={() => setAns(q.id, true)} /> 对</label>
              <label><input type="radio" name={q.id} onChange={() => setAns(q.id, false)} /> 错</label>
            </div>
          )}
          {q.type === "short" && (
            <textarea className="border rounded w-full p-1"
              onChange={(e) => setAns(q.id, e.target.value)} />
          )}
        </div>
      ))}
      <button onClick={submit} disabled={busy}
        className="bg-green-600 text-white px-4 py-1 rounded disabled:opacity-50">
        {busy ? "判分中…" : "交卷"}
      </button>
      {error && <p className="text-red-600 text-sm">{error}</p>}
    </div>
  );
}
