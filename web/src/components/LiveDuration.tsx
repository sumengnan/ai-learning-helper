import { useEffect, useState } from "react";
import { fmtDuration } from "./duration";

// 进行中的实时耗时：每秒自增。startedAt 为 epoch 毫秒，与 Date.now() 相减得耗时。
// 调用方须自行确保「确实还在跑」才渲染本组件——否则已停止/已中断的东西会一直读秒下去。
export function LiveDuration({ startedAt, format = fmtDuration }: {
  startedAt: number;
  format?: (ms: number) => string;
}) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return <>{format(now - startedAt)}</>;
}
