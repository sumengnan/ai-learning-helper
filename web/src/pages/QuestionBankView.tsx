import { useEffect, useState } from "react";
import { api } from "../api/client";

const TYPES: { key: string; label: string }[] = [
  { key: "single", label: "单选" },
  { key: "multiple", label: "多选" },
  { key: "truefalse", label: "判断" },
  { key: "short", label: "简答" },
];

interface Question {
  id: string;
  type: string;
  stem: string;
  source: string;
}

export default function QuestionBankView() {
  const [questions, setQuestions] = useState<Question[]>([]);
  const [topic, setTopic] = useState("");
  const [count, setCount] = useState(5);
  const [types, setTypes] = useState<string[]>(["single"]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = () => api.questions.list().then(setQuestions);
  useEffect(() => { refresh(); }, []);

  const toggleType = (k: string) =>
    setTypes((ts) => (ts.includes(k) ? ts.filter((t) => t !== k) : [...ts, k]));

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

  const remove = async (id: string) => {
    await api.questions.remove(id);
    await refresh();
  };

  return (
    <div className="p-6 space-y-4">
      <h2 className="text-xl font-bold">题库</h2>
      <div className="space-y-2 border rounded p-4">
        <input
          className="border rounded px-2 py-1 w-full" placeholder="出题主题（从知识库检索）"
          value={topic} onChange={(e) => setTopic(e.target.value)} />
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
          <button onClick={generate} disabled={busy || !topic.trim() || types.length === 0}
            className="bg-blue-600 text-white px-3 py-1 rounded disabled:opacity-50">
            {busy ? "出题中…" : "出题"}
          </button>
        </div>
        {error && <p className="text-red-600 text-sm">{error}</p>}
      </div>

      {questions.length === 0 ? (
        <p className="text-gray-500">暂无题目，先出题吧。</p>
      ) : (
        <ul className="space-y-2">
          {questions.map((q) => (
            <li key={q.id} className="border rounded p-3 flex justify-between items-start">
              <div>
                <span className="text-xs bg-gray-200 rounded px-1 mr-2">{q.type}</span>
                {q.stem}
                {q.source && <span className="text-xs text-gray-400 ml-2">· {q.source}</span>}
              </div>
              <button onClick={() => remove(q.id)}
                className="text-red-600 text-sm ml-3">删除</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
