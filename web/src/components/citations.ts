// 把 AI 正文里的 [n] 角标（n 在来源范围内、且非 markdown 链接）替换成指向来源的锚点链接，
// 交给 Markdown 渲染成可点上标。msgKey 让不同消息的锚点 id 唯一，避免跨消息串号。
export function linkifyCitations(content: string, count: number, msgKey: string): string {
  if (!count || !content) return content;
  return content.replace(/\[(\d+)\](?!\()/g, (m, d) => {
    const n = Number(d);
    return n >= 1 && n <= count ? `[${n}](#cite-${msgKey}-${n})` : m;
  });
}

export function citeId(msgKey: string, index: number): string {
  return `cite-${msgKey}-${index}`;
}
