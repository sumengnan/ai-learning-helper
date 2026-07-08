import { useEffect, useState } from "react";
import { api } from "../api/client";

interface Download {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  created_at: string;
}

export default function DownloadsView() {
  const [items, setItems] = useState<Download[]>([]);

  const refresh = () => api.downloads.list().then(setItems);
  useEffect(() => { refresh(); }, []);

  const remove = async (id: string) => {
    await api.downloads.remove(id);
    await refresh();
  };

  return (
    <div className="p-6 space-y-3">
      <h2 className="text-xl font-bold">下载管理</h2>
      {items.length === 0 ? (
        <p className="text-gray-500">暂无文件。聊天中让助手用 save_download 保存内容后会出现在这里。</p>
      ) : (
        <ul className="space-y-2">
          {items.map((d) => (
            <li key={d.id} className="border rounded p-3 flex justify-between items-center gap-3">
              <div className="flex items-center gap-3 min-w-0">
                {d.content_type.startsWith("image/") && (
                  <img src={`/api/downloads/${d.id}`} alt={d.filename}
                    className="w-12 h-12 object-cover rounded border" />
                )}
                <div className="min-w-0">
                  <div className="truncate">{d.filename}</div>
                  <div className="text-xs text-gray-400">
                    {d.content_type} · {d.size} 字节 · {d.created_at.slice(0, 10)}
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                <a href={`/api/downloads/${d.id}`} download={d.filename}
                  className="text-blue-600 text-sm">下载</a>
                <button onClick={() => remove(d.id)} className="text-red-600 text-sm">删除</button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
