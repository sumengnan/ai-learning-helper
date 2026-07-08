import { useState } from "react";
import { api } from "../api/client";

interface PaperQ { id: string; type: string; stem: string; options: string[] | null; }
interface DetailItem {
  question_id: string; type?: string; stem?: string; correct: boolean;
  correct_answer?: unknown; explanation?: string; feedback?: string | null;
}
interface Result { total: number; correct: number; score: number; detail: DetailItem[]; }

export default function ExamView() {
  const [count, setCount] = useState(5);
  const [paper, setPaper] = useState<PaperQ[]>([]);
  const [answers, setAnswers] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  const start = async () => {
    setBusy(true); setResult(null); setAnswers({});
    const data = await api.exams.compose(count, null);
    setPaper(data.questions);
    setBusy(false);
  };

  const setAns = (id: string, v: unknown) => setAnswers((a) => ({ ...a, [id]: v }));

  const toggleMulti = (id: string, idx: number) =>
    setAnswers((a) => {
      const cur = (a[id] as number[] | undefined) || [];
      return { ...a, [id]: cur.includes(idx) ? cur.filter((i) => i !== idx) : [...cur, idx] };
    });

  const submit = async () => {
    setBusy(true);
    const payload = paper.map((q) => ({ question_id: q.id, user_answer: answers[q.id] ?? null }));
    setResult(await api.exams.submit(payload));
    setBusy(false);
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
        <label>题数
          <input type="number" min={1} max={20} value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            className="border rounded px-2 py-1 w-16 ml-1" />
        </label>
        <button onClick={start} disabled={busy}
          className="bg-blue-600 text-white px-3 py-1 rounded ml-3 disabled:opacity-50">
          {busy ? "组卷中…" : "开始考试"}
        </button>
        <p className="text-gray-500 text-sm">若提示无题，请先到题库出题。</p>
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
    </div>
  );
}
