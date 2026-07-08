import type { ChatMessage } from "../types";
export function AgentProgress({ steps }: { steps: NonNullable<ChatMessage["steps"]> }) {
  if (!steps.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {steps.map((s, i) => (
        <details key={i} className="text-xs bg-gray-100 rounded px-2 py-1">
          <summary className={s.isError ? "text-red-600" : "text-gray-600"}>
            {s.result === undefined ? "调用工具" : "工具完成"}：{s.tool}
          </summary>
          <div className="mt-1 text-gray-500 break-all">参数：{JSON.stringify(s.args)}</div>
          {s.result !== undefined && <div className="mt-1 break-all">结果：{s.result}</div>}
        </details>
      ))}
    </div>
  );
}
