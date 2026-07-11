// 耗时格式化为「几分几秒」；不足 1 分只显示秒
export function fmtDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  if (total < 60) return `${total} 秒`;
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m} 分 ${s} 秒`;
}
