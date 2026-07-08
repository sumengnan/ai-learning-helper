import { useEffect, useState } from "react";
import { api } from "../api/client";

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
    <div className="p-6 space-y-3">
      <div className="flex justify-between items-center">
        <h2 className="text-xl font-bold">错题集</h2>
        <button onClick={removeSelected} disabled={selected.size === 0}
          className="bg-red-600 text-white px-3 py-1 rounded disabled:opacity-50">
          批量删除（{selected.size}）
        </button>
      </div>
      {items.length === 0 ? (
        <p className="text-gray-500">暂无错题。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((w) => (
            <li key={w.id} className="border rounded p-3 flex gap-2">
              <input type="checkbox" checked={selected.has(w.id)} onChange={() => toggle(w.id)} />
              <div>
                <div><span className="text-xs bg-gray-200 rounded px-1 mr-2">{w.snapshot.type}</span>{w.snapshot.stem}</div>
                <div className="text-sm text-gray-600">你的作答：{JSON.stringify(w.user_answer)}</div>
                <div className="text-sm text-gray-500">正确答案：{JSON.stringify(w.snapshot.answer)}</div>
                {w.snapshot.explanation && <div className="text-sm text-gray-400">解析：{w.snapshot.explanation}</div>}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
