// web/src/pages/KnowledgeView.tsx
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";

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
    <div className="p-6 max-w-2xl">
      <h1 className="text-xl font-bold mb-4">知识库</h1>
      <div className="mb-4">
        <input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" disabled={busy}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); }} />
        {busy && <span className="ml-2 text-gray-500">上传中…</span>}
        {error && <div className="text-red-600 mt-1">{error}</div>}
      </div>
      {docs.length === 0 ? (
        <div className="text-gray-400">还没有上传文档</div>
      ) : (
        <ul className="divide-y border rounded">
          {docs.map((d) => (
            <li key={d.id} className="flex justify-between items-center px-3 py-2">
              <span>{d.filename} <span className="text-xs text-gray-400">· {d.num_chunks} 块</span></span>
              <button className="text-red-500" onClick={() => remove(d.id)}>删除</button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
