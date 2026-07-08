import type { Conversation } from "../types";
export function ConversationList({ items, activeId, onSelect, onNew, onDelete }: {
  items: Conversation[]; activeId: string | null;
  onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void;
}) {
  return (
    <div className="w-60 border-r flex flex-col">
      <button className="m-2 bg-blue-500 text-white rounded py-2" onClick={onNew}>+ 新对话</button>
      <div className="flex-1 overflow-y-auto">
        {items.map((c) => (
          <div key={c.id}
            className={`px-3 py-2 cursor-pointer flex justify-between group ${c.id === activeId ? "bg-gray-200" : "hover:bg-gray-100"}`}
            onClick={() => onSelect(c.id)}>
            <span className="truncate">{c.title}</span>
            <button className="opacity-0 group-hover:opacity-100 text-red-500"
              onClick={(e) => { e.stopPropagation(); onDelete(c.id); }}>×</button>
          </div>
        ))}
      </div>
    </div>
  );
}
